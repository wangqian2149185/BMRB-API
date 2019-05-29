FROM alpine

RUN apk update && \
    apk --no-cache add \
        bash \
        ca-certificates \
        git \
        openjdk8-jre \
        perl \
        postgresql-libs \
        python3 \
        wget \
        uwsgi-http \
        uwsgi-python3 \
        libc6-compat && \
    apk --no-cache add --virtual .build-deps \
        gcc \
        musl-dev \
        postgresql-dev \
        python3-dev && \
    update-ca-certificates && \
    apk --update add tzdata && \
    cp /usr/share/zoneinfo/America/Chicago /etc/localtime && \
    apk del tzdata && \
    python3 -m ensurepip && \
    rm -r /usr/lib/python*/ensurepip && \
    pip3 install --upgrade pip setuptools && \
    if [ ! -e /usr/bin/pip ]; then ln -s pip3 /usr/bin/pip ; fi && \
    if [[ ! -e /usr/bin/python ]]; then ln -sf /usr/bin/python3 /usr/bin/python; fi && \
    rm -r /root/.cache

COPY configs/requirements.txt /tmp/
RUN pip install --no-cache -r /tmp/requirements.txt && \
    apk del .build-deps

WORKDIR /opt/wsgi
COPY ./wsgi /opt/wsgi/

COPY wsgi.conf /opt/wsgi/wsgi.conf

CMD [ "uwsgi", "--ini", "/opt/wsgi/wsgi.conf" ]