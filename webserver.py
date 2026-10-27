# -*- coding: utf-8 -*-
"""webserver.py —— 本地 HTTP 服务（标准库 http.server，零第三方依赖）

给 Web 前端提供两个东西：
  * 静态页面（static/ 目录，PyInstaller 打包后从 _MEIPASS 取）
  * GET /api/battery   三台设备的电量 JSON（带 1.5 秒缓存，防止频繁枚举 HID）
  * POST /api/shutdown 仅 --panel 模式有效：浏览器窗口关闭时让进程退出

只绑定 127.0.0.1 随机端口，不对外网开放。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import devices
import tray_common as tc

STATIC_DIR_NAME = "static"
_STATIC_MIME = {
    "style.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


def static_dir() -> str:
    """静态文件目录：PyInstaller 打包后在 _MEIPASS/static。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, STATIC_DIR_NAME)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        STATIC_DIR_NAME)


class BatteryState:
    """电量快照 + 短缓存（多张页面/托盘同时来问也不重复枚举设备）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Optional[dict] = None
        self._at = 0.0

    def snapshot(self, max_age: float = 1.5) -> dict:
        with self._lock:
            if self._cache and time.time() - self._at < max_age:
                return self._cache
        readings = devices.read_all()
        data = {
            "devices": [
                {
                    "key": key,
                    "title": r.title,
                    "subtitle": r.subtitle,
                    "level": r.level,
                    "status": r.status,
                    "detail": r.detail,
                    "error": r.error,
                }
                for key, r in zip(("headphone", "controller", "keyboard"),
                                  readings)
            ],
            "updated": time.strftime("%H:%M:%S"),
            "ok": sum(1 for r in readings if r.ok),
            "total": len(readings),
        }
        with self._lock:
            self._cache = data
            self._at = time.time()
        return data


def make_handler(state: BatteryState, shutdown_event: Optional[threading.Event],
                 panel_only: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BatteryViewer/1.0"

        def log_message(self, fmt, *args):     # 静默访问日志
            pass

        # -- 应答小工具 ------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _static(self, name: str) -> None:
            path = os.path.join(static_dir(), name)
            try:
                with open(path, "rb") as f:
                    body = f.read()
            except OSError:
                self._json(404, {"error": "not found"})
                return
            ctype = ("text/html; charset=utf-8" if name.endswith(".html")
                     else _STATIC_MIME.get(name, "application/octet-stream"))
            self._send(200, body, ctype)

        # -- 路由 ------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path == "/api/battery":
                try:
                    self._json(200, state.snapshot())
                except Exception as e:
                    self._json(500, {"error": str(e)})
            elif path.startswith("/static/"):
                name = os.path.basename(path)
                if name in _STATIC_MIME:
                    self._static(name)
                else:
                    self._json(404, {"error": "not found"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/shutdown":
                if panel_only and shutdown_event is not None:
                    self._json(200, {"ok": True})
                    shutdown_event.set()
                else:
                    self._json(403, {"ok": False, "reason": "托盘模式下不允许远程关闭"})
            else:
                self._json(404, {"error": "not found"})

    return Handler


def start_server(panel_only: bool = False,
                 preferred_ports: tuple[int, ...] = (8899, 8898, 8897)):
    """启动服务，返回 (url, server, shutdown_event)。

    优先用固定端口：Edge/Chrome 的 --app 窗口按“源”（端口）记忆窗口
    大小位置，端口一变记忆就丢了。固定端口被占（极少见）再退随机端口。
    """
    state = BatteryState()
    shutdown_event = threading.Event() if panel_only else None
    srv = None
    for port in preferred_ports:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port),
                                      make_handler(state, shutdown_event,
                                                   panel_only))
            break
        except OSError:
            continue
    if srv is None:
        srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                  make_handler(state, shutdown_event, panel_only))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    return url, srv, shutdown_event
