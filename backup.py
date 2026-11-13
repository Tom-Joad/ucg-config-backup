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
import hashlib
import json
import os
import pathlib
import re
import signal
import socket
import ssl
import sys
import time
import urllib.parse

import requests
import requests.adapters
import urllib3

VERSION = "2.4.0"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Settings come from the environment. Under cron, /usr/local/bin/ucg-backup
# fills it from the container environment (with-contenv). Nothing at module
# level may raise: a broken environment has to reach the top-level guard
# below so it still gets reported via the webhook.

# Trimmed like the init step does, so a value the container accepted also works here.
HOST = os.environ.get("UCG_HOST", "").strip()
USERNAME = os.environ.get("UCG_USERNAME", "")
PASSWORD = os.environ.get("UCG_PASSWORD", "")
SITE = os.environ.get("UCG_SITE", "").strip() or "default"
# The site goes into a URL path: only the characters a UniFi site name has.
SITE_RE = re.compile(r"[A-Za-z0-9_-]+")


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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_FORMAT = os.environ.get("WEBHOOK_FORMAT", "text").strip().lower() or "text"
NOTIFY_ON_SUCCESS = read_bool("NOTIFY_ON_SUCCESS", "false")
SYSTEM_BACKUP = read_bool("SYSTEM_BACKUP", "true")

LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
LOG_LEVEL = os.environ.get("LOG_LEVEL", "").strip().upper() or "INFO"  # rejected in main() if unknown


def read_fingerprint(value):
    # SHA-256 fingerprint as 64 hex digits, with or without colons, any case.
    # Returns the plain lower-case form, "" when unset, None when malformed.
    value = value.strip().replace(":", "").lower()
    if not value or re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return None


CERT_SHA256 = read_fingerprint(os.environ.get("UCG_CERT_SHA256", ""))
# Seconds a whole run may take (login to logout). The per-request timeouts
# only limit one wait each; a console that keeps answering slowly would hold
# the lock for good and every later run would be skipped.
try:
    RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "").strip() or "1800")
except ValueError:
    RUN_TIMEOUT = None  # rejected in main()
if RUN_TIMEOUT is not None and RUN_TIMEOUT < 1:
    RUN_TIMEOUT = None

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

# A real backup is far bigger than this (encrypted .unf, archive .unifi); an
# error text or a stub is not. Smaller than this is never kept as a backup.
MIN_BACKUP_BYTES = 1024
# Retention never removes the newest files of each kind, however old they
# are: a console that answers with errors for a while must not make the
# last good backups expire.
KEEP_NEWEST = 3

# What a console sends instead of a backup when something is off (a login
# page, an error object). A .unf file is encrypted binary.
NOT_A_BACKUP_TYPES = ("text/html", "application/json")

# The system backup's extension comes from the name the console gives the
# download (".unifi" today); this is the fallback when it names none.
SYSTEM_BACKUP_SUFFIX = ".unifi"
# The console builds the system backup while the request waits.
SYSTEM_BACKUP_TIMEOUT = (15, 300)

BASE = f"https://{HOST}"


class ConfigError(RuntimeError):
    """A setting is missing or wrong; logged as `config_invalid`."""


class CertificateMismatch(RuntimeError):
    """The console presents another certificate than the pinned one; logged as `certificate_mismatch`."""


def log(event, level="INFO", always=False, **fields):
    # One JSON object per line, as standards/runtime.md asks of Python apps:
    # `ts` in local time with its offset (UTC prints as Z), `level`, `event`,
    # then the fields. `always` is for the `starting` line, which LOG_LEVEL
    # must not hide.
    if not always and LOG_LEVELS[level] < LOG_LEVELS.get(LOG_LEVEL, LOG_LEVELS["INFO"]):
        return
    ts = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds").replace("+00:00", "Z")
    print(json.dumps({"ts": ts, "level": level, "event": event, **fields}, ensure_ascii=False, separators=(",", ":")), flush=True)


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
            log("webhook_format_unknown", "WARNING", format=WEBHOOK_FORMAT, fallback="text")
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
            log("webhook_url_invalid", "WARNING", error=type(exc).__name__)
            return
        except Exception as exc:  # noqa: BLE001
            problem = type(exc).__name__
        else:
            if resp.ok:
                return
            problem = f"HTTP {resp.status_code}"
            if resp.status_code < 500:
                log("webhook_rejected", "WARNING", target=target, problem=problem)
                return
        if attempt < len(WEBHOOK_RETRY_DELAYS):
            delay = WEBHOOK_RETRY_DELAYS[attempt]
            log("webhook_retry", "WARNING", target=target, problem=problem, delay_s=delay)
            time.sleep(delay)
        else:
            log("webhook_failed", "WARNING", target=target, problem=problem, attempts=attempt + 1)


