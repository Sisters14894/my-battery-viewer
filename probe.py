# -*- coding: utf-8 -*-
"""probe.py —— 探测 / 调试工具

排查“读不到电量”或适配新设备时用：

    python probe.py             全量探测（HID + 蓝牙 + XInput + 键盘协议）
    python probe.py hid         只列 HID 接口
    python probe.py bt          只列带电量的蓝牙设备
    python probe.py keyboard    深挖键盘：dump 接收器全部 Feature Report

适配新键盘的思路：
    1. python probe.py hid 找到接收器的 VID 和 vendor 接口（UsagePage ≥ 0xFF00）
    2. python probe.py keyboard 看哪些 Report ID 有回复、哪个字节像电量
       （换个电量状态再看一次，对比哪个字节变了）
    3. 把 VID / UsagePage / ReportID / 字节下标 填进 devices.py 顶部的常量
"""

from __future__ import annotations

import sys

import hid_win
import bt_battery


def probe_hid() -> None:
    print("=" * 70)
    print("所有 HID 接口")
    print("=" * 70)
    for d in hid_win.enumerate_hid():
        name = d.product or d.manufacturer or ""
        print(f"{d.vid_pid}  page=0x{d.usage_page:04X} usage=0x{d.usage:04X}"
              f"  in={d.input_len:3d} out={d.output_len:3d}"
              f" feat={d.feature_len:3d}  {name[:44]}")


def probe_bt() -> None:
    print("=" * 70)
    print("带电量属性的蓝牙设备（BTHENUM 经典 / BTHLE 低功耗）")
    print("=" * 70)
    devices = bt_battery.list_bt_devices()
    if not devices:
        print("（没有读到任何蓝牙电量，确认设备已连接）")
        return
    for d in devices:
        tag = "BLE " if d.is_le else "经典"
        print(f" [{tag}] {d.name}  电量={d.battery}%")
        print(f"        {d.instance_id}")


def probe_xinput() -> None:
    print("=" * 70)
    print("XInput 手柄")
    print("=" * 70)
    import xinput_battery as xi
    try:
        gp = xi.read_first()
    except OSError as e:
        print(f"xinput1_4.dll 加载失败：{e}")
        return
    if gp is None:
        print("没有在线的 XInput 手柄")
    else:
        print(f"用户索引 {gp.user_index}：{gp.describe()}"
              f"  估算≈{gp.estimate_percent}%")


def probe_keyboard() -> None:
    print("=" * 70)
    print("键盘 2.4G 接收器深挖（3554:FA09 全部 Feature Report）")
    print("=" * 70)
    ifaces = [d for d in hid_win.enumerate_hid() if d.vid == 0x3554]
    if not ifaces:
        print("没找到 VID 3554 的设备（接收器没插？）")
        return
    for d in ifaces:
        print(f"接口 page=0x{d.usage_page:04X} usage=0x{d.usage:04X} "
              f"feat={d.feature_len}")
        if d.feature_len == 0:
            continue
        for rid in range(0, 16):
            try:
                raw = hid_win.get_feature_report(d.path, rid, d.feature_len)
                print(f"    Report {rid:2d} -> {raw.hex(' ')}")
            except hid_win.HidError:
                pass    # 该 ID 不存在，正常
    print()
    print("提示：电量在 Feature Report 6 的第 4 字节（下标 3，十进制显示）。")
    print("     如显示不符，对比不同电量下的原始字节，改 devices.py 的")
    print("     KEYBOARD_LEVEL_BYTE 常量即可。")


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "hid"):
        probe_hid()
    if what in ("all", "bt"):
        probe_bt()
    if what in ("all", "xinput"):
        probe_xinput()
    if what in ("all", "keyboard"):
        probe_keyboard()


if __name__ == "__main__":
    main()
