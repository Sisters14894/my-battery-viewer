# -*- coding: utf-8 -*-
"""panel.py —— 我的设备电量面板（tkinter，三张卡片）

用法：
    python panel.py          打开面板（默认自动刷新）
    python panel.py --once   命令行读一次就退出

线程安全约定：tkinter 不是线程安全的，所有设备读取都在后台线程进行，
结果通过队列回到主线程统一刷新（after 轮询泵）。
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback

import devices
import tray_common as tc

BG = "#15171c"
PANEL = "#1e2127"
PANEL2 = "#272b32"
FG = "#e8eaf0"
MUTED = "#9aa1ad"
ACCENT = "#5b8cff"
FONT = ("Microsoft YaHei UI", 10)

DEVICE_ICONS = {"耳机": "🎧", "手柄": "🎮", "键盘": "⌨️"}


def _icon_for(title: str) -> str:
    for key, emoji in DEVICE_ICONS.items():
        if key in title:
            return emoji + " "
    return ""


def level_color(level) -> str:
    if level is None:
        return MUTED
    if level >= 60:
        return "#5ddc8a"
    if level >= 30:
        return "#f5c451"
    return "#ff6b6b"


class DeviceCard:
    """一张设备卡片：标题 / 副标题 / 大号百分比 / 进度条 / 说明。"""

    def __init__(self, parent, title: str):
        import tkinter as tk
        from tkinter import ttk

        self.frame = ttk.Frame(parent, style="Card.TFrame", padding=18)
        ttk.Label(self.frame, text=_icon_for(title) + title,
                  style="CardTitle.TLabel").pack(anchor="w")
        self.lab_sub = ttk.Label(self.frame, text="正在读取…",
                                 style="CardSub.TLabel")
        self.lab_sub.pack(anchor="w", pady=(3, 0))

        self.lab_level = ttk.Label(self.frame, text="--", style="CardBig.TLabel")
        self.lab_level.pack(anchor="w", pady=(12, 0))
        self.lab_status = ttk.Label(self.frame, text="", style="CardSub.TLabel")
        self.lab_status.pack(anchor="w")

        self.bar = ttk.Progressbar(self.frame, mode="determinate", maximum=100,
                                   style="Card.Horizontal.TProgressbar")
        self.bar.pack(fill="x", pady=(12, 10))

        self.lab_detail = ttk.Label(self.frame, text="", style="CardSub.TLabel",
                                    wraplength=250, justify="left")
        self.lab_detail.pack(anchor="w")

    def update(self, r: devices.Reading) -> None:
        self.lab_sub.configure(text=r.subtitle or " ")
        if r.ok:
            self.lab_level.configure(text=f"{r.clipped()}%",
                                     foreground=level_color(r.level))
            self.lab_status.configure(text=r.status or " ")
            self.bar["value"] = r.clipped()
            self.lab_detail.configure(text=r.detail or " ")
        else:
            self.lab_level.configure(text="--", foreground=MUTED)
            self.lab_status.configure(text=r.status or " ")
            self.bar["value"] = 0
            self.lab_detail.configure(text=r.error or "读取失败")


def build_ui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(tc.UI_TITLE)
    root.configure(bg=BG)
    root.geometry("960x560")
    root.minsize(880, 520)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, font=FONT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("H1.TLabel", background=BG, foreground=FG,
                    font=("Microsoft YaHei UI", 15, "bold"))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("CardTitle.TLabel", background=PANEL, foreground=FG,
                    font=("Microsoft YaHei UI", 12, "bold"))
    style.configure("CardSub.TLabel", background=PANEL, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9))
    style.configure("CardBig.TLabel", background=PANEL, foreground="#5ddc8a",
                    font=("Microsoft YaHei UI", 40, "bold"))
    style.configure("Card.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=PANEL2, borderwidth=0, thickness=8)
    style.configure("TButton", background=PANEL2, foreground=FG, borderwidth=0,
                    focusthickness=0, padding=(14, 7), font=FONT)
    style.map("TButton", background=[("active", "#31353d"),
                                     ("disabled", "#2a2d33")],
              foreground=[("disabled", "#6b7076")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", "#77a2ff")])
    style.configure("TCheckbutton", background=BG, foreground=FG, font=FONT)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                    foreground=FG)

    state = {"busy": False, "auto": True, "interval": 60, "after_id": None,
             "starting": False, "started_at": 0.0}
    results: "queue.Queue[tuple]" = queue.Queue()

    cfg = tc.load_config()
    state["interval"] = int(cfg["interval"])

    # ---- 顶部工具条 ----
    head = ttk.Frame(root, padding=(20, 16, 20, 8))
    head.pack(fill="x")
    ttk.Label(head, text=tc.UI_TITLE, style="H1.TLabel").pack(side="left")

    v_auto = tk.BooleanVar(value=True)
    v_interval = tk.StringVar(
        value=dict((f"{s}s", l) for s, l in tc.INTERVAL_CHOICES).get(
            f"{state['interval']}s", "1 分钟"))

    def on_auto():
        state["auto"] = v_auto.get()
        _reschedule()

    btn_refresh = ttk.Button(head, text="立即刷新", style="Accent.TButton")
    btn_refresh.pack(side="right")
    ttk.Checkbutton(head, text="自动刷新", variable=v_auto,
                    command=on_auto).pack(side="right", padx=(0, 12))
    combo = ttk.Combobox(head, textvariable=v_interval, width=10,
                         state="readonly",
                         values=[label for _, label in tc.INTERVAL_CHOICES])
    combo.pack(side="right", padx=(0, 6))
    ttk.Label(head, text="刷新间隔", style="Muted.TLabel").pack(side="right")

    # ---- 三张设备卡片 ----
    body = ttk.Frame(root, padding=(20, 4, 20, 4))
    body.pack(fill="both", expand=True)
    for col in range(3):
        body.columnconfigure(col, weight=1, uniform="card")
    body.rowconfigure(0, weight=1)

    readings_getter = devices.read_all
    cards = []
    for i, title in enumerate(("索尼 WH-1000XM4", "Xbox Series 手柄",
                               "Hi75C Pro 键盘")):
        card = DeviceCard(body, title)
        card.frame.grid(row=0, column=i, sticky="nsew",
                        padx=(0 if i == 0 else 8, 0 if i == 2 else 8))
        cards.append(card)

    foot = ttk.Label(root, text="正在读取…", style="Muted.TLabel",
                     padding=(20, 4))
    foot.pack(fill="x", side="bottom")

    # ---- 设置条 ----
    setbar = ttk.Frame(root, style="Panel.TFrame", padding=(16, 10))
    setbar.pack(fill="x", padx=20, pady=(2, 10))

    def on_interval(_event=None):
        sec = dict((label, sec) for sec, label in tc.INTERVAL_CHOICES)[
            v_interval.get()]
        cfg["interval"] = sec
        tc.save_config(cfg)
        state["interval"] = sec
        tc.post_to_tray(tc.MSG_RELOAD)
        _reschedule()
        refresh()

    combo.bind("<<ComboboxSelected>>", on_interval)

    def notify(text: str, seconds: float = 8.0):
        state["notice"] = text
        state["notice_until"] = time.time() + seconds
        refresh_tray_state()

    def on_boot():
        want = bool(v_boot.get())
        ok = tc.set_autostart(want)
        cfg["autostart"] = want
        tc.save_config(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        if want and not ok:
            notify("开机自启设置失败：无法创建启动文件夹快捷方式", 12)
        elif want:
            notify("已加入开机自启（任务管理器 → 启动 标签里能看到）", 12)
        else:
            notify("已取消开机自启")

    def refresh_tray_state():
        if state.get("starting"):
            return
        if state.get("notice") and time.time() < state.get("notice_until", 0):
            btn_tray.configure(
                state="normal",
                text="退出托盘程序" if tc.is_tray_running() else "启动托盘程序")
            lab_tray.configure(text=state["notice"])
            return
        state["notice"] = ""
        running = tc.is_tray_running()
        btn_tray.configure(state="normal",
                           text="退出托盘程序" if running else "启动托盘程序")
        lab_tray.configure(
            text="托盘运行中：右键托盘图标打开本窗口" if running
            else "托盘未运行：不会后台自动刷新")

    def tray_done(ok: bool):
        state["starting"] = False
        refresh_tray_state()
        if not ok:
            notify("托盘启动失败：可在命令行运行 python tray_app.py --debug 排查", 12)

    def on_tray_toggle():
        if tc.is_tray_running():
            tc.post_to_tray(tc.MSG_QUIT)
            for delay in (600, 1500, 3000):
                root.after(delay, refresh_tray_state)
            return
        state["starting"] = True
        state["started_at"] = time.time()
        btn_tray.configure(state="disabled", text="正在启动…")
        lab_tray.configure(text="正在启动托盘程序…")

        def work():
            import os
            import subprocess
            try:
                subprocess.Popen(
                    [tc.pythonw_path(),
                     os.path.join(tc.BASE_DIR, "tray_app.py")],
                    cwd=tc.BASE_DIR,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                time.sleep(1.5)
                ok = tc.is_tray_running()
            except Exception:
                traceback.print_exc()
                ok = False
            results.put(("tray", ok))

        threading.Thread(target=work, daemon=True).start()

    row = ttk.Frame(setbar, style="Panel.TFrame")
    row.pack(fill="x")
    ttk.Label(row, text="设置", style="CardTitle.TLabel").pack(side="left")
    v_boot = tk.BooleanVar(value=tc.is_autostart_enabled())
    ttk.Checkbutton(row, text="开机自启", variable=v_boot,
                    command=on_boot).pack(side="left", padx=(20, 0))

    lab_tray = ttk.Label(row, text="", style="CardSub.TLabel")
    lab_tray.pack(side="right", padx=(0, 12))
    btn_tray = ttk.Button(row, text="启动托盘程序", command=on_tray_toggle)
    btn_tray.pack(side="right")
    refresh_tray_state()

    # ---- 刷新逻辑 ----
    def apply(readings: list[devices.Reading]):
        for card, r in zip(cards, readings):
            card.update(r)
        ok = sum(1 for r in readings if r.ok)
        foot.configure(
            text=f"更新于 {time.strftime('%H:%M:%S')}    成功读取 {ok}/3 台设备"
                 + ("" if state["auto"] else "    （自动刷新已关闭）"))
        state["busy"] = False
        btn_refresh.configure(state="normal")

    def worker():
        try:
            readings = readings_getter()
        except Exception:
            traceback.print_exc()
            readings = [devices.Reading(t, "", error="读取异常")
                        for t in ("耳机", "手柄", "键盘")]
        results.put(("readings", readings))

    def refresh():
        if state["busy"]:
            return
        state["busy"] = True
        btn_refresh.configure(state="disabled")
        foot.configure(text="正在读取…")
        threading.Thread(target=worker, daemon=True).start()

    def _reschedule():
        if state["after_id"]:
            root.after_cancel(state["after_id"])
            state["after_id"] = None
        if state["auto"]:
            state["after_id"] = root.after(state["interval"] * 1000, _tick)

    def _tick():
        refresh()
        _reschedule()

    btn_refresh.configure(command=refresh)
    root.after(300, refresh)
    _reschedule()

    # ---- 后台结果泵 + 托盘状态跟随 ----
    def pump():
        try:
            while True:
                kind, payload = results.get_nowait()
                if kind == "readings":
                    apply(payload)
                elif kind == "tray":
                    tray_done(payload)
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
            state["busy"] = False
            btn_refresh.configure(state="normal")
        if state["starting"] and time.time() - state["started_at"] > 20:
            state["starting"] = False
        refresh_tray_state()
        root.after(1000, pump)

    root.after(1000, pump)

    def raise_me():
        try:
            root.lift()
            root.focus_force()
        except Exception:
            pass
    root.after(250, raise_me)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="我的设备电量")
    parser.add_argument("--once", action="store_true",
                        help="命令行读一次就退出")
    args = parser.parse_args()

    if args.once:
        devices.cli_once()
        return
    try:
        root = build_ui()
    except Exception:
        print("无法启动图形界面，改用命令行模式：")
        traceback.print_exc()
        devices.cli_once()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