def refresh_csrf(session, resp):
    # UniFi OS rotates the CSRF token on some firmware versions; if a
    # response hands back a new one, subsequent requests must use it or
    # they get rejected with 403 even though the session cookie is fine.
    new_token = resp.headers.get("x-updated-csrf-token") or resp.headers.get("x-csrf-token")
    if new_token:
        session.headers["X-CSRF-Token"] = new_token


def effective_settings():
    # For the `starting` line: what this run uses, never the password or the
    # webhook URL (only whether one is set). An unusable value shows as "invalid".
    def shown(value, ok=True):
        return value if ok and value is not None else "invalid"
    return {
        "host": HOST or None, "site": SITE, "user": USERNAME or None,
        "verify_ssl": VERIFY_SSL if VERIFY_SSL is not None else "invalid",
        "cert_pin": "set" if CERT_SHA256 else shown("off", CERT_SHA256 is not None),
        "system_backup": SYSTEM_BACKUP if SYSTEM_BACKUP is not None else "invalid",
        "retention_days": shown(RETENTION_DAYS), "run_timeout": shown(RUN_TIMEOUT),
        "webhook": "set" if WEBHOOK_URL else "off",
        "notify_on_success": NOTIFY_ON_SUCCESS if NOTIFY_ON_SUCCESS is not None else "invalid",
        "backup_dir": str(BACKUP_DIR),
        "log_level": LOG_LEVEL if LOG_LEVEL in LOG_LEVELS else "invalid",
    }


def start_deadline(seconds):
    # SIGALRM interrupts a blocked read, which a requests timeout does not
    # (it is per wait, so a trickling download never trips it).
    def expired(signum, frame):
        raise RuntimeError(
            f"The run took longer than RUN_TIMEOUT ({seconds} s) and was stopped. "
            "Is the console answering slowly, or the download stuck?"
        )
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)


def colon_hex(fingerprint):
    return ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2)).upper()


def console_fingerprint():
    # SHA-256 of the certificate the console presents, as 64 lower-case hex
    # digits; None when no TLS connection comes up (login reports that).
    # Nothing is sent over it, so the certificate is not verified here.
    parts = urllib.parse.urlsplit(f"//{HOST}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=parts.hostname) as tls:
                return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
    except (OSError, ValueError):
        return None


class PinnedAdapter(requests.adapters.HTTPAdapter):
    # Every connection of the session must present the pinned certificate
    # (urllib3 compares the SHA-256 after the handshake).
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, assert_fingerprint=CERT_SHA256, **kwargs)


def check_console_certificate(session):
    # Logs the console's fingerprint so it can be copied into
    # UCG_CERT_SHA256, and with a pin set refuses any other certificate.
    actual = console_fingerprint()
    if actual:
        log("console_certificate", sha256=colon_hex(actual), pinned=bool(CERT_SHA256))
    if not CERT_SHA256:
        return
    if actual and actual != CERT_SHA256:
        raise CertificateMismatch(
            f"The console's certificate does not match UCG_CERT_SHA256. Expected {colon_hex(CERT_SHA256)}, "
            f"got {colon_hex(actual)}. If the certificate was renewed on purpose, set the new fingerprint."
        )
    # The pin replaces the CA and host name checks, whatever VERIFY_SSL says.
    session.verify = False
    session.mount("https://", PinnedAdapter())


