#!/usr/bin/env python3
"""A stand-in for a UniFi OS console, for tests/smoke.sh. Not part of the image.

Usage: stub_console.py TLS_PORT HOOK_PORT HOOK_LOG CERT KEY

The UCG_SITE of the run picks the behaviour:
  good    a working console: login, backup command, two binary downloads
  err200  answers the downloads with an error text and status 200
  hang    never finishes the download
Everything POSTed to http://...:HOOK_PORT/ is appended to HOOK_LOG.
"""
import http.server
import re
import ssl
import sys
import threading
import time


class Console(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, status, body, headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        if self.path == "/api/auth/login":
            self.reply(200, b"{}", {"x-csrf-token": "stub", "Content-Type": "application/json"})
        elif self.path == "/api/auth/logout":
            self.reply(200, b"{}", {"Content-Type": "application/json"})
        elif match := re.fullmatch(r"/proxy/network/api/s/([^/]+)/cmd/backup", self.path):
            body = b'{"data":[{"url":"/dl/%s.unf"}]}' % match.group(1).encode()
            self.reply(200, body, {"Content-Type": "application/json"})
        else:
            self.reply(404, b"")

    def do_GET(self):
        if self.path == "/api/backup/download":
            self.reply(200, bytes(range(256)) * 16, {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="unifi_os_backup_1.unifi"',
            })
        elif match := re.fullmatch(r"/proxy/network/dl/([^/]+)\.unf", self.path):
            site = match.group(1)
            if site == "hang":
                time.sleep(120)
            if site == "err200":
                self.reply(200, b"Unauthorized", {"Content-Type": "text/plain"})
            else:
                self.reply(200, b"\x00unf" + bytes(range(255, -1, -1)) * 32, {"Content-Type": "application/octet-stream"})
        else:
            self.reply(404, b"")

    def log_message(self, *args):
        pass


def hook_handler(log_path):
    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            with open(log_path, "ab") as log:
                log.write(body + b"\n")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Hook


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a client that hangs up after the handshake (a fingerprint probe)


def make_console_server(port, cert, key, host="0.0.0.0"):
    tls = QuietServer((host, int(port)), Console)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    tls.socket = context.wrap_socket(tls.socket, server_side=True)
    return tls


if __name__ == "__main__":
    tls_port, hook_port, hook_log, cert, key = sys.argv[1:6]
    tls = make_console_server(tls_port, cert, key)
    hook = http.server.ThreadingHTTPServer(("0.0.0.0", int(hook_port)), hook_handler(hook_log))
    threading.Thread(target=hook.serve_forever, daemon=True).start()
    tls.serve_forever()
