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

# Quote a value for safe use inside single quotes in POSIX sh (handles
# passwords that themselves contain a single quote).
esc() { printf '%s' "$1" | sed "s/'/'\\\\''/g"; }

# busybox crond runs jobs with a minimal environment, so hand the config
# to backup.py via a sourced file instead of relying on inherited env vars.
{
  echo "export UCG_HOST='$(esc "${UCG_HOST}")'"
  echo "export UCG_USERNAME='$(esc "${UCG_USERNAME}")'"
  echo "export UCG_PASSWORD='$(esc "${UCG_PASSWORD}")'"
  echo "export UCG_SITE='$(esc "${UCG_SITE}")'"
  echo "export VERIFY_SSL='$(esc "${VERIFY_SSL}")'"
  echo "export LOCAL_BACKUP_DIR='$(esc "${LOCAL_BACKUP_DIR}")'"
  echo "export RETENTION_DAYS='$(esc "${RETENTION_DAYS}")'"
  echo "export WEBHOOK_URL='$(esc "${WEBHOOK_URL}")'"
  echo "export NOTIFY_ON_SUCCESS='$(esc "${NOTIFY_ON_SUCCESS}")'"
} > /run/backup.env
chmod 600 /run/backup.env

cat > /usr/local/bin/run-backup.sh <<'EOF'
#!/usr/bin/env sh
set -a
. /run/backup.env
set +a
exec python3 /usr/local/bin/backup.py
EOF
chmod +x /usr/local/bin/run-backup.sh

echo "${CRON_SCHEDULE} /usr/local/bin/run-backup.sh >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/crontabs/root

echo "Scheduled backup: '${CRON_SCHEDULE}' (container timezone: $(date +%Z))"

if [ "$RUN_ON_START" = "true" ]; then
  echo "RUN_ON_START=true, running an initial backup now..."
  /usr/local/bin/run-backup.sh || echo "Initial backup failed; will retry on the next scheduled run."
fi

exec crond -f -d 8
