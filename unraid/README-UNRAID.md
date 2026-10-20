# ucg-config-backup on Unraid

## Installing

**From Community Applications (recommended):** **Apps** tab → search for
`ucg-config-backup` → **Install**. All fields are pre-filled.

**From the template by hand:** copy
[`ucg-config-backup.xml`](ucg-config-backup.xml) to the flash drive, over
the network share to `\\<UNRAID-IP>\flash\config\plugins\dockerMan\templates-user\`
or from the Unraid terminal into `/boot/config/plugins/dockerMan/templates-user/`.
Then **Docker** tab → **Add Container** → **Template** → `ucg-config-backup`.

Either way:

1. Set `UCG_HOST`, `UCG_USERNAME` and `UCG_PASSWORD` (see
   [Prerequisites](../README.md#prerequisites-on-the-ucg-ultra) in the main
   README).
2. Check **Backup Directory**. The default is
   `/mnt/user/appdata/ucg-config-backup/backups`; a share you already back
   up elsewhere works as well.
3. **Apply**, then **Docker** → container icon → **Logs**: the first backup
   runs right away and ends with `Backup completed successfully.`

The defaults `PUID=99`, `PGID=100` and `UMASK=002` make the backups belong
to `nobody:users`, so they can be copied and deleted over SMB.

## Settings Unraid users usually change

All under **Show more settings**:

| Setting | Why |
| --- | --- |
| `TZ` | The schedule runs in this time zone |
| `CRON_SCHEDULE` | When the backup runs, default daily at 03:00 |
| `RETENTION_DAYS` | How long backups are kept, default 30 days |
| `WEBHOOK_URL`, `WEBHOOK_FORMAT`, `NOTIFY_ON_SUCCESS` | Notifications, e.g. to Home Assistant (`json`) or ntfy.sh (`text`) |

## Updating

**Docker** tab → **Check for Updates** → **apply update**. The template
carries its `TemplateURL`, so Unraid picks up new or changed fields.

**Coming from 2.1.x:** the *Config Directory* (`/config`) is no longer
used. Edit the container, remove that path, and delete
`/mnt/user/appdata/ucg-config-backup/config` when you like. Keeping it
does no harm.
