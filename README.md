# ucg-config-backup

Docker-Container, der regelmäßig ein Config-Backup eines Ubiquiti UniFi
Cloud Gateway Ultra (oder jeder anderen UniFi-OS-Konsole: UDM, UDM-Pro,
UDM-SE, Cloud Key Gen2+ ...) über die lokale API auslöst und herunterlädt.

## Funktionsweise

Die "Automatischen System-Backups" von UniFi OS laufen nur, wenn die
Konsole mit Ubiquiti's Cloud verbunden ist (das Backup wird dorthin
hochgeladen) — ohne Cloud-Verbindung entstehen keine automatischen
`.unf`-Dateien auf dem Gerät. Dieser Container umgeht das, indem er
denselben lokalen API-Aufruf macht, den der "Backup herunterladen"-Button
in der Network-App auslöst — das funktioniert komplett lokal, unabhängig
vom Cloud-Status:

1. Login an `/api/auth/login` mit einem lokalen Admin-Konto (Benutzername
   + Passwort, keine Cloud-SSO, kein 2FA).
2. `cmd/backup` über die (durch UniFi OS proxy­te) Network-API auslösen.
3. Die zurückgegebene, einmalig gültige Download-URL abrufen und die
   `.unf`-Datei nach `/backups` speichern.
4. Lokale Backups älter als `RETENTION_DAYS` löschen, Fehler optional per
   Webhook melden.

Zeitplan läuft über `crond` im Container (`CRON_SCHEDULE`), zusätzlich
optional ein Sofort-Lauf beim Start (`RUN_ON_START`).

## Voraussetzungen auf der UCG Ultra

Dediziertes lokales Admin-Konto für die Backups anlegen (kein
Cloud-/SSO-Konto, keine Zwei-Faktor-Authentifizierung — beides würde
diesen einfachen Login-Flow blockieren):

UniFi-OS-Oberfläche → **Einstellungen → Admins & Benutzer** →
**Admin einladen** → *"Restrict to local access only"* (oder
vergleichbare Option je nach Firmware) aktivieren, Rolle **Administrator**
(für `cmd/backup` erforderlich), 2FA für dieses Konto nicht aktivieren.

## Nutzung

```bash
cp .env.example .env
# .env anpassen: UCG_HOST, UCG_USERNAME, UCG_PASSWORD, ggf. Zeitplan/Retention

docker compose up -d --build
docker compose logs -f
```

Backups landen als `ucg-backup-<Zeitstempel>.unf` in `./backups`.

## Nutzung auf Unraid

Der Container wird automatisch als Image gebaut und nach `ghcr.io/tom-joad/ucg-config-backup`
gepusht (siehe `.github/workflows/docker-publish.yml`), das Repo bleibt
dabei privat. Für die Installation über Unraids Docker-UI gibt es zwei
Schritte:

### 1. Image-Zugriff einrichten

Das GHCR-Package ist standardmäßig genauso sichtbar wie das Repo
(privat). Zwei Optionen:

- **Empfohlen — Package öffentlich schalten:** Das Image selbst enthält
  keine Geheimnisse (Zugangsdaten werden erst zur Laufzeit per
  Umgebungsvariable übergeben). Auf GitHub: Profil → **Packages** →
  `ucg-config-backup` → **Package settings** → **Change visibility** →
  **Public**. Danach kann Unraid ohne Login pullen.
- **Alternativ — privat lassen:** Auf dem Unraid-Host per Terminal
  einmalig einloggen (Personal Access Token mit Scope `read:packages`
  reicht):
  ```bash
  docker login ghcr.io -u Tom-Joad -p <PERSONAL_ACCESS_TOKEN>
  ```
  Unraid nutzt für Docker-Pulls dieselbe lokale Docker-Konfiguration,
  ein Neuanlegen des Containers über die GUI funktioniert danach auch
  mit privatem Package.

### 2. Template installieren

Die fertige Vorlage liegt im Repo unter
[`unraid-template.xml`](unraid-template.xml). Da das Quell-Repo privat
ist, funktioniert das sonst übliche Eintragen einer Template-URL nicht
ohne Login — stattdessen die Datei lokal auf den Flash-Share kopieren:

1. Datei `unraid-template.xml` herunterladen (z. B. via `gh api` oder im
   Browser über die GitHub-Weboberfläche, dort eingeloggt).
