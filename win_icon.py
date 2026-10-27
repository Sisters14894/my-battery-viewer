# -*- coding: utf-8 -*-
"""win_icon.py —— 用 GDI 生成“电量数字”托盘图标（纯 ctypes，无 Pillow）

做法：CreateDIBSection 开一块 32bpp ARGB 位图，先铺底板（可选），
再用 DrawTextW 把数字画上去，最后扫描像素补 alpha 通道
（GDI 写 DIB 时不动 alpha，必须手动处理），CreateIconIndirect 变成 HICON。

三种底板形状用于区分设备（同一套电量颜色下）：
    PLATE_NONE   无底板（键盘）
    PLATE_ROUND  圆形底板（耳机）
    PLATE_SQUARE 圆角方形底板（手柄）
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

ICON_SIZE = 16

TRANSPARENT_BK = 1
TA_CENTER, TA_LEFT = 6, 0
DT_CENTER, DT_VCENTER, DT_SINGLELINE = 0x25, 0x4, 0x20
CLR_INVALID = 0xFFFFFFFF

PLATE_NONE, PLATE_ROUND, PLATE_SQUARE = 0, 1, 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.CreateFontW.restype = ctypes.c_void_p
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPCWSTR,
]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.SetTextColor.restype = ctypes.c_uint32
gdi32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
gdi32.Rectangle.restype = wintypes.BOOL
gdi32.Rectangle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.restype = wintypes.BOOL
gdi32.Ellipse.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int]
gdi32.RoundRect.restype = wintypes.BOOL
gdi32.RoundRect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateSolidBrush.restype = ctypes.c_void_p
gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint32]
gdi32.FillRgn.restype = wintypes.BOOL
gdi32.FillRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.c_void_p, wintypes.UINT]
user32.CreateIconIndirect.restype = ctypes.c_void_p
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [ctypes.c_void_p]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


# 字体名：Arial 在 16px 下数字清晰紧凑
def _make_font(height: int):
    FW_BOLD = 700
    return gdi32.CreateFontW(
        height, 0, 0, 0, FW_BOLD, 0, 0, 0,
        1,  # DEFAULT_CHARSET
        5,  # OUT_DEFAULT_PRECIS
        2,  # CLIP_DEFAULT_PRECIS
        5,  # CLEARTYPE_QUALITY
        0x20,  # FF_SWISS
        "Arial",
    )


def make_hicon(text: str, color_rgb: tuple[int, int, int], plate: int) -> int:
    """画一个数字图标。color_rgb=(R,G,B)；plate 见 PLATE_* 常量。"""
    bmi_hdr = BITMAPINFOHEADER(
        biSize=ctypes.sizeof(BITMAPINFOHEADER),
        biWidth=ICON_SIZE, biHeight=-ICON_SIZE,   # 负高 = top-down
        biPlanes=1, biBitCount=32, biCompression=0,  # BI_RGB
    )
    pixels = ctypes.c_void_p()
    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        return 0
    try:
        hbm = gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi_hdr), 0, ctypes.byref(pixels), None, 0)
        if not hbm or not pixels.value:
            return 0
        old = gdi32.SelectObject(hdc, hbm)

        # 1) 全透明清零
        ctypes.memset(pixels.value, 0, ICON_SIZE * ICON_SIZE * 4)

        r, g, b = color_rgb
        # 2) 底板（带底板时数字用深色，无底板用电量色）
        if plate == PLATE_ROUND:
            _fill_shape(hdc, gdi32.Ellipse, (r, g, b))
            text_rgb = (16, 18, 22)
        elif plate == PLATE_SQUARE:
            _fill_shape(hdc, lambda dc, *a: gdi32.RoundRect(dc, *a, 4, 4), (r, g, b))
            text_rgb = (16, 18, 22)
        else:
            text_rgb = (r, g, b)

        # 3) 数字
        font = _make_font(11 if len(text) <= 2 else 9)
        old_font = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT_BK)
        gdi32.SetTextColor(hdc, text_rgb[0] | (text_rgb[1] << 8) | (text_rgb[2] << 16))
        rc = RECT(0, 0, ICON_SIZE, ICON_SIZE)
        user32.DrawTextW(hdc, text, -1, ctypes.byref(rc),
                         DT_CENTER | DT_VCENTER | DT_SINGLELINE)
        gdi32.SelectObject(hdc, old_font)
        gdi32.DeleteObject(font)
        gdi32.SelectObject(hdc, old)

        # 4) 补 alpha：GDI 不写 alpha——非黑像素置 255，
        #    底板/抗锯齿边缘按亮度取比例
        if plate == PLATE_NONE:
            _apply_alpha_no_plate(pixels.value, text_rgb)
        else:
            _apply_alpha_plate(pixels.value)

        # 5) DIB -> HICON（单色 mask 全 0 = 完全由 color 位图决定）
        mask_hdr = BITMAPINFOHEADER(
            biSize=ctypes.sizeof(BITMAPINFOHEADER),
            biWidth=ICON_SIZE, biHeight=ICON_SIZE,
            biPlanes=1, biBitCount=1, biCompression=0,
        )
        mask_pixels = ctypes.c_void_p()
        mask_dc = gdi32.CreateCompatibleDC(None)
        try:
            hmask = gdi32.CreateDIBSection(
                mask_dc, ctypes.byref(mask_hdr), 0, ctypes.byref(mask_pixels),
                None, 0)
            if hmask and mask_pixels.value:
                ctypes.memset(mask_pixels.value, 0, ICON_SIZE * 2 * ((ICON_SIZE + 15) // 16) * 4 // 4)
        finally:
            gdi32.DeleteDC(mask_dc)

        info = ICONINFO(fIcon=True, hbmMask=hmask, hbmColor=hbm)
        hicon = user32.CreateIconIndirect(ctypes.byref(info))
        if hmask:
            gdi32.DeleteObject(hmask)
        gdi32.DeleteObject(hbm)
        return hicon or 0
    finally:
        gdi32.DeleteDC(hdc)


def _fill_shape(hdc, shape_fn, rgb):
    """用指定形状函数铺一层底板色。"""
    brush = gdi32.CreateSolidBrush(rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
    old = gdi32.SelectObject(hdc, brush)
    shape_fn(hdc, 0, 0, ICON_SIZE - 1, ICON_SIZE - 1)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(brush)


def _apply_alpha_no_plate(pixels_ptr, text_rgb):
    """无底板：只要 RGB 通道不全为 0 就当文字像素，alpha 按通道最大值。"""
    arr = (ctypes.c_ubyte * (ICON_SIZE * ICON_SIZE * 4)).from_address(pixels_ptr)
    for i in range(ICON_SIZE * ICON_SIZE):
        base = i * 4
        m = max(arr[base], arr[base + 1], arr[base + 2])
        arr[base + 3] = 255 if m >= 40 else m * 6   # 淡抗锯齿边缘也保留一点


def _apply_alpha_plate(pixels_ptr):
    """有底板：底板区域内 alpha=255。"""
    arr = (ctypes.c_ubyte * (ICON_SIZE * ICON_SIZE * 4)).from_address(pixels_ptr)
    for i in range(ICON_SIZE * ICON_SIZE):
        base = i * 4
        m = max(arr[base], arr[base + 1], arr[base + 2])
        arr[base + 3] = 255 if m > 0 else 0


def destroy_hicon(hicon: int) -> None:
    if hicon:
        user32.DestroyIcon(hicon)


def battery_color(level) -> tuple[int, int, int]:
    """电量 → 颜色（绿 ≥60 / 黄 ≥30 / 红 <30 / 灰 未知）。"""
    if level is None:
        return (150, 155, 160)
    if level >= 60:
        return (93, 220, 138)
    if level >= 30:
        return (245, 196, 81)
    return (255, 107, 107)


if __name__ == "__main__":
    # 自检：生成几个图标并确认句柄有效
    for text, plate in (("80", PLATE_ROUND), ("45", PLATE_SQUARE), ("100", PLATE_NONE)):
        h = make_hicon(text, battery_color(int(text) if text.isdigit() else None), plate)
        print(f"{text}: hicon={'OK' if h else 'FAIL'}")
        destroy_hicon(h)
