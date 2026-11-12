# Changelog

All notable changes to this project are listed here. Versions follow
[semantic versioning](https://semver.org/). A change that needs action when
upgrading (a renamed or removed setting, a different folder layout, a changed
webhook payload) only comes with a new major version.

## [Unreleased]

### Upgrading
- Every run now saves two files. If the log shows `System backup download
  rejected`, give the backup account full rights on the console itself, or
  set `SYSTEM_BACKUP=false` to keep the old behaviour. Unraid: add the
  variable by hand, an installed container's template isn't updated.

### Added
- The console's system backup ("Backup for All Applications and Settings" in
  the UniFi OS settings) is saved next to the Network backup, as
  `ucg-system-backup-<timestamp>.unifi`. Until now only the Network
  application's `.unf` file was saved, which is not the whole console.
  `RETENTION_DAYS` covers both kinds of file.
- `SYSTEM_BACKUP` (`true` by default): `false` saves the Network backup only.
- The webhook's JSON body has `system_filename`, `system_size_bytes` and
  `system_backups_kept`; on success `message` names both files.

## [2.2.8] - 2026-11-10

### Changed
- `docker-compose.yml` no longer sets Unraid's IDs and a Berlin time zone:
  `PUID`, `PGID`, `UMASK` and `TZ` default to `1000`, `1000`, `022` and
  `Etc/UTC` and can be set in `.env`. The optional settings moved from the
  compose file into `.env.example`. If you use the compose file and relied on
  the old values, add `PUID=99`, `PGID=100`, `UMASK=002` and `TZ=Europe/Berlin`
  to your `.env`.
- The Python dependencies of `requests` are pinned in `requirements.txt`, so
  every build of a release installs the same versions.

### Docs
- The README says that images are built from version tags and, rarely, by a
  manual run that also moves `latest`.
- The bug report form asks for the `starting` line at the top of the log.
### Fixed
- A problem while cleaning up old backups (a file removed in between, a share
  that refuses the delete) is now a `WARNING:` in the log. Before, it marked a
  backup that had been saved as failed and sent a failure webhook. The success
  webhook names the warnings.
- An answer to the backup command that isn't what the script expects (an HTML
  page from a proxy, a changed firmware answer) now gives the clear
  `Unexpected backup response, no download URL: ...` error with a hint to check
  `UCG_SITE`, instead of a raw JSON or key error.
- A `403` from the backup command is retried once after logging in again
  (fresh session and CSRF token) before the run fails.
- `CRON_SCHEDULE` is checked field by field: a value out of range (`61 25 * * *`),
  letters (`a b c d e`) or a line break stops the container with an `ERROR:`
  line. Before, crond never ran such a job and the container looked healthy.
- The start-up check now also covers `VERIFY_SSL`, `NOTIFY_ON_SUCCESS`,
  `RUN_ON_START` (`true` or `false` in any case; `yes` or `1` used to mean
  `false` without a word), `RETENTION_DAYS`, `WEBHOOK_FORMAT`, `WEBHOOK_URL`
  (must start with `http://` or `https://`) and `UCG_HOST` (a host name or IP
  address with an optional `:port`, no `https://` and no path). A bad value
  stops the container with an `ERROR:` line naming the setting. `docker exec`
  runs read the booleans the same way.
- A crontab that can't be installed now stops the container instead of
  starting it without a backup job.
- The `ucg-backup` wrapper gives the backup a `HOME` it can read, and runs
  Python directly when it isn't root. The container refuses to start with
  `LSIO_NON_ROOT_USER`, which never worked.
- A `WEBHOOK_URL` that can never work (no `http://`) is reported once, not
  retried for seven seconds.

## [2.2.7] - 2026-11-07

### Changed
- The base image moves to digest `d07ad0e` (Alpine 3.24.4, `apk-tools` 3.0.9,
  `tzdata` 2026f).

## [2.2.6] - 2026-11-03

### Security
- Security updates of the distribution packages, picked up by rebuilding the image:
  - `libexpat` 2.10.0-r0 (CVE-2026-117610, CVE-2026-118370)

## [2.2.5] - 2026-10-28

### Security
- Security updates of the distribution packages, picked up by rebuilding the image:
  - `python3` 3.14.10-r1

## [2.2.4] - 2026-10-27

### Security
- Security updates of the distribution packages, picked up by rebuilding the image:
  - `python3` 3.14.10-r0 (CVE-2026-114583, CVE-2026-115557, CVE-2026-12345)

## [2.2.3] - 2026-10-26

### Security
- Security updates of the distribution packages, picked up by rebuilding the image:
  - `curl` 8.23.0-r0 (CVE-2026-92392)
  - `libcurl` 8.23.0-r0 (CVE-2026-92392)

## [2.2.2] - 2026-10-24

### Changed
- Updated dependencies:
  - linuxserver/baseimage-alpine from `b661ece` to `7a59ee7`

## [2.2.1] - 2026-10-22

### Changed
- The build upgrades the Alpine packages before installing anything, so
  every image has the current fixes, not only those of the pinned base
  image. Before, packages the base image ships (such as musl, brotli,
  c-ares and ca-certificates) stayed at its state until the next base
  image update.

### Security
- Security updates of the distribution packages, picked up by rebuilding the image:
  - `musl` 1.2.6-r3 (CVE-2026-107891)

## [2.2.0] - 2026-10-20

