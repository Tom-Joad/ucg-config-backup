# Changelog

All notable changes to this project are listed here. Versions follow
[semantic versioning](https://semver.org/). A change that needs action when
upgrading (a renamed or removed setting, a different folder layout, a changed
webhook payload) only comes with a new major version.

## [2.0.0] - Unreleased

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
