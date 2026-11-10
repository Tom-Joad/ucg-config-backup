#!/usr/bin/env bash
# Smoke test of the image: settings check, cron, health check command,
# ownership, and that a backup runs as PUID with the settings and logs to
# the container log. There is no console to talk to, so the backup itself
# fails to connect; that failure must show up in the log.
# Usage: tests/smoke.sh <image>
set -euo pipefail

IMAGE=${1:?usage: smoke.sh <image>}
WORK=$(mktemp -d)
NAME=ucg-smoke-$$
SECRET=smoke-test-password-$$
# Use the caller's IDs so the test can clean up; abc must not be root.
PUID=$(id -u); PGID=$(id -g)
[[ $PUID != 0 ]] || { PUID=1000; PGID=1000; }

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    # The container writes some files as root (e.g. the crontab), which the
    # calling user can't remove; clean up through a container.
    docker run --rm --entrypoint sh -v "$WORK:/w" "$IMAGE" -c 'rm -rf /w/*' >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; docker logs "$NAME" >&2 2>&1 || true; exit 1; }

# Read the whole log first: with pipefail, `docker logs | grep -q` fails at
# random when grep exits early and docker logs gets SIGPIPE.
in_log() { local out; out=$(docker logs "$NAME" 2>&1); grep -qE -- "$1" <<<"$out"; }

wait_for_exit() {
    for _ in $(seq 1 30); do
        [[ $(docker inspect -f '{{.State.Status}}' "$NAME") == running ]] || return 0
        sleep 1
    done
    return 1
}

echo "== version"
version=$(docker run --rm --entrypoint python3 "$IMAGE" /usr/local/bin/backup.py --version)
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "unexpected version '$version'"

echo "== without UCG_HOST the container stops"
docker run -d --name "$NAME" -e UCG_USERNAME=x -e UCG_PASSWORD=x "$IMAGE" >/dev/null
wait_for_exit || fail "kept running without UCG_HOST"
in_log 'ERROR: UCG_HOST is required' || fail "no message about UCG_HOST"
docker rm -f "$NAME" >/dev/null

# A setting that must stop the container: name of the case, the expected
# ERROR text, then the extra docker run arguments.
expect_stop() {
    local what=$1 expected=$2; shift 2
    echo "== $what stops the container"
    docker run -d --name "$NAME" -e UCG_HOST=x -e UCG_USERNAME=x -e UCG_PASSWORD=x "$@" "$IMAGE" >/dev/null
    wait_for_exit || fail "kept running with $what"
    in_log "$expected" || fail "no ERROR for $what"
    # FORBIDDEN: text that must not appear in the log (a secret).
    if [[ -n ${FORBIDDEN:-} ]] && in_log "$FORBIDDEN"; then fail "the log of $what shows ${FORBIDDEN}"; fi
    docker rm -f "$NAME" >/dev/null
}

expect_stop "a schedule that isn't five fields" 'ERROR: CRON_SCHEDULE must have five' -e CRON_SCHEDULE='every day'
expect_stop "an out-of-range schedule" 'ERROR: CRON_SCHEDULE must have five' -e CRON_SCHEDULE='61 25 * * *'
expect_stop "a schedule of letters" 'ERROR: CRON_SCHEDULE must have five' -e CRON_SCHEDULE='a b c d e'
expect_stop "a schedule with a line break" 'ERROR: CRON_SCHEDULE must have five' -e CRON_SCHEDULE=$'0 3 * * *\n0 4 * * *'
expect_stop "VERIFY_SSL=yes" "ERROR: VERIFY_SSL must be 'true' or 'false'" -e VERIFY_SSL=yes
expect_stop "NOTIFY_ON_SUCCESS=1" "ERROR: NOTIFY_ON_SUCCESS must be" -e NOTIFY_ON_SUCCESS=1
expect_stop "RUN_ON_START=on" "ERROR: RUN_ON_START must be" -e RUN_ON_START=on
expect_stop "RETENTION_DAYS=abc" 'ERROR: RETENTION_DAYS must be a whole number' -e RETENTION_DAYS=abc
expect_stop "WEBHOOK_FORMAT=xml" "ERROR: WEBHOOK_FORMAT must be 'text' or 'json'" -e WEBHOOK_FORMAT=xml
expect_stop "a UCG_HOST with a scheme" 'ERROR: UCG_HOST must be a host name' -e UCG_HOST=https://192.0.2.1/
FORBIDDEN=smoke-secret expect_stop "a WEBHOOK_URL without a scheme" 'ERROR: WEBHOOK_URL must start with http' \
    -e WEBHOOK_URL=homeassistant.local:8123/api/webhook/smoke-secret
expect_stop "LSIO_NON_ROOT_USER" 'ERROR: LSIO_NON_ROOT_USER is not supported' -e LSIO_NON_ROOT_USER=true

echo "== without any volume the schedule is installed"
docker run -d --name "$NAME" -e UCG_HOST=x -e UCG_USERNAME=x -e UCG_PASSWORD=x \
    -e RUN_ON_START=false -e CRON_SCHEDULE='5 2 * * *' "$IMAGE" >/dev/null
for _ in $(seq 1 30); do
    in_log '\[ls\.io-init\] done' && break
    sleep 1
done
crontab=$(docker exec "$NAME" crontab -l -u root)
grep -qF '5 2 * * * /usr/local/bin/ucg-backup' <<<"$crontab" || fail "schedule not installed without volumes"
docker rm -f "$NAME" >/dev/null

wait_for_log() { # pattern
    for _ in $(seq 1 30); do
        in_log "$1" && return 0
        sleep 1
    done
    fail "no log line matching '$1'"
}

