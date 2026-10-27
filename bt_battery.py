# -*- coding: utf-8 -*-
r"""bt_battery.py —— 读取蓝牙设备的电量（Windows 未公开 PnP 属性）

原理
----
Windows 的蓝牙栈从两条途径拿到电量，都会写进对应设备节点上的一个
未公开 PnP 属性：

    PROPERTYKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2  (DEVPROP_TYPE_BYTE)
    值 = 电量百分比（0-100）

* 经典蓝牙耳机（HFP 免提）：耳机通过 AT+IPHONEACCEV 上报，
  属性挂在 **Hands-Free AG 节点**（BTHENUM\{...111E...}）上。
  Sony WH-1000XM4 走这条路，实测可直接读到。
* BLE 设备（GATT Battery Service 0x180F）：如蓝牙连接的 Xbox 手柄，
  属性挂在 BTHLE\DEV_XXXX 节点上。

注意：属性值 0 或属性不存在 = 未连接 / 设备没上报，不能当成“0% 电量”。
"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

from hid_win import GUID, setupapi

FMTID_BLUETOOTH = GUID(
    0x104EA319, 0x6EE2, 0x4701,
    (ctypes.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5),
)
PID_BATTERY_LEVEL = 2

DIGCF_ALLCLASSES = 0x04
DIGCF_PRESENT = 0x02
SPDRP_DEVICEDESC = 0x00
SPDRP_FRIENDLYNAME = 0x0C
ERROR_NOT_FOUND = 1168


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(PROPERTYKEY),
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]


@dataclass
class BtDevice:
    name: str
    instance_id: str
    battery: Optional[int] = None      # None=没有属性；0~100=电量
    is_le: bool = False                # True=BTHLE(BLE)，False=BTHENUM(经典)
    connected: Optional[bool] = None   # True/False=蓝牙在线状态；None=查不到


# --------------------------------------------------------------------------
# 蓝牙在线状态（bluetoothapis：paired 设备的 fConnected 标志）
# --------------------------------------------------------------------------
# 关键坑：BLE 设备节点的电量属性是**缓存值**——设备关机后节点和电量都还在，
# 直接读会把「关机前的电量」当成在线电量。必须用 fConnected 交叉验证。
bthprops = ctypes.WinDLL("bthprops.cpl", use_last_error=True)


class _BT_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("fReturnAuthenticated", wintypes.BOOL),
        ("fReturnRemembered", wintypes.BOOL),
        ("fReturnUnknown", wintypes.BOOL),
        ("fReturnConnected", wintypes.BOOL),
        ("fIssueInquiry", wintypes.BOOL),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", ctypes.c_void_p),
    ]


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wintypes.WORD) for n in
                ("wYear", "wMonth", "wDayOfWeek", "wDay",
                 "wHour", "wMinute", "wSecond", "wMilliseconds")]


class _BT_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Address", ctypes.c_ulonglong),      # BLUETOOTH_ADDRESS（6字节MAC）
        ("ulClassofDevice", wintypes.ULONG),
        ("fConnected", wintypes.BOOL),
        ("fRemembered", wintypes.BOOL),
        ("fAuthenticated", wintypes.BOOL),
        ("stLastSeen", _SYSTEMTIME),
        ("stLastUsed", _SYSTEMTIME),
        ("szName", ctypes.c_wchar * 248),
    ]


bthprops.BluetoothFindFirstDevice.restype = ctypes.c_void_p
bthprops.BluetoothFindFirstDevice.argtypes = [
    ctypes.POINTER(_BT_SEARCH_PARAMS), ctypes.POINTER(_BT_DEVICE_INFO)]
bthprops.BluetoothFindNextDevice.restype = wintypes.BOOL
bthprops.BluetoothFindNextDevice.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_BT_DEVICE_INFO)]
bthprops.BluetoothFindDeviceClose.restype = wintypes.BOOL
bthprops.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]


def connected_address_map() -> dict[int, str]:
    """{蓝牙地址(int): 设备名}，只含**当前在线**的已配对设备。

    蓝牙栈不可用 / 没有蓝牙radio时返回空表（调用方应退回不校验的行为）。
    """
    sp = _BT_SEARCH_PARAMS()
    sp.dwSize = ctypes.sizeof(_BT_SEARCH_PARAMS)
    sp.fReturnAuthenticated = True
    sp.fReturnRemembered = True
    sp.fReturnUnknown = True
    sp.fReturnConnected = True
    sp.fIssueInquiry = False
    sp.cTimeoutMultiplier = 1
    sp.hRadio = None
    info = _BT_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(_BT_DEVICE_INFO)
    out: dict[int, str] = {}
    handle = bthprops.BluetoothFindFirstDevice(ctypes.byref(sp), ctypes.byref(info))
    if not handle:
        return out     # 没有radio或没有设备
    while True:
        if info.fConnected:
            out[int(info.Address)] = info.szName
        if not bthprops.BluetoothFindNextDevice(handle, ctypes.byref(info)):
            break
    bthprops.BluetoothFindDeviceClose(handle)
    return out


def _mac_int_from_instance_id(iid: str) -> Optional[int]:
    """从设备实例 ID 里抠出 6 字节 MAC（蓝牙地址，大端整数形式）。

    BTHLE:  BTHLE\\DEV_F46AD7587FC7\\...
    BTHENUM: BTHENUM\\{...}_VID&...\\7&xx&0&AC800AA07F52_C00000000
    """
    m = re.search(r"DEV_([0-9A-Fa-f]{12})", iid)
    if not m:
        m = re.search(r"&([0-9A-Fa-f]{12})_C0", iid)
    if not m:
        return None
    return int(m.group(1), 16)


def _device_string(hdev, info, prop: int) -> str:
    buf = ctypes.create_string_buffer(1024)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(info), prop, None, buf, 1024, ctypes.byref(need)):
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0]


def _instance_id(hdev, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceInstanceIdW(
            hdev, ctypes.byref(info), buf, 512, ctypes.byref(need)):
        return ""
    return buf.value


def _read_battery(hdev, info) -> Optional[int]:
    """读电量属性；没有属性 / 值为 0 都返回 None（视为“未连接或未上报”）。"""
    key = PROPERTYKEY(FMTID_BLUETOOTH, PID_BATTERY_LEVEL)
    ptype = wintypes.ULONG(0)
    value = ctypes.c_ubyte(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 1, ctypes.byref(need), 0,
    )
    if not ok:
        return None
    level = int(value.value)
    if level <= 0:
        return None
    return min(100, level)


def _enumerate_prefix(prefix: str) -> list[BtDevice]:
    """枚举指定前缀（BTHENUM / BTHLE）的已连接蓝牙设备。"""
    out: list[BtDevice] = []
    seen: set[str] = set()
    hdev = setupapi.SetupDiGetClassDevsW(
        None, prefix, None, DIGCF_ALLCLASSES | DIGCF_PRESENT)
    if not hdev or hdev == ctypes.c_void_p(-1).value:
        return out
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, index, ctypes.byref(info)):
                break
            index += 1
            iid = _instance_id(hdev, info)
            if not iid or iid in seen:
                continue
            seen.add(iid)
            name = _device_string(hdev, info, SPDRP_FRIENDLYNAME)
            if not name:
                name = _device_string(hdev, info, SPDRP_DEVICEDESC)
            out.append(BtDevice(
                name=name or "(无名称)",
                instance_id=iid,
                battery=_read_battery(hdev, info),
                is_le=(prefix.upper() == "BTHLE"),
            ))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def list_bt_devices() -> list[BtDevice]:
    """列出所有能读到电量属性的蓝牙设备（BTHENUM + BTHLE 合并）。

    每台设备带 connected 标志（来自蓝牙栈的 fConnected）；
    蓝牙栈不可用时 connected=None，调用方按旧行为处理。
    """
    live = connected_address_map()
    out = []
    for d in _enumerate_prefix("BTHENUM"):
        if d.battery is not None:
            mac = _mac_int_from_instance_id(d.instance_id)
            d.connected = live.get(mac) is not None if mac is not None else None
            out.append(d)
    for d in _enumerate_prefix("BTHLE"):
        if d.battery is not None:
            mac = _mac_int_from_instance_id(d.instance_id)
            d.connected = live.get(mac) is not None if mac is not None else None
            out.append(d)
    return out


def find_by_name(*keywords: str) -> list[BtDevice]:
    """按名字关键词过滤（大小写不敏感，任一命中即算）。"""
    kws = [k.lower() for k in keywords]
    return [d for d in list_bt_devices()
            if any(k in d.name.lower() for k in kws)]


def dump() -> None:
    """命令行调试：打印所有带电量属性的蓝牙设备。"""
    for d in list_bt_devices():
        tag = "BLE " if d.is_le else "经典"
        state = {True: "在线", False: "离线(缓存)", None: "未知"}[d.connected]
        print(f" 🔋 [{tag}] {d.name}  电量={d.battery}%  [{state}]")
        print(f"      {d.instance_id}")


if __name__ == "__main__":
    dump()
