#!/usr/bin/env python3
"""Trigger and download a UniFi OS config backup via the local network API.

Used instead of pulling pre-existing autobackup files because "Automated
System Backups" on UniFi OS requires the console to be connected to
Ubiquiti's cloud. This talks to the same local API the "Download Backup"
button in the Network app uses, so it works fully offline/local-only.
"""
import datetime
import os
import pathlib
import sys
import time
import urllib.parse

import requests
import urllib3

VERSION = "2.0.0"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Settings come from the environment. Under cron, /usr/local/bin/ucg-backup
# fills it from the container environment (with-contenv), so the password
# never touches the file system. Nothing at module level may raise: a broken
# environment has to reach the top-level guard below so it still gets
# reported via the webhook.

HOST = os.environ.get("UCG_HOST", "")
USERNAME = os.environ.get("UCG_USERNAME", "")
PASSWORD = os.environ.get("UCG_PASSWORD", "")
SITE = os.environ.get("UCG_SITE", "default")
VERIFY_SSL = os.environ.get("VERIFY_SSL", "false").strip().lower() == "true"
BACKUP_DIR = pathlib.Path(os.environ.get("LOCAL_BACKUP_DIR", "/backups"))
try:
    RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
except ValueError:
    RETENTION_DAYS = None  # rejected in main(), so the failure goes out via the webhook
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_FORMAT = os.environ.get("WEBHOOK_FORMAT", "text").strip().lower() or "text"
NOTIFY_ON_SUCCESS = os.environ.get("NOTIFY_ON_SUCCESS", "false").strip().lower() == "true"

# Seconds to wait before each webhook retry. Home Assistant is briefly
# unreachable while it restarts, and the default 03:00 run can land in a
# maintenance window -- retry a little, but never block the run for long.
WEBHOOK_RETRY_DELAYS = (2, 5)

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
    if RETENTION_DAYS is None:
        raise RuntimeError(f"RETENTION_DAYS must be an integer, got '{os.environ['RETENTION_DAYS']}'")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.verify = VERIFY_SSL

    log(f"Logging in to {HOST} as {USERNAME}...")
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

    log(f"Triggering backup on site '{SITE}'...")
    resp = session.post(
        f"{BASE}/proxy/network/api/s/{SITE}/cmd/backup",
        json={"cmd": "backup", "days": "0"},
        timeout=30,
    )
    if resp.status_code == 403:
        raise RuntimeError(
            "Backup command rejected (403). Most likely cause: UCG_USERNAME does not "
            "have full Administrator / Full Management rights on the Network application "
            "(e.g. it's a Limited Admin or View Only role) -- grant it full management "
            "access and try again. If the role is already correct, this can also be a "
            "stale CSRF token; retrying usually resolves that."
        )
    resp.raise_for_status()
    refresh_csrf(session, resp)
    payload = resp.json().get("data", [])
    if not payload or "url" not in payload[0]:
        raise RuntimeError(f"Unexpected backup response, no download URL: {resp.text[:500]}")

    download_path = payload[0]["url"]
    if download_path.startswith("/proxy/"):
        download_url = f"{BASE}{download_path}"
    elif download_path.startswith("/"):
        download_url = f"{BASE}/proxy/network{download_path}"
    else:
        download_url = f"{BASE}/proxy/network/{download_path}"

    log(f"Downloading backup from {download_url}...")
    dl = session.get(download_url, timeout=60)
    dl.raise_for_status()
    if not dl.content:
        raise RuntimeError("Downloaded backup file is empty.")

    filename = f"ucg-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}.unf"
    target = BACKUP_DIR / filename
    target.write_bytes(dl.content)
    run["filename"] = filename
    run["size_bytes"] = len(dl.content)
    log(f"Saved {target} ({len(dl.content)} bytes).")

    try:
        session.post(f"{BASE}/api/auth/logout", timeout=10)
    except requests.RequestException:
        pass

    if RETENTION_DAYS > 0:
        cutoff = time.time() - RETENTION_DAYS * 86400
        removed = 0
        for f in BACKUP_DIR.glob("ucg-backup-*.unf"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        if removed:
            log(f"Retention: removed {removed} backup(s) older than {RETENTION_DAYS} days.")
    run["backups_kept"] = sum(1 for _ in BACKUP_DIR.glob("ucg-backup-*.unf"))

    log("Backup completed successfully.")


if __name__ == "__main__":
    if "--version" in sys.argv[1:]:
        print(VERSION)
        sys.exit(0)
    run = {"started": time.monotonic(), "filename": None, "size_bytes": None, "backups_kept": None}
    try:
        main(run)
    except Exception as exc:  # noqa: BLE001 - top-level guard, must always notify+exit non-zero
        log(f"ERROR: {exc}")
        notify(False, str(exc) or type(exc).__name__, run)
        sys.exit(1)
    if NOTIFY_ON_SUCCESS:
        notify(True, run["filename"], run)
