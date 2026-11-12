#!/usr/bin/env python3
"""Trigger and download UniFi OS backups via the console's local API.

Used instead of pulling pre-existing autobackup files because "Automated
System Backups" on UniFi OS requires the console to be connected to
Ubiquiti's cloud. This talks to the same local API the console's own
"Download Backup" buttons use, so it works fully offline/local-only. Two
files are saved per run: the Network application's backup (.unf) and the
console's system backup for all applications and settings (.unifi).
"""
import datetime
import os
import pathlib
import re
import sys
import time
import urllib.parse

import requests
import urllib3

VERSION = "2.2.8"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Settings come from the environment. Under cron, /usr/local/bin/ucg-backup
# fills it from the container environment (with-contenv). Nothing at module
# level may raise: a broken environment has to reach the top-level guard
# below so it still gets reported via the webhook.

HOST = os.environ.get("UCG_HOST", "")
USERNAME = os.environ.get("UCG_USERNAME", "")
PASSWORD = os.environ.get("UCG_PASSWORD", "")
SITE = os.environ.get("UCG_SITE", "default")


def read_bool(name, default):
    # "true"/"false" in any case. Anything else is None, rejected in main():
    # "yes" quietly meaning false would turn certificate checks off for
    # someone who thought they had asked for them.
    value = os.environ.get(name, "").strip().lower() or default
    return {"true": True, "false": False}.get(value)


VERIFY_SSL = read_bool("VERIFY_SSL", "false")
BACKUP_DIR = pathlib.Path(os.environ.get("LOCAL_BACKUP_DIR", "/backups"))
try:
    RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
except ValueError:
    RETENTION_DAYS = None  # rejected in main(), so the failure goes out via the webhook
if RETENTION_DAYS is not None and RETENTION_DAYS < 0:
    RETENTION_DAYS = None
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_FORMAT = os.environ.get("WEBHOOK_FORMAT", "text").strip().lower() or "text"
NOTIFY_ON_SUCCESS = read_bool("NOTIFY_ON_SUCCESS", "false")
SYSTEM_BACKUP = read_bool("SYSTEM_BACKUP", "true")

# Seconds to wait before each webhook retry. Home Assistant is briefly
# unreachable while it restarts, and the default 03:00 run can land in a
# maintenance window -- retry a little, but never block the run for long.
WEBHOOK_RETRY_DELAYS = (2, 5)

# Mistakes in WEBHOOK_URL that no retry can fix.
INVALID_URL_ERRORS = (
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
)

# A run killed half-way (SIGKILL, a container stop) leaves its hidden
# .partial file behind. Older ones than this can't belong to a running backup.
PARTIAL_MAX_AGE_S = 3600

# What a console sends instead of a backup when something is off (a login
# page, an error object). A .unf file is encrypted binary.
NOT_A_BACKUP_TYPES = ("text/html", "application/json")

# The system backup's extension comes from the name the console gives the
# download (".unifi" today); this is the fallback when it names none.
SYSTEM_BACKUP_SUFFIX = ".unifi"
# The console builds the system backup while the request waits.
SYSTEM_BACKUP_TIMEOUT = (15, 300)

BASE = f"https://{HOST}"


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def redact_url(url):
    # The URL path is usually the secret (HA webhook id, ntfy topic,
    # Healthchecks UUID), so only scheme and host ever reach the log.
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/..."


def build_payload(ok, message, run):
    # Every key is always present (null when unknown): Home Assistant
    # templates turn a missing key into `undefined`, but can handle null.
    return {
        "status": "ok" if ok else "failed",
        "message": message,
        "filename": run["filename"],
        "size_bytes": run["size_bytes"],
        "duration_s": round(time.monotonic() - run["started"], 1),
        "host": HOST or None,
        "site": SITE,
        "retention_days": RETENTION_DAYS,
        "backups_kept": run["backups_kept"],
        "system_filename": run["system_filename"],
        "system_size_bytes": run["system_size_bytes"],
        "system_backups_kept": run["system_backups_kept"],
        "timestamp": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
    }


def notify(ok, message, run):
    if not WEBHOOK_URL:
        return
    if WEBHOOK_FORMAT == "json":
        # requests sets Content-Type: application/json itself; Home
        # Assistant's webhook trigger only parses JSON and form bodies.
        body = {"json": build_payload(ok, message, run)}
    else:
        if WEBHOOK_FORMAT != "text":
            log(f"WARNING: unknown WEBHOOK_FORMAT '{WEBHOOK_FORMAT}', falling back to 'text'.")
        body = {"data": f"UCG backup {'OK' if ok else 'FAILED'}: {message}".encode()}

    target = redact_url(WEBHOOK_URL)
    for attempt in range(len(WEBHOOK_RETRY_DELAYS) + 1):
        # A failing webhook must never fail the backup run, hence the broad
        # catch. Only the exception type is logged: requests puts the full
        # URL (and with it the secret) into its messages.
        try:
            resp = requests.post(WEBHOOK_URL, timeout=10, **body)
        except INVALID_URL_ERRORS as exc:
            # A URL without a scheme can never work; retrying only delays the run.
            log(f"WARNING: webhook URL is not valid ({type(exc).__name__}), not retrying.")
            return
        except Exception as exc:  # noqa: BLE001
            problem = type(exc).__name__
        else:
            if resp.ok:
                return
            problem = f"HTTP {resp.status_code}"
            if resp.status_code < 500:
                log(f"WARNING: webhook to {target} rejected ({problem}), not retrying.")
                return
        if attempt < len(WEBHOOK_RETRY_DELAYS):
            delay = WEBHOOK_RETRY_DELAYS[attempt]
            log(f"WARNING: webhook to {target} failed ({problem}), retrying in {delay}s...")
            time.sleep(delay)
        else:
            log(f"WARNING: webhook to {target} failed ({problem}), giving up after {attempt + 1} attempts.")


