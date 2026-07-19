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

# busybox crond runs jobs with a minimal environment. Instead of writing
# the config (incl. the password) to a file on disk, pull it from PID 1's
# environment -- crond inherited the container env from the entrypoint,
# and /proc/1/environ is root-only and null-delimited (no quoting issues).
if "UCG_HOST" not in os.environ:
    with open("/proc/1/environ", "rb") as f:
        for entry in f.read().split(b"\0"):
            if b"=" in entry:
                key, _, value = entry.partition(b"=")
                os.environ.setdefault(key.decode(), value.decode())

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


def refresh_csrf(session, resp):
    # UniFi OS rotates the CSRF token on some firmware versions; if a
    # response hands back a new one, subsequent requests must use it or
    # they get rejected with 403 even though the session cookie is fine.
    new_token = resp.headers.get("x-updated-csrf-token") or resp.headers.get("x-csrf-token")
    if new_token:
        session.headers["X-CSRF-Token"] = new_token


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
