# -*- coding: utf-8 -*-
"""tray_common.py —— 面板与托盘共用的配置、自启动、单实例工具

* 配置（刷新间隔等）存 %LOCALAPPDATA%\\MyBatteryViewer\\config.json
* 开机自启用「启动」文件夹的 .lnk 快捷方式（pythonw 静默启动），
  通过 PowerShell 的 WScript.Shell COM 创建 —— PowerShell 是 Windows
  自带组件，仍然零第三方依赖。
* 托盘单实例：命名互斥体 + 隐藏窗口的窗口类名。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "我的设备电量"
CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR),
                          "MyBatteryViewer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# 托盘隐藏窗口的类名 / 标题（FindWindow 用）
WND_CLASS = "MyBatteryViewerTrayWnd"
MUTEX_NAME = "Global\\MyBatteryViewer_TraySingleton"

# 面板窗口标题（FindWindow 用）
UI_TITLE = "我的设备电量 · WH-1000XM4 / Xbox / Hi75C Pro"

# 托盘内部控制消息（RegisterWindowMessage 的名字）
MSG_QUIT = "MyBatteryViewer_TrayQuit"
MSG_RELOAD = "MyBatteryViewer_TrayReload"
MSG_OPEN = "MyBatteryViewer_TrayOpen"

INTERVAL_CHOICES = [
    (15, "15 秒"), (30, "30 秒"), (60, "1 分钟"),
    (120, "2 分钟"), (300, "5 分钟"), (600, "10 分钟"),
]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                ctypes.c_size_t, ctypes.c_ssize_t]


# ---- 配置 ---------------------------------------------------------------
def load_config() -> dict:
    cfg = {"interval": 60, "autostart": False}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---- Python 路径 ---------------------------------------------------------
def pythonw_path() -> str:
    """优先用 pythonw.exe（无黑窗）；找不到就退回当前解释器。"""
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else exe


# ---- 开机自启（启动文件夹 .lnk） ------------------------------------------
def autostart_lnk_path() -> str:
    startup = os.path.join(os.environ["APPDATA"],
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
    return os.path.join(startup, "我的设备电量托盘.lnk")


def is_autostart_enabled() -> bool:
    return os.path.isfile(autostart_lnk_path())


def set_autostart(enable: bool) -> bool:
    lnk = autostart_lnk_path()
    if not enable:
        try:
            os.remove(lnk)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return True
    target = pythonw_path()
    script = os.path.join(BASE_DIR, "tray_app.py")
    # PowerShell 一行创建快捷方式；引号全部转义好，避免路径带空格出问题
    # --tray-only：开机启动只驻留托盘，不弹面板窗口
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut('{_ps_escape(lnk)}');"
        f"$s.TargetPath = '{_ps_escape(target)}';"
        f"$s.Arguments = '\"{_ps_escape(script)}\" --tray-only';"
        f"$s.WorkingDirectory = '{_ps_escape(BASE_DIR)}';"
        "$s.WindowStyle = 7;"
        "$s.Description = '我的设备电量托盘';"
        "$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and os.path.isfile(lnk)
    except Exception:
        return False


def _ps_escape(s: str) -> str:
    return s.replace("'", "''")


# ---- 托盘进程管理 ---------------------------------------------------------
def tray_hwnd() -> Optional[int]:
    h = user32.FindWindowW(WND_CLASS, None)
    return h if h else None


def is_tray_running() -> bool:
    return tray_hwnd() is not None


def post_to_tray(msg_name: str) -> bool:
    hwnd = tray_hwnd()
    if not hwnd:
        return False
    msg = user32.RegisterWindowMessageW(msg_name)
    return bool(user32.PostMessageW(hwnd, msg, 0, 0))


def ui_hwnd() -> Optional[int]:
    h = user32.FindWindowW(None, UI_TITLE)
    return h if h else None


if __name__ == "__main__":
    print("配置文件:", CONFIG_PATH)
    print("当前配置:", load_config())
    print("托盘运行中:", is_tray_running())
    print("自启动:", is_autostart_enabled(), autostart_lnk_path())