def refresh_csrf(session, resp):
    # UniFi OS rotates the CSRF token on some firmware versions; if a
    # response hands back a new one, subsequent requests must use it or
    # they get rejected with 403 even though the session cookie is fine.
    new_token = resp.headers.get("x-updated-csrf-token") or resp.headers.get("x-csrf-token")
    if new_token:
        session.headers["X-CSRF-Token"] = new_token


def main(run):
    missing = [name for name in ("UCG_HOST", "UCG_USERNAME", "UCG_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required setting(s): {', '.join(missing)}")
    bad_bools = [
        name for name, value in (
            ("VERIFY_SSL", VERIFY_SSL), ("NOTIFY_ON_SUCCESS", NOTIFY_ON_SUCCESS), ("SYSTEM_BACKUP", SYSTEM_BACKUP),
        )
        if value is None
    ]
    if bad_bools:
        raise RuntimeError(f"{' and '.join(bad_bools)} must be 'true' or 'false'")
    if "/" in HOST:  # also covers a leading https://
        raise RuntimeError("UCG_HOST must be a host name or IP address (optionally with :port), without https:// or a path")
    if RETENTION_DAYS is None:
        raise RuntimeError(
            f"RETENTION_DAYS must be a whole number of 0 or more, got '{os.environ['RETENTION_DAYS']}'"
        )

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    log(f"ucg-config-backup {VERSION} starting")

    session = requests.Session()
    session.verify = VERIFY_SSL

    log(f"Logging in to {HOST} as {USERNAME}...")
    login(session)
    # Logged out on every path, so a failed run leaves no session open on
    # the console.
    try:
        save_backup(session, run)
        if SYSTEM_BACKUP:
            save_system_backup(session, run)
    finally:
        try:
            session.post(f"{BASE}/api/auth/logout", timeout=10)
        except requests.RequestException:
            pass

    try:
        apply_retention(run)
    except OSError as exc:  # e.g. the folder can't be listed
        run["retention_warnings"] = run.get("retention_warnings", 0) + 1  # the listing itself failed
        log(f"WARNING: retention failed: {exc}")
    log("Backup completed successfully.")


def login(session):
    resp = session.post(
        f"{BASE}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=15,
    )
    if resp.status_code == 401:
        raise RuntimeError(
            "Login rejected (401). Check UCG_USERNAME/UCG_PASSWORD, and make sure "
            "the account is a *local* admin without 2FA enabled."
        )
    resp.raise_for_status()

    csrf_token = resp.headers.get("x-csrf-token") or session.cookies.get("csrf_token", "")
    if not csrf_token:
        raise RuntimeError(
            "No CSRF token received after login. The account may require 2FA, "
            "which this script does not support -- use a dedicated local account without it."
        )
    session.headers.update({"X-CSRF-Token": csrf_token})
    refresh_csrf(session, resp)


def request_backup(session):
    return session.post(
        f"{BASE}/proxy/network/api/s/{SITE}/cmd/backup",
        json={"cmd": "backup", "days": "0"},
        timeout=30,
    )


def backup_download_path(resp):
    # Anything but a JSON object with a non-empty `data` list whose first item
    # has a `url` (an HTML page from a proxy, a changed firmware answer) gets
    # the same clear message instead of a JSONDecodeError or KeyError.
    try:
        payload = resp.json()
        data = payload["data"]
        return data[0]["url"]
    except (ValueError, LookupError, TypeError):
        raise RuntimeError(
            f"Unexpected backup response, no download URL: {resp.text[:500]} "
            "-- check that UCG_SITE is the right site name."
        ) from None


def save_backup(session, run):
    log(f"Triggering backup on site '{SITE}'...")
    resp = request_backup(session)
    if resp.status_code == 403:
        # A stale CSRF token or session gives a 403 as well: log in again
        # once, with a fresh session and token, before giving up.
        log("Backup command rejected (403), logging in again and retrying once...")
        session.cookies.clear()
        login(session)
        resp = request_backup(session)
    if resp.status_code == 403:
        raise RuntimeError(
            "Backup command rejected (403), also after logging in again. Most likely "
            "cause: UCG_USERNAME does not have full Administrator / Full Management "
            "rights on the Network application (e.g. it's a Limited Admin or View Only "
            "role) -- grant it full management access and try again."
        )
    resp.raise_for_status()
    refresh_csrf(session, resp)
    download_path = backup_download_path(resp)
    if download_path.startswith("/proxy/"):
        download_url = f"{BASE}{download_path}"
    elif download_path.startswith("/"):
        download_url = f"{BASE}/proxy/network{download_path}"
    else:
        download_url = f"{BASE}/proxy/network/{download_path}"

    log(f"Downloading backup from {download_url}...")
    dl = session.get(download_url, timeout=60)
    dl.raise_for_status()
    filename = f"ucg-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}.unf"
    run["filename"] = store_download(dl, filename)
    run["size_bytes"] = len(dl.content)


def system_backup_suffix(resp):
    # The extension of the name the console gives the download, so the file
    # can be handed back to the console's restore dialog as it is.
    disposition = resp.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=[^;]*?(\.[A-Za-z0-9]{1,10})[\"']?\s*(?:;|$)", disposition)
    return match.group(1).lower() if match else SYSTEM_BACKUP_SUFFIX


def save_system_backup(session, run):
    # The "Download Backup" button of the console itself ("Backup for All
    # Applications and Settings"). The Network backup above is already saved
    # when this fails.
    log("Downloading the system backup (all applications and settings)...")
    dl = session.get(f"{BASE}/api/backup/download", timeout=SYSTEM_BACKUP_TIMEOUT)
    if dl.status_code in (401, 403, 404):
        if dl.status_code == 404:
            reason = "this console does not offer it, its UniFi OS may be too old"
        else:
            reason = "UCG_USERNAME may not be allowed to download it, it needs full rights on the console itself"
        raise RuntimeError(
            f"System backup download rejected ({dl.status_code}): {reason}. The Network backup of this "
            "run is saved. Set SYSTEM_BACKUP=false to back up the Network application only."
        )
    dl.raise_for_status()
    filename = f"ucg-system-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}{system_backup_suffix(dl)}"
    run["system_filename"] = store_download(dl, filename)
    run["system_size_bytes"] = len(dl.content)


def store_download(dl, filename):
    if not dl.content:
        raise RuntimeError("Downloaded backup file is empty.")
    content_type = dl.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in NOT_A_BACKUP_TYPES:
        raise RuntimeError(f"Download returned {content_type} instead of a backup file.")

    target = BACKUP_DIR / filename
    # Written under a hidden name and renamed when complete: a run that dies
    # half-way leaves no partial backup that retention would count as one.
    partial = BACKUP_DIR / f".{filename}.partial"
    try:
        partial.write_bytes(dl.content)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    log(f"Saved {target} ({len(dl.content)} bytes).")
    return filename


def expire(f, cutoff, run):
    # Deletes f when it is older than cutoff and returns its size, else None.
    # The backup is already saved: a file that can't be inspected or removed
    # (gone in between, read-only share) is a warning, not a failed run.
    try:
        st = f.stat()
        if st.st_mtime < cutoff:
            f.unlink()
            return st.st_size
    except FileNotFoundError:
        pass
    except OSError as exc:
        run["retention_warnings"] += 1
        log(f"WARNING: could not remove {f.name}: {exc}")
    return None


def apply_retention(run):
    now = time.time()
    run["retention_warnings"] = 0
    for pattern in (".ucg-backup-*.unf.partial", ".ucg-system-backup-*.partial"):
        for f in BACKUP_DIR.glob(pattern):
            if expire(f, now - PARTIAL_MAX_AGE_S, run) is not None:
                log(f"Removed {f.name} left behind by an interrupted run.")

    if RETENTION_DAYS > 0:
        cutoff = now - RETENTION_DAYS * 86400
        sizes = [
            expire(f, cutoff, run)
            for pattern in ("ucg-backup-*.unf", "ucg-system-backup-*")
            for f in BACKUP_DIR.glob(pattern)
        ]
        sizes = [size for size in sizes if size is not None]
        if sizes:
            log(f"Retention: removed {len(sizes)} backup(s) older than {RETENTION_DAYS} days, freed {sum(sizes)} bytes.")
    run["backups_kept"] = sum(1 for _ in BACKUP_DIR.glob("ucg-backup-*.unf"))
    run["system_backups_kept"] = sum(1 for _ in BACKUP_DIR.glob("ucg-system-backup-*"))


def success_message(run):
    message = ", ".join(name for name in (run["filename"], run.get("system_filename")) if name)
    if run.get("retention_warnings"):
        message += f" (retention had {run['retention_warnings']} warning(s), see the log)"
    return message


def new_run():
    return {
        "started": time.monotonic(), "filename": None, "size_bytes": None, "backups_kept": None,
        "system_filename": None, "system_size_bytes": None, "system_backups_kept": None,
    }


if __name__ == "__main__":
    if "--version" in sys.argv[1:]:
        print(VERSION)
        sys.exit(0)
    run = new_run()
    try:
        main(run)
    except Exception as exc:  # noqa: BLE001 - top-level guard, must always notify+exit non-zero
        log(f"ERROR: {exc}")
        notify(False, str(exc) or type(exc).__name__, run)
        sys.exit(1)
    if NOTIFY_ON_SUCCESS:
        notify(True, success_message(run), run)
