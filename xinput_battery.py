# -*- coding: utf-8 -*-
"""xinput_battery.py —— 用 XInput 读取 Xbox 手柄电量（零第三方依赖）

直接 ctypes 加载系统自带的 xinput1_4.dll，调用 XInputGetBatteryInformation。

适用范围：USB 直连和 Xbox Wireless Adapter（专用接收器）。
手柄走**蓝牙**时不占用 XInput（系统把它枚举成 HID 游戏手柄），
那条路由 bt_battery.py 的 BLE GATT 电量属性负责，精确到 1%。

限制：XInput 只给 4 档电量（空/低/中/满），拿不到精确百分比。
蓝牙连接时如果系统已通过 GATT 拿到精确电量（见 bt_battery.py），
应优先用那条路径，这里作为 USB / 接收器模式的数据源。

小坑：蓝牙手柄只有先被 XInputGetState “激活”过一次，
XInputGetBatteryInformation 才会返回有效结果，所以每个索引先 GetState 一下。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Optional

BATTERY_DEVTYPE_GAMEPAD = 0x00

BTYPE_DISCONNECTED = 0
BTYPE_WIRED = 1
BTYPE_ALKALINE = 2
BTYPE_NIMH = 3
BTYPE_UNKNOWN = 255

_TYPE_NAMES = {
    BTYPE_DISCONNECTED: "断开",
    BTYPE_WIRED: "有线供电",
    BTYPE_ALKALINE: "电池（碱性）",
    BTYPE_NIMH: "电池（NiMH）",
    BTYPE_UNKNOWN: "未知",
}

# XInput 的 4 档 → 估算百分比（仅估算，展示时注明）
_LEVEL_ESTIMATE = {0: 5, 1: 35, 2: 70, 3: 100}
_LEVEL_NAMES = {0: "空", 1: "低", 2: "中", 3: "满"}


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BatteryType", ctypes.c_ubyte),
        ("BatteryLevel", ctypes.c_ubyte),
    ]


class _XInputStateDummy(ctypes.Structure):
    """XINPUT_STATE 的占位结构，只用来触发设备激活，内容不重要。"""
    _fields_ = [("dwPacketNumber", ctypes.c_uint32),
                ("Gamepad", ctypes.c_ubyte * 16)]


_xinput = None


def _dll():
    global _xinput
    if _xinput is None:
        _xinput = ctypes.WinDLL("xinput1_4")   # Win8+ 自带；Win7 用 xinput9_1_0
        _xinput.XInputGetBatteryInformation.restype = ctypes.c_uint32
        _xinput.XInputGetBatteryInformation.argtypes = [
            ctypes.c_uint32, ctypes.c_ubyte,
            ctypes.POINTER(XINPUT_BATTERY_INFORMATION),
        ]
        _xinput.XInputGetState.restype = ctypes.c_uint32
        _xinput.XInputGetState.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(_XInputStateDummy),
        ]
    return _xinput


@dataclass
class GamepadBattery:
    user_index: int
    battery_type: int
    battery_level: int                # 0-3，仅电池型有效

    @property
    def connected(self) -> bool:
        return self.battery_type not in (BTYPE_DISCONNECTED, BTYPE_UNKNOWN)

    @property
    def wired(self) -> bool:
        return self.battery_type == BTYPE_WIRED

    @property
    def estimate_percent(self) -> Optional[int]:
        """电池型供电时的估算百分比；有线/断开返回 None。"""
        if not self.connected or self.wired:
            return None
        return _LEVEL_ESTIMATE.get(self.battery_level)

    def describe(self) -> str:
        if not self.connected:
            return "未连接"
        if self.wired:
            return "有线供电（不耗电池）"
        return (f"{_TYPE_NAMES.get(self.battery_type, '?')} · "
                f"档位 {_LEVEL_NAMES.get(self.battery_level, '?')}")


def read_first() -> Optional[GamepadBattery]:
    """扫描 4 个用户索引，返回第一台在线的手柄。"""
    dll = _dll()
    state = _XInputStateDummy()
    for idx in range(4):
        dll.XInputGetState(idx, ctypes.byref(state))   # 先激活（蓝牙手柄必需）
        info = XINPUT_BATTERY_INFORMATION()
        dll.XInputGetBatteryInformation(
            idx, BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(info))
        gb = GamepadBattery(idx, info.BatteryType, info.BatteryLevel)
        if gb.connected:
            return gb
    return None


if __name__ == "__main__":
    gp = read_first()
    if gp is None:
        print("没有在线的 XInput 手柄")
    else:
        print(f"用户索引 {gp.user_index}：{gp.describe()} 估算≈{gp.estimate_percent}%")
