# -*- coding: utf-8 -*-
"""webui.py —— 把 Web 面板变成“应用窗口”

用 Edge 的 --app 模式打开：没有地址栏、没有标签页，看起来就是独立桌面应用。

窗口尺寸策略（实测踩坑结论）：
* 必须用**专用 profile**（--user-data-dir 指到我们自己的目录）：
  共用用户主 profile 时，Edge 的“启动加速”后台进程会忽略 --window-size，
  还会用主窗口的大小开新窗口，完全失控；独立 profile 下尺寸参数和
  JS 的 window.resizeTo 都严格生效。
* 启动时统一给基础尺寸 760x500（逻辑像素，DPI 无关），
  页面加载后由 app.js 用 localStorage 恢复用户上次的尺寸/位置——
  用户手动调整过一次，之后每次打开都自动恢复。
"""

from __future__ import annotations

import os
import subprocess
import webbrowser

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_BROWSER_CANDIDATES = [
    # Edge 是 Win10/11 自带的，优先
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
]

BASE_WINDOW_SIZE = "760,500"


def find_app_browser() -> str:
    """找到支持 --app 模式的浏览器，找不到返回空串。"""
    for cand in _BROWSER_CANDIDATES:
        path = os.path.expandvars(cand)
        if os.path.isfile(path):
            return path
    return ""


def profile_dir() -> str:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "MyBatteryViewer", "edge-profile")


def open_panel_window(url: str) -> bool:
    """以应用窗口模式打开面板。返回是否用了独立窗口（而非默认浏览器）。"""
    exe = find_app_browser()
    if exe:
        try:
            subprocess.Popen(
                [exe, f"--app={url}",
                 f"--window-size={BASE_WINDOW_SIZE}",
                 "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={profile_dir()}"],
                creationflags=CREATE_NO_WINDOW)
            return True
        except OSError:
            pass
    webbrowser.open(url)
    return False
