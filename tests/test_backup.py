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
    """Stands in for the console: login, cmd/backup, download, logout."""

    def __init__(self, backup_status=200):
        self.headers = {}
        self.cookies = {}
        self.verify = None
        self.calls = []
        self.backup_status = backup_status

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        if url.endswith("/api/auth/login"):
            return FakeResponse(headers={"x-csrf-token": "tok"})
        if url.endswith("/cmd/backup"):
            return FakeResponse(
                status=self.backup_status,
                json_data={"data": [{"url": "/dl/file.unf"}]},
            )
        return FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return FakeResponse(content=b"unf-data")


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


def new_run():
    return {"started": time.monotonic(), "filename": None, "size_bytes": None, "backups_kept": None}


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
        "retention_days", "backups_kept", "timestamp", "version",
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
    assert ("GET", "https://192.0.2.1/proxy/network/dl/file.unf") in session.calls
    assert session.headers["X-CSRF-Token"] == "tok"


def test_main_explains_403_on_backup_command(settings, monkeypatch):
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(backup_status=403))
    with pytest.raises(RuntimeError, match="403"):
        backup.main(new_run())
