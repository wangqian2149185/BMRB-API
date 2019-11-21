#!/usr/bin/python3

import csv
import logging
import xml.etree.ElementTree as ET

import psycopg2
import psycopg2.extras
import requests


# Todo: Check that uniprot is valid and not expired
# See: 26802	1	A	5O6F	D9Q632	D9QDZ8

# Todo: Dealing with when there are chains that aren't perfectly mapped


class PostgresHelper:
    """ Makes it more convenient to query postgres. It implements a context manager to ensure that the connection
    is closed.

     Since we never write to the DB using this class, no need to commit before closing. """

    def __init__(self,
                 host='localhost',
                 user='bmrb',
                 database='bmrbeverything',
                 port='5902',
                 cursor_factory=psycopg2.extras.DictCursor):
        self._host = host
        self._user = user
        self._database = database
        self._port = port
        self._cursor_factory = cursor_factory

    def __enter__(self):
        self._conn = psycopg2.connect(host=self._host, user=self._user, database=self._database,
                                      port=self._port, cursor_factory=self._cursor_factory)
        return self._conn, self._conn.cursor()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._conn.close()


class MappingFile:

    def __init__(self, file_name):
        self._file_name = file_name
        self.mapping = {}

    def __enter__(self):
        try:
            with open(self._file_name, 'r') as input_file:
                self.mapping = {x[0]: x[1] for x in csv.reader(input_file)}
        except FileNotFoundError:
            self.mapping = {}
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save()

    def save(self):
        with open(self._file_name, 'w') as out_file:
            csv_writer = csv.writer(out_file)
            csv_writer.writerows([[x, self.mapping[x]] for x in self.mapping.keys()])


class UniProtMapper(MappingFile):

    def get_uniprot(self, nickname: str):
        """ Returns the official UniProt ID from the nickname.

         Example: P4R3A_HUMAN -> Q6IN85"""

        if nickname:
            logging.info(f'Getting UniProt from nickname {nickname}')
        else:
            return None

        nickname = nickname.upper()

        if "_" not in nickname:
            return nickname

        # In case they specified multiple things
        if " " in nickname:
            for item in nickname.split(" "):
                nick = self.get_uniprot(item)
                if nick:
                    return nick
            return None

        if nickname in self.mapping:
            return self.mapping[nickname]

        mapping_url = f'https://www.uniprot.org/uniprot/?query={nickname}&sort=score&desc=' \
                      '&compress=no&fil=&limit=1&force=no&preview=true&format=tab&columns=id'
        result = requests.get(mapping_url).text.split('\n')[1]
        self.mapping[nickname] = result

        return result


class PDBMapper(MappingFile):

    def get_uniprot(self, pdb_id: str, chain: str):
        """ Returns the official UniProt ID from the PDB ID and chain."""

        if pdb_id and chain:
            logging.info(f'Getting UniProt from PDB {pdb_id}.{chain}')

        if not pdb_id:
            return None

        if chain:
            key = f'{pdb_id}.{chain}'
        else:
            key = pdb_id
        if key in self.mapping:
            return self.mapping[key]

        # Load the PDB ID chains into the mapping
        mapping_url = f'http://www.rcsb.org/pdb/rest/describeMol?structureId={pdb_id}'
        root = ET.fromstring(requests.get(mapping_url).text)
        num_polymers = len(list(root.iter('polymer')))
        for polymer in root.iter('polymer'):
            # Get the UniProt for the polymer
            try:
                uniprot_accession = polymer.findall('macroMolecule/accession')[0].attrib.get('id', None)
            except IndexError:
                uniprot_accession = None

            # Add each chain
            for chain_elem in polymer.findall('chain'):
                pdb_chain = chain_elem.attrib.get('id')
                chain_key = f'{pdb_id}.{pdb_chain}'

                self.mapping[chain_key] = uniprot_accession

            # Add the whole ID
            if num_polymers == 1:
                self.mapping[pdb_id] = uniprot_accession

        # If the chain isn't in the PDB file
        if key not in self.mapping:
            logging.warning("Unknown chain: %s", key)
            self.mapping[key] = None

        return self.mapping.get(key, None)


