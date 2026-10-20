# ucg-config-backup Docker Container

A self-contained container that backs up the configuration of a Ubiquiti
UniFi Cloud Gateway Ultra — or any other UniFi OS console (UDM, UDM-Pro,
UDM-SE, Cloud Key Gen2+, ...) — on a timer, **without needing the console
to be connected to Ubiquiti's cloud**.

Instead of relying on UniFi OS "Automated System Backups" (which only run
when the console is cloud-connected and upload the backup off-site), this
container calls the same local API the **Download Backup** button in the
Network app uses: it logs in with a local admin account, triggers
`cmd/backup`, and saves the resulting `.unf` file to a mounted volume.

Built on linuxserver.io's Alpine base image, with the usual `PUID`, `PGID`,
`UMASK` and `TZ` settings.

## Table of contents

* [How it works](#how-it-works)
* [Prerequisites on the UCG Ultra](#prerequisites-on-the-ucg-ultra)
* [Supported architectures](#supported-architectures)
* [Version tags](#version-tags)
* [Usage](#usage)
  * [docker compose](#docker-compose-recommended)
  * [docker cli](#docker-cli)
  * [Unraid](#unraid)
  * [Synology (Container Manager / Docker)](#synology-container-manager--docker)
* [Parameters](#parameters)
* [Environment variables](#environment-variables)
* [Webhook notifications](#webhook-notifications)
  * [Home Assistant](#home-assistant)
* [Volumes / paths](#volumes--paths)
* [Upgrading](#upgrading)
* [Security notes](#security-notes)
* [Troubleshooting](#troubleshooting)
* [Support info](#support-info)
* [Building locally and running the tests](#building-locally-and-running-the-tests)
* [Changelog, security and license](#changelog-security-and-license)

## How it works

On each run (scheduled via `CRON_SCHEDULE`, plus an optional immediate run
at container start via `RUN_ON_START`) the container:

1. Logs in at `/api/auth/login` with a local admin account (username +
   password, no cloud SSO, no 2FA) and captures the CSRF token.
2. Triggers a backup via the proxied Network API (`cmd/backup`).
3. Downloads the returned one-time backup URL and saves it as
   `ucg-backup-<timestamp>.unf` into `/backups`. A download that turns out
   to be a web page or an error message instead of a backup fails the run.
4. Logs out again, also when a step failed.
5. Deletes local backups older than `RETENTION_DAYS`, and optionally posts
   a notification to `WEBHOOK_URL`.

Only one backup runs at a time: a run that starts while another one is
still going (the start-up run, a manual one) is skipped with a warning.

## Prerequisites on the UCG Ultra

Create a **dedicated local admin account** for the backups (not a
cloud/SSO account, and without two-factor authentication — both would
block this simple login flow):

UniFi OS UI → **Settings → Admins & Users → Invite Admin** → enable
*"Restrict to local access only"* (or the equivalent for your firmware),
give it the **Administrator** role with **full management** rights on the
Network application (required for `cmd/backup`), and do **not** enable 2FA
for this account.

## Supported architectures

Simply pulling `ghcr.io/tom-joad/ucg-config-backup:latest` retrieves the
correct image for your architecture.

| Architecture | Available | Tag |
| :----: | :----: | ---- |
| x86-64 | yes | latest / \<version\> |
| arm64 | yes | latest / \<version\> |

## Version tags

| Tag | Available | Description |
| :----: | :----: | --- |
| latest | yes | Latest release |
| \<version\> | yes | A specific release, e.g. `2.2.0`, or `2.2` for the latest patch of a minor |
| \<sha\> | yes | Immutable build of a specific commit |

Images are only built from `v*` tags. They are signed with cosign (releases after 2.0.0 need a cosign 3.x client to
verify; 2.0.0 and older also verify with 2.x). To verify one:

```bash
cosign verify ghcr.io/tom-joad/ucg-config-backup:latest \
  --certificate-identity-regexp 'https://github.com/Tom-Joad/ucg-config-backup/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Usage

### docker compose (recommended)

This repository ships a [`docker-compose.yml`](docker-compose.yml) and an
[`.env.example`](.env.example). Copy `.env.example` to `.env`, fill in the
console login (and the webhook URL, if you use one), adjust the settings
under `environment:`, then run `docker compose up -d`. `.env` keeps the
password out of the compose file.

```yaml
services:
  ucg-config-backup:
    image: ghcr.io/tom-joad/ucg-config-backup:latest
    container_name: ucg-config-backup
    restart: unless-stopped
    env_file:
      - .env            # UCG_HOST, UCG_USERNAME, UCG_PASSWORD, WEBHOOK_URL
    environment:
      PUID: 1000
      PGID: 1000
      UMASK: "022"
      CRON_SCHEDULE: "0 3 * * *"
      RETENTION_DAYS: "30"
      TZ: Europe/Berlin
    volumes:
      - /path/to/backups:/backups
    security_opt:
      - no-new-privileges:true
```

### docker cli

```bash
docker run -d \
  --name=ucg-config-backup \
  -e UCG_HOST=<CONSOLE-IP> \
  -e UCG_USERNAME=backup-bot \
  -e UCG_PASSWORD=change-me \
  -e PUID=1000 \
  -e PGID=1000 \
  -e UMASK=022 \
  -e TZ=Europe/Berlin \
  -v /path/to/backups:/backups \
  --security-opt no-new-privileges \
  --restart unless-stopped \
  ghcr.io/tom-joad/ucg-config-backup:latest
```

### Unraid

Install it from **Community Applications** (search for
`ucg-config-backup`), or add the template by hand. Both ways, and how to
update an existing container, are in
[unraid/README-UNRAID.md](unraid/README-UNRAID.md).

This container has no web UI and exposes no ports. It writes `.unf` files
to `/backups` and reports in the container log.

### Synology (Container Manager / Docker)

The image is hosted on GitHub Container Registry (`ghcr.io`), not Docker
Hub, so DSM's built-in registry search won't find it — you need to add the
registry first.

1. Open **Container Manager** (DSM 7.2+) or **Docker** (older DSM) →
   **Registry** → settings gear icon → **Add**, and add:
   * **Name**: anything, e.g. `ghcr`
   * **URL**: `https://ghcr.io`
2. Go to **Registry**, search for `tom-joad/ucg-config-backup`, and
   download the `latest` tag — or, if search doesn't surface it, use
   **Image** → **Add** → **Add From URL** with
   `ghcr.io/tom-joad/ucg-config-backup:latest` directly.
3. **Image** → select the downloaded image → **Run** to open the container
   wizard:
   * **Port Settings**: none needed — this container exposes no ports.
   * **Volume**: map a shared folder (e.g. `docker/ucg-config-backup/backups`)
     to `/backups`.
   * **Environment**: add `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD`,
     `PUID`, `PGID` (the ID of your DSM user, see `id <user>` over SSH),
     `TZ`, and any optional variables below.
   * Enable **Enable auto-restart**.
4. Start the container, then confirm via **Container** → **Details** →
   **Log** that the first backup ran cleanly. Finished `.unf` files appear
   in the shared folder you mapped to `/backups`.

You can also schedule DSM's own **Hyper Backup** or a **Task Scheduler**
job to copy that shared folder off the NAS, so the UniFi config ends up in
your existing 3-2-1 backup rotation.

## Parameters

Container images are configured using parameters passed at runtime (such
as those above). The usual linuxserver.io parameters apply:

| Parameter | Function |
| :----: | --- |
| `-e PUID=1000` | User ID the backup runs as, and the owner of the backup files |
| `-e PGID=1000` | Group ID of the same |
| `-e UMASK=022` | Umask of the backup files (`002` lets the group write) |
| `-e TZ=Europe/Berlin` | Timezone for the schedule and log timestamps |
| `-v /backups` | The downloaded `.unf` backup files |

Don't use `--user` or `--init`; the image runs s6-overlay as PID 1 and
drops to `PUID:PGID` itself.

## Environment variables

| Variable | Required | Default | Description |
| --- | :----: | --- | --- |
| `UCG_HOST` | yes | – | IP or hostname of the UCG Ultra |
| `UCG_USERNAME` | yes | – | Local admin username (see [Prerequisites](#prerequisites-on-the-ucg-ultra)) |
| `UCG_PASSWORD` | yes | – | Password for that account |
| `UCG_SITE` | | `default` | UniFi site name |
| `VERIFY_SSL` | | `false` | Verify the console's TLS certificate (UniFi OS ships a self-signed cert by default) |
| `CRON_SCHEDULE` | | `0 3 * * *` | Cron expression for the backup run (five fields; anything else stops the container with an `ERROR:` line) |
| `RETENTION_DAYS` | | `30` | Delete local backups older than N days (`0` = keep forever; anything but a whole number of 0 or more fails the run) |
| `RUN_ON_START` | | `true` | Run a backup immediately when the container starts |
| `NOTIFY_ON_SUCCESS` | | `false` | Also send a webhook on success, not just on failure |
| `WEBHOOK_URL` | | – | URL to POST run notifications to (e.g. ntfy.sh, Healthchecks.io, Home Assistant) — treat it as a secret, see [Webhook notifications](#webhook-notifications) |
| `WEBHOOK_FORMAT` | | `text` | `text` for a plain-text body, `json` for structured fields (Home Assistant) |
| `PUID` / `PGID` | | `911` | User and group ID of the backup files, see [Parameters](#parameters) |
| `UMASK` | | `022` | Umask of the backup files |
| `TZ` | | `UTC` | Timezone for the schedule and log timestamps |

## Webhook notifications

If `WEBHOOK_URL` is set, the container sends a POST after a failed run —
and after a successful one too with `NOTIFY_ON_SUCCESS=true`.
`WEBHOOK_FORMAT` picks the body:

* **`text`** (default) — `UCG backup OK: <filename>` or
  `UCG backup FAILED: <error>` as a plain-text body. Works with ntfy.sh and
  Healthchecks.io.
* **`json`** — an `application/json` body with a fixed set of fields.
  Use this for Home Assistant: its webhook trigger only parses JSON and
  form bodies and silently drops a plain-text one.

```json
{
  "status": "ok",
  "message": "ucg-backup-20261020-030014.unf",
  "filename": "ucg-backup-20261020-030014.unf",
  "size_bytes": 2410496,
  "duration_s": 12.4,
  "host": "192.168.1.1",
  "site": "default",
  "retention_days": 30,
  "backups_kept": 30,
  "timestamp": "2026-10-20T03:00:26+02:00",
  "version": "2.2.0"
}
```

| Field | Description |
| --- | --- |
| `status` | Always `ok` or `failed` (lowercase) — the value to trigger on |
| `message` | Human-readable part: the filename on success, the error on failure |
| `filename` | Name of the saved `.unf` file |
| `size_bytes` | Size of the saved file — use it to catch empty or suspiciously small backups that still report `ok` |
| `duration_s` | Run duration in seconds |
| `host`, `site` | `UCG_HOST` and `UCG_SITE` of the run |
| `retention_days` | Effective `RETENTION_DAYS` |
| `backups_kept` | Number of `ucg-backup-*.unf` files in `/backups` after retention |
| `timestamp` | ISO 8601 with the offset of `TZ` |
| `version` | Container version |

Every key is always present; values that aren't known (e.g. `filename`
when the login failed) are `null`.

A failing webhook never fails the backup run: connection errors and
`5xx` answers are retried twice (after 2 s and 5 s), then the run carries
on. Logs only ever show the scheme and host of `WEBHOOK_URL`, since its
path usually is the secret (HA webhook id, ntfy topic, Healthchecks UUID).

### Home Assistant

Set `WEBHOOK_FORMAT=json` and point `WEBHOOK_URL` at a webhook trigger,
e.g. `http://homeassistant.local:8123/api/webhook/ucg-backup-change-me`.
This automation pushes a notification whenever a run fails:

```yaml
automation:
  - alias: "UCG backup failed"
    triggers:
      - trigger: webhook
        webhook_id: ucg-backup-change-me
        allowed_methods: [POST]
        local_only: true
    conditions:
      - condition: template
        value_template: "{{ trigger.json.status == 'failed' }}"
    actions:
      - action: notify.notify
        data:
          title: "UCG backup failed"
          message: "{{ trigger.json.message }}"
```

With `NOTIFY_ON_SUCCESS=true` every run reports in, so Home Assistant can
also track `trigger.json.timestamp` as a heartbeat and warn on an unusual
`size_bytes`. Pick a long random webhook id — anyone who knows it can
trigger the automation.

## Volumes / paths

| Path | Contents |
| --- | --- |
| `/backups` | Downloaded `.unf` backup files, named `ucg-backup-<timestamp>.unf`, owned by `PUID:PGID` |

`/backups` is the only volume; the container keeps no other state. Its
crontab is rebuilt from `CRON_SCHEDULE` at every start. If the host folder
for `/backups` doesn't exist yet or belongs to root, the container hands
it to `PUID:PGID` at start (not recursively); a folder that already
belongs to someone else is left alone.

## Upgrading

* **From 2.1.x:** the `/config` volume is no longer used. Remove the
  mapping (Unraid: the *Config Directory* path) and delete the folder on
  the host when you like; leaving it mapped does no harm.
* **From 1.x:** see the 2.0.0 entry in the [changelog](CHANGELOG.md): set
  `PUID`/`PGID` (and `UMASK`), and hand existing root-owned backups over
  once, e.g. `chown -R 99:100 /mnt/user/appdata/ucg-config-backup/backups`.

## Security notes

* Credentials are passed as environment variables. As in every
  linuxserver.io image, s6-overlay keeps a copy of the container
  environment in `/run/s6/container_environment` inside the container, so
  the password does sit in the container's file system (on the Docker
  host's disk) while the container exists. It is never built into the
  image, never written to `/backups`, and never logged. Anyone with root
  on the Docker host or `docker exec` access can read it — as they could
  with `docker inspect` anyway.
* The backup runs as the unprivileged `abc` user (`PUID:PGID`), not as
  root. Only the cron daemon itself and the start-up scripts run as root.
* `VERIFY_SSL=false` is the default because UniFi OS consoles ship a
  self-signed certificate. The login then goes over TLS without checking
  who answers, so someone in your LAN who can intercept the traffic could
  capture the password. If you've installed a trusted certificate on the
  console, set `VERIFY_SSL=true`.
* Use a dedicated local admin account scoped to what backups need — don't
  reuse your primary login.
* A `.unf` file holds the console's whole configuration. Keep `/backups`
  readable only by the users who need it.

## Troubleshooting

* **`Login rejected (401)`** — wrong username/password, or the account is
  a cloud/SSO account rather than a local one.
* **`No CSRF token received after login`** — usually means 2FA is enabled
  on the account; this flow doesn't support 2FA. Use a dedicated account
  without it.
* **`403` on the `cmd/backup` call (login succeeded)** — most often
  insufficient permissions: the account needs **full Administrator / Full
  Management** rights on the Network application, not "Limited Admin" or
  "View Only". Raise its role under **Admins & Users**. Less commonly it's
  a rotating CSRF token between login and the backup call — the script
  automatically adopts an `x-updated-csrf-token` from UniFi OS if present.
* **`Unexpected backup response, no download URL`** — the API response
  shape can differ slightly between firmware versions; check the full log
  (`docker logs ucg-config-backup`) and verify `UCG_SITE` (the site name
  is `default` unless you renamed it).
* **`Download returned text/html instead of a backup file`** — the console
  answered the download with a web page, usually because the session
  ended or the firmware changed the download path. Run it again; if it
  stays, open an issue with the log lines.
* **`WARNING: a backup is already running, skipping this one.`** — a
  scheduled or manual run started while another one was still going.
  Nothing to do.

## Support info

* Shell access while the container is running:
  `docker exec -it ucg-config-backup /bin/bash`
* Follow the logs in realtime: `docker logs -f ucg-config-backup`
* Trigger an on-demand backup without waiting for the schedule:
  `docker exec ucg-config-backup ucg-backup`
* The version: `docker exec ucg-config-backup cat /build_version`

## Building locally and running the tests

```bash
git clone https://github.com/Tom-Joad/ucg-config-backup.git
cd ucg-config-backup
docker build --pull -t ucg-config-backup:test .

# Unit tests (Python 3.14, like the image)
pip install -r requirements.txt pytest
pytest -q

# Smoke test of the built image (needs Docker, no console)
tests/smoke.sh ucg-config-backup:test
```

## Changelog, security and license

* [CHANGELOG.md](CHANGELOG.md) — what changed in each release
* [SECURITY.md](SECURITY.md) — how to report a vulnerability, and how to
  verify an image
* [MIT](LICENSE)
