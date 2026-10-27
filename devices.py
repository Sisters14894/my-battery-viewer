# -*- coding: utf-8 -*-
"""devices.py —— 三台设备的电量读取层（统一出口）

设备与读取通道（均在本机实测验证）：

1. Sony WH-1000XM4 耳机（蓝牙）
   蓝牙 HFP 免提节点上的未公开 PnP 电量属性（bt_battery.py）。
   耳机连接 Windows 时通过 AT+IPHONEACCEV 上报，精确到 1%。

2. Xbox Series 手柄（蓝牙 / Xbox 无线接收器 / USB 线）
   a. 蓝牙连接：BLE 节点上的 GATT 电量属性（bt_battery.py），精确到 1%；
   b. 接收器 / USB：XInput（xinput_battery.py），只有 4 档（空/低/中/满），
      按档位估算百分比。

3. Hi75C Pro 键盘（2.4G 接收器 3554:FA09）
   接收器的 vendor 自定义接口（UsagePage 0xFF04）暴露一条 Feature Report
   （Report ID 6，8 字节），其中第 4 字节（下标 3）为电量百分比。
   实测满电时返回：06 00 02 64 64 64 6B 4C。
   如果你的键盘读数不对，跑 probe.py 看原始字节，改下面的
   KEYBOARD_LEVEL_BYTE 即可。

用法：
    python devices.py --once    命令行读一次
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import Optional

import hid_win
import bt_battery
import xinput_battery as xi

# ---- 设备识别参数（换设备时改这里） ------------------------------------
HEADPHONE_NAME_KEYS = ("WH-1000XM4",)          # 蓝牙耳机名字关键词
CONTROLLER_NAME_KEYS = ("Xbox Wireless Controller", "Xbox 手柄")  # 蓝牙手柄
KEYBOARD_VID = 0x3554                          # Hi75C Pro 2.4G 接收器
KEYBOARD_USAGE_PAGE = 0xFF04                   # vendor 电量接口
KEYBOARD_REPORT_ID = 6                         # Feature Report 编号
KEYBOARD_LEVEL_BYTE = 3                        # 报文里电量所在的字节下标
# ------------------------------------------------------------------------


@dataclass
class Reading:
    """一次读取的结果，面板和托盘都消费这个结构。"""
    title: str
    subtitle: str = ""
    level: Optional[int] = None        # None=没读到（未连接/失败）
    status: str = ""                   # 一句话状态（如“有线供电”）
    detail: str = ""                   # 数据来源说明
    error: str = ""                    # 失败原因（level 为 None 时展示）

    @property
    def ok(self) -> bool:
        return self.level is not None

    def clipped(self) -> Optional[int]:
        return None if self.level is None else max(0, min(100, self.level))


# --------------------------------------------------------------------------
# 1. WH-1000XM4 耳机
# --------------------------------------------------------------------------
def read_headphone() -> Reading:
    r = Reading("索尼 WH-1000XM4", "蓝牙")
    found = bt_battery.find_by_name(*HEADPHONE_NAME_KEYS)
    if not found:
        # 节点都没了 = 耳机当前没连这台电脑
        r.error = "耳机未连接（或未配对）"
        return r
    # 优先 Hands-Free 节点（电量属性就挂在那里），否则取第一个
    hf = [d for d in found if "hands-free" in d.name.lower() or "免提" in d.name]
    dev = (hf or found)[0]
    if dev.connected is False:
        r.error = "耳机未连接（显示的是上次连接的缓存状态）"
        return r
    if dev.battery is None:
        r.error = "耳机已连接但未上报电量（重新连接可触发上报）"
        r.detail = dev.name
        return r
    r.level = dev.battery
    r.subtitle = f"蓝牙 · {dev.name}"
    r.detail = "HFP 电量上报（精确到 1%）"
    return r


# --------------------------------------------------------------------------
# 2. Xbox Series 手柄
# --------------------------------------------------------------------------
def _xbox_bt_mac() -> Optional[int]:
    """手柄蓝牙在线时，返回它的蓝牙地址（6 字节整数）；否则 None。

    判据：蓝牙连接的手柄会以 HID 游戏手柄接口（VID 045E、usage 0x0005）
    出现，且该接口**只在连接期间存在**——比蓝牙栈的 fConnected 标志可靠
    （BLE 设备的 fConnected 有时不刷新）。拿到接口的设备节点后沿父链
    上溯到 BTHLE\\DEV_<MAC>，就能精确对应到哪条 BLE 记录是在线的。
    """
    import re

    cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    cfg.CM_Get_Parent.restype = ctypes.c_long
    cfg.CM_Get_Parent.argtypes = [
        ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.c_ulong]
    cfg.CM_Get_Device_IDW.restype = ctypes.c_long
    cfg.CM_Get_Device_IDW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_ulong]

    for d in hid_win.enumerate_hid():
        if not (d.vid == 0x045E and d.usage_page == 0x0001
                and d.usage == 0x0005 and d.devinst):
            continue
        cur = d.devinst
        for _ in range(8):                      # HID接口 → HID服务 → BLE设备
            buf = ctypes.create_unicode_buffer(512)
            if cfg.CM_Get_Device_IDW(cur, buf, 512, 0) == 0:
                iid = buf.value.upper()
                if iid.startswith("BTHLE\\DEV_"):
                    m = re.search(r"DEV_([0-9A-F]{12})", iid)
                    return int(m.group(1), 16) if m else None
            parent = ctypes.c_uint(0)
            if cfg.CM_Get_Parent(ctypes.byref(parent), cur, 0) != 0:
                break
            cur = parent.value
    return None


def read_controller() -> Reading:
    r = Reading("Xbox Series 手柄", "")

    # 通道 A：蓝牙（BLE GATT 电量属性）—— 有精确百分比。
    # 注意：BLE 节点和电量属性在关机后仍留缓存，之前出现过手柄明明
    # 关机却显示 100% 的情况。所以这里用 HID 接口存在性判定在线，
    # 并按 MAC 精确匹配在线那台，其余记录（旧配对/缓存）一律不采信。
    bt_all = bt_battery.find_by_name(*CONTROLLER_NAME_KEYS)
    live_mac = _xbox_bt_mac()
    bt_level = None
    if live_mac is not None:
        for d in bt_all:
            if (bt_battery._mac_int_from_instance_id(d.instance_id) == live_mac
                    and d.battery is not None):
                bt_level = d.battery
                break
        else:   # MAC 对不上时退一步：只信声明在线的记录
            bt_level = max((d.battery for d in bt_all
                            if d.battery is not None and d.connected),
                           default=None)
        r.subtitle = "蓝牙"

    # 通道 B：XInput（USB / 无线接收器）—— 4 档。
    # （手柄走蓝牙时是 HID gamepad，不占 XInput，XInput 显示断开是正常的）
    try:
        gp = xi.read_first()
    except OSError:
        gp = None    # xinput1_4.dll 都加载不了（几乎不会发生）

    if gp is not None and gp.wired:
        r.level = None
        r.status = "有线供电"
        r.subtitle = "USB 连接"
        r.detail = "XInput：正在用 USB 线供电，不消耗电池"
        return r

    if bt_level is not None:
        r.level = bt_level
        r.subtitle = "蓝牙"
        r.detail = "BLE GATT 电量上报（精确到 1%）"
        if gp is not None and gp.estimate_percent is not None:
            r.detail += f"；XInput 同时在线（{gp.describe()}）"
        return r

    if gp is not None and gp.estimate_percent is not None:
        r.level = gp.estimate_percent
        r.subtitle = "接收器 / USB 适配器"
        r.detail = f"XInput {gp.describe()}（仅 4 档，百分比为估算）"
        return r

    r.error = "手柄未连接（蓝牙 / 接收器 / USB 都没找到）"
    return r


# --------------------------------------------------------------------------
# 3. Hi75C Pro 键盘
# --------------------------------------------------------------------------
def _find_keyboard_iface() -> Optional[hid_win.HidInterface]:
    for d in hid_win.enumerate_hid():
        if (d.vid == KEYBOARD_VID
                and d.usage_page == KEYBOARD_USAGE_PAGE
                and d.feature_len >= 8):
            return d
    return None


def read_keyboard() -> Reading:
    r = Reading("Hi75C Pro 键盘", "2.4G")
    iface = _find_keyboard_iface()
    if iface is None:
        # 2.4G 接收器不在，试试蓝牙电量属性（键盘蓝牙连电脑时）
        bt = bt_battery.find_by_name("hi75", "Hi75C")
        if bt and bt[0].battery is not None:
            r.level = bt[0].battery
            r.subtitle = "蓝牙"
            r.detail = "蓝牙电量属性"
            return r
        r.error = "没找到键盘 / 2.4G 接收器（确认接收器已插上）"
        return r

    r.subtitle = f"2.4G 接收器 {iface.vid_pid}"
    try:
        raw = hid_win.get_feature_report(iface.path, KEYBOARD_REPORT_ID, 8)
    except hid_win.HidError as e:
        r.error = f"读取失败：{e}"
        return r

    level = raw[KEYBOARD_LEVEL_BYTE] if len(raw) > KEYBOARD_LEVEL_BYTE else None
    if level is None or level > 100:
        r.error = f"返回数据异常：{raw.hex(' ')}"
        return r
    r.level = level
    r.detail = f"Feature Report #{KEYBOARD_REPORT_ID}：{raw.hex(' ')}"
    return r


# --------------------------------------------------------------------------
def read_all() -> list[Reading]:
    return [read_headphone(), read_controller(), read_keyboard()]


def cli_once() -> None:
    for r in read_all():
        if r.ok:
            print(f"{r.title}: {r.level}%   [{r.subtitle}] {r.detail}")
        else:
            msg = r.status or r.error
            print(f"{r.title}: {msg}")


if __name__ == "__main__":
    cli_once()