### Changed
- The `/config` volume is gone: the container keeps no state, and its
  crontab is rebuilt from `CRON_SCHEDULE` at every start. An existing
  `/config` mapping does no harm; remove it (Unraid: *Config Directory*)
  and delete the folder when you like.
- `docker-compose.yml` reads the console login and the webhook URL from a
  `.env` file (see `.env.example`) instead of holding them inline.

### Fixed
- A download that is a web page or an error message instead of a backup
  fails the run, instead of being saved as a `.unf` and reported as `ok`.
- The run logs out of the console also when a step failed, so no session
  stays open.
- A negative `RETENTION_DAYS` fails the run with an `ERROR:`, like any other
  invalid value; it used to be treated as "keep everything".
- A `.partial` file left by a run that was killed half-way is removed after
  an hour.
- The retention log line also names the space freed.

### Docs
- The security notes no longer claim the password never reaches a file:
  s6-overlay keeps the container environment in
  `/run/s6/container_environment`, as in every linuxserver.io image.
- New `unraid/README-UNRAID.md` (Community Applications, manual template,
  updating); the README has an upgrade section, the test commands and
  links to the changelog and security policy.

## [2.1.4] - 2026-10-19

### Fixed
- A backup is written under a hidden name and renamed when complete, so a
  run that dies half-way leaves no partial `.unf` that the retention counts.
- Only one backup runs at a time: a scheduled run that starts while another
  one is still going (the start-up run, a manual `docker exec`) is skipped
  with a warning.
- The base image's own maintenance jobs stay in root's crontab; the backup
  is added to them instead of replacing them.
- A `CRON_SCHEDULE` that isn't five fields, or a missing `UCG_HOST`,
  `UCG_USERNAME` or `UCG_PASSWORD`, stops the container with an `ERROR:`
  line.

### Changed
- Every run logs the version first.
- New base image digest (`linuxserver/baseimage-alpine:3.24`).

## [2.1.3] - 2026-10-12

### Changed
- The image is built on the current `baseimage-alpine` 3.24 release, with
  the latest Alpine package updates.

## [2.1.2] - 2026-10-04

### Changed
- The container log starts with a TomJoad Images banner instead of the
  base image's "custom build" one.

## [2.1.1] - 2026-10-03

### Fixed
- **The container showed as unhealthy.** The health check looked for a
  process named `crond`, but the base image runs cron as `busybox crond`.
  It now checks the command line, so a running scheduler counts as healthy.

### Changed
- The Unraid template moved to `unraid/ucg-config-backup.xml`, is in English
  and carries its `TemplateURL`, so Unraid picks up template changes.
  `PUID` and `PGID` are shown by default, and Extra Parameters set
  `--security-opt no-new-privileges`.
- `docker-compose.yml` sets `no-new-privileges` and rotates the container
  log (10 MB, 3 files).

### Added
- CI builds the image and runs a smoke test on every push and pull request
  (settings check, cron, health check, ownership, start-up run, password
  never in a file), plus shellcheck and a template check.

## [2.1.0] - 2026-10-03

### Changed
- Images are signed with cosign 3 (new signature format). Verifying them needs
  a cosign 3.x client; `2.0.0` and older still verify with cosign 2 and 3.
- Updated the linuxserver.io base image to Alpine 3.24 (Python 3.14) and the
  Docker and cosign GitHub Actions used to build and sign the image.

## [2.0.0] - 2026-10-03

### Changed
- **Rebuilt on the linuxserver.io Alpine base image** (s6-overlay). The
  backup no longer runs as root: it runs as `PUID:PGID`, so `.unf` files can
  be edited and deleted over SMB (Unraid: `PUID=99`, `PGID=100`,
  `UMASK=002`, now preset in the template).
- The schedule comes from a crontab in the new **`/config`** volume, written
  at every start from `CRON_SCHEDULE`. Settings are read via `with-contenv`
  instead of `/proc/1/environ`.
- Images are built from `v*` tags only, no longer from every push to `main`;
  the `latest` tag follows the newest release.
- Shell access is `docker exec -it ucg-config-backup /bin/bash`; an on-demand
  backup is `docker exec ucg-config-backup /usr/local/bin/ucg-backup`.

### Added
- `PUID`, `PGID`, `UMASK`; `--version` flag; tests; `pip-audit`, `gitleaks`
  and Trivy in CI; `SECURITY.md`, issue templates, `LICENSE` (MIT).

### Upgrading
1. Add a `/config` volume (Unraid: the template's new *Config Directory*).
2. Set `PUID`/`PGID` (and `UMASK`). Existing backups stay owned by root; fix
   them once, e.g. `chown -R 99:100 /mnt/user/appdata/ucg-config-backup/backups`.
3. Don't use `--user` or `--init`.

## [1.1.0] - 2026-09-11

### Added
- `WEBHOOK_FORMAT=json` for structured notifications (Home Assistant),
  webhook retries; the webhook URL is no longer logged and configuration
  errors are reported via the webhook too.

## [1.0.0] - 2026-07-19

Initial release: local-API backup flow, cron scheduling, retention, webhook
notifications, multi-arch image, Unraid and Synology documentation.

[2.2.8]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.7...v2.2.8
[2.2.7]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.6...v2.2.7
[2.2.6]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.5...v2.2.6
[2.2.5]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.4...v2.2.0
[2.1.4]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/Tom-Joad/ucg-config-backup/releases/tag/v1.1.0