with PostgresHelper() as psql_api:
    sql = '''
    SELECT ent."Entry_ID"                                              AS bmrb_id,
           ent."ID"                                                    AS entity_id,
           upper(coalesce(ea."PDB_chain_ID", ent."Polymer_strand_ID")) AS pdb_chain,
           upper(pdb_id)                                               AS pdb_id,
           CASE
               WHEN dbl."Accession_code" IS NOT NULL THEN 'Author supplied'
               ELSE null
               END                                                     as link_type,
           dbl."Accession_code"                                        AS uniprot_id,
           "Polymer_seq_one_letter_code"                               AS sequence,
           ent."Details"
    FROM macromolecules."Entity" AS ent
             LEFT JOIN web.pdb_link AS pdb ON pdb.bmrb_id = ent."Entry_ID"
             LEFT JOIN macromolecules."Entity_db_link" AS dbl
                       ON dbl."Entry_ID" = ent."Entry_ID" AND ent."ID" = dbl."Entity_ID"
             LEFT JOIN macromolecules."Entity_assembly" AS ea
                       ON ea."Entry_ID" = ent."Entry_ID" AND ea."Entity_ID" = ent."ID"
    WHERE "Polymer_seq_one_letter_code" IS NOT NULL
      AND "Polymer_seq_one_letter_code" != ''
      AND ent."Polymer_type" = 'polypeptide(L)'
      AND (dbl."Accession_code" IS NULL
        OR (dbl."Author_supplied" = 'yes' AND
            (lower(dbl."Database_code") = 'unp' OR lower(dbl."Database_code") = 'uniprot' OR
             lower(dbl."Database_code") = 'sp')))
    ORDER BY ent."Entry_ID"::int, ent."ID"::int;'''
    psql_api[1].execute(sql)
    sequences = psql_api[1].fetchall()
    for line in sequences:
        for pos, item in enumerate(line):
            if item is None:
                line[pos] = ''

with UniProtMapper('uniname.csv') as uni_name, PDBMapper('pdb_uniprot.csv') as pdb_map, \
        open('sequences_uniprot.csv', 'w') as uniprot_seq_file:
    sequences_out = csv.writer(uniprot_seq_file)
    sequences_out.writerow(
        ['bmrb_id', 'entity_id', 'pdb_chain', 'pdb_id', 'link_type', 'uniprot_id', 'protein_sequence', 'details'])

    for line in sequences:
        pdb_id = line[3].upper()
        full_chain_id = line[2].upper()
        if full_chain_id:
            chain_id = full_chain_id[0]
        else:
            chain_id = None

        # Only search for a UniProt ID if we don't already have an author one
        if not line[5]:
            uniprot_id = pdb_map.get_uniprot(pdb_id, chain_id)
            if uniprot_id:
                line[4] = 'PDB cross-referencing'
                line[5] = uniprot_id
            else:
                if len(full_chain_id) > 1 and pdb_id:
                    logging.info('A multichar sequence didn\'t match: %s.%s', pdb_id, full_chain_id)

        # Make sure the author didn't provide a nickname
        if "." in line[5]:
            line[5] = line[5].replace('.', '-')
        if "_" in line[5]:
            line[5] = uni_name.get_uniprot(line[5])

        sequences_out.writerow(line)
uniprot_seq_file.close()


# Put it in postgresql
def row_gen():
    with open('sequences_uniprot.csv', 'r') as seq_file_read:
        csv_reader = csv.reader(seq_file_read)
        next(csv_reader)
        for each_line in csv_reader:
            yield each_line


