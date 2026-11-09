"""Local durable webhook transport for the native Freqtrade Webhook RPC handler."""

# ruff: noqa: RUF001 -- Chinese notification copy uses native punctuation.

import fcntl
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests

from deploy.live_prepare_20261109.preflight import ROOT, STATE, private_env


PORT = 18789


def initialize():
    os.umask(0o077)
    STATE.mkdir(exist_ok=True)
    token = STATE / "notification_token"
    if not token.exists():
        token.write_text(secrets.token_urlsafe(32))
        token.chmod(0o600)
    return token.read_text().strip()


class Outbox:
    def __init__(self, path):
        self.path = path
        if path.is_symlink():
            raise ValueError("Notification outbox cannot be a symlink")
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, last_attempt REAL,
                result TEXT, delivered REAL)""")
        path.chmod(0o600)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, payload, now=None):
        now = time.time() if now is None else now
        if (
            not isinstance(payload, dict)
            or payload.get("msgtype") != "text"
            or not isinstance(payload.get("text"), dict)
        ):
            raise ValueError("Only native text notification payloads are admitted")
        content = payload["text"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Notification text is empty")
        stamp = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        content += "\n事件接收时间：" + stamp
        if len(content.encode()) > 1800:
            content = content.encode()[:1720].decode(errors="ignore") + "\n[超长内容已截断]"
        content = content.replace("仓位调整=True", "仓位调整=是").replace(
            "仓位调整=False", "仓位调整=否"
        )
        safe = {"msgtype": "text", "text": {"content": content}}
        with self.connect() as conn:
            return conn.execute(
                "INSERT INTO messages(payload,created) VALUES (?,?)",
                (json.dumps(safe, ensure_ascii=False), now),
            ).lastrowid

    def deliver_one(self, sender, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            latest = conn.execute("SELECT MAX(last_attempt) FROM messages").fetchone()[0]
            if latest is not None and now - latest < 4:
                return False
            row = conn.execute(
                "SELECT * FROM messages WHERE state='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None or row["next_attempt"] > now:
                return False
            if now - row["created"] > 86400:
                conn.execute(
                    "UPDATE messages SET state='dead',result='expired' WHERE id=?", (row["id"],)
                )
                return True
        # Errors must never include the private upstream webhook URL.
        try:
            code = sender(json.loads(row["payload"]))
            accepted = type(code) is int and code == 0
            outcome = "errcode:" + str(code) if type(code) is int else "invalid_response"
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            accepted, outcome = False, type(exc).__name__
        attempts = row["attempts"] + 1
        state = "sent" if accepted else "dead" if attempts >= 8 else "pending"
        with self.connect() as conn:
            conn.execute(
                """UPDATE messages SET state=?,attempts=?,last_attempt=?,next_attempt=?,
                         result=?,delivered=? WHERE id=?""",
                (
                    state,
                    attempts,
                    now,
                    now + min(300, 3 * 2**attempts),
                    outcome,
                    now if accepted else None,
                    row["id"],
                ),
            )
        return True

    def status(self):
        with self.connect() as conn:
            return {
                r["state"]: r["n"]
                for r in conn.execute("SELECT state,count(*) AS n FROM messages GROUP BY state")
            }


def upstream_sender():
    value = private_env(ROOT / "user_data/deployment/.env.notifications")["WECHAT_WORK_WEBHOOK"]
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "qyapi.weixin.qq.com"
        or parsed.path != "/cgi-bin/webhook/send"
        or not parse_qs(parsed.query).get("key")
    ):
        raise ValueError("Unexpected private notification destination")
    session = requests.Session()
    session.auth = lambda request: request

    def send(payload):
        response = session.post(value, json=payload, timeout=(3, 7), allow_redirects=False)
        response.raise_for_status()
        return response.json()["errcode"]

    return send


def handler(outbox, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            alive = self.server.dispatcher.is_alive()
            body = json.dumps(
                {"status": "running" if alive else "worker_failed", "outbox": outbox.status()}
            ).encode()
            self.send_response(200 if alive else 503)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not hmac.compare_digest(self.path, "/events/" + token):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size <= 0 or size > 8192:
                    raise ValueError("Invalid notification size")
                identifier = outbox.enqueue(json.loads(self.rfile.read(size)))
            except (ValueError, TypeError):
                self.send_error(400)
                return
            except sqlite3.Error:
                self.send_error(503)
                return
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"queued": identifier}).encode())

    return Handler


def main():
    token = initialize()
    lock = (STATE / "notification.lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    outbox = Outbox(STATE / "notification.sqlite")
    sender = upstream_sender()

    def dispatch():
        while True:
            try:
                outbox.deliver_one(sender)
            except sqlite3.Error:
                print("Notification storage error; delivery will be retried", flush=True)
            time.sleep(0.5)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), handler(outbox, token))
    server.dispatcher = Thread(target=dispatch, daemon=True)
    server.dispatcher.start()
    print("Native webhook notification relay listening on loopback", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
