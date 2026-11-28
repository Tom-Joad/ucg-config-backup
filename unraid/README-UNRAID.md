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
| `CRON_SCHEDULE` | When the backup runs, default daily at 03:00 |
| `RETENTION_DAYS` | How long backups are kept, default 30 days |
| `NETWORK_BACKUP` | `false` on a console without the Network application (a UniFi NVR with Protect): only the system backup is saved |
| `SYSTEM_BACKUP` | `false` saves only the Network backup, without the console's system backup |
| `WEBHOOK_URL`, `WEBHOOK_FORMAT`, `NOTIFY_ON_SUCCESS` | Notifications, e.g. to Home Assistant (`json`) or ntfy.sh (`text`) |

## A second console (or more)

The template has the fields for a second console under **Show more
settings**: `UCG_HOST_2`, `UCG_USERNAME_2`, `UCG_PASSWORD_2`, `UCG_NAME_2`
and `NETWORK_BACKUP_2`. Leave them empty for one console. With
`UCG_HOST_2` set, the container also backs up that console, into the
folder `UCG_NAME_2` (default `console-2`) inside the **Backup Directory**.
Set `NETWORK_BACKUP_2` to `false` if it is a UniFi NVR. See
[Several consoles](../README.md#several-consoles) in the main README for
what each console has for itself and what is shared.

For a third console, or a setting of the second one that has no field
(`UCG_SITE_2`, `SYSTEM_BACKUP_2`, `VERIFY_SSL_2`, `UCG_CERT_SHA256_2`),
**Edit** the container → **Add another Path, Port, Variable, Label or
Device** → **Config Type** `Variable`, with the name as **Key**, e.g.
`UCG_HOST_3`, `UCG_USERNAME_3`, `UCG_PASSWORD_3`. Containers installed
before 2.6.0 add the second console's fields the same way.

## Updating

**Docker** tab → **Check for Updates** → **apply update**. Unraid doesn't
update the template of an installed container: a setting added in a later
version needs **Edit** → **Add another Path, Port, Variable, Label or
Device**.

The schedule runs in the server's time zone: Unraid sets `TZ` from its own time zone (**Settings** → **Date and Time**) for every
container, so the template has no field for it.

**Coming from 2.1.x:** the *Config Directory* (`/config`) is no longer
used. Edit the container, remove that path, and delete
`/mnt/user/appdata/ucg-config-backup/config` when you like. Keeping it
does no harm.
