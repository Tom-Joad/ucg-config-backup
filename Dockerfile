FROM python:3.12-alpine

RUN apk add --no-cache tzdata curl \
    && pip install --no-cache-dir requests \
    && mkdir -p /etc/crontabs /backups

COPY entrypoint.sh /entrypoint.sh
COPY backup.py /usr/local/bin/backup.py
RUN chmod +x /entrypoint.sh /usr/local/bin/backup.py

VOLUME ["/backups"]

HEALTHCHECK --interval=5m --timeout=5s --retries=3 \
  CMD pgrep crond || exit 1

ENTRYPOINT ["/entrypoint.sh"]