def main(run):
    log("starting", always=True, version=VERSION, **effective_settings())
    missing = [
        name for name, value in (("UCG_HOST", HOST), ("UCG_USERNAME", os.environ.get("UCG_USERNAME")), ("UCG_PASSWORD", os.environ.get("UCG_PASSWORD")))
        if not value
    ]
    if missing:
        raise ConfigError(f"Missing required setting(s): {', '.join(missing)}")
    bad_bools = [
        name for name, value in (
            ("VERIFY_SSL", VERIFY_SSL), ("NOTIFY_ON_SUCCESS", NOTIFY_ON_SUCCESS), ("SYSTEM_BACKUP", SYSTEM_BACKUP),
        )
        if value is None
    ]
    if bad_bools:
        raise ConfigError(f"{' and '.join(bad_bools)} must be 'true' or 'false'")
    if LOG_LEVEL not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got '{LOG_LEVEL}'")
    if CERT_SHA256 is None:
        raise ConfigError("UCG_CERT_SHA256 must be a SHA-256 fingerprint: 64 hex digits, with or without colons")
    if "/" in HOST:  # also covers a leading https://
        raise ConfigError("UCG_HOST must be a host name or IP address (optionally with :port), without https:// or a path")
    if RETENTION_DAYS is None:
        raise ConfigError(
            f"RETENTION_DAYS must be a whole number of 0 or more, got '{os.environ['RETENTION_DAYS']}'"
        )
    if RUN_TIMEOUT is None:
        raise ConfigError(f"RUN_TIMEOUT must be a whole number of seconds, 1 or more, got '{os.environ['RUN_TIMEOUT']}'")
    if not SITE_RE.fullmatch(SITE):
        raise ConfigError("UCG_SITE may only contain letters, digits, '-' and '_' (it is the site's internal name, 'default' unless you changed it)")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    start_deadline(RUN_TIMEOUT)

    session = requests.Session()
    session.verify = VERIFY_SSL
    check_console_certificate(session)

    log("login", "DEBUG", host=HOST, user=USERNAME)
    login(session)
    # Logged out on every path, so a failed run leaves no session open on
    # the console.
    try:
        save_backup(session, run)
        if SYSTEM_BACKUP:
            save_system_backup(session, run)
    finally:
        logout(session)

    try:
        apply_retention(run)
    except OSError as exc:  # e.g. the folder can't be listed
        run["retention_warnings"] = run.get("retention_warnings", 0) + 1  # the listing itself failed
        log("retention_failed", "WARNING", error=str(exc))
    log("backup_completed", filename=run["filename"], system_filename=run["system_filename"])


def logout(session):
    # Best effort: the run's own result matters more than the goodbye.
    try:
        session.post(f"{BASE}/api/auth/logout", timeout=10)
    except requests.RequestException:
        pass


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
    # has a string `url` (an HTML page from a proxy, a changed firmware
    # answer, `"url": null`) gets the same clear message instead of a
    # JSONDecodeError or KeyError. The body itself is not quoted: it is the
    # console's text and doesn't belong in a log or a webhook.
    try:
        url = resp.json()["data"][0]["url"]
    except (ValueError, LookupError, TypeError):
        url = None
    if not isinstance(url, str) or not url:
        raise RuntimeError(
            f"Unexpected backup response (HTTP {resp.status_code}, {len(resp.content or b'')} bytes), "
            "no download URL -- check that UCG_SITE is the right site name."
        )
    # The path is joined onto the console's own address. One with a scheme or
    # host would send the session cookie elsewhere.
    parts = urllib.parse.urlsplit(url)
    if parts.scheme or parts.netloc or ".." in parts.path.split("/") or "\\" in url or any(c.isspace() for c in url):
        raise RuntimeError("The console's download URL is not a path on the console itself, not following it.")
    return url


def save_backup(session, run):
    log("backup_triggered", "DEBUG", site=SITE)
    resp = request_backup(session)
    if resp.status_code == 403:
        # A stale CSRF token or session gives a 403 as well: log in again
        # once, with a fresh session and token, before giving up.
        log("backup_rejected_retrying", "WARNING", status=403)
        logout(session)  # the first session would otherwise stay open on the console
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

    log("download_started", "DEBUG", url=download_url)
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
    log("system_download_started", "DEBUG")
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


def looks_like_text(content):
    # An error message in plain text, a stub page, `null`: what a console
    # sends instead of a backup. Encrypted or compressed data is never
    # entirely printable (and has NUL bytes), so the first kilobyte decides.
    head = content[:1024]
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:  # not just a character cut off at the end
            return False
        text = head[:exc.start].decode("utf-8")
    return all(c.isprintable() or c in "\r\n\t" for c in text)


