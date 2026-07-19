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

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = os.environ["UCG_HOST"]
USERNAME = os.environ["UCG_USERNAME"]
PASSWORD = os.environ["UCG_PASSWORD"]
SITE = os.environ.get("UCG_SITE", "default")
VERIFY_SSL = os.environ.get("VERIFY_SSL", "false").strip().lower() == "true"
BACKUP_DIR = pathlib.Path(os.environ.get("LOCAL_BACKUP_DIR", "/backups"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
NOTIFY_ON_SUCCESS = os.environ.get("NOTIFY_ON_SUCCESS", "false").strip().lower() == "true"

BASE = f"https://{HOST}"


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def notify(status, message):
    if not WEBHOOK_URL:
        return
    try:
        requests.post(WEBHOOK_URL, data=f"UCG backup {status}: {message}".encode(), timeout=10)
    except requests.RequestException as exc:
        log(f"WARNING: webhook notification failed: {exc}")


def main():
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

    log(f"Triggering backup on site '{SITE}'...")
    resp = session.post(
        f"{BASE}/proxy/network/api/s/{SITE}/cmd/backup",
        json={"cmd": "backup", "days": "0"},
        timeout=30,
    )
    resp.raise_for_status()
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

    log("Backup completed successfully.")
    if NOTIFY_ON_SUCCESS:
        notify("OK", filename)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - top-level guard, must always notify+exit non-zero
        log(f"ERROR: {exc}")
        notify("FAILED", str(exc))
        sys.exit(1)