with PostgresHelper() as psql_api:
    api_conn, api_cur = psql_api[0], psql_api[1]

    create_sql = '''
DROP TABLE IF EXISTS web.uniprot_mappings_tmp CASCADE;
CREATE TABLE IF NOT EXISTS web.uniprot_mappings_tmp
(
    id                     serial primary key,
    bmrb_id                text,
    entity_id              int,
    pdb_chain              text,
    pdb_id                 text,
    link_type              text,
    uniprot_id             text,
    protein_sequence       text,
    details                text
);
'''

    api_cur.execute(create_sql)
    insert_query = 'INSERT INTO web.uniprot_mappings_tmp (bmrb_id, entity_id, pdb_chain, pdb_id, link_type, uniprot_id, protein_sequence, details) VALUES %s'
    psycopg2.extras.execute_values(api_cur, insert_query, row_gen(), template=None, page_size=100)

    sql_insert = '''
INSERT INTO web.uniprot_mappings_tmp (bmrb_id, entity_id, pdb_chain, pdb_id, link_type, uniprot_id, protein_sequence, details)
    (SELECT dbl."Entry_ID",
            dbl."Entity_ID"::int,
            null,
            pdb_id,
            'Sequence mapping',
            REPLACE (dbl."Accession_code", '.', '-'),
            ent."Polymer_seq_one_letter_code",
            ent."Details"
     FROM macromolecules."Entity_db_link" AS dbl
              LEFT JOIN macromolecules."Entity" AS ent
                        ON dbl."Entry_ID" = ent."Entry_ID" AND ent."ID" = dbl."Entity_ID"
              LEFT JOIN macromolecules."Entity_assembly" AS ea
                        ON ea."Entry_ID" = dbl."Entry_ID" AND ea."Entity_ID" = dbl."Entity_ID"
              LEFT JOIN web.pdb_link AS pdb ON pdb.bmrb_id = ent."Entry_ID"
     WHERE dbl."Author_supplied" = 'no'
       AND dbl."Database_code" = 'SP'
    );
'''
    api_cur.execute(sql_insert)

    final_preparation = '''
DELETE FROM web.uniprot_mappings_tmp
WHERE id IN (
    SELECT
        id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER w AS rnum
        FROM web.uniprot_mappings_tmp
        WINDOW w AS (
            PARTITION BY bmrb_id, pdb_id, entity_id, link_type, uniprot_id
            ORDER BY id
        )
 
    ) t
WHERE t.rnum > 1);
DELETE FROM web.uniprot_mappings_tmp WHERE uniprot_id = '' OR uniprot_id IS NULL;
CREATE UNIQUE INDEX ON web.uniprot_mappings_tmp (bmrb_id, pdb_id, entity_id, link_type, uniprot_id);


CREATE MATERIALIZED VIEW IF NOT EXISTS web.hupo_psi_id_tmp AS
(
SELECT uni.id,
       uni.uniprot_id,
       'bmrb:' || bmrb_id                   AS source,
       entity."Polymer_seq_one_letter_code" AS "regionSequenceExperimental",
       'ECO:0001238'                        AS "experimentType",
       CASE
           WHEN cit."PubMed_ID" IS NOT NULL THEN 'pubmed:' || cit."PubMed_ID"
           WHEN cit."DOI" IS NOT NULL THEN 'doi:' || cit."DOI"
           ELSE null END                    AS "experimentReference",
       (SELECT "Date"
        from macromolecules."Release"
        WHERE "Entry_ID" = entity."Entry_ID"
        ORDER BY "Release_number"::int DESC
        LIMIT 1)                            AS "lastModified",
       uni.link_type                        AS "regionDefinitionSource"
FROM web.uniprot_mappings_tmp AS uni
         LEFT JOIN macromolecules."Entity" AS entity
                   ON entity."Entry_ID" = bmrb_id AND entity."ID"::int = entity_id
         LEFT JOIN macromolecules."Entry" AS entry ON entry."ID" = bmrb_id
         LEFT JOIN macromolecules."Citation" AS cit ON cit."Entry_ID" = entry."ID"
    AND cit."Class" = 'entry citation'
ORDER BY uniprot_id);
CREATE UNIQUE INDEX ON web.hupo_psi_id_tmp (uniprot_id, id, source, "regionSequenceExperimental",
                                                                    "experimentType", "experimentReference",
                                                                    "lastModified", "regionDefinitionSource");
                                                                    
-- Permissions
GRANT ALL PRIVILEGES ON web.hupo_psi_id_tmp to web;
GRANT ALL PRIVILEGES ON web.hupo_psi_id_tmp to bmrb;
GRANT ALL PRIVILEGES ON web.uniprot_mappings_tmp to web;
GRANT ALL PRIVILEGES ON web.uniprot_mappings_tmp to bmrb;

-- Move table and view into place
ALTER TABLE IF EXISTS web.uniprot_mappings RENAME TO uniprot_mappings_old;
ALTER TABLE web.uniprot_mappings_tmp RENAME TO uniprot_mappings;
DROP TABLE IF EXISTS uniprot_mappings_old;

ALTER MATERIALIZED VIEW IF EXISTS web.hupo_psi_id RENAME TO hupo_psi_id_tmp_old;
ALTER MATERIALIZED VIEW web.hupo_psi_id_tmp RENAME TO hupo_psi_id;
DROP MATERIALIZED VIEW IF EXISTS hupo_psi_id_tmp_old;
'''

    api_cur.execute(final_preparation)
    api_conn.commit()
