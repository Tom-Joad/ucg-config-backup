#!/usr/bin/env sh
set -eu

: "${UCG_HOST:?UCG_HOST is required}"
: "${UCG_USERNAME:?UCG_USERNAME is required}"
: "${UCG_PASSWORD:?UCG_PASSWORD is required}"

UCG_SITE="${UCG_SITE:-default}"
VERIFY_SSL="${VERIFY_SSL:-false}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 3 * * *}"
RUN_ON_START="${RUN_ON_START:-true}"
NOTIFY_ON_SUCCESS="${NOTIFY_ON_SUCCESS:-false}"
WEBHOOK_URL="${WEBHOOK_URL:-}"

mkdir -p "${LOCAL_BACKUP_DIR}"

# No config file needed: backup.py reads missing settings from
# /proc/1/environ, which crond (exec'd below as PID 1) inherits from
# this entrypoint. The password never touches the filesystem.
echo "${CRON_SCHEDULE} python3 /usr/local/bin/backup.py >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/crontabs/root

echo "Scheduled backup: '${CRON_SCHEDULE}' (container timezone: $(date +%Z))"

if [ "$RUN_ON_START" = "true" ]; then
  echo "RUN_ON_START=true, running an initial backup now..."
  python3 /usr/local/bin/backup.py || echo "Initial backup failed; will retry on the next scheduled run."
fi

exec crond -f -d 8
