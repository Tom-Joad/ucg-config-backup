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
* [Environment variables](#environment-variables)
* [Volumes / paths](#volumes--paths)
* [Security notes](#security-notes)
* [Troubleshooting](#troubleshooting)
* [Support info](#support-info)
* [Building locally](#building-locally)
* [Versions](#versions)

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
| x86-64 | ✅ | latest / \<sha tag\> |
| arm64 | ✅ | latest / \<sha tag\> |

## Version tags

| Tag | Available | Description |
| :----: | :----: | --- |
| latest | ✅ | Latest build from `main` |
| \<sha\> | ✅ | Immutable build pinned to a specific commit |

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
      - TZ=Europe/Berlin
    volumes:
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
  -e TZ=Europe/Berlin \
  -v /path/to/backups:/backups \
  --restart unless-stopped \
  ghcr.io/tom-joad/ucg-config-backup:latest
```

### Unraid

You can add this container in one of two ways.

**Option A — from the pre-filled template (recommended):**

1. Copy [`unraid-template.xml`](unraid-template.xml) onto the Unraid flash
   share, e.g. via the network share to
   `\\<UNRAID-IP>\flash\config\plugins\dockerMan\templates-user\` (or from
   the Unraid terminal into
   `/boot/config/plugins/dockerMan/templates-user/`).
2. **Docker** tab → **Add Container** → open the **Template** dropdown →
   select `ucg-config-backup`. All fields are pre-filled (backup path,
   environment variables).
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
   back up elsewhere).
4. **Variables**: add `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD`, and any
   optional variables from the table below (at minimum set `TZ`).
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
     `docker/ucg-config-backup/backups`) → `/backups`.
   * **Environment**: add `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD`,
     `TZ`, and any optional variables below.
   * Enable **Enable auto-restart**.
4. Start the container, then confirm via **Container** → **Details** →
   **Log** that the first backup ran cleanly. Finished `.unf` files appear
   in the shared folder you mapped to `/backups`.

You can also schedule DSM's own **Hyper Backup** or a **Task Scheduler**
job to copy that shared folder off the NAS, so the UniFi config ends up in
your existing 3-2-1 backup rotation.

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
| `WEBHOOK_URL` | | – | URL for plain-text POST notifications (e.g. ntfy.sh, Healthchecks.io) |
| `TZ` | | `UTC` | Timezone for the schedule and log timestamps |

## Volumes / paths

| Path | Contents |
| --- | --- |
| `/backups` | Downloaded `.unf` backup files, named `ucg-backup-<timestamp>.unf` |

A single volume mount at `/backups` is enough to persist all output.

## Security notes

* Credentials are passed as environment variables and read from the
  container's process environment at runtime — the password is never
  written to the image or to a file on disk.
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
  `docker exec -it ucg-config-backup /bin/sh`
* Follow the logs in realtime: `docker logs -f ucg-config-backup`
* Trigger an on-demand backup without waiting for the schedule:
  `docker exec ucg-config-backup python3 /usr/local/bin/backup.py`

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

* **19.07.2026:** — Initial release: local-API backup flow, cron
  scheduling, retention, webhook notifications, multi-arch image, and
  Unraid/Synology documentation.
