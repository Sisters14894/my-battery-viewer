# -*- coding: utf-8 -*-
"""hid_win.py —— 纯 ctypes 的 Windows HID 访问层（零第三方依赖）

只调用系统自带的 setupapi.dll / hid.dll / kernel32.dll，
不需要安装 hidapi、pywinusb 等任何第三方库。

本项目只用到三件事：
  1. 枚举所有 HID 接口（VID / PID / UsagePage / Usage / 报告长度 / 产品名）
  2. 用 0 访问权限打开接口（鼠标/键盘这类系统独占集合也能打开）
  3. 读 Feature Report（Hi75C Pro 键盘的电量就在这里）

注意：Windows 会独占打开“鼠标 / 键盘”等顶层集合，普通读写权限会得到
“拒绝访问”；用 dwDesiredAccess = 0 打开依然可以读属性和 Feature Report。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

if sys.platform != "win32":
    raise RuntimeError("hid_win.py 仅支持 Windows")

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", ctypes.c_wchar * 1),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidD_GetFeature.restype = wintypes.BOOL
hid.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
for _fn in ("HidD_GetProductString", "HidD_GetManufacturerString"):
    _f = getattr(hid, _fn)
    _f.restype = wintypes.BOOL
    _f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]

# HID 设备接口类 GUID {4D1E55B2-F16F-11CF-88CB-001111000030}
HID_GUID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


class HidpCaps(ctypes.Structure):
    """完整的 HIDP_CAPS（不能截短：HidP_GetCaps 会写满整个结构体）。"""
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HidpCaps)]


def _wide_string(func, handle, size: int = 512) -> str:
    buf = ctypes.create_string_buffer(size)
    try:
        if not func(handle, buf, size):
            return ""
    except Exception:
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0].strip()


@dataclass
class HidInterface:
    """一个 HID 接口（= 一个顶层集合）。"""
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product: str = ""
    manufacturer: str = ""
    devinst: int = 0        # 配置管理器设备节点（用于向上追溯父设备）

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"


def enumerate_hid() -> list[HidInterface]:
    """枚举当前存在的所有 HID 接口。"""
    result: list[HidInterface] = []
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(HID_GUID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        raise OSError("SetupDiGetClassDevsW 失败 err=%d" % ctypes.get_last_error())
    try:
        index = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(HID_GUID), index, ctypes.byref(iface)):
                break
            index += 1

            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8  # cbSize（x64）
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(iface), buf, need.value, None,
                    ctypes.byref(devinfo)):
                continue
            path = ctypes.cast(
                ctypes.addressof(buf) + 4, ctypes.c_wchar_p).value or ""
            if not path:
                continue
            item = _probe_interface(path)
            if item is not None:
                item.devinst = devinfo.DevInst
                result.append(item)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return result


def _probe_interface(path: str) -> Optional[HidInterface]:
    """打开一个 HID 接口读取 VID/PID、usage 与产品名。"""
    lower = path.lower()
    vid = pid = 0
    if "vid_" in lower:
        try:
            vid = int(lower.split("vid_")[1][:4], 16)
        except ValueError:
            pass
    if "pid_" in lower:
        try:
            pid = int(lower.split("pid_")[1][:4], 16)
        except ValueError:
            pass
    item = HidInterface(
        path=path, vid=vid, pid=pid, usage_page=0, usage=0,
        input_len=0, output_len=0, feature_len=0,
        product="", manufacturer="",
    )

    handle = kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return item  # 打不开也保留，至少有路径和 vid/pid

    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            item.vid = attrs.VendorID
            item.pid = attrs.ProductID
        item.product = _wide_string(hid.HidD_GetProductString, handle)
        item.manufacturer = _wide_string(hid.HidD_GetManufacturerString, handle)

        preparsed = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) and preparsed:
            try:
                caps = HidpCaps()
                if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) == 0x00110000:
                    item.usage_page = caps.UsagePage
                    item.usage = caps.Usage
                    item.input_len = caps.InputReportByteLength
                    item.output_len = caps.OutputReportByteLength
                    item.feature_len = caps.FeatureReportByteLength
            finally:
                hid.HidD_FreePreparsedData(preparsed)
    finally:
        kernel32.CloseHandle(handle)
    return item


class HidError(OSError):
    pass


def get_feature_report(path: str, report_id: int, length: int) -> bytes:
    """打开接口读一条 Feature Report，返回的 bytes[0] 是 report id。

    用完即关：本项目只在刷新时读一次，不值得长期持有句柄。
    """
    handle = kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if not handle or handle == INVALID_HANDLE_VALUE:
        raise HidError(f"打开 HID 接口失败 err={ctypes.get_last_error()}")
    try:
        buf = ctypes.create_string_buffer(length)
        buf[0] = bytes([report_id & 0xFF])
        if not hid.HidD_GetFeature(handle, buf, length):
            raise HidError(f"读 Feature Report 0x{report_id:02X} 失败 "
                           f"err={ctypes.get_last_error()}")
        return buf.raw
    finally:
        kernel32.CloseHandle(handle)


__all__ = [
    "HidInterface", "HidError", "enumerate_hid", "get_feature_report",
    "GUID", "setupapi", "kernel32",
]