# Docker creates a missing host folder as root. The test makes one the same
# way, through a container: a folder the calling user creates already belongs
# to PUID and would never reach the hand-over.
echo "== a root-owned backup folder is handed to PUID"
docker run --rm --entrypoint sh -v "$WORK:/w" "$IMAGE" -c 'mkdir /w/rootdir && chown 0:0 /w/rootdir'
[[ $(stat -c %u "$WORK/rootdir") == 0 ]] || fail "the test folder is not root-owned"
docker run -d --name "$NAME" -e PUID="$PUID" -e PGID="$PGID" -e UCG_HOST=x -e UCG_USERNAME=x -e UCG_PASSWORD=x \
    -e RUN_ON_START=false -v "$WORK/rootdir:/backups" "$IMAGE" >/dev/null
wait_for_log '\[ls\.io-init\] done'
[[ $(stat -c %u "$WORK/rootdir") == "$PUID" ]] || fail "a root-owned /backups was not handed to PUID"
docker rm -f "$NAME" >/dev/null

echo "== a backup folder of another user is left alone"
docker run --rm --entrypoint sh -v "$WORK:/w" "$IMAGE" -c 'mkdir /w/otherdir && chown 4242:4242 /w/otherdir'
docker run -d --name "$NAME" -e PUID="$PUID" -e PGID="$PGID" -e UCG_HOST=x -e UCG_USERNAME=x -e UCG_PASSWORD=x \
    -e RUN_ON_START=false -v "$WORK/otherdir:/backups" "$IMAGE" >/dev/null
wait_for_log '\[ls\.io-init\] done'
[[ $(stat -c %u "$WORK/otherdir") == 4242 ]] || fail "a /backups of another user was changed"
docker rm -f "$NAME" >/dev/null

# The wrapper drops to abc and gives it a HOME it can read. A stand-in for
# backup.py reports who runs it; the real one needs a console.
echo "== the wrapper runs Python as PUID with a usable HOME"
cat > "$WORK/fake-backup.py" <<'PY'
import os
print(f"FAKE-RUN uid={os.getuid()} home={os.environ.get('HOME')}")
PY
chmod 644 "$WORK/fake-backup.py"
docker run -d --name "$NAME" -e PUID="$PUID" -e PGID="$PGID" -e UCG_HOST=x -e UCG_USERNAME=x -e UCG_PASSWORD=x \
    -e RUN_ON_START=TRUE -v "$WORK/fake-backup.py:/usr/local/bin/backup.py:ro" "$IMAGE" >/dev/null
wait_for_log 'FAKE-RUN'
in_log "FAKE-RUN uid=$PUID home=/tmp" || fail "the wrapper did not run Python as PUID with HOME=/tmp"
docker rm -f "$NAME" >/dev/null

echo "== start"
# A /config mount left over from 2.1.x, with its old crontab: the image no
# longer uses it, and the schedule from CRON_SCHEDULE has to win.
mkdir -p "$WORK/config/crontabs" "$WORK/backups"
echo '1 1 * * * /usr/local/bin/ucg-backup' > "$WORK/config/crontabs/root"
# Nothing listens on 443 inside the container, so the login fails at once.
docker run -d --name "$NAME" -e PUID="$PUID" -e PGID="$PGID" \
    -e UCG_HOST=127.0.0.1 -e UCG_USERNAME=smoke -e UCG_PASSWORD="$SECRET" \
    -e RUN_ON_START=true -e CRON_SCHEDULE='17 4 * * *' \
    -v "$WORK/config:/config" -v "$WORK/backups:/backups" "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
    in_log 'ERROR: ' && break
    sleep 2
done

in_log 'RUN_ON_START=true' || fail "no start-up run"
in_log 'BASED ON IMAGES FROM LINUXSERVER\.IO' || fail "the start banner is not ours"
if in_log 'Based on images from linuxserver\.io'; then fail "the base image's banner is still shown"; fi
in_log 'ERROR: ' || fail "the start-up backup didn't run or didn't log its failure"

# The health check's own command.
docker exec "$NAME" sh -c 'pgrep -f "^busybox crond" >/dev/null' || fail "crond is not running"
crontab=$(docker exec "$NAME" crontab -l -u root)
grep -qF '17 4 * * * /usr/local/bin/ucg-backup' <<<"$crontab" || fail "schedule not in root's crontab"
if grep -qF '1 1 * * *' <<<"$crontab"; then fail "the old crontab from /config was installed"; fi
grep -qF 'run-parts /etc/periodic/daily' <<<"$crontab" || fail "the base image's own cron jobs were dropped"

# One backup at a time: with the lock held, a second run is skipped.
docker exec -d "$NAME" sh -c 'exec 9>>/tmp/ucg-backup.lock; flock 9; sleep 10'
sleep 1
second=$(docker exec "$NAME" /usr/local/bin/ucg-backup 2>&1 || true)
grep -q 'already running, skipping' <<<"$second" || fail "a second run was not skipped: $second"

[[ $(docker exec "$NAME" id -u abc) == "$PUID" ]] || fail "abc doesn't have PUID"
[[ $(stat -c %u "$WORK/backups") == "$PUID" ]] || fail "/backups not owned by PUID"

# The password is never written to a file of the app's own. (s6 keeps the
# container environment in /run/s6/container_environment, as in every
# linuxserver.io image; that is documented, not tested here.)
if docker exec "$NAME" grep -rqs "$SECRET" /config /backups /etc /tmp; then
    fail "the password was written to a file"
fi

echo "OK"