def check_download(dl):
    # Checks that a download is a backup before it is kept and counted.
    if not dl.content:
        raise RuntimeError("Downloaded backup file is empty.")
    content_type = dl.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in NOT_A_BACKUP_TYPES:
        raise RuntimeError(f"Download returned {content_type} instead of a backup file.")
    # A cut-off transfer: requests usually raises, but not every proxy
    # closes the connection cleanly.
    expected = dl.headers.get("content-length", "")
    if expected.isdigit() and not dl.headers.get("content-encoding") and int(expected) != len(dl.content):
        raise RuntimeError(f"Download is incomplete ({len(dl.content)} of {expected} bytes).")
    if len(dl.content) < MIN_BACKUP_BYTES:
        raise RuntimeError(
            f"Download is only {len(dl.content)} bytes, too small for a backup (minimum {MIN_BACKUP_BYTES}); "
            "not kept. The console probably answered with an error."
        )
    if looks_like_text(dl.content):
        raise RuntimeError("Download is plain text, not a backup file; not kept. The console probably answered with an error.")


def write_durably(partial, target, content):
    # Data to disk before the rename, so a power cut never leaves a complete
    # name on an empty file; then the rename itself.
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    partial.replace(target)
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
    except OSError:  # a share that can't open a folder: the file itself is safe
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def store_download(dl, filename):
    check_download(dl)
    target = BACKUP_DIR / filename
    # Written under a hidden name and renamed when complete: a run that dies
    # half-way leaves no partial backup that retention would count as one.
    partial = BACKUP_DIR / f".{filename}.partial"
    try:
        write_durably(partial, target, dl.content)
    finally:
        partial.unlink(missing_ok=True)
    log("backup_saved", file=filename, size_bytes=len(dl.content))
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
        log("remove_failed", "WARNING", file=f.name, error=str(exc))
    return None


def files_to_expire(pattern):
    # Every file of one kind except the newest KEEP_NEWEST real backups.
    # Files below MIN_BACKUP_BYTES (left by versions that kept error texts)
    # don't count as backups and don't protect anything.
    entries = []
    for f in BACKUP_DIR.glob(pattern):
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        entries.append((st.st_mtime, st.st_size >= MIN_BACKUP_BYTES, f))
    entries.sort(key=lambda e: e[0], reverse=True)
    protected = set(list(f for _, good, f in entries if good)[:KEEP_NEWEST])
    return [f for _, _, f in entries if f not in protected]


def apply_retention(run):
    now = time.time()
    run["retention_warnings"] = 0
    for pattern in (".ucg-backup-*.unf.partial", ".ucg-system-backup-*.partial"):
        for f in BACKUP_DIR.glob(pattern):
            if expire(f, now - PARTIAL_MAX_AGE_S, run) is not None:
                log("partial_removed", file=f.name)

    if RETENTION_DAYS > 0:
        cutoff = now - RETENTION_DAYS * 86400
        sizes = [
            expire(f, cutoff, run)
            for pattern in ("ucg-backup-*.unf", "ucg-system-backup-*")
            for f in files_to_expire(pattern)
        ]
        sizes = [size for size in sizes if size is not None]
        if sizes:
            log("retention_done", removed=len(sizes), older_than_days=RETENTION_DAYS, freed_bytes=sum(sizes))
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
    if "--skipped" in sys.argv[1:]:
        # Called by the cron wrapper when it has skipped runs in a row
        # because an earlier one still holds the lock.
        count = sys.argv[sys.argv.index("--skipped") + 1]
        message = (
            f"{count} backup runs in a row were skipped because an earlier run is still going. "
            "It may be stuck; it ends itself after RUN_TIMEOUT. If this repeats, restart the container."
        )
        log("runs_skipped", "ERROR", skipped=count, error=message)
        notify(False, message, run)
        sys.exit(1)
    try:
        try:
            main(run)
        finally:
            signal.alarm(0)  # the deadline is for the backup, not for the webhook after it
    except Exception as exc:  # noqa: BLE001 - top-level guard, must always notify+exit non-zero
        message = str(exc) or type(exc).__name__
        event = {ConfigError: "config_invalid", CertificateMismatch: "certificate_mismatch"}.get(type(exc), "run_failed")
        log(event, "ERROR", error=message)
        notify(False, message, run)
        sys.exit(1)
    if NOTIFY_ON_SUCCESS:
        notify(True, success_message(run), run)
