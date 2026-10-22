# Changelog

All notable changes to this project are listed here. Versions follow
[semantic versioning](https://semver.org/). A change that needs action when
upgrading (a renamed or removed setting, a different folder layout, a changed
webhook payload) only comes with a new major version.

## [Unreleased]

### Changed
- The build upgrades the Alpine packages before installing anything, so
  every image has the current fixes, not only those of the pinned base
  image. Before, packages the base image ships (such as musl, brotli,
  c-ares and ca-certificates) stayed at its state until the next base
  image update.

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

[2.2.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.4...v2.2.0
[2.1.4]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Tom-Joad/ucg-config-backup/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/Tom-Joad/ucg-config-backup/releases/tag/v1.1.0