2. Per Netzwerkfreigabe `\\<UNRAID-IP>\flash\config\plugins\dockerMan\templates-user\`
   ablegen, z. B. als `ucg-config-backup.xml` (oder per Unraid-Terminal
   nach `/boot/config/plugins/dockerMan/templates-user/` kopieren).
3. Unraid-Weboberfläche → **Docker**-Tab → **Add Container** →
   Dropdown-Feld **Template** öffnen → `ucg-config-backup` auswählen.
   Alle Felder sind vorausgefüllt (Backup-Pfad, Env-Variablen).
4. `UCG_HOST`, `UCG_USERNAME`, `UCG_PASSWORD` eintragen (siehe
   Voraussetzungen oben), restliche Werte bei Bedarf anpassen.
5. **Apply** — der Container läuft danach nach `CRON_SCHEDULE` und
   schreibt die `.unf`-Dateien in den gewählten Backup-Pfad
   (Standard: `/mnt/user/appdata/ucg-config-backup/backups`).

Logs lassen sich in Unraid direkt über das Container-Symbol → **Logs**
einsehen (identisch zu `docker compose logs`).

## Konfiguration

| Variable | Pflicht | Standard | Beschreibung |
| --- | --- | --- | --- |
| `UCG_HOST` | ja | – | IP/Hostname der UCG Ultra |
| `UCG_USERNAME` | ja | – | Lokaler Admin-Benutzername (siehe oben) |
| `UCG_PASSWORD` | ja | – | Passwort dieses Kontos |
| `UCG_SITE` | nein | `default` | UniFi-Site-Name |
| `VERIFY_SSL` | nein | `false` | TLS-Zertifikat prüfen (UniFi OS nutzt standardmäßig ein selbstsigniertes Zertifikat) |
| `CRON_SCHEDULE` | nein | `0 3 * * *` | Cron-Ausdruck für den Backup-Lauf |
| `RETENTION_DAYS` | nein | `30` | Lokale Backups älter als N Tage werden gelöscht (`0` = deaktiviert) |
| `RUN_ON_START` | nein | `true` | Sofort-Backup beim Containerstart |
| `NOTIFY_ON_SUCCESS` | nein | `false` | Auch bei Erfolg einen Webhook senden |
| `WEBHOOK_URL` | nein | – | Ziel-URL für Klartext-POST-Benachrichtigungen (z. B. ntfy.sh, Healthchecks.io) |
| `TZ` | nein | `UTC` | Zeitzone für Zeitplan/Logs |

## Sicherheit

- `.env` (enthält das Passwort) ist in `.gitignore` — landet nie im Repo.
- `VERIFY_SSL=false` ist Standard, weil UniFi-OS-Konsolen werksseitig ein
  selbstsigniertes Zertifikat verwenden; das Passwort wird trotzdem nur
  innerhalb des eigenen LAN übertragen. Wer ein eigenes/vertrauenswürdiges
  Zertifikat auf der Konsole hinterlegt hat, sollte `VERIFY_SSL=true`
  setzen.
- Für dieses Konto ausschließlich die Rechte vergeben, die für Backups
  nötig sind (lokales Administrator-Konto ohne Cloud-Zugriff), nicht das
  eigene Haupt-Login wiederverwenden.

## Troubleshooting

- **`Login rejected (401)`** — Benutzername/Passwort falsch, oder das
  Konto ist ein Cloud-/SSO-Konto statt eines lokalen Kontos.
- **`No CSRF token received after login`** — meist bedeutet das, dass für
  das Konto 2FA aktiv ist; dieser einfache Flow unterstützt kein 2FA.
  Dediziertes Konto ohne 2FA verwenden.
- **`Unexpected backup response, no download URL`** — die Struktur der
  API-Antwort kann sich zwischen Firmware-Versionen leicht unterscheiden;
  den vollständigen Log-Ausschnitt prüfen (`docker compose logs`), ggf.
  `UCG_SITE` kontrollieren (Site-Name statt `default`, falls umbenannt).
- **`403` beim `cmd/backup`-Aufruf (Login lief durch)** — meist fehlende
  Rechte: Das Konto braucht **volle Administrator-/"Full
  Management"-Rechte für die Network-App**, nicht "Limited Admin" oder
  "View Only". In der UniFi-OS-Oberfläche unter **Admins & Benutzer** die
  Rolle des Backup-Kontos entsprechend anheben. Seltener liegt es an
  einem rotierenden CSRF-Token zwischen Login und Backup-Aufruf — das
  Script übernimmt automatisch einen von UniFi OS mitgesendeten
  `x-updated-csrf-token`, falls vorhanden.
