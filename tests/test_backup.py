from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time

import pytest
import requests

import backup

# Stand-ins for real downloads: binary and bigger than backup.MIN_BACKUP_BYTES.
UNF_DATA = b"\x00unf" + bytes(range(256)) * 8
SYSTEM_DATA = b"\x00system" + bytes(range(255, -1, -1)) * 8


class FakeResponse:
    def __init__(self, status=200, headers=None, json_data=None, content=b"", text=""):
        self.status_code = status
        self.headers = headers or {}
        self._json = json_data
        self.content = content
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Stands in for the console: login, cmd/backup, both downloads, logout."""

    def __init__(self, backup_status=200, download_type="application/octet-stream", backup_json=None,
                 backup_statuses=None, system_status=200, system_headers=None):
        self.headers = {}
        self.cookies = {}
        self.verify = None
        self.mounted = {}
        self.calls = []
        self.backup_status = backup_status
        self.download_type = download_type
        self.backup_json = {"data": [{"url": "/dl/file.unf"}]} if backup_json is None else backup_json
        # One status per cmd/backup call, the last one repeats.
        self.backup_statuses = list(backup_statuses or [backup_status])
        self.system_status = system_status
        self.system_headers = {"content-type": "application/octet-stream"} if system_headers is None else system_headers

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        if url.endswith("/api/auth/login"):
            return FakeResponse(headers={"x-csrf-token": "tok"})
        if url.endswith("/cmd/backup"):
            status = self.backup_statuses.pop(0) if len(self.backup_statuses) > 1 else self.backup_statuses[0]
            return FakeResponse(status=status, json_data=self.backup_json, text="<html>raw answer</html>")
        return FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        if url.endswith("/api/backup/download"):
            return FakeResponse(status=self.system_status, headers=self.system_headers, content=SYSTEM_DATA)
        return FakeResponse(headers={"content-type": self.download_type}, content=UNF_DATA)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("UCG_HOST", "192.0.2.1")
    monkeypatch.setenv("UCG_USERNAME", "bot")
    monkeypatch.setenv("UCG_PASSWORD", "secret")
    monkeypatch.setattr(backup, "HOST", "192.0.2.1")
    monkeypatch.setattr(backup, "BASE", "https://192.0.2.1")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup, "RETENTION_DAYS", 30)
    monkeypatch.setattr(backup, "console_fingerprint", lambda: None)  # 192.0.2.1 is not reachable
    return tmp_path


new_run = backup.new_run


def test_version_flag():
    out = subprocess.run(
        [sys.executable, backup.__file__, "--version"], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == backup.VERSION


def test_redact_url_hides_path_and_credentials():
    url = "http://user:pw@homeassistant.local:8123/api/webhook/secret-id?x=1"
    assert backup.redact_url(url) == "http://homeassistant.local:8123/..."


def test_payload_always_has_every_key():
    payload = backup.build_payload(False, "boom", new_run())
    assert payload["status"] == "failed"
    assert payload["filename"] is None
    assert set(payload) == {
        "status", "message", "filename", "size_bytes", "duration_s", "host", "site",
        "retention_days", "backups_kept", "system_filename", "system_size_bytes", "system_backups_kept",
        "timestamp", "version",
    }
    assert payload["version"] == backup.VERSION


def test_notify_does_nothing_without_url(monkeypatch):
    monkeypatch.setattr(backup, "WEBHOOK_URL", "")
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not post"))
    backup.notify(True, "x", new_run())


def test_notify_text_body(monkeypatch):
    sent = {}
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "text")
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.update(kw) or FakeResponse())
    backup.notify(False, "login failed", new_run())
    assert sent["data"] == b"UCG backup FAILED: login failed"


def test_notify_json_body(monkeypatch):
    sent = {}
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "json")
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.update(kw) or FakeResponse())
    backup.notify(True, "f.unf", new_run())
    assert sent["json"]["status"] == "ok"


def test_notify_retries_on_5xx_then_gives_up(monkeypatch, capsys):
    calls = []
    sleeps = []
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "text")
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResponse(status=503))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    backup.notify(False, "x", new_run())
    assert len(calls) == 3
    assert sleeps == [2, 5]
    assert "secret" not in capsys.readouterr().out


def test_notify_does_not_retry_on_4xx(monkeypatch):
    calls = []
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "text")
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResponse(status=404))
    backup.notify(False, "x", new_run())
    assert len(calls) == 1


def test_main_requires_credentials(settings, monkeypatch):
    monkeypatch.delenv("UCG_PASSWORD")
    with pytest.raises(RuntimeError, match="UCG_PASSWORD"):
        backup.main(new_run())


def test_main_rejects_bad_retention(settings, monkeypatch):
    monkeypatch.setattr(backup, "RETENTION_DAYS", None)
    monkeypatch.setenv("RETENTION_DAYS", "abc")
    with pytest.raises(RuntimeError, match="RETENTION_DAYS"):
        backup.main(new_run())


def test_main_saves_backup_and_applies_retention(settings, monkeypatch):
    old = settings / "ucg-backup-20200101-000000.unf"
    old.write_bytes(b"old")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    other = settings / "keep-me.txt"
    other.write_text("not a backup")
    os.utime(other, (long_ago, long_ago))

    session = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: session)
    run = new_run()
    backup.main(run)

    assert not old.exists()
    assert other.exists()
    assert run["size_bytes"] == len(UNF_DATA)
    assert (settings / run["filename"]).read_bytes() == UNF_DATA
    assert run["backups_kept"] == 1
    assert run["system_filename"].endswith(".unifi")
    assert run["system_size_bytes"] == len(SYSTEM_DATA)
    assert (settings / run["system_filename"]).read_bytes() == SYSTEM_DATA
    assert run["system_backups_kept"] == 1
    assert ("GET", "https://192.0.2.1/api/backup/download") in session.calls
    assert ("GET", "https://192.0.2.1/proxy/network/dl/file.unf") in session.calls
    assert session.headers["X-CSRF-Token"] == "tok"


def test_main_explains_403_on_backup_command(settings, monkeypatch):
    session = FakeSession(backup_status=403)
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="403"):
        backup.main(new_run())
    # A failed run still logs out.
    assert ("POST", "https://192.0.2.1/api/auth/logout") in session.calls


def test_main_rejects_negative_retention(monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "-5")
    out = subprocess.run(
        [sys.executable, "-c", "import backup; print(backup.RETENTION_DAYS)"],
        capture_output=True, text=True, check=True, cwd=os.path.dirname(backup.__file__),
    )
    assert out.stdout.strip() == "None"


def test_main_rejects_an_html_page_as_backup(settings, monkeypatch):
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(download_type="text/html; charset=utf-8"))
    with pytest.raises(RuntimeError, match="text/html"):
        backup.main(new_run())
    assert not list(settings.glob("ucg-backup-*"))


def test_retention_removes_stale_partials_and_logs_freed_space(settings, capsys):
    long_ago = time.time() - 40 * 86400
    stale = settings / ".ucg-backup-20200101-000000.unf.partial"
    stale.write_bytes(b"half")
    os.utime(stale, (long_ago, long_ago))
    fresh = settings / ".ucg-backup-20990101-000000.unf.partial"
    fresh.write_bytes(b"in progress")
    old = settings / "ucg-backup-20200101-000000.unf"
    old.write_bytes(b"12345")
    os.utime(old, (long_ago, long_ago))

    run = new_run()
    backup.apply_retention(run)

    assert not stale.exists()
    assert fresh.exists()
    assert not old.exists()
    assert run["backups_kept"] == 0
    assert '"freed_bytes":5' in capsys.readouterr().out


class HtmlResponse(FakeResponse):
    def json(self):
        raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)


@pytest.mark.parametrize("answer", [{"data": {"url": "/dl/file.unf"}}, {"data": []}, {}, [], {"data": [{}]}, {"data": [1]}])
def test_unexpected_backup_answer_gives_the_clear_error(settings, monkeypatch, answer):
    session = FakeSession(backup_json=answer)
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match=r"Unexpected backup response \(HTTP 200, \d+ bytes\), no download URL.*UCG_SITE"):
        backup.main(new_run())


def test_a_non_json_backup_answer_gives_the_clear_error(settings, monkeypatch):
    class HtmlSession(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/cmd/backup"):
                return HtmlResponse(text="<html>Bad gateway</html>")
            return super().post(url, **kwargs)

    monkeypatch.setattr(requests, "Session", lambda: HtmlSession())
    with pytest.raises(RuntimeError, match="Unexpected backup response") as err:
        backup.main(new_run())
    assert "Bad gateway" not in str(err.value)  # the console's text stays out of the log and webhook


def test_a_403_is_retried_once_after_a_fresh_login(settings, monkeypatch):
    session = FakeSession(backup_statuses=[403, 200])
    monkeypatch.setattr(requests, "Session", lambda: session)
    run = new_run()
    backup.main(run)
    assert [c for c in session.calls if c[1].endswith("/api/auth/login")] == [("POST", "https://192.0.2.1/api/auth/login")] * 2
    assert [c for c in session.calls if c[1].endswith("/cmd/backup")] == [("POST", "https://192.0.2.1/proxy/network/api/s/default/cmd/backup")] * 2
    assert run["filename"]
    # The first session is closed before the second login.
    kinds = [c[1].rsplit("/", 1)[-1] for c in session.calls if c[0] == "POST"]
    assert kinds[:4] == ["login", "backup", "logout", "login"]


def test_a_second_403_fails_after_one_retry(settings, monkeypatch):
    session = FakeSession(backup_status=403)
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="also after logging in again"):
        backup.main(new_run())
    assert len([c for c in session.calls if c[1].endswith("/cmd/backup")]) == 2


def old_backup(folder, name="ucg-backup-20200101-000000.unf"):
    f = folder / name
    f.write_bytes(b"old")
    long_ago = time.time() - 40 * 86400
    os.utime(f, (long_ago, long_ago))
    return f


def test_a_retention_error_does_not_fail_the_run(settings, monkeypatch, capsys):
    old_backup(settings)
    real_unlink = type(settings).unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith("ucg-backup-2020"):
            raise PermissionError("read-only share")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(settings), "unlink", unlink)
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    run = new_run()
    backup.main(run)
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines()]
    removal = next(e for e in events if e["event"] == "remove_failed")
    assert removal["level"] == "WARNING" and removal["file"] == "ucg-backup-20200101-000000.unf"
    assert "read-only share" in removal["error"]
    assert not [e for e in events if e["level"] == "ERROR"]
    assert events[-1]["event"] == "backup_completed"
    assert run["filename"] and run["retention_warnings"] == 1
    assert run["backups_kept"] == 2
    assert run["system_backups_kept"] == 1


def test_a_file_that_vanishes_during_retention_is_ignored(settings, monkeypatch):
    old_backup(settings)
    real_stat = type(settings).stat

    def stat(self, *args, **kwargs):
        if self.name.startswith("ucg-backup-2020"):
            raise FileNotFoundError(self.name)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(settings), "stat", stat)
    run = new_run()
    backup.apply_retention(run)
    assert run["retention_warnings"] == 0


def test_success_message_mentions_retention_warnings():
    run = new_run() | {"filename": "f.unf", "retention_warnings": 2}
    assert backup.success_message(run) == "f.unf (retention had 2 warning(s), see the log)"
    assert backup.success_message(new_run() | {"filename": "f.unf"}) == "f.unf"
    both = new_run() | {"filename": "f.unf", "system_filename": "s.unifi"}
    assert backup.success_message(both) == "f.unf, s.unifi"


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("TRUE", True), (" True ", True), ("false", False), ("False", False),
    ("", False), ("yes", None), ("1", None), ("on", None),
])
def test_read_bool_accepts_only_true_and_false(monkeypatch, value, expected):
    monkeypatch.setenv("SOME_FLAG", value)
    assert backup.read_bool("SOME_FLAG", "false") is expected


def test_read_bool_uses_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert backup.read_bool("SOME_FLAG", "true") is True


@pytest.mark.parametrize("name", ["VERIFY_SSL", "NOTIFY_ON_SUCCESS", "SYSTEM_BACKUP"])
def test_main_rejects_a_boolean_that_is_not_true_or_false(settings, monkeypatch, name):
    monkeypatch.setattr(backup, name, None)
    with pytest.raises(RuntimeError, match=f"{name} must be 'true' or 'false'"):
        backup.main(new_run())


@pytest.mark.parametrize("host", ["https://192.0.2.1", "192.0.2.1/", "192.0.2.1/path"])
def test_main_rejects_a_host_with_scheme_or_path(settings, monkeypatch, host):
    monkeypatch.setattr(backup, "HOST", host)
    monkeypatch.setenv("UCG_HOST", host)
    with pytest.raises(RuntimeError, match="UCG_HOST must be a host name"):
        backup.main(new_run())


@pytest.mark.parametrize("error", [
    requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema, requests.exceptions.InvalidURL,
])
def test_notify_does_not_retry_an_invalid_url(monkeypatch, capsys, error):
    calls = []
    sleeps = []

    def post(*args, **kwargs):
        calls.append(1)
        raise error("Invalid URL 'homeassistant.local:8123/api/webhook/secret'")

    monkeypatch.setattr(backup, "WEBHOOK_URL", "homeassistant.local:8123/api/webhook/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "text")
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    backup.notify(False, "x", new_run())
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert sleeps == []
    event = json.loads(out.splitlines()[0])
    assert event["event"] == "webhook_url_invalid" and event["error"] == error.__name__
    assert "secret" not in out


def test_system_backup_can_be_switched_off(settings, monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(backup, "SYSTEM_BACKUP", False)
    run = new_run()
    backup.main(run)
    assert run["filename"] and run["system_filename"] is None
    assert run["system_backups_kept"] == 0
    assert not [c for c in session.calls if c[1].endswith("/api/backup/download")]
    assert not list(settings.glob("ucg-system-backup-*"))


@pytest.mark.parametrize("status,hint", [(403, "full rights on the console"), (401, "full rights"), (404, "too old")])
def test_a_rejected_system_backup_fails_the_run_but_keeps_the_network_backup(settings, monkeypatch, status, hint):
    session = FakeSession(system_status=status)
    monkeypatch.setattr(requests, "Session", lambda: session)
    run = new_run()
    with pytest.raises(RuntimeError, match=f"System backup download rejected \\({status}\\).*{hint}.*SYSTEM_BACKUP=false"):
        backup.main(run)
    assert (settings / run["filename"]).read_bytes() == UNF_DATA
    assert run["system_filename"] is None
    assert not list(settings.glob("ucg-system-backup-*"))
    assert ("POST", "https://192.0.2.1/api/auth/logout") in session.calls


def test_a_web_page_instead_of_the_system_backup_fails_the_run(settings, monkeypatch):
    session = FakeSession(system_headers={"content-type": "text/html"})
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="text/html"):
        backup.main(new_run())
    assert not list(settings.glob("ucg-system-backup-*"))


@pytest.mark.parametrize("disposition,suffix", [
    ('attachment; filename="unifi_os_backup_1794507188630_9a571516.unifi"', ".unifi"),
    ("attachment; filename=backup.UBV", ".ubv"),
    ("attachment; filename*=UTF-8''unifi_os_backup_1.unifi", ".unifi"),
    ('attachment; filename="backup.tar.gz"; size=3', ".gz"),
    ('attachment; filename="../../etc/passwd"', ".unifi"),
    ('attachment; filename="no-extension"', ".unifi"),
    ("", ".unifi"),
])
def test_system_backup_takes_its_extension_from_the_console(disposition, suffix):
    resp = FakeResponse(headers={"content-disposition": disposition})
    assert backup.system_backup_suffix(resp) == suffix


def test_retention_covers_system_backups(settings):
    old = old_backup(settings, "ucg-system-backup-20200101-000000.unifi")
    stale = old_backup(settings, ".ucg-system-backup-20200101-000000.unifi.partial")
    fresh = settings / "ucg-system-backup-20990101-000000.unifi"
    fresh.write_bytes(b"new")
    run = new_run()
    backup.apply_retention(run)
    assert not old.exists() and not stale.exists()
    assert fresh.exists()
    assert run["system_backups_kept"] == 1
    assert run["backups_kept"] == 0


# --- what counts as a backup -------------------------------------------------

class TextSession(FakeSession):
    """A console that answers the download with the given body and headers."""

    def __init__(self, content, headers=None, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.dl_headers = headers or {}

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return FakeResponse(headers=self.dl_headers, content=self.content)


@pytest.mark.parametrize("content,headers,message", [
    (b"Unauthorized", {"content-type": "text/plain"}, "too small"),
    (b"Unauthorized", {}, "too small"),
    (b"\x00" * 10, {"content-type": "application/octet-stream"}, "too small"),
    (b"Internal Server Error\n" * 100, {"content-type": "application/octet-stream"}, "plain text"),
    (b"Internal Server Error\n" * 100, {}, "plain text"),
    (UNF_DATA, {"content-length": str(len(UNF_DATA) + 100)}, "incomplete"),
])
def test_an_error_text_or_cut_off_download_is_not_kept_as_a_backup(settings, monkeypatch, content, headers, message):
    monkeypatch.setattr(requests, "Session", lambda: TextSession(content, headers))
    with pytest.raises(RuntimeError, match=message):
        backup.main(new_run())
    assert not list(settings.glob("ucg-*"))
    assert not list(settings.glob(".*partial"))


@pytest.mark.parametrize("content", [
    UNF_DATA,
    b"PK\x03\x04" + bytes(range(256)) * 8,
    bytes(range(256)) * 4 + "ä".encode("utf-8")[:1],  # binary, cut in the middle of a character
])
def test_binary_data_is_kept(settings, monkeypatch, content):
    monkeypatch.setattr(requests, "Session", lambda: TextSession(content, {"content-length": str(len(content))}))
    monkeypatch.setattr(backup, "SYSTEM_BACKUP", False)
    run = new_run()
    backup.main(run)
    assert (settings / run["filename"]).read_bytes() == content


def test_the_download_is_synced_before_the_rename(settings, monkeypatch):
    events = []
    real_fsync, real_replace = os.fsync, type(settings).replace
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(type(settings), "replace", lambda self, t: events.append("replace") or real_replace(self, t))
    backup.store_download(FakeResponse(headers={}, content=UNF_DATA), "ucg-backup-20300101-000000.unf")
    assert events[:2] == ["fsync", "replace"]


def good_backup(folder, name, age_days):
    f = folder / name
    f.write_bytes(UNF_DATA)
    when = time.time() - age_days * 86400
    os.utime(f, (when, when))
    return f


def test_retention_always_keeps_the_newest_backups(settings):
    # Everything is older than RETENTION_DAYS (30): a console that failed for a
    # while must not make the last good backups expire.
    files = [good_backup(settings, f"ucg-backup-2020010{i}-000000.unf", 40 + i) for i in range(1, 6)]
    systems = [good_backup(settings, f"ucg-system-backup-2020010{i}-000000.unifi", 40 + i) for i in range(1, 6)]
    run = new_run()
    backup.apply_retention(run)
    assert [f.exists() for f in files] == [True] * backup.KEEP_NEWEST + [False] * (5 - backup.KEEP_NEWEST)
    assert [f.exists() for f in systems] == [True] * backup.KEEP_NEWEST + [False] * (5 - backup.KEEP_NEWEST)
    assert run["backups_kept"] == backup.KEEP_NEWEST


def test_small_leftovers_of_older_versions_do_not_protect_anything(settings):
    # Versions before this one kept error texts as backups. They are the
    # newest files but not backups, so the real ones stay protected.
    real = [good_backup(settings, f"ucg-backup-2020010{i}-000000.unf", 40 + i) for i in range(1, 4)]
    junk = settings / "ucg-backup-20200110-000000.unf"
    junk.write_bytes(b"Unauthorized")
    when = time.time() - 35 * 86400
    os.utime(junk, (when, when))
    backup.apply_retention(new_run())
    assert all(f.exists() for f in real)
    assert not junk.exists()


def test_recent_backups_are_never_touched(settings):
    fresh = [good_backup(settings, f"ucg-backup-2030010{i}-000000.unf", i) for i in range(1, 6)]
    backup.apply_retention(new_run())
    assert all(f.exists() for f in fresh)


# --- the download address ----------------------------------------------------

@pytest.mark.parametrize("path,url", [
    ("/dl/file.unf", "https://192.0.2.1/proxy/network/dl/file.unf"),
    ("dl/file.unf", "https://192.0.2.1/proxy/network/dl/file.unf"),
    ("/proxy/network/dl/file.unf", "https://192.0.2.1/proxy/network/dl/file.unf"),
    ("/dl/file.unf?x=1", "https://192.0.2.1/proxy/network/dl/file.unf?x=1"),
])
def test_download_path_variants(settings, monkeypatch, path, url):
    session = FakeSession(backup_json={"data": [{"url": path}]})
    monkeypatch.setattr(requests, "Session", lambda: session)
    backup.main(new_run())
    assert ("GET", url) in session.calls


@pytest.mark.parametrize("answer", [
    None, 5, "", ["/dl/a"], "https://evil.example/dl.unf", "//evil.example/dl.unf", "http://192.0.2.1/dl.unf",
    "/dl/../../etc/passwd", "../x", "/dl/a b", "/dl\\x", "javascript:alert(1)",
])
def test_a_download_url_that_is_not_a_path_on_the_console_is_refused(settings, monkeypatch, answer):
    session = FakeSession(backup_json={"data": [{"url": answer}]})
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="download URL|no download URL"):
        backup.main(new_run())
    # Nothing was fetched from anywhere, and the session was closed.
    assert not [c for c in session.calls if c[0] == "GET"]
    assert ("POST", "https://192.0.2.1/api/auth/logout") in session.calls


# --- login --------------------------------------------------------------------

def test_a_rejected_login_names_the_account_hint(settings, monkeypatch):
    class Rejecting(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/api/auth/login"):
                return FakeResponse(status=401)
            return super().post(url, **kwargs)

    monkeypatch.setattr(requests, "Session", lambda: Rejecting())
    with pytest.raises(RuntimeError, match=r"Login rejected \(401\).*local"):
        backup.main(new_run())


def test_a_login_without_csrf_token_names_2fa(settings, monkeypatch):
    class NoToken(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/api/auth/login"):
                return FakeResponse(headers={})
            return super().post(url, **kwargs)

    monkeypatch.setattr(requests, "Session", lambda: NoToken())
    with pytest.raises(RuntimeError, match="2FA"):
        backup.main(new_run())


def test_a_server_error_on_login_fails_the_run(settings, monkeypatch):
    class Broken(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/api/auth/login"):
                return FakeResponse(status=502)
            return super().post(url, **kwargs)

    monkeypatch.setattr(requests, "Session", lambda: Broken())
    with pytest.raises(requests.HTTPError, match="502"):
        backup.main(new_run())


# --- settings -------------------------------------------------------------------

@pytest.mark.parametrize("site", ["a/b", "..", "default?x=1", "a b", "a#b", "ä"])
def test_main_rejects_a_site_that_does_not_belong_in_a_url_path(settings, monkeypatch, site):
    monkeypatch.setattr(backup, "SITE", site)
    monkeypatch.setattr(requests, "Session", lambda: pytest.fail("must not connect"))
    with pytest.raises(RuntimeError, match="UCG_SITE"):
        backup.main(new_run())


@pytest.mark.parametrize("site", ["default", "5f3a9b2c", "my-site_2"])
def test_main_accepts_a_normal_site(settings, monkeypatch, site):
    monkeypatch.setattr(backup, "SITE", site)
    session = FakeSession()
    monkeypatch.setattr(requests, "Session", lambda: session)
    backup.main(new_run())
    assert ("POST", f"https://192.0.2.1/proxy/network/api/s/{site}/cmd/backup") in session.calls


def import_with_env(**env):
    out = subprocess.run(
        [sys.executable, "-c", "import backup; print(repr((backup.HOST, backup.SITE, backup.WEBHOOK_URL, backup.RUN_TIMEOUT)))"],
        capture_output=True, text=True, check=True, cwd=os.path.dirname(backup.__file__),
        env={**os.environ, **env},
    )
    return out.stdout.strip()


def test_blanks_around_the_host_are_trimmed_like_the_init_step_does():
    assert import_with_env(UCG_HOST="  192.0.2.1:8443 ", UCG_SITE=" default ", WEBHOOK_URL=" https://h.example/x ") == \
        "('192.0.2.1:8443', 'default', 'https://h.example/x', 1800)"


@pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5"])
def test_main_rejects_a_bad_run_timeout(settings, monkeypatch, value):
    monkeypatch.setattr(backup, "RUN_TIMEOUT", None)
    monkeypatch.setenv("RUN_TIMEOUT", value)
    with pytest.raises(RuntimeError, match="RUN_TIMEOUT"):
        backup.main(new_run())


def test_the_starting_line_comes_first_and_shows_settings_but_no_secrets(settings, monkeypatch, capsys):
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/very-secret-id")
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    backup.main(new_run())
    lines = capsys.readouterr().out.splitlines()
    first = json.loads(lines[0])
    assert first["event"] == "starting" and first["version"] == backup.VERSION and first["level"] == "INFO"
    expected = {"host": "192.0.2.1", "site": "default", "retention_days": 30, "webhook": "set", "system_backup": True,
                "cert_pin": "off", "log_level": "INFO"}
    assert {k: first[k] for k in expected} == expected
    assert "secret" not in "".join(lines)


def test_a_settings_error_comes_after_the_starting_line(settings, monkeypatch, capsys):
    monkeypatch.delenv("UCG_PASSWORD")
    with pytest.raises(RuntimeError):
        backup.main(new_run())
    assert json.loads(capsys.readouterr().out.splitlines()[0])["event"] == "starting"


# --- one run may not hang forever ------------------------------------------------

def test_a_run_that_takes_too_long_is_stopped(settings, monkeypatch):
    class Hanging(FakeSession):
        def get(self, url, **kwargs):
            time.sleep(5)  # a console that keeps the download open

    session = Hanging()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(backup, "RUN_TIMEOUT", 1)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="RUN_TIMEOUT"):
            backup.main(new_run())
    finally:
        signal_off()
    assert time.monotonic() - started < 4
    assert ("POST", "https://192.0.2.1/api/auth/logout") in session.calls
    assert not list(settings.glob("ucg-*"))


def signal_off():
    import signal
    signal.alarm(0)


def test_a_finished_run_leaves_no_pending_deadline(settings, monkeypatch):
    import signal
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    try:
        backup.main(new_run())
        assert signal.alarm(0) > 0  # still armed after main(), the entry point disarms it before the webhook
    finally:
        signal_off()


def test_skipped_runs_send_a_failed_webhook(tmp_path):
    import http.server
    import threading

    received = []

    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["content-length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = subprocess.run(
            [sys.executable, backup.__file__, "--skipped", "3"], capture_output=True, text=True,
            env={**os.environ, "WEBHOOK_URL": f"http://127.0.0.1:{server.server_port}/secret-id", "WEBHOOK_FORMAT": "json"},
        )
    finally:
        server.shutdown()
    assert out.returncode == 1
    assert b'"status": "failed"' in received[0] or b'"status":"failed"' in received[0]
    assert b"3 backup runs in a row were skipped" in received[0]
    assert "secret-id" not in out.stdout + out.stderr


# --- the success webhook -----------------------------------------------------------

def test_the_success_webhook_names_both_files(settings, monkeypatch):
    sent = {}
    monkeypatch.setattr(backup, "WEBHOOK_URL", "https://hook.example/secret")
    monkeypatch.setattr(backup, "WEBHOOK_FORMAT", "json")
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.update(kw) or FakeResponse())
    run = new_run()
    backup.main(run)
    backup.notify(True, backup.success_message(run), run)
    body = sent["json"]
    assert body["status"] == "ok"
    assert body["filename"] == run["filename"] and body["system_filename"] == run["system_filename"]
    assert body["size_bytes"] == len(UNF_DATA) and body["backups_kept"] == 1
    assert run["filename"] in body["message"] and run["system_filename"] in body["message"]


# --- JSON log lines ------------------------------------------------------------------

def run_logs(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_every_log_line_is_one_json_object_with_ts_level_and_event(settings, monkeypatch, capsys):
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    backup.main(new_run())
    events = run_logs(capsys)
    assert [e["event"] for e in events][0] == "starting"
    assert "backup_saved" in [e["event"] for e in events]
    for e in events:
        assert set(e) >= {"ts", "level", "event"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}(Z|[+-]\d\d:\d\d)", e["ts"])


def test_the_timestamp_has_milliseconds_and_utc_prints_as_z(monkeypatch, capsys):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    backup.log("x")
    assert json.loads(capsys.readouterr().out)["ts"].endswith("Z")


def test_log_level_hides_lower_levels_but_never_the_starting_line(settings, monkeypatch, capsys):
    monkeypatch.setattr(backup, "LOG_LEVEL", "ERROR")
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    backup.main(new_run())
    assert [e["event"] for e in run_logs(capsys)] == ["starting"]


def test_debug_shows_the_steps(settings, monkeypatch, capsys):
    monkeypatch.setattr(backup, "LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    backup.main(new_run())
    assert {"login", "backup_triggered", "download_started"} <= {e["event"] for e in run_logs(capsys)}


def test_main_rejects_an_unknown_log_level(settings, monkeypatch, capsys):
    monkeypatch.setattr(backup, "LOG_LEVEL", "LOUD")
    with pytest.raises(backup.ConfigError, match="LOG_LEVEL"):
        backup.main(new_run())
    assert run_logs(capsys)[0]["log_level"] == "invalid"


def run_entry_point(env, *args):
    return subprocess.run(
        [sys.executable, backup.__file__, *args], capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if not k.startswith(("UCG_", "WEBHOOK"))}, **env},
    )


def test_a_bad_setting_is_logged_as_config_invalid():
    out = run_entry_point({"UCG_HOST": "192.0.2.1", "UCG_USERNAME": "u", "UCG_PASSWORD": "p", "RETENTION_DAYS": "x"})
    events = [json.loads(line) for line in out.stdout.splitlines()]
    assert out.returncode == 1
    assert [e["event"] for e in events] == ["starting", "config_invalid"]
    assert events[1]["level"] == "ERROR" and "RETENTION_DAYS" in events[1]["error"]


def test_a_failed_run_is_logged_as_run_failed(settings, monkeypatch, capsys):
    out = run_entry_point({"UCG_HOST": "127.0.0.1:9", "UCG_USERNAME": "u", "UCG_PASSWORD": "p", "UCG_CERT_SHA256": ""})
    events = [json.loads(line) for line in out.stdout.splitlines()]
    assert out.returncode == 1 and events[-1]["event"] == "run_failed" and events[-1]["level"] == "ERROR"


# --- certificate pinning ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("", ""),
    ("  ", ""),
    ("AB" * 32, "ab" * 32),
    (":".join(["Ab"] * 32), "ab" * 32),
    (" " + "ab" * 32 + " ", "ab" * 32),
    ("ab" * 31, None),
    ("ab" * 33, None),
    ("zz" * 32, None),
    ("ab:" * 31 + "a", None),
])
def test_read_fingerprint(value, expected):
    assert backup.read_fingerprint(value) == expected


def test_main_rejects_a_malformed_pin(settings, monkeypatch):
    monkeypatch.setattr(backup, "CERT_SHA256", None)
    monkeypatch.setattr(requests, "Session", lambda: pytest.fail("must not connect"))
    with pytest.raises(backup.ConfigError, match="UCG_CERT_SHA256"):
        backup.main(new_run())


def make_certificate(folder):
    cert, key = folder / "stub.crt", folder / "stub.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=stub",
         "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True,
    )
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


@pytest.fixture
def tls_console(tmp_path, monkeypatch):
    """The stub console on a local TLS port with a self-signed certificate."""
    if shutil.which("openssl") is None:
        pytest.skip("no openssl")
    from tests import stub_console

    cert, key, fingerprint = make_certificate(tmp_path)
    server = stub_console.make_console_server(0, str(cert), str(key), host="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{server.server_address[1]}"
    folder = tmp_path / "backups"
    folder.mkdir()
    monkeypatch.setattr(backup, "HOST", host)
    monkeypatch.setattr(backup, "BASE", f"https://{host}")
    monkeypatch.setattr(backup, "BACKUP_DIR", folder)
    monkeypatch.setattr(backup, "USERNAME", "bot")
    monkeypatch.setattr(backup, "PASSWORD", "secret")
    monkeypatch.setattr(backup, "SITE", "good")
    monkeypatch.setattr(backup, "RETENTION_DAYS", 30)
    monkeypatch.setenv("UCG_HOST", host)
    monkeypatch.setenv("UCG_USERNAME", "bot")
    monkeypatch.setenv("UCG_PASSWORD", "secret")
    yield fingerprint, folder
    server.shutdown()


def test_the_console_certificate_is_logged_and_the_run_works_without_a_pin(tls_console, monkeypatch, capsys):
    fingerprint, folder = tls_console
    monkeypatch.setattr(backup, "VERIFY_SSL", False)
    backup.main(new_run())
    event = next(e for e in run_logs(capsys) if e["event"] == "console_certificate")
    assert event["sha256"] == backup.colon_hex(fingerprint) and event["pinned"] is False
    assert len(list(folder.glob("ucg-*"))) == 2


@pytest.mark.parametrize("verify_ssl", [False, True])
def test_a_matching_pin_works_whatever_verify_ssl_says(tls_console, monkeypatch, verify_ssl):
    # A self-signed certificate fails VERIFY_SSL=true; the pin replaces that check.
    fingerprint, folder = tls_console
    monkeypatch.setattr(backup, "VERIFY_SSL", verify_ssl)
    monkeypatch.setattr(backup, "CERT_SHA256", fingerprint)
    run = new_run()
    backup.main(run)
    assert (folder / run["filename"]).exists()


def test_a_pin_that_does_not_match_fails_the_run_naming_both(tls_console, monkeypatch):
    fingerprint, folder = tls_console
    other = "cd" * 32
    monkeypatch.setattr(backup, "VERIFY_SSL", False)
    monkeypatch.setattr(backup, "CERT_SHA256", other)
    with pytest.raises(RuntimeError, match="does not match UCG_CERT_SHA256") as err:
        backup.main(new_run())
    assert backup.colon_hex(other) in str(err.value) and backup.colon_hex(fingerprint) in str(err.value)
    assert not list(folder.glob("ucg-*"))


def test_the_pinned_adapter_refuses_another_certificate_on_every_connection(tls_console, monkeypatch):
    # Even when the up-front check is bypassed (a certificate swapped in
    # between), the connection itself is checked.
    fingerprint, folder = tls_console
    monkeypatch.setattr(backup, "CERT_SHA256", "cd" * 32)
    session = requests.Session()
    session.verify = False
    session.mount("https://", backup.PinnedAdapter())
    with pytest.raises(requests.exceptions.SSLError):
        session.get(backup.BASE + "/api/backup/download", timeout=5)
    monkeypatch.setattr(backup, "CERT_SHA256", fingerprint)
    session = requests.Session()
    session.verify = False
    session.mount("https://", backup.PinnedAdapter())
    assert session.get(backup.BASE + "/api/backup/download", timeout=5).status_code == 200
