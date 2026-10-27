# -*- coding: utf-8 -*-
"""tray_app.py —— 托盘电量监视器（纯 ctypes + 标准库，无第三方依赖）

单个托盘图标：
    数字 = 三台设备中**最低**的电量（最需要关注的那台），
    颜色随电量：绿 ≥60 / 黄 30-59 / 红 <30 / 灰 = 全部未读到。

面板：内置本地 HTTP 服务 + Web 前端，用 Edge/Chrome 的 --app 模式
弹出独立应用窗口（无地址栏）。右键菜单 →「打开面板」。

运行：
    pythonw tray_app.py                托盘驻留 + 弹出面板窗口（exe 双击默认）
    pythonw tray_app.py --tray-only    只驻留托盘（开机自启用）
    python  tray_app.py --panel        只开面板窗口，不开托盘
    python  tray_app.py --debug        前台运行并打印日志
    python  tray_app.py --status       查看状态
    python  tray_app.py --quit         让运行中的实例退出

实现要点（都是前人踩过的坑）：
* 销毁窗口后必须 PostQuitMessage(0)，否则进程残留还占着单实例锁；
* 任务栏重启（explorer 崩溃/重启）后要监听 TaskbarCreated 重新挂图标；
* 已有实例在跑时，再次双击 exe 发 MSG_OPEN 唤起新的面板窗口；
* 读取放后台线程，绝不阻塞消息循环；
* SetTimer 驱动刷新，空闲时几乎不占 CPU。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Optional

import devices
import tray_common as tc
import win_icon
import webserver
import webui
from hid_win import GUID

USER32 = tc.user32
KERNEL32 = tc.kernel32
SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
MSG_TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH_DONE = WM_APP + 2

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
SW_RESTORE = 9

WM_COMMAND = 0x0111
WM_NULL = 0x0000
MF_STRING, MF_GRAYED, MF_DISABLED, MF_SEPARATOR = 0x0, 0x1, 0x2, 0x800
TPM_RIGHTBUTTON = 0x0002

# 右键菜单命令 ID
CMD_DEVICE_BASE = 10     # 10/11/12 = 三台设备的信息行（不可点击）
CMD_REFRESH = 20
CMD_OPEN_UI = 21
CMD_QUIT = 30

# (读取函数名 key, 菜单/提示里的显示名) —— 单图标方案：图标数字 = 三台中最低电量
TRAY_DEVICES = [
    ("headphone", "🎧 WH-1000XM4"),
    ("controller", "🎮 Xbox 手柄"),
    ("keyboard", "⌨️ Hi75C Pro"),
]

TRAY_ICON_ID = 1

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", ctypes.c_void_p),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", ctypes.c_void_p), ("message", wintypes.UINT),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


USER32.CreatePopupMenu.restype = ctypes.c_void_p
USER32.CreatePopupMenu.argtypes = []
USER32.AppendMenuW.restype = wintypes.BOOL
USER32.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
USER32.DestroyMenu.restype = wintypes.BOOL
USER32.DestroyMenu.argtypes = [ctypes.c_void_p]
USER32.TrackPopupMenu.restype = wintypes.BOOL
USER32.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_void_p]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]


USER32.RegisterClassExW.restype = wintypes.WORD
USER32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
USER32.CreateWindowExW.restype = ctypes.c_void_p
USER32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
USER32.DestroyWindow.restype = wintypes.BOOL
USER32.DestroyWindow.argtypes = [ctypes.c_void_p]
USER32.SetTimer.restype = ctypes.c_size_t
USER32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT,
                            ctypes.c_void_p]
USER32.KillTimer.restype = wintypes.BOOL
USER32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
USER32.GetMessageW.restype = ctypes.c_int
USER32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                               wintypes.UINT, wintypes.UINT]
USER32.PostQuitMessage.restype = None
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.TranslateMessage.restype = wintypes.BOOL
USER32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
USER32.DispatchMessageW.restype = ctypes.c_ssize_t
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]


class TrayApp:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.cfg = tc.load_config()
        self.hwnd: Optional[int] = None
        self.wndproc_ref = None
        self.busy = False
        self.icons_added = False
        # key -> (level, 一句话说明)
        self.levels = {key: (None, "正在读取…")
                       for key, _ in TRAY_DEVICES}
        self.taskbar_created = USER32.RegisterWindowMessageW("TaskbarCreated")
        self.msg_quit = USER32.RegisterWindowMessageW(tc.MSG_QUIT)
        self.msg_reload = USER32.RegisterWindowMessageW(tc.MSG_RELOAD)
        self.msg_open = USER32.RegisterWindowMessageW(tc.MSG_OPEN)
        self.url = ""      # Web 面板地址（run() 里启动服务后填）
        self.log("启动，配置 = " + json.dumps(self.cfg, ensure_ascii=False))

    def log(self, msg: str) -> None:
        if self.debug:
            print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    # ---- 图标 ----------------------------------------------------------
    def _lowest_level(self):
        """三台中在线设备的最低电量（图标显示它——最需要关注的那台）。"""
        vals = [v[0] for v in self.levels.values() if v[0] is not None]
        return min(vals) if vals else None

    def _summary_tip(self) -> str:
        parts = []
        for key, title in TRAY_DEVICES:
            level, _ = self.levels[key]
            parts.append(f"{title} {level}%" if level is not None
                         else f"{title} --")
        return "   ".join(parts) + f"（{time.strftime('%H:%M')} 更新）"

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = TRAY_ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = MSG_TRAY_CALLBACK
        return nid

    def _apply_icon(self, action: int) -> bool:
        level = self._lowest_level()
        text = str(level) if level is not None else "--"
        nid = self._nid()
        nid.hIcon = win_icon.make_hicon(text, win_icon.battery_color(level),
                                        win_icon.PLATE_NONE)
        nid.szTip = self._summary_tip()
        return bool(SHELL32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    def add_icons(self) -> None:
        ok = self._apply_icon(NIM_ADD)
        self.log(f"挂图标 -> {'ok' if ok else '失败'}")
        self.icons_added = True

    def update_icons(self) -> None:
        if not self.icons_added:
            self.add_icons()
            return
        self._apply_icon(NIM_MODIFY)

    def remove_icons(self) -> None:
        nid = self._nid()
        nid.uFlags = 0
        SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.icons_added = False

    # ---- 右键菜单：列出全部设备电量 ------------------------------------
    def show_menu(self) -> None:
        menu = USER32.CreatePopupMenu()
        if not menu:
            return
        try:
            for i, (key, title) in enumerate(TRAY_DEVICES):
                level, err = self.levels[key]
                text = (f"{title}：{level}%" if level is not None
                        else f"{title}：{err or '未连接'}")
                USER32.AppendMenuW(menu, MF_STRING | MF_GRAYED | MF_DISABLED,
                                   CMD_DEVICE_BASE + i, text)
            USER32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            USER32.AppendMenuW(menu, MF_STRING, CMD_REFRESH, "立即刷新")
            USER32.AppendMenuW(menu, MF_STRING, CMD_OPEN_UI, "打开面板")
            USER32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            USER32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "退出")

            pt = POINT()
            USER32.GetCursorPos(ctypes.byref(pt))
            # 弹菜单前必须把宿主窗口调到前台，并在菜单后补 WM_NULL，
            # 否则菜单可能在别处点击时不消失（KB135788 的经典问题）
            USER32.SetForegroundWindow(self.hwnd)
            USER32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0,
                                  self.hwnd, None)
            USER32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            USER32.DestroyMenu(menu)

    # ---- 定时 / 配置 ----------------------------------------------------
    def apply_timer(self) -> None:
        if self.hwnd:
            USER32.KillTimer(self.hwnd, 1)
            USER32.SetTimer(self.hwnd, 1, int(self.cfg["interval"]) * 1000,
                            None)

    def apply_config(self) -> None:
        new = tc.load_config()
        if new["interval"] != self.cfg["interval"]:
            self.log(f"刷新间隔 -> {new['interval']} 秒")
            self.cfg = new
            self.apply_timer()
        else:
            self.cfg = new
        # 自启只读不改：托盘绝不替用户动系统设置
        actual = tc.is_autostart_enabled()
        if bool(self.cfg.get("autostart")) != actual:
            self.cfg["autostart"] = actual
            tc.save_config(self.cfg)

    # ---- 刷新 ----------------------------------------------------------
    def refresh_now(self) -> None:
        if self.busy or not self.hwnd:
            return
        self.busy = True
        threading.Thread(target=self._worker, args=(self.hwnd,),
                         daemon=True).start()

    def _worker(self, hwnd: int) -> None:
        try:
            r = devices.read_headphone()
            self.levels["headphone"] = (r.level, r.status or r.error)
            r = devices.read_controller()
            self.levels["controller"] = (r.level, r.status or r.error)
            r = devices.read_keyboard()
            self.levels["keyboard"] = (r.level, r.status or r.error)
        except Exception:
            self.log("读取异常:\n" + traceback.format_exc())
        USER32.PostMessageW(hwnd, MSG_REFRESH_DONE, 0, 0)

    def on_refresh_done(self) -> None:
        self.busy = False
        parts = [f"{k}={v[0]}" for k, v in self.levels.items()]
        self.log("刷新完成：" + " ".join(parts))
        self.update_icons()

    # ---- 打开面板 / 退出 -------------------------------------------------
    def open_ui(self) -> None:
        if not self.url:
            self.url, _, _ = webserver.start_server(panel_only=False)
            self.log(f"Web 服务启动：{self.url}")
        ok = webui.open_panel_window(self.url)
        self.log(f"打开面板 -> {'应用窗口' if ok else '默认浏览器'}")

    def quit(self) -> None:
        self.log("退出")
        self.remove_icons()
        if self.hwnd:
            # WM_DESTROY -> PostQuitMessage -> 消息循环退出，进程干净结束
            USER32.DestroyWindow(self.hwnd)

    # ---- 窗口过程 -------------------------------------------------------
    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == MSG_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self.show_menu()          # 右键：菜单列出全部设备电量
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.refresh_now()        # 左键：立即刷新
            return 0
        if msg == WM_COMMAND:
            cmd = int(wparam) & 0xFFFF
            if cmd == CMD_REFRESH:
                self.refresh_now()
            elif cmd == CMD_OPEN_UI:
                self.open_ui()
            elif cmd == CMD_QUIT:
                self.quit()
            return 0
        if msg == MSG_REFRESH_DONE:
            self.on_refresh_done()
            return 0
        if msg == WM_TIMER:
            self.apply_config()
            self.refresh_now()
            return 0
        if msg == self.msg_quit:
            self.quit()
            return 0
        if msg == self.msg_reload:
            self.apply_config()
            return 0
        if msg == self.msg_open:
            self.open_ui()          # 再次双击 exe 时唤起面板窗口
            return 0
        if msg == self.taskbar_created:
            self.log("任务栏重建，重新挂图标")
            self.add_icons()
            return 0
        if msg == WM_DESTROY:
            # 不调用 PostQuitMessage 的话进程会残留并占着单实例锁
            USER32.PostQuitMessage(0)
            return 0
        return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self, open_window: bool = True) -> int:
        hinst = KERNEL32.GetModuleHandleW(None)
        self.wndproc_ref = WNDPROC(self.wnd_proc)   # 必须持有引用防 GC
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self.wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = tc.WND_CLASS
        if not USER32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):     # 1410=类已注册（上次实例残留，无碍）
                self.log(f"RegisterClassExW 失败 err={err}")
                return 1

        hwnd = USER32.CreateWindowExW(0, tc.WND_CLASS, tc.APP_NAME, 0,
                                      0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            self.log(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
            return 1
        self.hwnd = hwnd

        self.add_icons()
        self.apply_config()
        self.apply_timer()
        self.refresh_now()

        if open_window:
            self.open_ui()      # 双击 exe：面板窗口 + 托盘一起出现

        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))
        self.log("消息循环结束")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="我的设备电量")
    parser.add_argument("--debug", action="store_true", help="前台运行并输出日志")
    parser.add_argument("--status", action="store_true", help="打印状态")
    parser.add_argument("--quit", action="store_true", help="让运行中的实例退出")
    parser.add_argument("--panel", action="store_true",
                        help="只打开面板窗口，不驻留托盘")
    parser.add_argument("--tray-only", action="store_true",
                        help="只驻留托盘，不弹面板窗口（开机自启用）")
    args = parser.parse_args()

    if args.status:
        print("配置：", json.dumps(tc.load_config(), ensure_ascii=False))
        print("托盘运行中：", tc.is_tray_running())
        print("开机自启：", tc.is_autostart_enabled())
        return 0
    if args.quit:
        ok = tc.post_to_tray(tc.MSG_QUIT)
        print("已通知托盘退出" if ok else "托盘没在运行")
        return 0

    # 单实例：已有实例在跑时——
    #   * 默认 / --panel：发 MSG_OPEN 唤起新的面板窗口后退出
    #   * --tray-only：静默退出
    KERNEL32.CreateMutexW(None, False, tc.MUTEX_NAME)
    if ctypes.get_last_error() == 183 and tc.is_tray_running():
        if not args.tray_only:
            tc.post_to_tray(tc.MSG_OPEN)
        return 0

    try:
        USER32.SetProcessDPIAware()   # 高分屏下图标不糊
    except Exception:
        pass

    # ---- 只开面板模式：服务 + 窗口，窗口关了进程就退 ----
    if args.panel:
        import time
        url, _, shutdown = webserver.start_server(panel_only=True)
        webui.open_panel_window(url)
        if not args.debug:
            time.sleep(1.0)     # 给页面留出发起首次请求的时间
        while shutdown and not shutdown.is_set():
            time.sleep(0.3)
        return 0

    app = TrayApp(debug=args.debug)
    try:
        return app.run(open_window=not args.tray_only)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
