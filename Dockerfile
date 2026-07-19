FROM python:3.12-alpine

RUN apk add --no-cache tzdata curl \
    && pip install --no-cache-dir requests \
    # pip is build-time only; removing it from the runtime image drops its
    # CVE surface (e.g. CVE-2025-8869) and anything an attacker could use
    # to pull code into a running container.
    && pip uninstall -y pip \
    && mkdir -p /etc/crontabs /backups

COPY entrypoint.sh /entrypoint.sh
COPY backup.py /usr/local/bin/backup.py
RUN chmod +x /entrypoint.sh /usr/local/bin/backup.py

VOLUME ["/backups"]

HEALTHCHECK --interval=5m --timeout=5s --retries=3 \
  CMD pgrep crond || exit 1

ENTRYPOINT ["/entrypoint.sh"]
