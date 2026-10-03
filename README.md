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

## Table of contents

* [How it works](#how-it-works)
* [Prerequisites on the UCG Ultra](#prerequisites-on-the-ucg-ultra)
* [Supported architectures](#supported-architectures)
* [Version tags](#version-tags)
* [Usage](#usage)
  * [docker-compose](#docker-compose-recommended)
  * [docker cli](#docker-cli)
  * [Unraid](#unraid)
  * [Synology (Container Manager / Docker)](#synology-container-manager--docker)
* [Parameters](#parameters)
* [Environment variables](#environment-variables)
* [Webhook notifications](#webhook-notifications)
  * [Home Assistant](#home-assistant)
* [Volumes / paths](#volumes--paths)
* [Security notes](#security-notes)
* [Troubleshooting](#troubleshooting)
* [Support info](#support-info)
* [Building locally](#building-locally)
* [Versions](#versions)
* [License](#license)

## How it works

On each run (scheduled via `CRON_SCHEDULE`, plus an optional immediate run
at container start via `RUN_ON_START`) the container:

1. Logs in at `/api/auth/login` with a local admin account (username +
   password, no cloud SSO, no 2FA) and captures the CSRF token.
2. Triggers a backup via the proxied Network API (`cmd/backup`).
3. Downloads the returned one-time backup URL and saves it as
   `ucg-backup-<timestamp>.unf` into `/backups`.
4. Deletes local backups older than `RETENTION_DAYS`, and optionally posts
   a notification to `WEBHOOK_URL`.

The credentials are read from the container environment at runtime and are
never written to the image or to disk.

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
| x86-64 | ✅ | latest / \<version\> |
| arm64 | ✅ | latest / \<version\> |

## Version tags

| Tag | Available | Description |
| :----: | :----: | --- |
| latest | ✅ | Latest release |
| \<version\> | ✅ | A specific release, e.g. `2.0.0`, or `2.0` for the latest patch of a minor |
| \<sha\> | ✅ | Immutable build of a specific commit |

Images are only built from `v*` tags. They are signed with cosign (releases after 2.0.0 need a cosign 3.x client to
verify; 2.0.0 and older also verify with 2.x). To verify one:

```bash
cosign verify ghcr.io/tom-joad/ucg-config-backup:latest \
  --certificate-identity-regexp 'https://github.com/Tom-Joad/ucg-config-backup/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Usage

Here are some example snippets to help you get started creating a
container.

### docker-compose (recommended)

```yaml
---
services:
  ucg-config-backup:
    image: ghcr.io/tom-joad/ucg-config-backup:latest
    container_name: ucg-config-backup
    environment:
      - UCG_HOST=192.168.1.1
      - UCG_USERNAME=backup-bot
      - UCG_PASSWORD=change-me
      - CRON_SCHEDULE=0 3 * * *
      - RETENTION_DAYS=30
      - PUID=1000
      - PGID=1000
      - UMASK=022
      - TZ=Europe/Berlin
    volumes:
      - /path/to/config:/config
      - /path/to/backups:/backups
    restart: unless-stopped
```

This repo ships a ready-to-use `docker-compose.yml` — edit the values
under `environment:` to match your setup, then run `docker compose up -d`.

### docker cli

See the [docker run reference](https://docs.docker.com/engine/reference/commandline/run/)
for more info.

```bash
docker run -d \
  --name=ucg-config-backup \
  -e UCG_HOST=192.168.1.1 \
  -e UCG_USERNAME=backup-bot \
  -e UCG_PASSWORD=change-me \
  -e PUID=1000 \
  -e PGID=1000 \
  -e UMASK=022 \
  -e TZ=Europe/Berlin \
  -v /path/to/config:/config \
  -v /path/to/backups:/backups \
  --restart unless-stopped \
  ghcr.io/tom-joad/ucg-config-backup:latest
```

### Unraid

You can add this container in one of two ways.

**Option A — from the pre-filled template (recommended):**

1. Copy [`unraid/ucg-config-backup.xml`](unraid/ucg-config-backup.xml) onto the Unraid flash
   share, e.g. via the network share to
   `\\<UNRAID-IP>\flash\config\plugins\dockerMan\templates-user\` (or from
   the Unraid terminal into
   `/boot/config/plugins/dockerMan/templates-user/`).
2. **Docker** tab → **Add Container** → open the **Template** dropdown →
   select `ucg-config-backup`. All fields are pre-filled (config and backup
   path, `PUID=99`, `PGID=100`, `UMASK=002`, environment variables). With
   these values the backups belong to `nobody:users` and can be edited and
   deleted over SMB.
3. Set `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD` (see
   [Prerequisites](#prerequisites-on-the-ucg-ultra)); adjust the rest as
   needed.
4. **Apply**.

**Option B — add it manually:**

1. **Docker** tab → **Add Container**, then toggle **Advanced View** (top
   right) so you can add custom paths and variables.
2. Fill in:
   * **Name**: `ucg-config-backup`
   * **Repository**: `ghcr.io/tom-joad/ucg-config-backup:latest`
   * **Network Type**: `Bridge`
3. **Path Mappings**: Container Path `/backups` → Host Path e.g.
   `/mnt/user/appdata/ucg-config-backup/backups` (or a share you already
   back up elsewhere), and `/config` →
   `/mnt/user/appdata/ucg-config-backup/config`.
4. **Variables**: add `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD`,
   `PUID=99`, `PGID=100`, `UMASK=002`, and any optional variables from the
   table below (at minimum set `TZ`).
5. Apply, then check **Docker** → container icon → **Logs** to confirm the
   first backup ran cleanly.

This container has no web UI and runs headless — it writes `.unf` files to
the mounted `/backups` path and exits each run's work back to the
scheduler.

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
   * **Volume**: add a folder mapping for a shared folder (e.g.
     `docker/ucg-config-backup/backups`) → `/backups`, and one (e.g.
     `docker/ucg-config-backup/config`) → `/config`.
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
| `-v /config` | The container's own state (the crontab) |
| `-v /backups` | The downloaded `.unf` backup files |

Don't use `--user` or `--init`; the image runs s6-overlay as PID 1 and
drops to `PUID:PGID` itself.

## Environment variables

| Variable | Required | Default | Description |
| --- | :----: | --- | --- |
| `UCG_HOST` | ✅ | – | IP or hostname of the UCG Ultra |
| `UCG_USERNAME` | ✅ | – | Local admin username (see [Prerequisites](#prerequisites-on-the-ucg-ultra)) |
| `UCG_PASSWORD` | ✅ | – | Password for that account |
| `UCG_SITE` | | `default` | UniFi site name |
| `VERIFY_SSL` | | `false` | Verify the console's TLS certificate (UniFi OS ships a self-signed cert by default) |
| `CRON_SCHEDULE` | | `0 3 * * *` | Cron expression for the backup run |
| `RETENTION_DAYS` | | `30` | Delete local backups older than N days (`0` = keep forever) |
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
  "message": "ucg-backup-20260911-030014.unf",
  "filename": "ucg-backup-20260911-030014.unf",
  "size_bytes": 2410496,
  "duration_s": 12.4,
  "host": "192.168.1.1",
  "site": "default",
  "retention_days": 30,
  "backups_kept": 30,
  "timestamp": "2026-09-11T03:00:26+02:00",
  "version": "2.1.0"
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
| `/config` | The container's own state: the crontab in `/config/crontabs/root` is regenerated from `CRON_SCHEDULE` at every start |

`/backups` alone is enough to keep all output. If the host folder for
`/backups` or `/config` doesn't exist yet or belongs to root, the
container hands it to `PUID:PGID` at start (not recursively); folders that
already belong to someone else are left alone.

## Security notes

* Credentials are passed as environment variables and read from the
  container environment at runtime (s6 `with-contenv`, kept in a tmpfs) —
  the password is never written to the image or to a file on disk.
* The backup runs as the unprivileged `abc` user (`PUID:PGID`), not as
  root. Only the cron daemon itself and the start-up scripts run as root.
* `VERIFY_SSL=false` is the default because UniFi OS consoles ship a
  self-signed certificate; the login still only travels across your own
  LAN. If you've installed a trusted certificate on the console, set
  `VERIFY_SSL=true`.
* Use a dedicated local admin account scoped to what backups need — don't
  reuse your primary login.
* The `docker-compose.yml` holds the password inline — if you commit a
  customized copy to your own repo, keep that repo private.

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

## Support info

* Shell access while the container is running:
  `docker exec -it ucg-config-backup /bin/bash`
* Follow the logs in realtime: `docker logs -f ucg-config-backup`
* Trigger an on-demand backup without waiting for the schedule:
  `docker exec ucg-config-backup /usr/local/bin/ucg-backup`

## Building locally

```bash
git clone https://github.com/Tom-Joad/ucg-config-backup.git
cd ucg-config-backup
docker build \
  --no-cache \
  --pull \
  -t ghcr.io/tom-joad/ucg-config-backup:latest .
```

## Versions

* **03.10.2026:** — 2.1.0: images are signed with cosign 3 (verifying needs a
  cosign 3.x client), base image updated to Alpine 3.24, build actions updated.
* **03.10.2026:** — 2.0.0: rebuilt on the linuxserver.io Alpine base
  image (s6-overlay): `PUID`/`PGID`/`UMASK`, backups no longer owned by
  root, new `/config` volume. See the [changelog](CHANGELOG.md) for the
  upgrade steps.
* **11.09.2026:** — 1.1.0: `WEBHOOK_FORMAT=json` for structured
  notifications (Home Assistant), webhook retries, webhook URL no longer
  logged, and configuration errors are now reported via the webhook too.
* **19.07.2026:** — Initial release: local-API backup flow, cron
  scheduling, retention, webhook notifications, multi-arch image, and
  Unraid/Synology documentation.

## License

[MIT](LICENSE)
