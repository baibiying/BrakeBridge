import base64
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from time import monotonic

from flask import current_app

import figeon
import social
from social_providers import ProviderError
from storage import get_db

TIMEOUT = 300
SESSIONS_LOCK = threading.Lock()
LOGIN_SETTING = "figeon_vendor_login"


class Verification:
    def __init__(self, process):
        self.process = process
        self.results = queue.Queue()
        self.deadline = monotonic() + TIMEOUT
        self.lock = threading.Lock()
        self.view = {"state": "connecting", "message": "正在打开转单云验证页。"}
        threading.Thread(target=self.read, daemon=True).start()
        timer = threading.Timer(TIMEOUT, self.expire)
        timer.daemon = True
        timer.start()

    def read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.results.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.results.put({"state": "unavailable", "message": "验证窗口已关闭，请重新打开。"})

    def expire(self):
        with self.lock:
            if self.view.get("state") in {"connecting", "challenge"}:
                self.close()
                self.view = {"state": "expired", "message": "验证码会话已过期，请打开新会话。"}

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()

    def wait(self):
        try:
            self.view = self.results.get(timeout=45)
        except queue.Empty:
            self.close()
            self.view = {"state": "unavailable", "message": "转单云验证加载超时，请重试。"}
        if self.view.get("state") == "authorized":
            token = self.view.pop("token", "")
            self.view = {"state": "connecting", "message": "转单云已登录，正在验证飞鸽。"}
            try:
                figeon.connect(token)
                self.view = {"state": "verified", "message": "转单云验证通过，飞鸽帐号已连接。"}
            except (ProviderError, ValueError) as error:
                self.view = {"state": "unavailable", "message": str(error)}
        if self.view.get("state") != "challenge":
            self.close()
        return self.public()

    def public(self):
        return {key: self.view[key] for key in ("state", "message") if key in self.view} | {
            "image_ready": self.view.get("state") == "challenge" and bool(self.view.get("image")),
            "expires_in": max(0, round(self.deadline - monotonic())),
        }

    def image(self):
        if self.view.get("state") != "challenge" or self.deadline <= monotonic():
            raise ValueError("验证码已过期，请先打开新会话。")
        return base64.b64decode(self.view["image"], validate=True)

    def submit(self, code):
        with self.lock:
            if self.view.get("state") != "challenge" or self.deadline <= monotonic():
                raise ValueError("验证码会话已过期，请重新打开。")
            if not code or len(code) > 16 or not code.isalnum():
                raise ValueError("请输入图中的验证码。")
            self.view = {"state": "connecting", "message": "正在提交验证。"}
            try:
                self.process.stdin.write(json.dumps({"code": code}) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                self.close()
                raise ValueError("原验证会话已断开，请重新打开。") from error
            return self.wait()


def login_record():
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (LOGIN_SETTING,)).fetchone()
    return json.loads(row["value"]) if row else None


def save_login(username, password):
    username = social.text(username, 160, "转单云帐号")
    if not username or not password or len(password) > 256:
        raise ValueError("请填写转单云帐号和密码。")
    encrypted = social.cipher().encrypt(password.encode()).decode()
    db = get_db()
    with db:
        db.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                      ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                   (LOGIN_SETTING, json.dumps({"username": username, "password": encrypted})))


def login_summary():
    saved = login_record()
    name = saved["username"] if saved else current_app.config["SOCIAL_FIGEON_USER"]
    return {"configured": configured(), "account": (name[:3] + "•••" + name[-3:]) if len(name) >= 7 else ""}


def configured():
    cfg = current_app.config
    return bool(login_record() or (cfg["SOCIAL_FIGEON_ENABLED"] and cfg["SOCIAL_FIGEON_USER"] and cfg["SOCIAL_FIGEON_PASSWORD"]))


def launch():
    cfg = current_app.config
    saved = login_record()
    name = saved["username"] if saved else cfg["SOCIAL_FIGEON_USER"]
    password = social.credential({"active": True, "credential": saved["password"]}) if saved else cfg["SOCIAL_FIGEON_PASSWORD"]
    env = {**os.environ, "SOCIAL_FIGEON_USER": name, "SOCIAL_FIGEON_PASSWORD": password,
           "SOCIAL_FIGEON_CHROME": cfg["SOCIAL_FIGEON_CHROME"]}
    script = Path(__file__).parent / "frontend" / "scripts" / "figeon-challenge.mjs"
    try:
        process = subprocess.Popen(["node", str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as error:
        raise ValueError("验证码连接程序无法启动，请检查 Node.js 和 Playwright。") from error
    return Verification(process)


def session(session_key):
    with SESSIONS_LOCK:
        return current_app.extensions.setdefault("social_verifications", {}).get(session_key)


def start(session_key):
    if not configured():
        raise ValueError("请先保存转单云帐号和密码。")
    with SESSIONS_LOCK:
        sessions = current_app.extensions.setdefault("social_verifications", {})
        for key in [key for key, item in sessions.items() if monotonic() >= item.deadline]:
            del sessions[key]
        previous = sessions.get(session_key)
        if previous and previous.view.get("state") in {"challenge", "connecting"}:
            return previous.public()
        verification = launch()
        sessions[session_key] = verification
    return verification.wait()
