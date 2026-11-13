#!/usr/bin/env bash
# Smoke test of the image: settings check, cron, health check command,
# ownership, and that a backup runs as PUID with the settings and logs to
# the container log. A stub console (tests/stub_console.py) answers the
# backup runs; without one, the start-up run fails to connect and that
# failure must show up in the log.
# Usage: tests/smoke.sh <image>
set -euo pipefail

IMAGE=${1:?usage: smoke.sh <image>}
WORK=$(mktemp -d)
NAME=ucg-smoke-$$
SECRET=smoke-test-password-$$
# Use the caller's IDs so the test can clean up; abc must not be root.
PUID=$(id -u); PGID=$(id -g)
[[ $PUID != 0 ]] || { PUID=1000; PGID=1000; }

STUB_PID=
cleanup() {
    [[ -z $STUB_PID ]] || kill "$STUB_PID" 2>/dev/null || true
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
expect_stop "SYSTEM_BACKUP=yes" "ERROR: SYSTEM_BACKUP must be" -e SYSTEM_BACKUP=yes
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

# --- runs against a stub console ----------------------------------------------
# The stub listens on the host; the container reaches it as host.docker.internal.
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("", 0)); print(s.getsockname()[1])'; }
TLS_PORT=$(free_port); HOOK_PORT=$(free_port)
HOOKS="$WORK/hooks.log"; : > "$HOOKS"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=stub \
    -keyout "$WORK/stub.key" -out "$WORK/stub.crt" >/dev/null 2>&1
python3 "$(dirname "$0")/stub_console.py" "$TLS_PORT" "$HOOK_PORT" "$HOOKS" "$WORK/stub.crt" "$WORK/stub.key" &
STUB_PID=$!
for _ in $(seq 1 20); do
    python3 -c "import socket; socket.create_connection(('127.0.0.1', $TLS_PORT), 1)" 2>/dev/null && break
    sleep 0.5
done

# A container that waits (no start-up run) with the stub as its console.
# Arguments: the stub's site, then more docker run arguments.
start_stub_container() {
    local site=$1; shift
    docker run -d --name "$NAME" --add-host host.docker.internal:host-gateway \
        -e PUID="$PUID" -e PGID="$PGID" -e RUN_ON_START=false \
        -e UCG_HOST="host.docker.internal:$TLS_PORT" -e UCG_USERNAME=smoke -e UCG_PASSWORD="$SECRET" \
        -e UCG_SITE="$site" -e WEBHOOK_URL="http://host.docker.internal:$HOOK_PORT/hook-secret" \
        -e WEBHOOK_FORMAT=json "$@" -v "$WORK/stub-backups:/backups" "$IMAGE" >/dev/null
    wait_for_log '\[ls\.io-init\] done'
}
reset_stub_backups() { docker run --rm --entrypoint sh -v "$WORK:/w" "$IMAGE" -c 'rm -rf /w/stub-backups; mkdir /w/stub-backups; chown '"$PUID:$PGID"' /w/stub-backups'; }

echo "== a run against the stub saves both files, as PUID, with the UMASK"
reset_stub_backups
start_stub_container good -e UMASK=077 -e NOTIFY_ON_SUCCESS=true
in_log 'ucg-config-backup [0-9.]+ starting' || fail "the init step wrote no starting line"
docker exec "$NAME" /usr/local/bin/ucg-backup >"$WORK/run.out" 2>&1 || { cat "$WORK/run.out" >&2; fail "the run against the stub failed"; }
head -n 1 "$WORK/run.out" | grep -qE 'ucg-config-backup [0-9.]+ starting \(host=host\.docker\.internal:[0-9]+ site=good ' || fail "the run's first line is not the starting line: $(head -n 1 "$WORK/run.out")"
if grep -q "$SECRET\|hook-secret" "$WORK/run.out"; then fail "the run's output shows a secret"; fi
[[ $(find "$WORK/stub-backups" -name 'ucg-backup-*.unf' | wc -l) == 1 ]] || fail "no Network backup saved"
[[ $(find "$WORK/stub-backups" -name 'ucg-system-backup-*.unifi' | wc -l) == 1 ]] || fail "no system backup saved"
[[ $(find "$WORK/stub-backups" -name '.*partial' | wc -l) == 0 ]] || fail "a partial file was left"
for f in "$WORK"/stub-backups/ucg-*; do
    [[ $(stat -c '%u' "$f") == "$PUID" ]] || fail "$f is not owned by PUID"
    [[ $(stat -c '%a' "$f") == 600 ]] || fail "$f is not 600 with UMASK=077 ($(stat -c '%a' "$f"))"
done
for _ in $(seq 1 10); do grep -q '"status": *"ok"' "$HOOKS" && break; sleep 1; done
grep -q '"status": *"ok"' "$HOOKS" || fail "no success webhook arrived"

echo "== a user other than root gets a clear message, not 'already running'"
set +e
other=$(docker exec -u abc "$NAME" /usr/local/bin/ucg-backup 2>&1); other_rc=$?
set -e
[[ $other_rc != 0 ]] || fail "a run as abc exited 0"
grep -q 'cannot open' <<<"$other" || fail "no clear message for a run as abc: $other"
if grep -q 'already running' <<<"$other"; then fail "a run as abc claimed another run was going"; fi
docker rm -f "$NAME" >/dev/null

echo "== an error text with status 200 is no backup"
reset_stub_backups
: > "$HOOKS"
start_stub_container err200
set +e
docker exec "$NAME" /usr/local/bin/ucg-backup >"$WORK/run.out" 2>&1; rc=$?
set -e
[[ $rc != 0 ]] || fail "an error text was accepted as a backup"
grep -q 'ERROR: Download is only' "$WORK/run.out" || fail "no error about the small download: $(cat "$WORK/run.out")"
[[ -z $(find "$WORK/stub-backups" -name 'ucg-*') ]] || fail "an error text was kept as a backup"
for _ in $(seq 1 10); do grep -q '"status": *"failed"' "$HOOKS" && break; sleep 1; done
grep -q '"status": *"failed"' "$HOOKS" || fail "no failure webhook arrived"
docker rm -f "$NAME" >/dev/null

echo "== a hanging download is stopped, and skipped runs raise an alarm"
reset_stub_backups
: > "$HOOKS"
start_stub_container hang -e RUN_TIMEOUT=25
docker exec -d "$NAME" sh -c '/usr/local/bin/ucg-backup >/proc/1/fd/1 2>&1'
sleep 3
for _ in 1 2 3; do
    skipped=$(docker exec "$NAME" /usr/local/bin/ucg-backup 2>&1 || true)
    grep -q 'already running, skipping' <<<"$skipped" || fail "a run during the hanging one was not skipped: $skipped"
done
for _ in $(seq 1 10); do grep -q '3 backup runs in a row were skipped' "$HOOKS" && break; sleep 1; done
grep -q '3 backup runs in a row were skipped' "$HOOKS" || fail "no alarm after three skipped runs"
[[ $(grep -c '3 backup runs in a row' "$HOOKS") == 1 ]] || fail "the alarm was sent more than once"
for _ in $(seq 1 40); do in_log 'run took longer than RUN_TIMEOUT' && break; sleep 1; done
in_log 'run took longer than RUN_TIMEOUT' || fail "the hanging run was not stopped by RUN_TIMEOUT"
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
