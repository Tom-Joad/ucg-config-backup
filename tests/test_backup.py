from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
import requests

import backup


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
        self.calls = []
        self.backup_status = backup_status
        self.download_type = download_type
        self.backup_json = {"data": [{"url": "/dl/file.unf"}]} if backup_json is None else backup_json
        # One status per cmd/backup call, the last one repeats.
        self.backup_statuses = list(backup_statuses or [backup_status])
        self.system_status = system_status
        self.system_headers = {"content-type": "application/octet-stream"} if system_headers is None else system_headers

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
            return FakeResponse(status=self.system_status, headers=self.system_headers, content=b"system-data")
        return FakeResponse(headers={"content-type": self.download_type}, content=b"unf-data")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("UCG_HOST", "192.0.2.1")
    monkeypatch.setenv("UCG_USERNAME", "bot")
    monkeypatch.setenv("UCG_PASSWORD", "secret")
    monkeypatch.setattr(backup, "HOST", "192.0.2.1")
    monkeypatch.setattr(backup, "BASE", "https://192.0.2.1")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup, "RETENTION_DAYS", 30)
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
    assert run["size_bytes"] == len(b"unf-data")
    assert (settings / run["filename"]).read_bytes() == b"unf-data"
    assert run["backups_kept"] == 1
    assert run["system_filename"].endswith(".unifi")
    assert run["system_size_bytes"] == len(b"system-data")
    assert (settings / run["system_filename"]).read_bytes() == b"system-data"
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
    assert "freed 5 bytes" in capsys.readouterr().out


class HtmlResponse(FakeResponse):
    def json(self):
        raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)


@pytest.mark.parametrize("answer", [{"data": {"url": "/dl/file.unf"}}, {"data": []}, {}, [], {"data": [{}]}, {"data": [1]}])
def test_unexpected_backup_answer_gives_the_clear_error(settings, monkeypatch, answer):
    session = FakeSession(backup_json=answer)
    monkeypatch.setattr(requests, "Session", lambda: session)
    with pytest.raises(RuntimeError, match="Unexpected backup response, no download URL: .*UCG_SITE"):
        backup.main(new_run())


def test_a_non_json_backup_answer_gives_the_clear_error(settings, monkeypatch):
    class HtmlSession(FakeSession):
        def post(self, url, **kwargs):
            if url.endswith("/cmd/backup"):
                return HtmlResponse(text="<html>Bad gateway</html>")
            return super().post(url, **kwargs)

    monkeypatch.setattr(requests, "Session", lambda: HtmlSession())
    with pytest.raises(RuntimeError, match="Unexpected backup response, no download URL: <html>Bad gateway</html>"):
        backup.main(new_run())


def test_a_403_is_retried_once_after_a_fresh_login(settings, monkeypatch):
    session = FakeSession(backup_statuses=[403, 200])
    monkeypatch.setattr(requests, "Session", lambda: session)
    run = new_run()
    backup.main(run)
    assert [c for c in session.calls if c[1].endswith("/api/auth/login")] == [("POST", "https://192.0.2.1/api/auth/login")] * 2
    assert [c for c in session.calls if c[1].endswith("/cmd/backup")] == [("POST", "https://192.0.2.1/proxy/network/api/s/default/cmd/backup")] * 2
    assert run["filename"]


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
    assert "WARNING: could not remove ucg-backup-20200101-000000.unf: read-only share" in out
    assert "ERROR" not in out
    assert "Backup completed successfully." in out
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
    assert f"webhook URL is not valid ({error.__name__})" in out
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
    assert (settings / run["filename"]).read_bytes() == b"unf-data"
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
