# linuxserver.io's Alpine 3.23 base: s6-overlay, PUID/PGID/UMASK/TZ, the abc
# user, cron and docker mods, as in every linuxserver.io container.
# Pinned by digest (a multi-arch index); Dependabot proposes new digests.
FROM ghcr.io/linuxserver/baseimage-alpine:3.24@sha256:e4772029b98af17b6670341d07cbd54138a3dc7f6323af1ef76bbc02fd0a813d

# image.source makes a GHCR package inherit the repository's visibility.
LABEL org.opencontainers.image.source="https://github.com/Tom-Joad/ucg-config-backup" \
      org.opencontainers.image.title="ucg-config-backup" \
      org.opencontainers.image.description="Scheduled local config backups of a UniFi OS console (UCG Ultra, UDM, ...), no cloud connection needed" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /tmp/requirements.txt

# /lsiopy is linuxserver.io's place for a Python venv and already on PATH.
# pip is only needed to build the image. It is removed afterwards: that drops
# its CVE surface (e.g. CVE-2025-8869) and anything an attacker could use to
# pull code into a running container.
RUN apk add --no-cache python3 \
 && python3 -m venv /lsiopy \
 && /lsiopy/bin/pip install --no-cache-dir --requirement /tmp/requirements.txt \
 && /lsiopy/bin/pip uninstall --yes pip \
 && rm /tmp/requirements.txt

COPY backup.py /usr/local/bin/backup.py
# s6 services (init-ucg-config, svc-ucg-initial) and the cron wrapper.
COPY root/ /
RUN chmod +x /usr/local/bin/backup.py /usr/local/bin/ucg-backup \
 && chmod +x /etc/s6-overlay/s6-rc.d/init-ucg-config/run /etc/s6-overlay/s6-rc.d/svc-ucg-initial/run \
 && printf 'ucg-config-backup version: %s\n' "$(python3 /usr/local/bin/backup.py --version)" > /build_version

# Crontab and the container's own state.
VOLUME /config
# Backups are written here, owned by PUID:PGID.
VOLUME /backups

HEALTHCHECK --interval=5m --timeout=5s --retries=3 \
  CMD pgrep crond || exit 1

# The entrypoint stays the base image's /init (s6-overlay), which must run as
# PID 1: don't add `--init` to `docker run`, and don't use `--user` (set
# PUID/PGID instead).
