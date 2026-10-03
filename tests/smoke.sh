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
in_log 'UCG_HOST is required' || fail "no message about UCG_HOST"
docker rm -f "$NAME" >/dev/null

echo "== start"
mkdir -p "$WORK/config" "$WORK/backups"
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
in_log 'ERROR: ' || fail "the start-up backup didn't run or didn't log its failure"

# The health check's own command.
docker exec "$NAME" sh -c 'pgrep -f "^busybox crond" >/dev/null' || fail "crond is not running"
crontab=$(docker exec "$NAME" crontab -l -u root)
grep -qF '17 4 * * * /usr/local/bin/ucg-backup' <<<"$crontab" || fail "schedule not in root's crontab"

[[ $(docker exec "$NAME" id -u abc) == "$PUID" ]] || fail "abc doesn't have PUID"
[[ $(stat -c %u "$WORK/config") == "$PUID" ]] || fail "/config not owned by PUID"
[[ $(stat -c %u "$WORK/backups") == "$PUID" ]] || fail "/backups not owned by PUID"

# The password is never written to a file.
if docker exec "$NAME" grep -rqs "$SECRET" /config /backups /etc /tmp; then
    fail "the password was written to a file"
fi

echo "OK"
