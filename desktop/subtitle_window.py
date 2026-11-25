"""
ScreenLex 双语字幕悬浮舱（方案 A：DWM 透视原位遮盖）与独立双语滚动字幕台（方案 B）。
支持词级 0ms 点按即查、自动跟随原生字幕坐标、历史字幕滚动回看、链接字幕载入及自动暂停视频。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import shiboken6
try:
    import numpy as _np
except Exception:
    _np = None
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QTouchEvent,
)
from PySide6.QtWidgets import (
    QAbstractSlider,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .database import get_setting, set_setting
from .subtitle_engine import BilibiliAuthManager
from .ui_icons import (
    EditorialPullQuoteFrame,
    LEXICON_THEME,
    VectorIconButton,
    draw_hud_bar,
    editorial_scrollbar_qss,
    enhance_scroll_area,
    supports_win11_native_chrome,
    enforce_window_topmost,
    restore_topmost_if_needed,
)


MODE_INPLACE_HUD = "inplace"
MODE_CONSOLE_DRAWER = "console"

HUD_SETTINGS_KEY = "hud_appearance"
HUD_WIDTH_PRESETS = {
    "narrow": 540,
    "medium": 780,
    "wide": 1040,
}
HUD_WEIGHT_CHOICES = ("light", "normal", "bold")
DEFAULT_HUD_SETTINGS: Dict[str, Any] = {
    "font_size_pt": 13.5,
    "font_weight": "normal",
    "bg_opacity_pct": 92,
    "display_lang": "bilingual",
    "width_preset": "medium",
    "hud_theme": "dark",
    "auto_follow": True,
    "user_pos": None,
}

_WDA_NONE = 0x00000000
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _make_ui_font(
    size_pt: float = 10.0,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    display: bool = False,
    mono: bool = False,
    serif: bool = False,
    italic: bool = False,
) -> QFont:
    """构建跨语种混排字体栈：英文衬线优先 Georgia + Noto Serif SC，功能文本优先 Segoe UI Variable Text + Microsoft YaHei UI。"""
    f = QFont()
    if mono:
        f.setFamilies(["Cascadia Mono", "Consolas", "Microsoft YaHei UI"])
    elif serif:
        f.setFamilies(["Georgia", "Noto Serif SC", "STSong", "Microsoft YaHei UI"])
    elif display:
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI Variable Text", "Microsoft YaHei UI"])
    else:
        f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    f.setPointSizeF(float(size_pt))
    f.setWeight(weight)
    if italic:
        f.setItalic(True)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f

# 独立 Win32 API 句柄与显式 64 位类型声明（严禁修改共享 windll.user32 全局缓存）
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindow.restype = wintypes.BOOL
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.IsIconic.restype = wintypes.BOOL
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.SetForegroundWindow.argtypes = [wintypes.HWND]
_u32.SetForegroundWindow.restype = wintypes.BOOL
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_u32.AttachThreadInput.restype = wintypes.BOOL
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetClassNameW.restype = ctypes.c_int
_u32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
_u32.GetDC.argtypes = [wintypes.HWND]
_u32.GetDC.restype = wintypes.HDC
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.ReleaseDC.restype = ctypes.c_int
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetWindowRect.restype = wintypes.BOOL
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
_u32.SetWindowPos.restype = wintypes.BOOL

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
_gdi32.BitBlt.restype = wintypes.BOOL


class _BITMAPINFOHEADER(ctypes.Structure):
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


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP


def sample_screen_region_rgb(x: int, y: int, w: int, h: int) -> Optional[Any]:
    """
    高速采样屏幕区域 RGB 像素样本（Nx3, 0..255）。
    配合 WDA_EXCLUDEFROMCAPTURE 窗体可 0ms 透视采样字幕背后的真实背景色（不采样字幕本身）。
    """
    if w <= 4 or h <= 2:
        return None
    hdc_scr = None
    hdc_mem = None
    hbmp = None
    old_obj = None
    try:
        hdc_scr = _u32.GetDC(None)
        if not hdc_scr:
            return None
        hdc_mem = _gdi32.CreateCompatibleDC(hdc_scr)
        if not hdc_mem:
            return None
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = int(w)
        bmi.bmiHeader.biHeight = -int(h)
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        bits_ptr = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0)
        if not hbmp or not bits_ptr.value:
            return None
        old_obj = _gdi32.SelectObject(hdc_mem, hbmp)
        if not _gdi32.BitBlt(hdc_mem, 0, 0, int(w), int(h), hdc_scr, int(x), int(y), 0x00CC0020):
            return None
        step_x = max(1, int(w) // 24)
        step_y = max(1, int(h) // 8)
        if _np is not None:
            arr = _np.frombuffer(
                (ctypes.c_ubyte * (int(w) * int(h) * 4)).from_address(bits_ptr.value),
                dtype=_np.uint8,
            ).reshape(int(h), int(w), 4)
            sub = arr[::step_y, ::step_x, :3][:, :, ::-1]
            if sub.size == 0:
                return None
            return _np.ascontiguousarray(sub.reshape(-1, 3), dtype=_np.float32)
        buf = (ctypes.c_ubyte * (int(w) * int(h) * 4)).from_address(bits_ptr.value)
        samples: List[Tuple[int, int, int]] = []
        for iy in range(0, int(h), step_y):
            row_off = iy * int(w) * 4
            for ix in range(0, int(w), step_x):
                off = row_off + ix * 4
                samples.append((int(buf[off + 2]), int(buf[off + 1]), int(buf[off])))
        return samples or None
    except Exception:
        return None
    finally:
        if old_obj and hdc_mem:
            _gdi32.SelectObject(hdc_mem, old_obj)
        if hbmp:
            _gdi32.DeleteObject(hbmp)
        if hdc_mem:
            _gdi32.DeleteDC(hdc_mem)
        if hdc_scr:
            _u32.ReleaseDC(None, hdc_scr)


def sample_screen_region_luma(x: int, y: int, w: int, h: int) -> Optional[float]:
    """高速采样屏幕区域中位感知亮度 (0..255)。"""
    samples = sample_screen_region_rgb(x, y, w, h)
    if samples is None:
        return None
    if _np is not None and hasattr(samples, "shape"):
        if samples.size == 0:
            return None
        luma = 0.299 * samples[:, 0] + 0.587 * samples[:, 1] + 0.114 * samples[:, 2]
        return float(_np.median(luma))
    lumas = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in samples)
    return float(lumas[len(lumas) // 2]) if lumas else None


def _quantile_sorted(sorted_vals: List[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = max(0.0, min(1.0, q)) * (n - 1)
    lo = int(pos)
    hi = min(n - 1, lo + 1)
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def analyze_backdrop_contrast(
    rgb_samples: Any,
    hud_rgb: Tuple[float, float, float],
    alpha: float,
    prev_fg_is_light: Optional[bool] = None,
    prev_halo_strength: float = 0.0,
) -> Tuple[bool, float]:
    """
    基于透明合成后的有效背景 RGB 样本，计算 Rec.709 感知亮度分位数、WCAG 相对亮度及色度，
    返回 (fg_is_light, halo_strength)：
    - 纯黑/纯白高对比背景保持 halo_strength=0.0，延续干净 Editorial 字体质感；
    - 深蓝衣物、彩色背景或“亮墙+深衣”混合画面自动切为高对比亮字并按最劣局部对比度平滑注入可读性描边。
    """
    a = max(0.0, min(1.0, float(alpha)))
    inv_a = 1.0 - a
    hr, hg, hb = float(hud_rgb[0]), float(hud_rgb[1]), float(hud_rgb[2])

    if _np is not None and hasattr(rgb_samples, "shape"):
        arr = _np.asarray(rgb_samples, dtype=_np.float32).reshape(-1, 3)
        if arr.shape[0] == 0:
            return (True if prev_fg_is_light is None else bool(prev_fg_is_light), float(prev_halo_strength))
        eff = _np.clip(
            arr * inv_a + _np.array([hr * a, hg * a, hb * a], dtype=_np.float32),
            0.0,
            255.0,
        )
        luma = 0.2126 * eff[:, 0] + 0.7152 * eff[:, 1] + 0.0722 * eff[:, 2]
        chroma = eff.max(axis=1) - eff.min(axis=1)
        c_srgb = eff / 255.0
        c_lin = _np.where(c_srgb <= 0.04045, c_srgb / 12.92, ((c_srgb + 0.055) / 1.055) ** 2.4)
        rel_y = 0.2126 * c_lin[:, 0] + 0.7152 * c_lin[:, 1] + 0.0722 * c_lin[:, 2]
        p15, p20, p50, p85 = [float(v) for v in _np.percentile(luma, [15, 20, 50, 85])]
        y15, y85 = [float(v) for v in _np.percentile(rel_y, [15, 85])]
        c50 = float(_np.median(chroma))
        deep_dark_ratio = float(_np.mean(luma < 100.0))
    else:
        pts = list(rgb_samples or [])
        if not pts:
            return (True if prev_fg_is_light is None else bool(prev_fg_is_light), float(prev_halo_strength))
        lumas: List[float] = []
        rel_ys: List[float] = []
        chromas: List[float] = []
        deep_cnt = 0
        for r, g, b in pts:
            er = max(0.0, min(255.0, hr * a + float(r) * inv_a))
            eg = max(0.0, min(255.0, hg * a + float(g) * inv_a))
            eb = max(0.0, min(255.0, hb * a + float(b) * inv_a))
            lv = 0.2126 * er + 0.7152 * eg + 0.0722 * eb
            lumas.append(lv)
            if lv < 100.0:
                deep_cnt += 1
            chromas.append(max(er, eg, eb) - min(er, eg, eb))
            rl, gl, bl = (
                (c / 255.0 / 12.92 if (c / 255.0) <= 0.04045 else ((c / 255.0 + 0.055) / 1.055) ** 2.4)
                for c in (er, eg, eb)
            )
            rel_ys.append(0.2126 * rl + 0.7152 * gl + 0.0722 * bl)
        lumas.sort()
        rel_ys.sort()
        chromas.sort()
        p15 = _quantile_sorted(lumas, 0.15)
        p20 = _quantile_sorted(lumas, 0.20)
        p50 = _quantile_sorted(lumas, 0.50)
        p85 = _quantile_sorted(lumas, 0.85)
        y15 = _quantile_sorted(rel_ys, 0.15)
        y85 = _quantile_sorted(rel_ys, 0.85)
        c50 = _quantile_sorted(chromas, 0.50)
        deep_dark_ratio = deep_cnt / len(pts)

    if prev_fg_is_light is None:
        want_dark = (p50 >= 156.0) and (p20 >= 122.0) and (deep_dark_ratio < 0.18) and not (c50 >= 55.0 and p50 < 176.0)
        fg_is_light = not want_dark
    elif prev_fg_is_light:
        can_go_dark = (p50 >= 166.0) and (p20 >= 134.0) and (deep_dark_ratio < 0.14) and not (c50 >= 50.0 and p50 < 184.0)
        fg_is_light = not can_go_dark
    else:
        fg_is_light = (p50 <= 146.0) or (p20 <= 112.0) or (deep_dark_ratio >= 0.22) or (c50 >= 62.0 and p50 <= 166.0)

    if fg_is_light:
        cr_worst = (0.955 + 0.05) / (y85 + 0.05)
        raw_halo = 0.0 if cr_worst >= 5.5 else min(1.0, (5.5 - cr_worst) / (5.5 - 1.4)) * 0.88
    else:
        cr_worst = (y15 + 0.05) / (0.015 + 0.05)
        raw_halo = 0.0 if cr_worst >= 5.5 else min(1.0, (5.5 - cr_worst) / (5.5 - 1.6)) * 0.72

    if raw_halo < 0.08:
        raw_halo = 0.0
    else:
        raw_halo = round(raw_halo * 20.0) / 20.0

    prev_s = max(0.0, min(1.0, float(prev_halo_strength)))
    if prev_fg_is_light is not None and fg_is_light == prev_fg_is_light:
        if abs(raw_halo - prev_s) < 0.08 and (raw_halo == 0.0) == (prev_s == 0.0):
            raw_halo = prev_s

    return bool(fg_is_light), float(raw_halo)


class _DwmMargins(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
_dwmapi.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
_dwmapi.DwmIsCompositionEnabled.restype = ctypes.c_long
_dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_DwmMargins)]
_dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
_dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


def supports_dwm_per_pixel_alpha() -> bool:
    """检测当前 Windows 是否开启 DWM 合成，用于启用非分层每像素透明绘制（规避 Win11 WS_EX_LAYERED+防截屏冻结）。"""
    if sys.platform != "win32":
        return False
    try:
        enabled = wintypes.BOOL(0)
        hr = _dwmapi.DwmIsCompositionEnabled(ctypes.byref(enabled))
        return hr == 0 and bool(enabled.value)
    except Exception:
        return False


def apply_dwm_per_pixel_alpha(widget: QWidget) -> bool:
    """通过 DwmExtendFrameIntoClientArea(-1) 为非分层窗口启用原生 DWM 每像素 Alpha 合成。"""
    if sys.platform != "win32" or not shiboken6.isValid(widget):
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        margins = _DwmMargins(-1, -1, -1, -1)
        return _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) == 0
    except Exception:
        return False


def apply_capture_exclusion(widget: QWidget, exclude: bool = True) -> bool:
    """
    调用 Windows SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)：
    启用后窗口对人眼可见，但屏幕截图 API（ImageGrab/BitBlt/DXGI）会自动透视该窗口，
    使原位遮盖的字幕舱不会挡住底层原生字幕的 OCR 提取。
    """
    if not shiboken6.isValid(widget):
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        affinity = _WDA_EXCLUDEFROMCAPTURE if exclude else _WDA_NONE
        res = _u32.SetWindowDisplayAffinity(hwnd, affinity)
        return bool(res)
    except Exception:
        return False


def _strip_word_punctuation(raw_token: str) -> str:
    """从字幕词块中剥离两端标点，保留内部连字符、撇号简缩词及成对括号公式（如 O(1)）。"""
    if not raw_token:
        return ""
    # 若为成对括号包裹的算法表达式（如 O(1), O(N)），剥离外围逗号句号但保留闭括号
    stripped_outer = re.sub(r"^[,.;:!?\"'“”‘’]+|[,.;:!?\"'“”‘’]+$", "", raw_token)
    if re.fullmatch(r"[A-Za-z]+\([A-Za-z0-9_]+\)", stripped_outer):
        return stripped_outer
    cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", raw_token)
    if cleaned and any(c.isascii() and c.isalpha() for c in cleaned):
        return cleaned
    return ""


class VideoPlaybackController:
    """
    查词自动暂停/恢复控制器：
    当用户点击字幕单词查词时自动暂停外部视频播放，关闭词典卡时按需恢复。
    """

    def __init__(self):
        self._paused_by_us = False
        self._paused_hwnd: int = 0
        self._last_toggle_ts: float = 0.0

    @property
    def is_paused_by_us(self) -> bool:
        return self._paused_by_us

    def _is_safe_media_window(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        try:
            if not _u32.IsWindow(hwnd) or not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd):
                return False
            cls_buf = ctypes.create_unicode_buffer(128)
            _u32.GetClassNameW(hwnd, cls_buf, 128)
            cls_name = cls_buf.value
            if cls_name in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "Shell_TrayWnd", "Progman", "WorkerW"):
                return False
            return True
        except Exception:
            return False

    def _send_space_to_hwnd(self, target_hwnd: int, restore_prev_fg: bool = True) -> bool:
        if not self._is_safe_media_window(target_hwnd):
            return False
        try:
            prev_fg = int(_u32.GetForegroundWindow() or 0)
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            tgt_tid = _u32.GetWindowThreadProcessId(target_hwnd, None)
            attached = False
            if tgt_tid and tgt_tid != cur_tid:
                attached = bool(_u32.AttachThreadInput(cur_tid, tgt_tid, True))
            try:
                ok = bool(_u32.SetForegroundWindow(target_hwnd))
                if not ok:
                    return False
                VK_SPACE = 0x20
                KEYEVENTF_KEYUP = 0x0002
                _u32.keybd_event(VK_SPACE, 0x39, 0, 0)
                _u32.keybd_event(VK_SPACE, 0x39, KEYEVENTF_KEYUP, 0)
            finally:
                if attached:
                    _u32.AttachThreadInput(cur_tid, tgt_tid, False)
            if restore_prev_fg and prev_fg and prev_fg != target_hwnd and _u32.IsWindow(prev_fg):
                # 仅在需要立刻夺回焦点时留极短投递窗口，避免恢复/暂停阻塞 UI 主线程
                time.sleep(0.015)
                _u32.SetForegroundWindow(prev_fg)
            return True
        except Exception:
            return False

    def pause_if_needed(self, target_hwnd: int) -> bool:
        if self._paused_by_us:
            return False
        now = time.time()
        if now - self._last_toggle_ts < 0.35:
            return False
        # 暂停时无需强制切回原字幕焦点（随后 DictionaryCard 会弹出承接焦点）
        if self._send_space_to_hwnd(target_hwnd, restore_prev_fg=False):
            self._paused_by_us = True
            self._paused_hwnd = target_hwnd
            self._last_toggle_ts = now
            return True
        return False

    def resume_if_paused(self) -> bool:
        if not self._paused_by_us:
            return False
        hwnd = self._paused_hwnd
        self._paused_by_us = False
        self._paused_hwnd = 0
        self._last_toggle_ts = time.time()
        return self._send_space_to_hwnd(hwnd, restore_prev_fg=False)

    def reset_state(self):
        self._paused_by_us = False
        self._paused_hwnd = 0


def _draw_halo_text(
    painter: QPainter,
    rect: QRectF,
    flags: int,
    text: str,
    strength: float,
    dark_halo: bool,
):
    """为混杂或低对比度背景下的透明字幕绘制锐利自适应可读性描边与轻微投影。"""
    s = max(0.0, min(1.0, float(strength)))
    if s <= 0.02 or not text:
        return
    base = (10, 12, 16) if dark_halo else (250, 249, 246)
    if dark_halo:
        painter.setPen(QColor(base[0], base[1], base[2], max(0, min(255, int(round(130 * s))))))
        painter.drawText(rect.translated(0.0, 1.5), flags, text)
    cardinal_a = max(0, min(255, int(round(165 * s))))
    diag_a = max(0, min(255, int(round(115 * s))))
    painter.setPen(QColor(base[0], base[1], base[2], cardinal_a))
    for dx, dy in ((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)):
        painter.drawText(rect.translated(dx, dy), flags, text)
    painter.setPen(QColor(base[0], base[1], base[2], diag_a))
    for dx, dy in ((-0.85, -0.85), (0.85, -0.85), (-0.85, 0.85), (0.85, 0.85)):
        painter.drawText(rect.translated(dx, dy), flags, text)


class InteractiveSubtitleText(QWidget):
    """
    纯 QPainter 高性能交互式英文字幕词流控件：
    - 毫秒级自动折行计算与精确词块矩形缓存，支持数百条历史流畅滚动无卡顿；
    - 鼠标/触控点按任意单词立即触发 word_clicked(clean_word, global_pos)；
    - 遵循 Editorial 风格规范：悬停时呈现 2px 陈玫色 (#816D70) 锐利底线，零圆角胶囊与阴影噪点。
    """
    word_clicked = Signal(str, QPoint)

    def __init__(
        self,
        font_size_pt: float = 12.0,
        font_weight: QFont.Weight = QFont.Weight.Normal,
        text_color: str = "#F9F8F6",
        hover_text_color: str = "#F9F8F6",
        hover_bg_color: str = "rgba(129, 109, 112, 255)",
        align_center: bool = False,
        serif: bool = False,
        italic: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._text: str = ""
        self._serif: bool = bool(serif or align_center)
        self._italic: bool = bool(italic)
        self._weight_key: str = "normal"
        self._font = _make_ui_font(
            font_size_pt,
            weight=font_weight,
            display=align_center,
            serif=self._serif,
            italic=italic,
        )
        self._text_color = QColor(text_color)
        self._hover_text_color = QColor(hover_text_color)
        self._hover_bg_color = self._parse_qcolor(hover_bg_color)
        self._underline_color = QColor(LEXICON_THEME["mauve"])
        self._align_center = align_center
        self._transparent_bg: bool = False
        self._shadow_strength: float = 0.0
        self._dark_halo: bool = True

        self._word_items: List[Dict[str, Any]] = []
        self._hovered_idx: int = -1
        self._pressed_idx: int = -1
        self._press_global_pos = QPoint()
        self._cached_width: int = -1
        self._cached_height: int = 24
        self._line_spacing: float = 5.0
        self._captures_gesture: bool = True  # 通知 SmoothScrollController 放行词流内触控点击

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _parse_qcolor(val: str) -> QColor:
        m = re.match(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", val.strip())
        if m:
            return QColor(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        return QColor(val)

    def set_shadow_mode(self, transparent_bg: bool, strength: Optional[float] = None, dark_halo: bool = True):
        if strength is None:
            s = 1.0 if transparent_bg else 0.0
        else:
            s = max(0.0, min(1.0, float(strength)))
        self._transparent_bg = bool(transparent_bg or s > 0.02)
        self._shadow_strength = s
        self._dark_halo = bool(dark_halo)
        self.update()

    def set_underline_color(self, color: str):
        self._underline_color = QColor(color)
        self.update()

    def set_palette_colors(self, text_color: str, hover_text_color: str, hover_bg_color: str):
        self._text_color = QColor(text_color)
        self._hover_text_color = QColor(hover_text_color)
        self._hover_bg_color = self._parse_qcolor(hover_bg_color)
        self.update()

    def set_font_size(self, font_size_pt: float):
        pt = max(9.0, min(26.0, float(font_size_pt)))
        if abs(self._font.pointSizeF() - pt) < 0.05:
            return
        self._font.setPointSizeF(pt)
        self._cached_width = -1
        self._relayout(max(120, self.width() if self.width() > 60 else 560))
        self.updateGeometry()
        self.update()

    def set_font_weight(self, weight_key: str):
        wk = str(weight_key or "normal")
        if wk not in HUD_WEIGHT_CHOICES:
            wk = "normal"
        if wk == self._weight_key:
            return
        self._weight_key = wk
        pt = self._font.pointSizeF()
        if wk == "light":
            # Windows 下 Georgia 仅含 Regular/Bold，细体切换至原生支持 Light 字重的栈以确保笔画真实变细
            f = QFont()
            f.setFamilies(["Segoe UI Light", "Microsoft YaHei UI Light", "Noto Serif SC", "Microsoft YaHei UI"])
            f.setPointSizeF(pt)
            f.setWeight(QFont.Weight.Light)
            if self._italic:
                f.setItalic(True)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            self._font = f
        else:
            q_w = QFont.Weight.Bold if wk == "bold" else QFont.Weight.Normal
            self._font = _make_ui_font(
                pt,
                weight=q_w,
                display=self._align_center,
                serif=self._serif,
                italic=self._italic,
            )
        self._cached_width = -1
        self._relayout(max(120, self.width() if self.width() > 60 else 560))
        self.updateGeometry()
        self.update()

    def set_text(self, text: str):
        norm = (text or "").strip()
        if norm == self._text:
            return
        self._text = norm
        self._hovered_idx = -1
        self._pressed_idx = -1
        self._cached_width = -1
        self._relayout(max(120, self.width() if self.width() > 60 else 560))
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def get_word_items(self) -> List[Dict[str, Any]]:
        """返回词块矩形与对应单词（供单元测试精确命中测试）。"""
        return list(self._word_items)

    def _compute_layout_items(self, avail_w: int) -> Tuple[List[Dict[str, Any]], int]:
        """纯函数：计算指定可用宽度下的全部词块矩形与高度，不产生副作用。"""
        avail_w = max(80, int(avail_w))
        if not self._text:
            return [], 24

        fm = QFontMetricsF(self._font)
        space_w = max(4.0, fm.horizontalAdvance(" "))
        line_h = fm.height()

        tokens = [t for t in self._text.split() if t]
        lines: List[List[Tuple[str, str, float]]] = []
        cur_line: List[Tuple[str, str, float]] = []
        cur_w = 0.0

        for tok in tokens:
            tw = fm.horizontalAdvance(tok)
            step = tw if not cur_line else (space_w + tw)
            if cur_line and (cur_w + step > avail_w - 4):
                lines.append(cur_line)
                cur_line = [(tok, _strip_word_punctuation(tok), tw)]
                cur_w = tw
            else:
                cur_line.append((tok, _strip_word_punctuation(tok), tw))
                cur_w += step
        if cur_line:
            lines.append(cur_line)

        items: List[Dict[str, Any]] = []
        y = 2.0
        for line in lines:
            total_line_w = sum(item[2] for item in line) + space_w * max(0, len(line) - 1)
            x = max(0.0, (avail_w - total_line_w) / 2.0) if self._align_center else 0.0
            for tok, clean, tw in line:
                rect = QRectF(x, y, tw, line_h)
                items.append(
                    {
                        "raw": tok,
                        "clean": clean,
                        "rect": rect,
                    }
                )
                x += tw + space_w
            y += line_h + self._line_spacing

        total_h = max(24, int(round(y - self._line_spacing + 4.0)))
        return items, total_h

    def measure_height_for_width(self, avail_w: int) -> int:
        _, h = self._compute_layout_items(avail_w)
        return h

    def _relayout(self, avail_w: int):
        avail_w = max(80, int(avail_w))
        if avail_w == self._cached_width and self._word_items is not None:
            return
        self._cached_width = avail_w
        items, total_h = self._compute_layout_items(avail_w)
        self._word_items = items
        self._cached_height = total_h
        self.setFixedHeight(total_h)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self.measure_height_for_width(w)

    def sizeHint(self) -> QSize:
        w = self.width() if self.width() > 60 else 560
        return QSize(w, self.measure_height_for_width(w))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(self.width())

    def _hit_test(self, pos: QPoint) -> int:
        px, py = float(pos.x()), float(pos.y())
        for idx, item in enumerate(self._word_items):
            if not item["clean"]:
                continue
            r: QRectF = item["rect"]
            if r.adjusted(-2.5, -2.5, 2.5, 2.5).contains(px, py):
                return idx
        return -1

    def leaveEvent(self, event):
        if self._hovered_idx != -1:
            self._hovered_idx = -1
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        idx = self._hit_test(event.position().toPoint())
        if idx != self._hovered_idx:
            self._hovered_idx = idx
            if idx != -1:
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            else:
                self.unsetCursor()
            self.update()
        if self._pressed_idx != -1:
            # 若按下并横向拖拽超出 10px，释放按下态并透传拖拽
            if (event.globalPosition().toPoint() - self._press_global_pos).manhattanLength() > 10:
                self._pressed_idx = -1
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._hit_test(event.position().toPoint())
            if idx != -1:
                self._pressed_idx = idx
                self._press_global_pos = event.globalPosition().toPoint()
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._pressed_idx != -1:
            idx = self._pressed_idx
            self._pressed_idx = -1
            self.update()
            hit = self._hit_test(event.position().toPoint())
            if hit == idx and (event.globalPosition().toPoint() - self._press_global_pos).manhattanLength() <= 10:
                word = self._word_items[idx]["clean"]
                if word:
                    self.word_clicked.emit(word, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pt = pts[0].position().toPoint()
                g_pt = pts[0].globalPosition().toPoint()
                et = event.type()
                if et == QEvent.Type.TouchBegin:
                    idx = self._hit_test(pt)
                    if idx != -1:
                        self._pressed_idx = idx
                        self._hovered_idx = idx
                        self._press_global_pos = g_pt
                        self.update()
                        event.accept()
                        return True
                elif et == QEvent.Type.TouchEnd and self._pressed_idx != -1:
                    idx = self._pressed_idx
                    self._pressed_idx = -1
                    self._hovered_idx = -1
                    self.update()
                    if (g_pt - self._press_global_pos).manhattanLength() <= 16:
                        word = self._word_items[idx]["clean"]
                        if word:
                            self.word_clicked.emit(word, g_pt)
                    event.accept()
                    return True
                elif et == QEvent.Type.TouchCancel and self._pressed_idx != -1:
                    self._pressed_idx = -1
                    self._hovered_idx = -1
                    self.update()
                    event.accept()
                    return True
        return super().event(event)

    def paintEvent(self, event):
        if not self._word_items:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)

        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for idx, item in enumerate(self._word_items):
            r: QRectF = item["rect"]
            is_active = (idx == self._hovered_idx or idx == self._pressed_idx)
            if self._shadow_strength > 0.02:
                _draw_halo_text(painter, r, flags, item["raw"], self._shadow_strength, self._dark_halo)
            if is_active:
                painter.setPen(self._hover_text_color)
                painter.drawText(r, flags, item["raw"])
                painter.fillRect(QRectF(r.left(), r.bottom() - 1.5, r.width(), 2.0), self._underline_color)
            else:
                painter.setPen(self._text_color)
                painter.drawText(r, flags, item["raw"])


class _ShadowLabel(QLabel):
    """纯文字排版标签：高透明度/半透明状态下按背景对比度自动启用可读性描边与投影，高对比纯背景保持零重影。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._shadow_enabled = False
        self._transparent_bg = False
        self._shadow_strength: float = 0.0
        self._dark_halo: bool = True
        self._text_color: Optional[QColor] = None

    def set_text_color(self, color: str):
        self._text_color = QColor(color)
        self.update()

    def set_shadow_enabled(self, enabled: bool):
        self._shadow_enabled = bool(enabled)
        self._shadow_strength = 1.0 if enabled else 0.0
        self.update()

    def set_transparent_bg(self, val: bool, strength: Optional[float] = None, dark_halo: bool = True):
        s = (1.0 if val else 0.0) if strength is None else max(0.0, min(1.0, float(strength)))
        self._shadow_strength = s
        self._shadow_enabled = (s > 0.02)
        self._transparent_bg = bool(val or s > 0.02)
        self._dark_halo = bool(dark_halo)
        self.update()

    def paintEvent(self, event):
        if self._shadow_strength <= 0.02 or not self.text():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        rect = QRectF(self.contentsRect())
        flags = int(self.alignment()) | (int(Qt.TextFlag.TextWordWrap) if self.wordWrap() else 0)
        txt = self.text()
        _draw_halo_text(painter, rect, flags, txt, self._shadow_strength, self._dark_halo)
        fg = self._text_color if self._text_color is not None else self.palette().color(QPalette.ColorRole.WindowText)
        painter.setPen(fg)
        painter.drawText(rect, flags, txt)


class _TranscriptRowWidget(QFrame):
    """独立字幕台历史流中的单条双语字幕条目 (Editorial Hairline Card)。"""
    word_clicked = Signal(str, str, str, QPoint)
    row_activated = Signal(int)

    def __init__(self, entry_id: int, ts_str: str, en_text: str, cn_text: str, is_current: bool = False, clickable_row: bool = False, parent=None):
        super().__init__(parent)
        self._entry_id = entry_id
        self._ts_str = ts_str or "--:--"
        self._en_text = en_text
        self._cn_text = cn_text
        self._clickable_row = clickable_row
        self._is_current = is_current
        self._hovered: bool = False
        self._row_press_pos: Optional[QPoint] = None

        self.setObjectName("TranscriptRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("#TranscriptRow { background: transparent; border: none; }")
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 15, 12, 15)
        layout.setSpacing(12)

        self.ts_label = QLabel(ts_str or "--:--")
        self.ts_label.setFixedSize(48, 20)
        self.ts_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.ts_label.setFont(_make_ui_font(9.5, serif=True))
        self._apply_ts_style(is_current)
        layout.addWidget(self.ts_label, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)

        self.en_widget = InteractiveSubtitleText(
            font_size_pt=10.5,
            font_weight=QFont.Weight.Normal,
            text_color="#1C1C1C" if is_current else "#2E2E2E",
            hover_text_color="#1C1C1C",
            hover_bg_color="rgba(129, 109, 112, 255)",
            align_center=False,
        )
        self.en_widget.set_text(en_text)
        self.en_widget.word_clicked.connect(self._on_word_clicked)
        body.addWidget(self.en_widget)

        self.cn_label = QLabel(cn_text or "正在翻译...")
        self.cn_label.setWordWrap(True)
        self.cn_label.setTextFormat(Qt.TextFormat.PlainText)
        self.cn_label.setFont(_make_ui_font(9.5))
        self._apply_cn_style(cn_text, is_current)
        body.addWidget(self.cn_label)

        layout.addLayout(body, stretch=1)

    def _apply_ts_style(self, is_current: bool):
        color = "#1C1C1C" if is_current else "#6B7280"
        weight = "500" if is_current else "400"
        self.ts_label.setStyleSheet(
            f"color: {color}; background: transparent; border: none; border-radius: 0px; padding-top: 2px; font-weight: {weight};"
        )

    def _apply_cn_style(self, cn_text: str, is_current: bool):
        if not cn_text:
            color = "#9CA3AF"
        elif is_current:
            color = "#4B4F56"
        else:
            color = "#6B7280"
        self.cn_label.setStyleSheet(
            f"color: {color}; font-size: 12.5px; background: transparent; border: none;"
        )

    def enterEvent(self, event):
        super().enterEvent(event)
        if not self._hovered:
            self._hovered = True
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hovered:
            self._hovered = False
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        band_rect = QRectF(2.0, 0.0, max(1.0, float(self.width() - 10)), float(self.height()))
        if self._hovered and not self._is_current:
            painter.fillRect(band_rect, QColor(LEXICON_THEME["surface"]))
        elif self._is_current:
            painter.fillRect(band_rect, QColor("#F3F2EE"))
        if self.width() > 24:
            rule_alpha = 90 if self._is_current else (102 if self._hovered else 26)
            painter.setPen(QPen(QColor(28, 28, 28, rule_alpha), 1.0))
            painter.drawLine(2, 0, self.width() - 8, 0)
            if self._is_current and self.height() > 2:
                painter.drawLine(2, self.height() - 1, self.width() - 8, self.height() - 1)
        if self._is_current and self.height() > 6:
            painter.fillRect(QRectF(2.0, 1.0, 2.0, float(self.height() - 2)), QColor(LEXICON_THEME["mauve"]))
        painter.end()
        super().paintEvent(event)

    def update_content(
        self,
        ts_str: str,
        en_text: str,
        cn_text: str,
        is_current: bool,
        clickable_row: bool,
        entry_id: Optional[int] = None,
    ) -> bool:
        if entry_id is not None:
            self._entry_id = entry_id
        self._clickable_row = clickable_row
        changed = False
        norm_ts = ts_str or "--:--"
        if norm_ts != getattr(self, "_ts_str", ""):
            self._ts_str = norm_ts
            self.ts_label.setText(norm_ts)
            changed = True
        if en_text != self._en_text:
            self._en_text = en_text
            self.en_widget.set_text(en_text)
            changed = True
        if cn_text != self._cn_text:
            self._cn_text = cn_text
            self.cn_label.setText(cn_text or "正在翻译...")
            self._apply_cn_style(cn_text, is_current)
            self.cn_label.show()
            changed = True
        if is_current != self._is_current:
            self._is_current = is_current
            self.en_widget.set_palette_colors(
                "#1C1C1C" if is_current else "#2E2E2E",
                "#1C1C1C",
                "rgba(129, 109, 112, 255)",
            )
            self._apply_ts_style(is_current)
            self._apply_cn_style(self._cn_text, is_current)
            self.update()
            changed = True
        return changed

    def _on_word_clicked(self, word: str, pos: QPoint):
        self.word_clicked.emit(word, self._en_text, self._cn_text, pos)

    def mousePressEvent(self, event: QMouseEvent):
        if self._clickable_row and event.button() == Qt.MouseButton.LeftButton:
            self._row_press_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._clickable_row and event.button() == Qt.MouseButton.LeftButton and self._row_press_pos is not None:
            dist = (event.globalPosition().toPoint() - self._row_press_pos).manhattanLength()
            self._row_press_pos = None
            if dist <= 10 and self.rect().contains(event.position().toPoint()):
                self.row_activated.emit(self._entry_id)
        else:
            self._row_press_pos = None
        super().mouseReleaseEvent(event)


class _HudStylePopover(QFrame):
    """悬浮字幕条外观调节浮层 (Editorial 暖米纸极简面板，零圆角零阴影)。"""
    font_size_changed = Signal(float)
    font_weight_changed = Signal(str)
    bg_opacity_changed = Signal(int)
    display_lang_changed = Signal(str)
    width_preset_changed = Signal(str)
    hud_theme_changed = Signal(str)
    settings_commit_requested = Signal()
    reset_requested = Signal()
    bilibili_login_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(LEXICON_THEME["bg"]))
        self.setPalette(pal)
        self.setFixedWidth(340)

        self._lang_btns: Dict[str, QPushButton] = {}
        self._width_btns: Dict[str, QPushButton] = {}
        self._theme_btns: Dict[str, QPushButton] = {}
        self._weight_btns: Dict[str, QPushButton] = {}
        self._init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if supports_win11_native_chrome() and shiboken6.isValid(self) and _dwmapi is not None:
            try:
                hwnd = int(self.winId())
                if hwnd:
                    pref = ctypes.c_int(1)
                    _dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(33), ctypes.byref(pref), wintypes.DWORD(ctypes.sizeof(pref)))
            except Exception:
                pass
        apply_capture_exclusion(self, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(LEXICON_THEME["bg"]))
        painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.75, 0.75, max(1.0, self.width() - 1.5), max(1.0, self.height() - 1.5)))
        painter.end()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        lang_row = self._build_seg_row(
            [("bilingual", "双语"), ("en_only", "仅英文"), ("cn_only", "仅中文")],
            self._lang_btns,
            self._on_lang_clicked,
        )
        root.addLayout(lang_row)

        width_row = self._build_seg_row(
            [("narrow", "紧凑"), ("medium", "适中"), ("wide", "宽幅")],
            self._width_btns,
            self._on_width_clicked,
        )
        root.addLayout(width_row)

        theme_row = self._build_seg_row(
            [("dark", "墨黑"), ("light", "暖米纸")],
            self._theme_btns,
            self._on_theme_clicked,
        )
        root.addLayout(theme_row)

        slider_qss = (
            "QSlider::groove:horizontal { height: 1px; background: rgba(28,28,28,52); border: none; border-radius: 0px; }"
            "QSlider::sub-page:horizontal { height: 2px; background: #1C1C1C; border: none; border-radius: 0px; }"
            "QSlider::handle:horizontal { background: #816D70; border: none; width: 8px; height: 14px; margin: -6px 0px; border-radius: 0px; }"
            "QSlider::handle:horizontal:hover { background: #1C1C1C; }"
        )
        lbl_qss = "color: #6B7280; font-size: 11.5px; background: transparent; border: none;"
        val_qss = "color: #1C1C1C; font-size: 12px; background: transparent; border: none; font-family: 'Georgia', serif;"

        # 1. 字号
        font_block = QVBoxLayout()
        font_block.setSpacing(5)
        f_top = QHBoxLayout()
        l_font = QLabel("字号")
        l_font.setFont(_make_ui_font(9.2))
        l_font.setStyleSheet(lbl_qss)
        f_top.addWidget(l_font)
        f_top.addStretch(1)
        self.font_val_lbl = QLabel("14pt")
        self.font_val_lbl.setStyleSheet(val_qss)
        f_top.addWidget(self.font_val_lbl)
        font_block.addLayout(f_top)

        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(10, 22)
        self.font_slider.setSingleStep(1)
        self.font_slider.setFixedHeight(18)
        self.font_slider.setStyleSheet(slider_qss)
        self.font_slider.valueChanged.connect(self._on_font_changed)
        self.font_slider.sliderReleased.connect(self.settings_commit_requested.emit)
        font_block.addWidget(self.font_slider)
        root.addLayout(font_block)

        # 1.5 字重（细 / 标准 / 粗）
        weight_block = QVBoxLayout()
        weight_block.setSpacing(5)
        l_weight = QLabel("字重")
        l_weight.setFont(_make_ui_font(9.2))
        l_weight.setStyleSheet(lbl_qss)
        weight_block.addWidget(l_weight)
        weight_block.addLayout(
            self._build_seg_row(
                [("light", "细"), ("normal", "标准"), ("bold", "粗")],
                self._weight_btns,
                self._on_weight_clicked,
            )
        )
        root.addLayout(weight_block)

        # 2. 透明度
        op_block = QVBoxLayout()
        op_block.setSpacing(5)
        o_top = QHBoxLayout()
        l_op = QLabel("透明度")
        l_op.setFont(_make_ui_font(9.2))
        l_op.setStyleSheet(lbl_qss)
        o_top.addWidget(l_op)
        o_top.addStretch(1)
        self.opacity_val_lbl = QLabel("92%")
        self.opacity_val_lbl.setStyleSheet(val_qss)
        o_top.addWidget(self.opacity_val_lbl)
        op_block.addLayout(o_top)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setSingleStep(2)
        self.opacity_slider.setFixedHeight(18)
        self.opacity_slider.setStyleSheet(slider_qss)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.opacity_slider.sliderReleased.connect(self.settings_commit_requested.emit)
        op_block.addWidget(self.opacity_slider)
        root.addLayout(op_block)

        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {LEXICON_THEME['divider']}; border: none;")
        root.addWidget(div)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.pos_hint_lbl = QLabel("")
        self.pos_hint_lbl.hide()

        reset_btn = QPushButton("重置归位")
        reset_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        reset_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #1C1C1C; border: none; "
            "border-bottom: 1px solid rgba(28,28,28,102); border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { border-bottom: 1px solid #1C1C1C; }"
        )
        reset_btn.clicked.connect(self.reset_requested.emit)
        foot.addWidget(reset_btn)
        foot.addStretch(1)

        self.bili_btn = QPushButton("B站扫码")
        self.bili_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.bili_btn.setToolTip("扫码登录 B站账号，拉取需登录可见的官方英文字幕")
        self._apply_bili_link_style(False)
        self.bili_btn.clicked.connect(self._on_bili_clicked)
        foot.addWidget(self.bili_btn)
        root.addLayout(foot)

    def _apply_bili_link_style(self, logged_in: bool):
        line_col = LEXICON_THEME["mauve"] if logged_in else "rgba(28,28,28,102)"
        txt_col = "#1C1C1C" if logged_in else "#6B7280"
        self.bili_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {txt_col}; border: none; "
            f"border-bottom: 1px solid {line_col}; border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 500; }}"
            f"QPushButton:hover {{ color: #1C1C1C; border-bottom: 1px solid #816D70; }}"
        )

    def _build_seg_row(self, items: List[Tuple[str, str]], store: Dict[str, QPushButton], slot) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(0)
        for idx, (key, text) in enumerate(items):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFixedHeight(29)
            left_rule = "border-left: none;" if idx > 0 else "border-left: 1px solid rgba(28,28,28,51);"
            btn.setStyleSheet(
                f"QPushButton {{ background: #F9F8F6; color: #1C1C1C; border: 1px solid rgba(28,28,28,51); {left_rule} "
                f"border-radius: 0px; padding: 0 10px; font-size: 11.5px; font-weight: 500; }}"
                f"QPushButton:hover {{ background: #F3F2EE; border-color: rgba(28,28,28,110); }}"
                f"QPushButton:checked {{ background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; }}"
            )
            btn.clicked.connect(lambda _chk=False, k=key: slot(k))
            store[key] = btn
            row.addWidget(btn)
        return row

    def _on_font_changed(self, val: int):
        self.font_val_lbl.setText(f"{val}pt")
        self.font_size_changed.emit(float(val))

    def _on_opacity_changed(self, val: int):
        self.opacity_val_lbl.setText("纯字" if val == 0 else f"{val}%")
        self.bg_opacity_changed.emit(int(val))

    def _on_lang_clicked(self, key: str):
        for k, b in self._lang_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self.display_lang_changed.emit(key)
        self.settings_commit_requested.emit()

    def _on_width_clicked(self, key: str):
        for k, b in self._width_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self.width_preset_changed.emit(key)
        self.settings_commit_requested.emit()

    def _on_theme_clicked(self, key: str):
        for k, b in self._theme_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self.hud_theme_changed.emit(key)
        self.settings_commit_requested.emit()

    def _on_weight_clicked(self, key: str):
        for k, b in self._weight_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
        self.font_weight_changed.emit(key)
        self.settings_commit_requested.emit()

    def sync_state(self, settings: Dict[str, Any]):
        pt = int(round(float(settings.get("font_size_pt", 13.5))))
        fw = str(settings.get("font_weight", "normal"))
        op = int(settings.get("bg_opacity_pct", 92))
        lang = str(settings.get("display_lang", "bilingual"))
        wp = str(settings.get("width_preset", "medium"))
        ht = str(settings.get("hud_theme", "dark"))
        auto_follow = bool(settings.get("auto_follow", True))
        user_pos = settings.get("user_pos")

        self.font_slider.blockSignals(True)
        self.font_slider.setValue(max(10, min(22, pt)))
        self.font_slider.blockSignals(False)
        self.font_val_lbl.setText(f"{self.font_slider.value()}pt")

        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(max(0, min(100, op)))
        self.opacity_slider.blockSignals(False)
        self.opacity_val_lbl.setText("纯字" if self.opacity_slider.value() == 0 else f"{self.opacity_slider.value()}%")

        if hasattr(self, "bili_btn"):
            has_auth = BilibiliAuthManager.has_sessdata()
            if has_auth:
                uname = BilibiliAuthManager.get_display_name()
                self.bili_btn.setText(f"B站: {uname[:8]}" if uname else "B站已登")
            else:
                self.bili_btn.setText("B站扫码")
            self._apply_bili_link_style(has_auth)

        for k, b in self._lang_btns.items():
            b.blockSignals(True)
            b.setChecked(k == lang)
            b.blockSignals(False)

        for k, b in self._width_btns.items():
            b.blockSignals(True)
            b.setChecked(k == wp)
            b.blockSignals(False)

        for k, b in self._theme_btns.items():
            b.blockSignals(True)
            b.setChecked(k == ht)
            b.blockSignals(False)

        for k, b in self._weight_btns.items():
            b.blockSignals(True)
            b.setChecked(k == fw)
            b.blockSignals(False)

        if not auto_follow and isinstance(user_pos, (list, tuple)) and len(user_pos) == 2:
            self.pos_hint_lbl.setText(f"位置: 固定 ({int(user_pos[0])}, {int(user_pos[1])})")
        else:
            self.pos_hint_lbl.setText("位置: 自动贴合")

    def _on_bili_clicked(self):
        self.hide()
        self.bilibili_login_requested.emit()


class _BilibiliLoginDialog(QFrame):
    """独立 B站官方二维码扫码登录浮窗：不读取浏览器本地 Cookie，扫码结果仅保存在本地应用设置表。"""
    auth_changed = Signal()
    _qr_ready = Signal(str, str, str)
    _poll_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(LEXICON_THEME["bg"]))
        self.setPalette(pal)
        self.setFixedWidth(332)

        self._qrcode_key: str = ""
        self._login_gen: int = 0
        self._polling_busy: bool = False
        self._dragging: bool = False
        self._drag_offset = QPoint()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1500)
        self._poll_timer.timeout.connect(self._on_poll_tick)

        self._qr_ready.connect(self._on_qr_ready)
        self._poll_ready.connect(self._on_poll_ready)

        self._init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if supports_win11_native_chrome() and shiboken6.isValid(self) and _dwmapi is not None:
            try:
                hwnd = int(self.winId())
                if hwnd:
                    pref = ctypes.c_int(1)
                    _dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(33), ctypes.byref(pref), wintypes.DWORD(ctypes.sizeof(pref)))
            except Exception:
                pass
        apply_capture_exclusion(self, True)

    def closeEvent(self, event):
        self._poll_timer.stop()
        self._login_gen += 1
        self._polling_busy = False
        super().closeEvent(event)

    def hideEvent(self, event):
        self._poll_timer.stop()
        self._login_gen += 1
        self._polling_busy = False
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(LEXICON_THEME["bg"]))
        painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.75, 0.75, max(1.0, self.width() - 1.5), max(1.0, self.height() - 1.5)))
        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("B站扫码登录")
        title.setFont(_make_ui_font(13.5, serif=True))
        title.setStyleSheet("color: #1C1C1C; background: transparent; border: none;")
        head.addWidget(title)
        head.addStretch(1)

        close_x = VectorIconButton(
            kind="close",
            color="#6B7280",
            hover_color="#1C1C1C",
            hover_bg=LEXICON_THEME["hover_bg"],
            size=22,
            radius=0,
        )
        close_x.clicked.connect(self.close)
        head.addWidget(close_x)
        root.addLayout(head)

        desc = QLabel("仅用于获取需登录可见的官方英文 CC/AI 字幕。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6B7280; font-size: 11.5px; line-height: 1.5; background: transparent; border: none;")
        root.addWidget(desc)

        qr_wrap = QHBoxLayout()
        qr_wrap.addStretch(1)
        self.qr_label = QLabel("正在生成二维码...")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(196, 196)
        self.qr_label.setStyleSheet(
            "QLabel { background: #FFFFFF; color: #6B7280; border-radius: 0px; "
            "border: 1px solid rgba(28,28,28,38); font-size: 12px; }"
        )
        qr_wrap.addWidget(self.qr_label)
        qr_wrap.addStretch(1)
        root.addLayout(qr_wrap)

        self.status_lbl = QLabel("请使用哔哩哔哩手机端扫一扫")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #1C1C1C; font-size: 12px; font-weight: 500; background: transparent; border: none;")
        root.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_qss = (
            "QPushButton { background: #F9F8F6; color: #1C1C1C; border: 1px solid rgba(28,28,28,51); "
            "border-radius: 0px; padding: 5px 12px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { background: #1C1C1C; color: #F9F8F6; border-color: #1C1C1C; }"
        )
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_btn.setStyleSheet(btn_qss)
        self.refresh_btn.clicked.connect(self.start_qr_session)
        btn_row.addWidget(self.refresh_btn)

        self.logout_btn = QPushButton("退出登录")
        self.logout_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.logout_btn.setStyleSheet(
            "QPushButton { background: #F9F8F6; color: #816D70; border: 1px solid #816D70; "
            "border-radius: 0px; padding: 5px 12px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { background: #816D70; color: #F9F8F6; }"
        )
        self.logout_btn.clicked.connect(self._on_logout_clicked)
        btn_row.addWidget(self.logout_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet(
            "QPushButton { background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; "
            "border-radius: 0px; padding: 5px 14px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { background: #F9F8F6; color: #1C1C1C; }"
        )
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def refresh_auth_view(self):
        has_auth = BilibiliAuthManager.has_sessdata()
        self.logout_btn.setVisible(has_auth)
        self.start_qr_session()

    def start_qr_session(self):
        self._poll_timer.stop()
        self._login_gen += 1
        self._polling_busy = False
        gen = self._login_gen
        self._qrcode_key = ""
        self.qr_label.setPixmap(QPixmap())
        self.qr_label.setText("正在获取二维码...")
        if BilibiliAuthManager.has_sessdata():
            uname = BilibiliAuthManager.get_display_name() or "已登录用户"
            self.status_lbl.setText(f"当前已登录 B站：{uname}（可重新扫码切换账号）")
        else:
            self.status_lbl.setText("正在连接 B站登录接口...")

        def _worker():
            try:
                res = BilibiliAuthManager.start_qr_login()
                if gen == self._login_gen:
                    self._qr_ready.emit(res.get("url", ""), res.get("qrcode_key", ""), "")
            except Exception as exc:
                if gen == self._login_gen:
                    self._qr_ready.emit("", "", str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_qr_ready(self, url: str, qrcode_key: str, err: str):
        if err or not url or not qrcode_key:
            self.qr_label.setText("二维码加载失败")
            self.status_lbl.setText(err or "获取 B站登录二维码失败，请点击刷新重试")
            return
        try:
            import qrcode

            qr_img = qrcode.make(url).convert("RGB")
            w, h = qr_img.size
            raw = qr_img.tobytes("raw", "RGB")
            qimg = QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(qimg).scaled(
                184,
                184,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.qr_label.setText("")
            self.qr_label.setPixmap(pix)
            self._qrcode_key = qrcode_key
            if BilibiliAuthManager.has_sessdata():
                uname = BilibiliAuthManager.get_display_name() or "已登录用户"
                self.status_lbl.setText(f"当前已登录：{uname} · 也可扫码换绑账号")
            else:
                self.status_lbl.setText("请使用哔哩哔哩手机 App 扫一扫")
            if self.isVisible():
                self._poll_timer.start()
        except Exception as exc:
            self.qr_label.setText("渲染失败")
            self.status_lbl.setText(f"二维码生成失败：{exc}")

    def _on_poll_tick(self):
        if not self.isVisible():
            self._poll_timer.stop()
            return
        if not self._qrcode_key or self._polling_busy:
            return
        self._polling_busy = True
        key = self._qrcode_key
        gen = self._login_gen

        def _worker():
            try:
                res = BilibiliAuthManager.poll_qr_login(key)
            except Exception as exc:
                res = {"status": "error", "message": str(exc)}
            if gen == self._login_gen:
                self._poll_ready.emit(res)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_poll_ready(self, res: Dict[str, Any]):
        self._polling_busy = False
        status = str(res.get("status", "") or "")
        msg = str(res.get("message", "") or "")
        if status == "scanned":
            self.status_lbl.setText("已扫码！请在哔哩哔哩手机端点击确认登录")
        elif status == "expired":
            self._poll_timer.stop()
            self.status_lbl.setText("二维码已失效，请点击“刷新二维码”重新获取")
        elif status == "success":
            self._poll_timer.stop()
            uname = str(res.get("uname", "") or "已登录")
            self.status_lbl.setText(f"登录成功：{uname}，正在刷新 B站字幕轨...")
            self.logout_btn.show()
            self.auth_changed.emit()
            QTimer.singleShot(850, self._close_if_still_open)
        elif status == "error" and msg:
            self.status_lbl.setText(msg)

    def _close_if_still_open(self):
        if shiboken6.isValid(self) and self.isVisible():
            self.close()

    def _on_logout_clicked(self):
        BilibiliAuthManager.clear_auth()
        self.logout_btn.hide()
        self.status_lbl.setText("已清除本地 B站登录态")
        self.auth_changed.emit()


class _HudTopBar(QWidget):
    """原位字幕条顶栏：极低透明度模式下为调整按键提供紧凑深色高对比衬底，离开后随整体渐隐。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dock_alpha: float = 0.0
        self._show_dock: bool = False
        self.setObjectName("SubtitleHudTop")
        self.setStyleSheet("QWidget#SubtitleHudTop { background: transparent; border: none; }")

    def set_dock_style(self, alpha: float, show_dock: bool):
        a = max(0.0, min(1.0, float(alpha)))
        sd = bool(show_dock)
        if abs(self._dock_alpha - a) > 0.005 or self._show_dock != sd:
            self._dock_alpha = a
            self._show_dock = sd
            self.update()

    def paintEvent(self, event):
        if self._show_dock and self._dock_alpha > 0.02:
            btn_rect = QRect()
            for child in self.findChildren(QWidget):
                if child.parent() is self and child.isVisible() and not isinstance(child, QLabel):
                    r = child.geometry()
                    btn_rect = r if btn_rect.isNull() else btn_rect.united(r)
            if not btn_rect.isNull():
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                pad_r = btn_rect.adjusted(-7, -2, 7, 2).intersected(self.rect())
                a = self._dock_alpha
                painter.setPen(QPen(QColor(249, 248, 246, max(0, min(255, int(round(46 * a))))), 1.0))
                painter.setBrush(QColor(24, 24, 26, max(0, min(255, int(round(148 * a))))))
                painter.drawRoundedRect(QRectF(pad_r).adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0)
                painter.end()
        super().paintEvent(event)


class _SubtitleRegionSelectorOverlay(QWidget):
    """全屏半透明字幕区框选浮层（支持鼠标/触屏拖框，精准换算物理像素坐标）。"""

    region_selected = Signal(int, int, int, int)
    cancelled = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self._dragging = False
        self._start_pt = QPoint()
        self._cur_pt = QPoint()
        self._screen_rect = QRect(0, 0, 1280, 800)
        self._dpr = 1.0

    def begin_selection(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self._screen_rect = screen.geometry() if screen else QRect(0, 0, 1280, 800)
        self._dpr = float(screen.devicePixelRatio() or 1.0) if screen else 1.0
        self._dragging = False
        self._start_pt = QPoint()
        self._cur_pt = QPoint()
        self.setGeometry(self._screen_rect)
        self.show()
        self.raise_()
        self.activateWindow()
        self.update()

    def _finish_drag(self):
        self._dragging = False
        r = QRect(self._start_pt, self._cur_pt).normalized()
        self.hide()
        if r.width() >= 40 and r.height() >= 18:
            gx1 = int(round((self._screen_rect.x() + r.left()) * self._dpr))
            gy1 = int(round((self._screen_rect.y() + r.top()) * self._dpr))
            gx2 = int(round((self._screen_rect.x() + r.right() + 1) * self._dpr))
            gy2 = int(round((self._screen_rect.y() + r.bottom() + 1) * self._dpr))
            self.region_selected.emit(gx1, gy1, gx2, gy2)
        else:
            self.cancelled.emit()

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pt = pts[0].position().toPoint()
                et = event.type()
                if et == QEvent.Type.TouchBegin:
                    self._dragging = True
                    self._start_pt = pt
                    self._cur_pt = pt
                    self.update()
                elif et == QEvent.Type.TouchUpdate and self._dragging:
                    self._cur_pt = pt
                    self.update()
                elif et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel) and self._dragging:
                    self._cur_pt = pt
                    self._finish_drag()
                event.accept()
                return True
        return super().event(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            self._dragging = False
            self.hide()
            self.cancelled.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._start_pt = event.position().toPoint()
            self._cur_pt = self._start_pt
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._cur_pt = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._cur_pt = event.position().toPoint()
            self._finish_drag()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self._dragging = False
            self.hide()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        apply_capture_exclusion(self, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scrim = QColor(14, 16, 22, 110)
        if self._dragging:
            sel = QRect(self._start_pt, self._cur_pt).normalized().intersected(self.rect())
            if sel.width() > 2 and sel.height() > 2:
                # 挖空选区内部保持视频原画面清晰透视，仅在四周绘制压暗遮罩
                w, h = self.width(), self.height()
                painter.fillRect(0, 0, w, sel.top(), scrim)
                painter.fillRect(0, sel.bottom() + 1, w, max(0, h - sel.bottom() - 1), scrim)
                painter.fillRect(0, sel.top(), sel.left(), sel.height(), scrim)
                painter.fillRect(sel.right() + 1, sel.top(), max(0, w - sel.right() - 1), sel.height(), scrim)

                painter.setPen(QPen(QColor("#F9F8F6"), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(sel)
                painter.setPen(QPen(QColor("#816D70"), 1.0, Qt.PenStyle.DashLine))
                painter.drawRect(sel.adjusted(2, 2, -2, -2))

                dim_txt = f"{sel.width()} × {sel.height()} px · 松手开启 OCR 字幕"
                tip_w = 240
                tx = max(8, min(w - tip_w - 8, sel.center().x() - tip_w // 2))
                ty = sel.bottom() + 10 if sel.bottom() + 42 < h else max(8, sel.top() - 34)
                draw_hud_bar(painter, QRect(tx, ty, tip_w, 26), dim_txt, radius=0)
                painter.end()
                return
        painter.fillRect(self.rect(), scrim)
        bar_w = min(460, max(280, self.width() - 32))
        top_r = QRect(self.width() // 2 - bar_w // 2, 20, bar_w, 28)
        draw_hud_bar(painter, top_r, "拖动框选视频字幕区域（PPT/网页/本地视频通用）· 右键 / ESC 取消", radius=0)
        painter.end()


class BilingualSubtitleWindow(QWidget):
    """
    ScreenLex 双模双语字幕窗：
    - MODE_INPLACE_HUD：开启 DWM 截屏透视（WDA_EXCLUDEFROMCAPTURE），半透明暗色磨砂字幕条 + 影视级文字阴影，自然透出底层视频画面；
    - MODE_CONSOLE_DRAWER：独立停靠双语滚动字幕台，展示当前句与完整双语历史滚屏流。
    """
    word_clicked = Signal(str, str, str, QPoint)
    link_submitted = Signal(str)
    switch_to_ocr_requested = Signal()
    custom_region_selected = Signal(int, int, int, int)
    custom_region_cleared = Signal()
    clear_requested = Signal()
    transcript_item_activated = Signal(int)
    seek_relative_requested = Signal(float)
    bilibili_auth_changed = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self._use_native_chrome = False
        self._use_dwm_glass: bool = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.setStyleSheet(
            'QToolTip { background-color: #1C1C1C; color: #F9F8F6; border: 1px solid rgba(249, 248, 246, 48); '
            'border-radius: 0px; padding: 5px 9px; font-family: "Segoe UI Variable Text", "Microsoft YaHei UI"; font-size: 12px; }'
        )

        self._view_mode: str = MODE_INPLACE_HUD
        self._auto_follow: bool = True
        self._dwm_flushing: bool = False
        self._dwm_flush_base_h: int = 0
        self._dwm_pad_h: int = 0
        self._hud_needs_dwm_flush: bool = False
        self._last_painted_size = QSize()
        self._last_dwm_commit_ts: float = 0.0
        self._dwm_post_paint_timer = QTimer(self)
        self._dwm_post_paint_timer.setSingleShot(True)
        self._dwm_post_paint_timer.setInterval(4)
        self._dwm_post_paint_timer.timeout.connect(self._flush_dwm_after_paint)
        self._auto_pause: bool = True
        self._capture_excluded: bool = True
        self._capture_exclusion_applied: bool = False
        self._hud_hovered: bool = False
        self._custom_region_active: bool = False
        self._region_selector: Optional[_SubtitleRegionSelectorOverlay] = None
        self._hud_theme: str = str(DEFAULT_HUD_SETTINGS["hud_theme"])

        self._active_en: str = ""
        self._active_cn: str = ""
        self._status_notice: str = ""
        self._latest_history: List[Dict[str, Any]] = []
        self._latest_title: str = ""
        self._target_hwnd: int = 0
        self._last_follow_anchor: Optional[Tuple[int, int]] = None
        self._hud_target_rect: Optional[QRect] = None
        self._user_scroll_locked: bool = False
        self._programmatic_scroll: bool = False
        self._current_mode: str = "ocr"
        self._last_transcript_idx: int = -1
        self._last_snapshot_engine_id: int = 0
        self._last_snapshot_seq: int = 0
        self._row_widgets: List[_TranscriptRowWidget] = []
        self._hist_batch_data: Optional[Tuple[List[Dict[str, Any]], str, int]] = None
        self._hist_batch_size: int = 40
        self._hist_batch_timer = QTimer(self)
        self._hist_batch_timer.setSingleShot(True)
        self._hist_batch_timer.setInterval(0)
        self._hist_batch_timer.timeout.connect(self._mount_next_history_batch)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(2000)
        self._notice_timer.timeout.connect(self._clear_transient_notice)

        self._dragging: bool = False
        self._drag_offset = QPoint()
        self._resizing: bool = False
        self._resize_start_global = QPoint()
        self._resize_start_size = QSize()

        self._playback_ctrl = VideoPlaybackController()

        self._hud_anim_timer = QTimer(self)
        self._hud_anim_timer.setInterval(16)
        self._hud_anim_timer.timeout.connect(self._on_hud_anim_tick)

        self._hover_hide_timer = QTimer(self)
        self._hover_hide_timer.setSingleShot(True)
        self._hover_hide_timer.setInterval(160)
        self._hover_hide_timer.timeout.connect(self._do_hide_hud_top)

        self._hud_top_reveal: float = 0.0
        self._hud_top_fade_start: float = 0.0
        self._hud_top_fade_target: float = 0.0
        self._hud_top_fade_t0: float = 0.0
        self._hud_top_fade_dur: float = 0.16
        self._hud_top_fade_timer = QTimer(self)
        self._hud_top_fade_timer.setInterval(16)
        self._hud_top_fade_timer.timeout.connect(self._on_hud_top_fade_tick)

        self._proximity_timer = QTimer(self)
        self._proximity_timer.setInterval(85)
        self._proximity_timer.timeout.connect(self._check_low_opacity_proximity)

        self._auto_fg_is_light: Optional[bool] = None
        self._auto_halo_strength: float = 0.0
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setInterval(280)
        self._backdrop_timer.timeout.connect(self._on_backdrop_poll_tick)

        self._hud_size = QSize(540, 56)
        self._console_size = QSize(590, 480)
        self._hud_user_pos: Optional[QPoint] = None
        self._console_user_pos: Optional[QPoint] = None

        self._font_size_pt: float = float(DEFAULT_HUD_SETTINGS["font_size_pt"])
        self._font_weight: str = str(DEFAULT_HUD_SETTINGS["font_weight"])
        self._bg_opacity_pct: int = int(DEFAULT_HUD_SETTINGS["bg_opacity_pct"])
        self._display_lang: str = str(DEFAULT_HUD_SETTINGS["display_lang"])
        self._width_preset: str = str(DEFAULT_HUD_SETTINGS["width_preset"])
        self._hud_theme: str = str(DEFAULT_HUD_SETTINGS["hud_theme"])
        self._hud_theme_blend: float = 1.0 if self._hud_theme == "light" else 0.0
        self._hud_theme_anim_start: float = self._hud_theme_blend
        self._hud_theme_anim_target: float = self._hud_theme_blend
        self._hud_theme_anim_t0: float = 0.0
        self._hud_theme_anim_dur: float = 0.24
        self._hud_theme_timer = QTimer(self)
        self._hud_theme_timer.setInterval(16)
        self._hud_theme_timer.timeout.connect(self._on_hud_theme_anim_tick)
        self._style_popover: Optional[_HudStylePopover] = None
        self._bili_login_dialog: Optional[_BilibiliLoginDialog] = None

        self._topmost_guard_timer = QTimer(self)
        self._topmost_guard_timer.setInterval(1500)
        self._topmost_guard_timer.timeout.connect(self._on_topmost_guard_tick)

        self._init_ui()
        self._load_hud_settings()
        self._refresh_bili_login_btns()
        self._apply_initial_geometry()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)
        self._root_layout = root

        # ==================== 1. 模式 A：Editorial 原位字幕条 (Ink Band / Warm Paper) ====================
        self.hud_frame = QFrame()
        self.hud_frame.setObjectName("SubtitleHudFrame")
        self.hud_frame.setStyleSheet("QFrame#SubtitleHudFrame { background: transparent; border: none; }")
        hud_vbox = QVBoxLayout(self.hud_frame)
        hud_vbox.setContentsMargins(24, 12, 24, 14)
        hud_vbox.setSpacing(5)
        self._hud_vbox = hud_vbox

        # 顶栏控制条（日常观影默认隐藏，鼠标悬停或极低透明度靠近时平滑浮现）
        self.hud_top_widget = _HudTopBar()
        hud_top = QHBoxLayout(self.hud_top_widget)
        hud_top.setContentsMargins(0, 0, 0, 2)
        hud_top.setSpacing(8)

        self.hud_status_label = QLabel("")
        self.hud_status_label.setFont(_make_ui_font(8.2))
        self.hud_status_label.setStyleSheet(
            "color: rgba(249, 248, 246, 200); font-size: 11px; background: transparent; border: none;"
        )
        self.hud_status_label.hide()
        hud_top.addWidget(self.hud_status_label)
        hud_top.addStretch(1)

        seek_btn_qss = (
            "QPushButton { background: transparent; color: #D1D5DB; border: none; "
            "border-bottom: 1px solid rgba(249,248,246,80); border-radius: 0px; "
            "padding: 1px 3px; font-size: 10px; font-weight: 400; letter-spacing: 1.5px; }"
            "QPushButton:hover { color: #F9F8F6; border-bottom: 1px solid #F9F8F6; }"
        )
        self.hud_back2_btn = QPushButton("-2S")
        self.hud_back2_btn.setFixedHeight(19)
        self.hud_back2_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.hud_back2_btn.setToolTip("字幕时间轴后退 2 秒")
        self.hud_back2_btn.setStyleSheet(seek_btn_qss)
        self.hud_back2_btn.clicked.connect(lambda: self.seek_relative_requested.emit(-2.0))
        self.hud_back2_btn.hide()
        hud_top.addWidget(self.hud_back2_btn)

        self.hud_fwd2_btn = QPushButton("+2S")
        self.hud_fwd2_btn.setFixedHeight(19)
        self.hud_fwd2_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.hud_fwd2_btn.setToolTip("字幕时间轴前进 2 秒")
        self.hud_fwd2_btn.setStyleSheet(seek_btn_qss)
        self.hud_fwd2_btn.clicked.connect(lambda: self.seek_relative_requested.emit(2.0))
        self.hud_fwd2_btn.hide()
        hud_top.addWidget(self.hud_fwd2_btn)

        self.hud_theme_btn = VectorIconButton(
            kind="sun",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_theme_btn.setToolTip("切换暖米纸背景")
        self.hud_theme_btn.clicked.connect(self._toggle_hud_theme)
        hud_top.addWidget(self.hud_theme_btn)

        self.hud_pin_btn = VectorIconButton(
            kind="pin",
            color="#F9F8F6",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_pin_btn.setToolTip("归位：自动贴合字幕位置（拖动字幕舱后可点此恢复）")
        self.hud_pin_btn.clicked.connect(self._toggle_auto_follow)
        hud_top.addWidget(self.hud_pin_btn)

        self.hud_pause_btn = VectorIconButton(
            kind="pause_play",
            color="#F9F8F6",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_pause_btn.setToolTip("点词暂停：查词自动暂停视频（已开启）")
        self.hud_pause_btn.clicked.connect(self._toggle_auto_pause)
        hud_top.addWidget(self.hud_pause_btn)

        self.hud_ocr_btn = VectorIconButton(
            kind="reticle",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_ocr_btn.setToolTip("框选字幕区域（强制屏幕 OCR，适用 PPT/本地视频；右键清除选区）")
        self.hud_ocr_btn.clicked.connect(self._open_custom_region_selector)
        self.hud_ocr_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.hud_ocr_btn.customContextMenuRequested.connect(lambda _pt: self._clear_custom_region_ui())
        hud_top.addWidget(self.hud_ocr_btn)

        self.hud_link_btn = VectorIconButton(
            kind="link",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
            parent=self.hud_top_widget,
        )
        self.hud_link_btn.setToolTip("链接：载入 YouTube 或 Bilibili 完整字幕")
        self.hud_link_btn.clicked.connect(self._open_link_bar_in_console)
        self.hud_link_btn.hide()

        self.hud_expand_btn = VectorIconButton(
            kind="dock",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_expand_btn.setToolTip("字幕台：展开独立双语滚动字幕台")
        self.hud_expand_btn.clicked.connect(lambda: self.set_view_mode(MODE_CONSOLE_DRAWER))
        hud_top.addWidget(self.hud_expand_btn)

        self.hud_style_btn = VectorIconButton(
            kind="style",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(249,248,246,18)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_style_btn.setToolTip("样式：调节字号、字重、透明度、语言、宽度与 B站扫码")
        self.hud_style_btn.clicked.connect(self._open_style_popover)
        hud_top.addWidget(self.hud_style_btn)

        self.hud_close_btn = VectorIconButton(
            kind="close",
            color="#D1D5DB",
            hover_color="#F9F8F6",
            hover_bg="rgba(129,109,112,180)",
            size=22,
            radius=0,
            label="",
            bg_color="",
        )
        self.hud_close_btn.setToolTip("关闭双语字幕")
        self.hud_close_btn.clicked.connect(self.close)
        hud_top.addWidget(self.hud_close_btn)

        self.hud_top_widget.hide()
        hud_vbox.addWidget(self.hud_top_widget)
        hud_vbox.addStretch(1)

        # 轻量化切源瞬态提示条（日常观影顶栏隐藏时也可在 2 秒内直观感知 YouTube / B站 / OCR 切换）
        self.hud_notice_label = QLabel("")
        self.hud_notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hud_notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_notice_label.setFont(_make_ui_font(8.5, weight=QFont.Weight.Medium))
        self.hud_notice_label.setStyleSheet(
            "color: rgba(249, 248, 246, 210); font-size: 11px; letter-spacing: 0.4px; "
            "background: transparent; border: none; padding-bottom: 1px;"
        )
        self.hud_notice_label.hide()
        hud_vbox.addWidget(self.hud_notice_label)

        self.hud_en_widget = InteractiveSubtitleText(
            font_size_pt=float(DEFAULT_HUD_SETTINGS["font_size_pt"]),
            font_weight=QFont.Weight.Normal,
            text_color="#9CA3AF",
            hover_text_color="#F9F8F6",
            hover_bg_color="rgba(129, 109, 112, 255)",
            align_center=True,
            serif=True,
        )
        self.hud_en_widget.set_text("等待字幕")
        self.hud_en_widget.word_clicked.connect(self._on_hud_word_clicked)
        hud_vbox.addWidget(self.hud_en_widget)

        self.hud_cn_label = _ShadowLabel("")
        self.hud_cn_label.setWordWrap(True)
        self.hud_cn_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hud_cn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_cn_label.setContentsMargins(2, 1, 2, 2)
        self.hud_cn_label.setFont(_make_ui_font(10.0, weight=QFont.Weight.Normal))
        self.hud_cn_label.setStyleSheet(
            "color: #A1A1AA; font-size: 13.5px; font-weight: 400; background: transparent; border: none;"
        )
        self.hud_cn_label.hide()
        hud_vbox.addWidget(self.hud_cn_label)
        hud_vbox.addStretch(1)

        self._root_layout.addWidget(self.hud_frame)

        # ==================== 2. 模式 B：独立双语滚动字幕台 (Editorial Console) ====================
        self.console_frame = QFrame()
        self.console_frame.setObjectName("SubtitleConsoleFrame")
        self.console_frame.setStyleSheet("QFrame#SubtitleConsoleFrame { background: transparent; border: none; }")
        con_vbox = QVBoxLayout(self.console_frame)
        con_vbox.setContentsMargins(24, 20, 24, 16)
        con_vbox.setSpacing(10)

        # 刊头栏（衬线标题 "字幕台" + 右侧 4 个极简 1px 线框图标）
        con_top = QHBoxLayout()
        con_top.setSpacing(8)

        self.con_title = QLabel("字幕台")
        self.con_title.setFont(_make_ui_font(20.0, weight=QFont.Weight.Normal, serif=True))
        self.con_title.setStyleSheet(
            "color: #1C1C1C; background: transparent; border: none;"
        )
        con_top.addWidget(self.con_title)

        self.con_mode_badge = QLabel("", self.console_frame)
        self.con_mode_badge.setFont(_make_ui_font(8.8))
        self.con_mode_badge.setStyleSheet(
            "color: #6B7280; background: transparent; border: none; padding: 4px 6px 0px 6px; font-size: 11px; font-weight: 400;"
        )
        self.con_mode_badge.hide()
        con_top.addStretch(1)

        self.con_pin_btn = VectorIconButton(
            kind="pin",
            color=LEXICON_THEME["text"] if self._auto_follow else "#6B7280",
            hover_color=LEXICON_THEME["mauve"] if self._auto_follow else "#6B7280",
            hover_bg=LEXICON_THEME["hover_bg"],
            size=24,
            radius=0,
            label="",
            bg_color="",
        )
        self.con_pin_btn.setToolTip("归位：自动贴合字幕位置")
        self.con_pin_btn.clicked.connect(self._toggle_auto_follow)
        con_top.addWidget(self.con_pin_btn)

        self.con_pause_btn = VectorIconButton(
            kind="pause_play",
            color=LEXICON_THEME["text"] if self._auto_pause else "#6B7280",
            hover_color=LEXICON_THEME["mauve"] if self._auto_pause else "#6B7280",
            hover_bg=LEXICON_THEME["hover_bg"],
            size=24,
            radius=0,
            label="",
            bg_color="",
        )
        self.con_pause_btn.setToolTip("点词暂停：查词自动暂停视频（已开启）")
        self.con_pause_btn.clicked.connect(self._toggle_auto_pause)
        con_top.addWidget(self.con_pause_btn)

        self.con_inplace_btn = VectorIconButton(
            kind="subtitle",
            color="#6B7280",
            hover_color=LEXICON_THEME["text"],
            hover_bg=LEXICON_THEME["hover_bg"],
            size=24,
            radius=0,
            label="",
            bg_color="",
        )
        self.con_inplace_btn.setToolTip("悬浮条：切回悬浮字幕条")
        self.con_inplace_btn.clicked.connect(lambda: self.set_view_mode(MODE_INPLACE_HUD))
        con_top.addWidget(self.con_inplace_btn)

        self.con_close_btn = VectorIconButton(
            kind="close",
            color="#1C1C1C",
            hover_color=LEXICON_THEME["mauve"],
            hover_bg=LEXICON_THEME["hover_neutral"],
            size=24,
            radius=0,
            label="",
            bg_color="",
        )
        self.con_close_btn.setToolTip("关闭双语字幕")
        self.con_close_btn.clicked.connect(self.close)
        con_top.addWidget(self.con_close_btn)
        con_vbox.addLayout(con_top)

        # 兼容属性保留（测试引用，界面收敛至次级下划线文字链接与折叠栏）
        self.con_link_btn = VectorIconButton(kind="link", size=20, radius=0, label="", bg_color="", parent=self.console_frame)
        self.con_link_btn.clicked.connect(self._toggle_link_bar)
        self.con_link_btn.hide()
        self.con_bili_btn = VectorIconButton(kind="qrcode", size=20, radius=0, label="", bg_color="", parent=self.console_frame)
        self.con_bili_btn.clicked.connect(self._open_bilibili_login)
        self.con_bili_btn.hide()
        self.con_clear_btn = VectorIconButton(kind="trash", size=20, radius=0, label="", bg_color="", parent=self.console_frame)
        self.con_clear_btn.clicked.connect(self._on_clear_clicked)
        self.con_clear_btn.hide()

        # 次级下划线控制栏（B站: Gekit / OCR / 链接 | -2s / +2s）
        con_sub_bar = QHBoxLayout()
        con_sub_bar.setContentsMargins(0, 0, 0, 2)
        con_sub_bar.setSpacing(14)

        sub_link_qss = (
            "QPushButton { background: transparent; color: #6B7280; border: none; "
            "border-bottom: 1px solid rgba(28,28,28,76); border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 400; }"
            "QPushButton:hover { color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }"
        )
        sub_link_muted_qss = (
            "QPushButton { background: transparent; color: #9CA3AF; border: none; "
            "border-bottom: 1px solid rgba(28,28,28,45); border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 400; }"
            "QPushButton:hover { color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }"
        )
        self.con_bili_link = QPushButton("B站扫码")
        self.con_bili_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_bili_link.setToolTip("B站账号授权与扫码登录")
        self.con_bili_link.setStyleSheet(sub_link_qss)
        self.con_bili_link.clicked.connect(self._open_bilibili_login)
        con_sub_bar.addWidget(self.con_bili_link)

        self.con_url_link = QPushButton("链接")
        self.con_url_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_url_link.setToolTip("展开视频链接输入与清空记录")
        self.con_url_link.setStyleSheet(sub_link_muted_qss)
        self.con_url_link.clicked.connect(self._toggle_link_bar)
        con_sub_bar.addWidget(self.con_url_link)

        self.con_ocr_link = QPushButton("")
        self.con_ocr_link.hide()

        self.con_region_link = QPushButton("框选字幕")
        self.con_region_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_region_link.setToolTip("框选画面字幕区域定向 OCR（适用 PPT 嵌视频/本地视频，右键重置）")
        self.con_region_link.setStyleSheet(sub_link_muted_qss)
        self.con_region_link.clicked.connect(self._open_custom_region_selector)
        self.con_region_link.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.con_region_link.customContextMenuRequested.connect(lambda _pt: self._clear_custom_region_ui())
        con_sub_bar.addWidget(self.con_region_link)

        self.con_export_link = QPushButton("导出笔记")
        self.con_export_link.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_export_link.setToolTip("一键导出当前整节双语字幕笔记（Markdown / TXT）")
        self.con_export_link.setStyleSheet(sub_link_muted_qss)
        self.con_export_link.clicked.connect(self._export_bilingual_notes)
        con_sub_bar.addWidget(self.con_export_link)

        con_sub_bar.addStretch(1)

        con_seek_qss = (
            "QPushButton { background: transparent; color: #6B7280; border: none; "
            "border-bottom: 1px solid rgba(28,28,28,70); border-radius: 0px; padding: 1px 2px; font-size: 11px; font-weight: 400; letter-spacing: 0.6px; }"
            "QPushButton:hover { color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }"
        )
        self.con_back2_btn = QPushButton("-2s")
        self.con_back2_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_back2_btn.setToolTip("字幕时间轴后退 2 秒")
        self.con_back2_btn.setStyleSheet(con_seek_qss)
        self.con_back2_btn.clicked.connect(lambda: self.seek_relative_requested.emit(-2.0))
        con_sub_bar.addWidget(self.con_back2_btn)

        self.con_fwd2_btn = QPushButton("+2s")
        self.con_fwd2_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.con_fwd2_btn.setToolTip("字幕时间轴前进 2 秒")
        self.con_fwd2_btn.setStyleSheet(con_seek_qss)
        self.con_fwd2_btn.clicked.connect(lambda: self.seek_relative_requested.emit(2.0))
        con_sub_bar.addWidget(self.con_fwd2_btn)
        con_vbox.addLayout(con_sub_bar)

        # 可折叠 YouTube / Bilibili 链接输入栏 (Editorial 直角发丝线风格)
        self.link_bar = QFrame()
        self.link_bar.setObjectName("SubtitleLinkBar")
        self.link_bar.setStyleSheet(
            "#SubtitleLinkBar { background: transparent; border: none; border-top: 1px solid rgba(28,28,28,26); padding-top: 6px; }"
        )
        lb_layout = QHBoxLayout(self.link_bar)
        lb_layout.setContentsMargins(0, 6, 0, 2)
        lb_layout.setSpacing(10)

        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("YouTube 或 B站链接 / BV号...")
        self.link_input.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {LEXICON_THEME['text']}; border: none; "
            f"border-bottom: 1px solid rgba(28,28,28,102); border-radius: 0px; padding: 4px 2px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-bottom: 1.5px solid #1C1C1C; }}"
        )
        self.link_input.returnPressed.connect(self._submit_link)
        lb_layout.addWidget(self.link_input, stretch=1)

        self.link_go_btn = QPushButton("加载")
        self.link_go_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.link_go_btn.setStyleSheet(
            "QPushButton { background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; "
            "border-radius: 0px; padding: 4px 12px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { background: #F9F8F6; color: #1C1C1C; }"
        )
        self.link_go_btn.clicked.connect(self._submit_link)
        lb_layout.addWidget(self.link_go_btn)

        sec_btn_qss = (
            "QPushButton { background: transparent; color: #6B7280; border: none; "
            "border-bottom: 1px solid rgba(28,28,28,76); border-radius: 0px; padding: 2px 1px; font-size: 11.5px; font-weight: 500; }"
            "QPushButton:hover { color: #816D70; border-bottom: 1px solid #816D70; }"
        )
        self.link_clear_btn = QPushButton("清空")
        self.link_clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.link_clear_btn.setToolTip("清空当前字幕历史记录")
        self.link_clear_btn.setStyleSheet(sec_btn_qss)
        self.link_clear_btn.clicked.connect(self._on_clear_clicked)
        lb_layout.addWidget(self.link_clear_btn)

        self.link_bili_btn = QPushButton("B站扫码", self.link_bar)
        self.link_bili_btn.hide()
        self.link_ocr_btn = QPushButton("OCR", self.link_bar)
        self.link_ocr_btn.hide()

        self.link_bar.hide()
        con_vbox.addWidget(self.link_bar)

        div_top = QFrame()
        div_top.setFixedHeight(1)
        div_top.setStyleSheet(f"background: {LEXICON_THEME['divider']}; border: none;")
        con_vbox.addWidget(div_top)

        # 顶部 Pull Quote 实时焦点区（紧贴刊头横线下方：左侧 2px 陈玫竖脊与左引号 + Georgia 斜体）
        self.live_focus_box = EditorialPullQuoteFrame(top_pad=10.0)
        self.live_focus_box.setObjectName("LiveFocusBox")
        self.live_focus_box.setStyleSheet(
            "#LiveFocusBox { background: transparent; border: none; border-bottom: 1px solid rgba(28,28,28,26); border-radius: 0px; }"
        )
        lf_vbox = QVBoxLayout(self.live_focus_box)
        lf_vbox.setContentsMargins(32, 10, 8, 12)
        lf_vbox.setSpacing(6)

        self.con_active_en = InteractiveSubtitleText(
            font_size_pt=12.0,
            font_weight=QFont.Weight.Normal,
            text_color=LEXICON_THEME["text"],
            hover_text_color=LEXICON_THEME["text"],
            hover_bg_color="rgba(129, 109, 112, 255)",
            align_center=False,
            serif=True,
            italic=True,
        )
        self.con_active_en.set_text("等待字幕")
        self.con_active_en.word_clicked.connect(self._on_hud_word_clicked)
        lf_vbox.addWidget(self.con_active_en)

        self.con_active_cn = QLabel("")
        self.con_active_cn.setWordWrap(True)
        self.con_active_cn.setTextFormat(Qt.TextFormat.PlainText)
        self.con_active_cn.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 12.5px; background: transparent; border: none;"
        )
        self.con_active_cn.hide()
        lf_vbox.addWidget(self.con_active_cn)
        con_vbox.addWidget(self.live_focus_box)

        # 中部历史双语字幕滚屏流 (Editorial Hairline Cards + 极细 Ink 滚动条)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(editorial_scrollbar_qss(LEXICON_THEME["bg"], width=4))
        self.scroll.verticalScrollBar().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("SubtitleScrollContent")
        self.scroll_content.setStyleSheet(f"#SubtitleScrollContent {{ background: {LEXICON_THEME['bg']}; border: none; }}")
        self.history_layout = QVBoxLayout(self.scroll_content)
        self.history_layout.setContentsMargins(0, 2, 4, 2)
        self.history_layout.setSpacing(0)

        self.empty_hint = QLabel("暂无历史字幕")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 12px; padding: 36px 18px; background: transparent; border: none;"
        )
        self.history_layout.addWidget(self.empty_hint)
        self.history_layout.addStretch(1)

        self.scroll.setWidget(self.scroll_content)
        enhance_scroll_area(self.scroll, top_feather=14, bottom_feather=14, feather_color=LEXICON_THEME["bg"])
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        if self.scroll.viewport() is not None:
            self.scroll.viewport().installEventFilter(self)
        con_vbox.addWidget(self.scroll, stretch=1)

        self._root_layout.addWidget(self.console_frame)
        self.console_frame.hide()
        self.hud_frame.show()
        self.setMinimumSize(300, 42)
        self._hud_idle_palette = True
        self._refresh_bili_login_btns()

    def _apply_initial_geometry(self):
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        preset_w = HUD_WIDTH_PRESETS.get(self._width_preset, 780)
        w = max(540, min(avail.width() - 16, min(self._hud_size.width(), preset_w) if self._width_preset == "narrow" else max(self._hud_size.width(), min(preset_w, 640))))
        need_h = self._measure_hud_height_for_width(w)
        h = max(self._hud_size.height(), need_h)
        if not self._auto_follow and self._hud_user_pos is not None:
            x = max(avail.left() + 6, min(avail.right() - w - 6, self._hud_user_pos.x()))
            y = max(avail.top() + 6, min(avail.bottom() - h - 6, self._hud_user_pos.y()))
        else:
            x = avail.center().x() - w // 2
            y = avail.bottom() - h - 54
        self.setGeometry(x, y, w, h)

    def _ensure_on_screen(self):
        screen = QApplication.screenAt(self.frameGeometry().center()) or self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        w = min(self.width(), max(300, avail.width() - 16))
        h = min(self.height(), max(42, avail.height() - 16))
        x = max(avail.left() + 6, min(avail.right() - w - 6, self.x()))
        y = max(avail.top() + 6, min(avail.bottom() - h - 6, self.y()))
        self.setGeometry(x, y, w, h)

    def _apply_chrome_for_mode(self):
        # 启用非分层 DWM 每像素 Alpha 合成，并禁用 Win11 DWM 外框圆角光晕（由 paintEvent 自绘边线与透明背景）
        if not shiboken6.isValid(self):
            return
        if getattr(self, "_use_dwm_glass", False):
            apply_dwm_per_pixel_alpha(self)
        if not supports_win11_native_chrome() or _dwmapi is None:
            return
        try:
            hwnd = int(self.winId())
            if hwnd:
                pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND
                _dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(33), ctypes.byref(pref), wintypes.DWORD(ctypes.sizeof(pref)))
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_on_screen()
        self._apply_chrome_for_mode()
        # 无论原位字幕舱还是独立双语字幕台，始终对截屏隐形，彻底杜绝自我 OCR 死循环
        if self._capture_excluded:
            ok = apply_capture_exclusion(self, True)
            self._capture_exclusion_applied = ok
            if not ok and self._view_mode == MODE_INPLACE_HUD:
                self.set_view_mode(MODE_CONSOLE_DRAWER)
                self.hud_status_label.setText("当前系统不支持透视遮盖，已切换为独立字幕台")
            if ok:
                QTimer.singleShot(
                    0,
                    lambda: shiboken6.isValid(self)
                    and self.isVisible()
                    and self._force_hud_full_repaint(flush_dwm=not getattr(self, "_use_dwm_glass", False)),
                )
                QTimer.singleShot(
                    35,
                    lambda: shiboken6.isValid(self)
                    and self.isVisible()
                    and self._sample_and_apply_backdrop(force_repaint=True),
                )
        else:
            apply_capture_exclusion(self, False)
            self._capture_exclusion_applied = False
        self._sync_proximity_watcher()
        if self._view_mode == MODE_CONSOLE_DRAWER and self._hist_batch_data is not None and not self._hist_batch_timer.isActive():
            self._hist_batch_timer.start()
        enforce_window_topmost(self, activate=False)
        if hasattr(self, "_topmost_guard_timer") and not self._topmost_guard_timer.isActive():
            self._topmost_guard_timer.start()

    def _on_topmost_guard_tick(self):
        if not shiboken6.isValid(self) or not self.isVisible():
            return
        if (
            self._view_mode != MODE_INPLACE_HUD
            or getattr(self, "_dwm_flushing", False)
            or self._dragging
            or self._resizing
            or (self._style_popover is not None and self._style_popover.isVisible())
            or (self._bili_login_dialog is not None and self._bili_login_dialog.isVisible())
        ):
            return
        restore_topmost_if_needed(self, force=False)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WinIdChange and shiboken6.isValid(self):
            self._apply_chrome_for_mode()
            if self.isVisible() and self._capture_excluded:
                self._capture_exclusion_applied = apply_capture_exclusion(self, True)
            if self.isVisible():
                enforce_window_topmost(self, activate=False)

    def hideEvent(self, event):
        self._hud_anim_timer.stop()
        self._hud_theme_timer.stop()
        self._hover_hide_timer.stop()
        self._hud_top_fade_timer.stop()
        self._dwm_post_paint_timer.stop()
        self._proximity_timer.stop()
        self._backdrop_timer.stop()
        self._hist_batch_timer.stop()
        self._notice_timer.stop()
        if hasattr(self, "_topmost_guard_timer"):
            self._topmost_guard_timer.stop()
        if hasattr(self, "hud_notice_label"):
            self.hud_notice_label.setText("")
            self.hud_notice_label.hide()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._hud_anim_timer.stop()
        self._hud_theme_timer.stop()
        self._hover_hide_timer.stop()
        self._hud_top_fade_timer.stop()
        self._dwm_post_paint_timer.stop()
        self._proximity_timer.stop()
        self._backdrop_timer.stop()
        self._hist_batch_timer.stop()
        self._hist_batch_data = None
        self._notice_timer.stop()
        if hasattr(self, "_topmost_guard_timer"):
            self._topmost_guard_timer.stop()
        if hasattr(self, "hud_notice_label"):
            self.hud_notice_label.setText("")
            self.hud_notice_label.hide()
        self._hud_hovered = False
        self._hud_top_reveal = 0.0
        self._selecting_region = False
        if self._style_popover is not None:
            self._style_popover.hide()
        if self._region_selector is not None:
            self._region_selector.hide()
        if getattr(self, "_bili_login_dialog", None) is not None:
            self._bili_login_dialog.close()
        self._playback_ctrl.reset_state()
        self.closed.emit()
        super().closeEvent(event)

    def _is_low_opacity_hud(self) -> bool:
        return self._view_mode == MODE_INPLACE_HUD and self._bg_opacity_pct < 28

    def _is_adaptive_contrast_active(self) -> bool:
        return self._view_mode == MODE_INPLACE_HUD and self._bg_opacity_pct < 60

    def _sync_proximity_watcher(self):
        if self.isVisible() and self._is_low_opacity_hud():
            if not self._proximity_timer.isActive():
                self._proximity_timer.start()
        else:
            self._proximity_timer.stop()
        if self.isVisible() and self._is_adaptive_contrast_active():
            if not self._backdrop_timer.isActive():
                self._backdrop_timer.start()
        else:
            self._backdrop_timer.stop()
            if not self._is_adaptive_contrast_active():
                self._auto_fg_is_light = None
                self._auto_halo_strength = 0.0

    def _compute_text_sample_local_rect(self) -> QRect:
        """计算紧贴当前中英文字幕实际排版区域（排除 wide 模式下左右空白翼区）的采样矩形。"""
        w = max(1, self.width())
        h = max(1, self.height())
        top_vis = hasattr(self, "hud_top_widget") and self.hud_top_widget.isVisible()
        fallback = QRect(
            int(w * 0.14),
            int(h * (0.44 if top_vis else 0.18)),
            max(24, int(w * 0.72)),
            max(8, int(h * (0.46 if top_vis else 0.70))),
        )
        boxes: List[QRect] = []
        if hasattr(self, "hud_en_widget") and self.hud_en_widget.isVisible() and self.hud_en_widget.height() > 2:
            en_tl = self.hud_en_widget.mapTo(self, QPoint(0, 0))
            en_box = QRect(en_tl, self.hud_en_widget.size())
            items = self.hud_en_widget.get_word_items()
            if items:
                x1 = min(it["rect"].left() for it in items)
                x2 = max(it["rect"].right() for it in items)
                lx1 = max(en_box.left(), int(en_tl.x() + x1) - 14)
                lx2 = min(en_box.right(), int(en_tl.x() + x2) + 14)
                if lx2 > lx1:
                    en_box = QRect(lx1, en_box.top(), lx2 - lx1, en_box.height())
            boxes.append(en_box)

        if (
            hasattr(self, "hud_cn_label")
            and self.hud_cn_label.isVisible()
            and self.hud_cn_label.height() > 2
            and self.hud_cn_label.text().strip()
        ):
            cn_tl = self.hud_cn_label.mapTo(self, QPoint(0, 0))
            cn_box = QRect(cn_tl, self.hud_cn_label.size())
            fm = QFontMetricsF(self.hud_cn_label.font())
            br = fm.boundingRect(
                QRectF(0, 0, max(40.0, float(cn_box.width())), 400.0),
                int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter),
                self.hud_cn_label.text(),
            )
            span_w = min(cn_box.width(), max(24, int(round(br.width())) + 28))
            cx = cn_box.center().x()
            boxes.append(QRect(cx - span_w // 2, cn_box.top(), span_w, cn_box.height()))

        if not boxes:
            return fallback
        u = boxes[0]
        for b in boxes[1:]:
            u = u.united(b)

        min_w = int(round(w * 0.30))
        max_x1 = int(round(w * 0.07))
        max_x2 = int(round(w * 0.93))
        x1 = max(max_x1, u.left())
        x2 = min(max_x2, u.right())
        if x2 - x1 < min_w:
            cx = max(max_x1 + min_w // 2, min(max_x2 - min_w // 2, (x1 + x2) // 2))
            x1 = max(max_x1, cx - min_w // 2)
            x2 = min(max_x2, x1 + min_w)
        y1 = max(2, u.top() - 2)
        y2 = min(h - 2, u.bottom() + 2)
        if x2 - x1 <= 4 or y2 - y1 <= 2:
            return fallback
        return QRect(x1, y1, x2 - x1, y2 - y1)

    def _sample_underlying_bg_rgb(self) -> Optional[Any]:
        if not (self.isVisible() and self._capture_exclusion_applied and sys.platform == "win32"):
            return None
        try:
            hwnd = int(self.winId())
            rc = wintypes.RECT()
            if not hwnd or not _u32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return None
            rw = int(rc.right - rc.left)
            rh = int(rc.bottom - rc.top)
            if rw <= 40 or rh <= 24:
                return None
            local_r = self._compute_text_sample_local_rect()
            sx_scale = rw / max(1, self.width())
            sy_scale = rh / max(1, self.height())
            sx = max(int(rc.left), min(int(rc.right) - 8, int(round(rc.left + local_r.x() * sx_scale))))
            sy = max(int(rc.top), min(int(rc.bottom) - 4, int(round(rc.top + local_r.y() * sy_scale))))
            sw = max(24, min(int(rc.right) - sx, int(round(local_r.width() * sx_scale))))
            sh = max(8, min(int(rc.bottom) - sy, int(round(local_r.height() * sy_scale))))
            return sample_screen_region_rgb(sx, sy, sw, sh)
        except Exception:
            return None

    def _sample_underlying_bg_luma(self) -> Optional[float]:
        samples = self._sample_underlying_bg_rgb()
        if samples is None:
            return None
        if _np is not None and hasattr(samples, "shape"):
            if samples.size == 0:
                return None
            luma = 0.299 * samples[:, 0] + 0.587 * samples[:, 1] + 0.114 * samples[:, 2]
            return float(_np.median(luma))
        lumas = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in samples)
        return float(lumas[len(lumas) // 2]) if lumas else None

    def _sample_and_apply_backdrop(self, force_repaint: bool = True):
        if not self._is_adaptive_contrast_active():
            return
        rgb_samples = self._sample_underlying_bg_rgb()
        if rgb_samples is None:
            return
        alpha = max(0.0, min(1.0, float(self._bg_opacity_pct) / 100.0))
        hud_rgb = (249.0, 248.0, 246.0) if self._hud_theme == "light" else (28.0, 28.0, 28.0)
        nxt_fg, nxt_halo = analyze_backdrop_contrast(
            rgb_samples,
            hud_rgb,
            alpha,
            prev_fg_is_light=self._auto_fg_is_light,
            prev_halo_strength=self._auto_halo_strength,
        )
        halo_changed = (
            abs(nxt_halo - self._auto_halo_strength) >= 0.04
            or (nxt_halo > 0.02) != (self._auto_halo_strength > 0.02)
        )
        if nxt_fg != self._auto_fg_is_light or halo_changed:
            self._auto_fg_is_light = nxt_fg
            self._auto_halo_strength = nxt_halo
            use_halo = nxt_halo > 0.02
            self.hud_en_widget.set_shadow_mode(use_halo, strength=nxt_halo, dark_halo=nxt_fg)
            self.hud_cn_label.set_transparent_bg(use_halo, strength=nxt_halo, dark_halo=nxt_fg)
            self._apply_hud_theme_chrome()
            self._sync_hud_text_palette()
            if force_repaint:
                self._force_hud_full_repaint(flush_dwm=True)

    def _on_backdrop_poll_tick(self):
        if not (self.isVisible() and self._is_adaptive_contrast_active()):
            self._backdrop_timer.stop()
            return
        self._sample_and_apply_backdrop(force_repaint=True)

    def _hud_proximity_rect(self, expand: bool = False) -> QRect:
        pad = 36 if expand else 28
        return self.frameGeometry().adjusted(-pad, -pad, pad, 26 if expand else 20)

    def _apply_hud_top_reveal(self):
        a = max(0.0, min(1.0, float(self._hud_top_reveal)))
        self.hud_top_widget.set_dock_style(a, show_dock=(self._bg_opacity_pct < 28))
        for btn in (
            self.hud_theme_btn,
            self.hud_pin_btn,
            self.hud_pause_btn,
            self.hud_ocr_btn,
            self.hud_link_btn,
            self.hud_expand_btn,
            self.hud_style_btn,
            self.hud_close_btn,
        ):
            btn.set_opacity(a)

    def _show_hud_top(self, immediate: bool = False):
        self._hover_hide_timer.stop()
        if self._view_mode != MODE_INPLACE_HUD:
            return
        was_hidden = not self.hud_top_widget.isVisible()
        self._hud_hovered = True
        if was_hidden and not immediate:
            self._hud_top_reveal = 0.0
            self._apply_hud_top_reveal()
        if was_hidden:
            self.hud_top_widget.show()
            self._sync_hud_hover_geometry()
        if immediate:
            self._hud_top_fade_timer.stop()
            self._hud_top_reveal = 1.0
            self._hud_top_fade_target = 1.0
            self._apply_hud_top_reveal()
            self._force_hud_full_repaint(flush_dwm=True)
            return
        if self._hud_top_fade_timer.isActive() and self._hud_top_fade_target >= 0.99:
            return
        if self._hud_top_reveal >= 0.99 and not was_hidden:
            return
        self._hud_top_fade_timer.stop()
        self._hud_top_fade_start = self._hud_top_reveal
        self._hud_top_fade_target = 1.0
        self._hud_top_fade_dur = 0.22
        self._hud_top_fade_t0 = time.monotonic()
        self._apply_hud_top_reveal()
        self._hud_top_fade_timer.start()

    def _check_low_opacity_proximity(self):
        if not (self.isVisible() and self._is_low_opacity_hud()):
            self._proximity_timer.stop()
            return
        pop_open = self._style_popover is not None and self._style_popover.isVisible()
        gpos = QCursor.pos()
        if pop_open or self._hud_hovered:
            if pop_open or self._hud_proximity_rect(expand=True).contains(gpos):
                if self._hud_top_fade_target < 0.5:
                    self._show_hud_top()
            elif not self._hover_hide_timer.isActive() and not (
                self._hud_top_fade_timer.isActive() and self._hud_top_fade_target <= 0.01
            ):
                self._hover_hide_timer.start()
        elif self._hud_proximity_rect(expand=False).contains(gpos):
            self._show_hud_top()

    def _hud_base_height(self) -> int:
        if self._view_mode != MODE_INPLACE_HUD:
            return self.height()
        return max(42, self.height() - getattr(self, "_dwm_pad_h", 0))

    def _sync_hud_hover_geometry(self):
        if self._view_mode != MODE_INPLACE_HUD:
            return
        self.hud_en_widget._relayout(max(120, self.width() - 60))
        self._hud_vbox.invalidate()
        self._hud_vbox.activate()
        self._root_layout.invalidate()
        self._root_layout.activate()
        need_h = self._measure_hud_height_for_width(self.width())
        base_h = self._hud_base_height()
        pad = getattr(self, "_dwm_pad_h", 0)
        if need_h > base_h or abs(base_h - need_h) >= 2:
            if self._last_follow_anchor is not None and self._auto_follow:
                anchor_bottom = self.y() + base_h
                new_y = max(4, anchor_bottom - need_h)
                self.setGeometry(self.x(), new_y, self.width(), need_h + pad)
            else:
                self.resize(self.width(), need_h + pad)
            if self._hud_user_pos is not None and not self._auto_follow:
                self._hud_size = QSize(self.width(), need_h)
            self._hud_vbox.activate()
            self._root_layout.activate()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._view_mode == MODE_INPLACE_HUD:
            self._show_hud_top()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._view_mode == MODE_INPLACE_HUD and self._hud_hovered:
            self._hover_hide_timer.start()

    def _do_hide_hud_top(self):
        if self._style_popover is not None and self._style_popover.isVisible():
            return
        if self._view_mode != MODE_INPLACE_HUD or not self._hud_hovered:
            return
        gpos = QCursor.pos()
        if self._is_low_opacity_hud():
            if self._hud_proximity_rect(expand=True).contains(gpos):
                return
        elif self.rect().contains(self.mapFromGlobal(gpos)):
            return
        if self._hud_top_fade_timer.isActive() and self._hud_top_fade_target <= 0.01:
            return
        self._hud_top_fade_timer.stop()
        self._hud_top_fade_start = self._hud_top_reveal
        self._hud_top_fade_target = 0.0
        self._hud_top_fade_dur = 0.30
        self._hud_top_fade_t0 = time.monotonic()
        self._hud_top_fade_timer.start()

    def _on_hud_top_fade_tick(self):
        elapsed = max(0.0, time.monotonic() - self._hud_top_fade_t0)
        raw = min(1.0, elapsed / max(0.01, self._hud_top_fade_dur))
        eased = raw * raw * (3.0 - 2.0 * raw)
        self._hud_top_reveal = self._hud_top_fade_start + (self._hud_top_fade_target - self._hud_top_fade_start) * eased
        self._apply_hud_top_reveal()
        self._force_hud_full_repaint(flush_dwm=True)
        if raw >= 1.0:
            self._hud_top_fade_timer.stop()
            self._hud_top_reveal = self._hud_top_fade_target
            self._apply_hud_top_reveal()
            if self._hud_top_fade_target <= 0.01:
                self._hud_hovered = False
                self.hud_top_widget.hide()
                self._sync_hud_hover_geometry()
                self._force_hud_full_repaint(flush_dwm=True)

    def paintEvent(self, event):
        clip = event.rect()
        painter = QPainter(self)
        painter.setClipRect(clip)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.eraseRect(clip)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        active_frame = self.hud_frame if self._view_mode == MODE_INPLACE_HUD else self.console_frame
        if active_frame.isVisible():
            rect = QRectF(active_frame.geometry()).adjusted(0.75, 0.75, -0.75, -0.75)
            is_flushing = getattr(self, "_dwm_flushing", False) and getattr(self, "_dwm_flush_base_h", 0) > 20
            draw_rect = (
                QRectF(rect.left(), rect.top(), rect.width(), max(1.0, float(self._dwm_flush_base_h - 12) - 1.5))
                if is_flushing
                else rect
            )
            if self._view_mode == MODE_INPLACE_HUD:
                bg_alpha = max(0, min(255, int(round(self._bg_opacity_pct / 100.0 * 255.0))))
                blend = max(0.0, min(1.0, float(getattr(self, "_hud_theme_blend", 1.0 if self._hud_theme == "light" else 0.0))))
                # 低透明度区间 (<35%) 随背景同步平滑淡出边框，0% 纯字模式保持完全无框全透明
                border_alpha = 0 if bg_alpha <= 4 else min(255, int(round(bg_alpha * (255.0 / 90.0))))
                painter.setPen(QPen(QColor(28, 28, 28, border_alpha), 1.5) if border_alpha > 0 else Qt.PenStyle.NoPen)
                painter.setBrush(self._lerp_color(QColor(28, 28, 28, bg_alpha), QColor(249, 248, 246, bg_alpha), blend))
                painter.drawRect(draw_rect)
            else:
                painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
                painter.setBrush(QColor(249, 248, 246, 255))
                painter.drawRect(draw_rect)
        painter.end()
        super().paintEvent(event)
        if not getattr(self, "_dwm_flushing", False):
            cur_sz = self.size()
            same_size = (cur_sz == getattr(self, "_last_painted_size", QSize()))
            force_dirty = bool(getattr(self, "_hud_needs_dwm_flush", False))
            self._last_painted_size = cur_sz
            if (
                (same_size or force_dirty)
                and self.isVisible()
                and sys.platform == "win32"
                and self._view_mode == MODE_INPLACE_HUD
                and getattr(self, "_capture_exclusion_applied", False)
                and self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            ):
                self._hud_needs_dwm_flush = False
                elapsed_ms = (time.monotonic() - getattr(self, "_last_dwm_commit_ts", 0.0)) * 1000.0
                delay_ms = 12 if elapsed_ms >= 22.0 else max(10, int(round(24.0 - elapsed_ms)))
                if not self._dwm_post_paint_timer.isActive():
                    self._dwm_post_paint_timer.start(delay_ms)

    # ---------------- 模式切换与状态控制 ----------------

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def is_capture_excluded(self) -> bool:
        return self._capture_exclusion_applied

    @property
    def auto_follow(self) -> bool:
        return self._auto_follow

    @property
    def auto_pause(self) -> bool:
        return self._auto_pause

    def set_view_mode(self, mode: str):
        if mode not in (MODE_INPLACE_HUD, MODE_CONSOLE_DRAWER):
            return
        if self._view_mode == mode:
            return
        self._hud_anim_timer.stop()
        self._hud_top_fade_timer.stop()
        self._hover_hide_timer.stop()
        self._hud_theme_timer.stop()
        self._dwm_post_paint_timer.stop()
        self._hist_batch_timer.stop()
        self._hist_batch_data = None
        if self._view_mode == MODE_INPLACE_HUD:
            self._hud_size = QSize(self.width(), max(42, self.height() - getattr(self, "_dwm_pad_h", 0)))
            if not self._auto_follow:
                self._hud_user_pos = self.pos()
        else:
            self._console_size = self.size()
            self._console_user_pos = self.pos()

        self._view_mode = mode
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

        if mode == MODE_INPLACE_HUD:
            self.console_frame.hide()
            self.hud_frame.show()
            self._hud_hovered = False
            self.hud_top_widget.hide()
            self._root_layout.activate()
            self.setMinimumSize(300, 42)
            self._last_follow_anchor = None
            target_sz = self._hud_size
            if self._hud_user_pos is not None and not self._auto_follow:
                pos = self._hud_user_pos
            else:
                pos = QPoint(avail.center().x() - target_sz.width() // 2, avail.bottom() - target_sz.height() - 54)
        else:
            self.hud_frame.hide()
            self.console_frame.show()
            self._dwm_pad_h = 0
            self._root_layout.setContentsMargins(6, 6, 6, 6)
            self._root_layout.activate()
            self.setMinimumSize(320, 240)
            target_sz = self._console_size
            if self._console_user_pos is not None:
                pos = self._console_user_pos
            else:
                pos = QPoint(avail.right() - target_sz.width() - 24, avail.center().y() - target_sz.height() // 2)
            pending_hist = getattr(self, "_pending_history_sync", None)
            if pending_hist is not None:
                self._pending_history_sync = None
                self._sync_history_rows(*pending_hist)

        x = max(avail.left() + 6, min(avail.right() - target_sz.width() - 6, pos.x()))
        y = max(avail.top() + 6, min(avail.bottom() - target_sz.height() - 6, pos.y()))
        self.setGeometry(x, y, target_sz.width(), target_sz.height())
        self._apply_chrome_for_mode()
        if self.isVisible() and self._capture_excluded:
            ok = apply_capture_exclusion(self, True)
            self._capture_exclusion_applied = ok
            if not ok and mode == MODE_INPLACE_HUD:
                self.set_view_mode(MODE_CONSOLE_DRAWER)
                return
        self.update()
        self._sync_proximity_watcher()

    @staticmethod
    def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
        t = max(0.0, min(1.0, float(t)))
        return QColor(
            int(round(c1.red() + (c2.red() - c1.red()) * t)),
            int(round(c1.green() + (c2.green() - c1.green()) * t)),
            int(round(c1.blue() + (c2.blue() - c1.blue()) * t)),
            int(round(c1.alpha() + (c2.alpha() - c1.alpha()) * t)),
        )

    def _dwm_commit_layered_surface(self):
        if not self.isVisible() or sys.platform != "win32" or getattr(self, "_dwm_flushing", False):
            return
        self._dwm_post_paint_timer.stop()
        try:
            # Win11 DWM 在 WA_TranslucentBackground + WDA_EXCLUDEFROMCAPTURE 组合下会缓存同尺寸 UpdateLayeredWindow 位图：
            # 若在同一同步调用内 h->h+1->h 切回原尺寸，VSync 采样时尺寸未变（鼠标停在字幕区固定高度时尤其明显）；
            # 改用 4 态持久透明底边奇偶步进 (0->1->2->3->0)，确保每次提交跨过 VSync 的 HWND 高度都不同，同时字幕内框零位移。
            if (
                self._view_mode == MODE_INPLACE_HUD
                and self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                and getattr(self, "_capture_exclusion_applied", False)
            ):
                cur_sz = self.size()
                if cur_sz.width() > 60 and cur_sz.height() > 40:
                    need_h = self._measure_hud_height_for_width(cur_sz.width())
                    base_h = max(need_h, cur_sz.height() - getattr(self, "_dwm_pad_h", 0))
                    next_pad = (getattr(self, "_dwm_pad_h", 0) + 1) & 3
                    if base_h + next_pad == cur_sz.height():
                        next_pad = (next_pad + 1) & 3
                    self._dwm_flushing = True
                    self._dwm_flush_base_h = base_h
                    self._dwm_pad_h = next_pad
                    try:
                        self._root_layout.setContentsMargins(6, 6, 6, 6 + next_pad)
                        self._hud_vbox.invalidate()
                        self._hud_vbox.activate()
                        self._root_layout.invalidate()
                        self._root_layout.activate()
                        self.resize(cur_sz.width(), base_h + next_pad)
                        self._hud_vbox.activate()
                        self._root_layout.activate()
                        if self.hud_en_widget.isVisible():
                            self.hud_en_widget.repaint()
                        if self.hud_cn_label.isVisible():
                            self.hud_cn_label.repaint()
                        self.repaint(self.rect())
                        if getattr(self, "_capture_exclusion_applied", False):
                            hwnd = int(self.winId())
                            if hwnd:
                                _u32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE)
                    finally:
                        self._dwm_flushing = False
                        self._dwm_flush_base_h = 0
                        self._last_painted_size = self.size()
                        self._last_dwm_commit_ts = time.monotonic()
            else:
                # 非分层 DWM 模式 (WS_EX_LAYERED=False) 下 Qt 通过 BitBlt 直接更新重定向上下文，无需 SetWindowPos 抖动
                if self.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
                    hwnd = int(self.winId())
                    if hwnd:
                        _u32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, wintypes.UINT(0x0017))
                self._last_dwm_commit_ts = time.monotonic()
        except Exception:
            pass

    def _flush_dwm_after_paint(self):
        self._hud_needs_dwm_flush = False
        if not self.isVisible() or getattr(self, "_dwm_flushing", False):
            return
        self._dwm_commit_layered_surface()

    def _force_hud_full_repaint(self, flush_dwm: bool = False):
        if self._view_mode == MODE_INPLACE_HUD:
            targets = (
                self.hud_frame,
                self.hud_top_widget,
                self.hud_en_widget,
                self.hud_cn_label,
                self.hud_theme_btn,
                self.hud_pin_btn,
                self.hud_pause_btn,
                self.hud_ocr_btn,
                self.hud_link_btn,
                self.hud_expand_btn,
                self.hud_style_btn,
                self.hud_close_btn,
                self.hud_back2_btn,
                self.hud_fwd2_btn,
                self.hud_status_label,
                self.hud_notice_label,
            )
        else:
            targets = (
                self.console_frame,
                self.con_pin_btn,
                self.con_pause_btn,
                self.con_inplace_btn,
                self.con_close_btn,
                self.con_bili_link,
                self.con_url_link,
                self.con_back2_btn,
                self.con_fwd2_btn,
                self.con_mode_badge,
                self.con_active_en,
                self.con_active_cn,
                self.live_focus_box,
            )
        for child in targets:
            child.update()
        self.update(self.rect())
        if self.isVisible():
            self.repaint(self.rect())
            for child in targets:
                if child.isVisible():
                    child.repaint()
            if flush_dwm:
                self._dwm_commit_layered_surface()

    def _on_hud_theme_anim_tick(self):
        elapsed = max(0.0, time.monotonic() - self._hud_theme_anim_t0)
        raw = min(1.0, elapsed / max(0.01, self._hud_theme_anim_dur))
        eased = raw * raw * (3.0 - 2.0 * raw)
        self._hud_theme_blend = self._hud_theme_anim_start + (self._hud_theme_anim_target - self._hud_theme_anim_start) * eased
        self._sync_hud_text_palette()
        self._force_hud_full_repaint(flush_dwm=True)
        if raw >= 1.0:
            self._hud_theme_timer.stop()
            self._hud_theme_blend = self._hud_theme_anim_target
            self._apply_hud_appearance(resize_now=False)
            self._force_hud_full_repaint(flush_dwm=True)

    @property
    def hud_theme(self) -> str:
        return self._hud_theme

    def _toggle_hud_theme(self):
        self._hover_hide_timer.stop()
        next_theme = "light" if self._hud_theme == "dark" else "dark"
        if next_theme == "light" and self._bg_opacity_pct < 22:
            self._bg_opacity_pct = 88
        self.set_hud_theme(next_theme, animate=True)
        self._save_hud_settings()

    def set_hud_theme(self, theme: str, animate: bool = False):
        if theme not in ("dark", "light"):
            return
        self._hud_theme = theme
        if theme == "light" and self._bg_opacity_pct < 22:
            self._bg_opacity_pct = 88
        target_blend = 1.0 if theme == "light" else 0.0
        if animate and self.isVisible() and self._view_mode == MODE_INPLACE_HUD and abs(self._hud_theme_blend - target_blend) > 0.01:
            self._hud_theme_timer.stop()
            self._hud_theme_anim_start = self._hud_theme_blend
            self._hud_theme_anim_target = target_blend
            self._hud_theme_anim_t0 = time.monotonic()
            self._apply_hud_theme_chrome()
            self._hud_theme_timer.start()
            self._on_hud_theme_anim_tick()
        else:
            self._hud_theme_timer.stop()
            self._hud_theme_blend = target_blend
            self._apply_hud_appearance(resize_now=False)
            self._force_hud_full_repaint(flush_dwm=True)
        if self._style_popover is not None:
            self._style_popover.sync_state(self.get_hud_settings())

    def _toggle_auto_follow(self):
        self._auto_follow = not self._auto_follow
        if self._auto_follow:
            self._hud_user_pos = None
            self._last_follow_anchor = None
        else:
            self._hud_user_pos = self.pos()
        self._update_pin_indicator()
        self._force_hud_full_repaint(flush_dwm=True)
        self._save_hud_settings()
        if self._style_popover is not None:
            self._style_popover.sync_state(self.get_hud_settings())

    def get_hud_settings(self) -> Dict[str, Any]:
        pos_val = [int(self._hud_user_pos.x()), int(self._hud_user_pos.y())] if self._hud_user_pos is not None else None
        return {
            "font_size_pt": round(float(self._font_size_pt), 1),
            "font_weight": self._font_weight,
            "bg_opacity_pct": int(self._bg_opacity_pct),
            "display_lang": self._display_lang,
            "width_preset": self._width_preset,
            "hud_theme": self._hud_theme,
            "auto_follow": bool(self._auto_follow),
            "user_pos": pos_val,
        }

    def _save_hud_settings(self):
        try:
            set_setting(HUD_SETTINGS_KEY, self.get_hud_settings())
        except Exception:
            pass

    def _load_hud_settings(self):
        raw = get_setting(HUD_SETTINGS_KEY, None)
        if not isinstance(raw, dict):
            raw = {}
        try:
            self._font_size_pt = max(10.0, min(22.0, float(raw.get("font_size_pt", DEFAULT_HUD_SETTINGS["font_size_pt"]))))
        except Exception:
            self._font_size_pt = float(DEFAULT_HUD_SETTINGS["font_size_pt"])
        fw = str(raw.get("font_weight", DEFAULT_HUD_SETTINGS["font_weight"]))
        self._font_weight = fw if fw in HUD_WEIGHT_CHOICES else "normal"
        try:
            self._bg_opacity_pct = max(0, min(100, int(raw.get("bg_opacity_pct", DEFAULT_HUD_SETTINGS["bg_opacity_pct"]))))
        except Exception:
            self._bg_opacity_pct = int(DEFAULT_HUD_SETTINGS["bg_opacity_pct"])
        lang = str(raw.get("display_lang", DEFAULT_HUD_SETTINGS["display_lang"]))
        self._display_lang = lang if lang in ("bilingual", "en_only", "cn_only") else "bilingual"
        wp = str(raw.get("width_preset", DEFAULT_HUD_SETTINGS["width_preset"]))
        self._width_preset = wp if wp in HUD_WIDTH_PRESETS else "medium"
        ht = str(raw.get("hud_theme", DEFAULT_HUD_SETTINGS["hud_theme"]))
        self._hud_theme = ht if ht in ("dark", "light") else "dark"
        self._hud_theme_timer.stop()
        self._hud_theme_blend = 1.0 if self._hud_theme == "light" else 0.0
        self._auto_follow = bool(raw.get("auto_follow", True))
        upos = raw.get("user_pos")
        if isinstance(upos, (list, tuple)) and len(upos) == 2 and not self._auto_follow:
            try:
                self._hud_user_pos = QPoint(int(upos[0]), int(upos[1]))
            except Exception:
                self._hud_user_pos = None
                self._auto_follow = True
        else:
            self._hud_user_pos = None
        self._update_pin_indicator()
        self._apply_hud_appearance(resize_now=False)

    def _reset_hud_settings(self):
        self._font_size_pt = float(DEFAULT_HUD_SETTINGS["font_size_pt"])
        self._font_weight = str(DEFAULT_HUD_SETTINGS["font_weight"])
        self._bg_opacity_pct = int(DEFAULT_HUD_SETTINGS["bg_opacity_pct"])
        self._display_lang = str(DEFAULT_HUD_SETTINGS["display_lang"])
        self._width_preset = str(DEFAULT_HUD_SETTINGS["width_preset"])
        self._hud_theme = str(DEFAULT_HUD_SETTINGS["hud_theme"])
        self._hud_theme_timer.stop()
        self._hud_theme_blend = 1.0 if self._hud_theme == "light" else 0.0
        self._auto_follow = True
        self._hud_user_pos = None
        self._last_follow_anchor = None
        self._update_pin_indicator()
        self._apply_hud_appearance(resize_now=True)
        self._apply_hud_width_preset()
        self._save_hud_settings()
        if self._style_popover is not None:
            self._style_popover.sync_state(self.get_hud_settings())

    def _is_dark_hud_foreground(self) -> bool:
        """低透明度 (<60%) 下按真实背后画面亮度自动反相；高透明度下遵循墨黑/暖米纸主题。"""
        if self._bg_opacity_pct < 60 and getattr(self, "_auto_fg_is_light", None) is not None:
            return bool(self._auto_fg_is_light)
        return self._hud_theme == "dark"

    def _sync_hud_text_palette(self):
        if self._bg_opacity_pct < 60 and getattr(self, "_auto_fg_is_light", None) is not None:
            blend = 0.0 if self._auto_fg_is_light else 1.0
        else:
            blend = max(0.0, min(1.0, float(getattr(self, "_hud_theme_blend", 0.0))))
        want_idle = getattr(self, "_hud_idle_palette", not bool(self._active_en))
        low_op = (self._bg_opacity_pct <= 50)
        en_dark = QColor(("#B4B8C0" if low_op else "#9CA3AF") if want_idle else "#FAF9F6")
        en_light = QColor("#6B7280" if want_idle else "#111111")
        en_col = self._lerp_color(en_dark, en_light, blend).name()
        hover_col = self._lerp_color(QColor("#FAF9F6"), QColor("#111111"), blend).name()
        cn_dark = QColor("#E4E2DC" if low_op else "#D8D6D0")
        cn_light = QColor("#23262D" if low_op else "#3A3D45")
        cn_col = self._lerp_color(cn_dark, cn_light, blend).name()
        ul_col = self._lerp_color(QColor("#C89EA3"), QColor(LEXICON_THEME["mauve"]), blend).name()
        self.hud_en_widget.set_palette_colors(en_col, hover_col, "rgba(129, 109, 112, 255)")
        self.hud_en_widget.set_underline_color(ul_col)
        cn_px = max(11, int(round(self._font_size_pt * 1.125)))
        if self._display_lang == "cn_only":
            cn_px += 2
        fw = getattr(self, "_font_weight", "normal")
        if fw == "light":
            cn_f = QFont()
            cn_f.setFamilies(["Microsoft YaHei UI Light", "Segoe UI Light", "Microsoft YaHei UI"])
            cn_f.setPixelSize(cn_px)
            cn_f.setWeight(QFont.Weight.Light)
            cn_css = "font-family: 'Microsoft YaHei UI Light', 'Segoe UI Light', 'Microsoft YaHei UI'; font-weight: 300;"
        elif fw == "bold":
            cn_f = _make_ui_font(10.0, weight=QFont.Weight.Bold)
            cn_f.setPixelSize(cn_px)
            cn_css = "font-weight: 700;"
        else:
            cn_f = _make_ui_font(10.0, weight=QFont.Weight.Normal)
            cn_f.setPixelSize(cn_px)
            cn_css = "font-weight: 400;"
        self.hud_cn_label.setFont(cn_f)
        self.hud_cn_label.set_text_color(cn_col)
        self.hud_cn_label.setStyleSheet(
            f"color: {cn_col}; font-size: {cn_px}px; {cn_css} background: transparent; border: none;"
        )

    def _apply_hud_theme_chrome(self):
        dark_fg = self._is_dark_hud_foreground() or (self._bg_opacity_pct < 28)
        is_paper = (self._hud_theme == "light")
        if hasattr(self, "hud_theme_btn"):
            self.hud_theme_btn.set_kind("moon" if is_paper else "sun")
            self.hud_theme_btn.setToolTip("切换墨黑背景" if is_paper else "切换暖米纸背景")

        if dark_fg:
            icon_idle = "#D1D5DB"
            icon_active = "#F9F8F6"
            icon_muted = "#9CA3AF"
            hover_bg = "rgba(249,248,246,18)"
            close_hover_bg = "rgba(129,109,112,180)"
            status_col = "rgba(249, 248, 246, 200)"
            seek_qss = (
                "QPushButton { background: transparent; color: #D1D5DB; border: none; "
                "border-bottom: 1px solid rgba(249,248,246,80); border-radius: 0px; "
                "padding: 1px 3px; font-size: 10px; font-weight: 400; letter-spacing: 1.5px; }"
                "QPushButton:hover { color: #F9F8F6; border-bottom: 1px solid #F9F8F6; }"
            )
        else:
            icon_idle = "#6B7280"
            icon_active = "#1C1C1C"
            icon_muted = "#9CA3AF"
            hover_bg = "rgba(28,28,28,12)"
            close_hover_bg = "rgba(129,109,112,48)"
            status_col = "#6B7280"
            seek_qss = (
                "QPushButton { background: transparent; color: #6B7280; border: none; "
                "border-bottom: 1px solid rgba(28,28,28,70); border-radius: 0px; "
                "padding: 1px 3px; font-size: 10px; font-weight: 400; letter-spacing: 1.5px; }"
                "QPushButton:hover { color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }"
            )

        self.hud_status_label.setStyleSheet(
            f"color: {status_col}; font-size: 11px; background: transparent; border: none;"
        )
        if hasattr(self, "hud_notice_label"):
            notice_col = "#D8C3C6" if dark_fg else LEXICON_THEME["mauve"]
            self.hud_notice_label.setStyleSheet(
                f"color: {notice_col}; font-size: 11px; letter-spacing: 0.4px; "
                f"background: transparent; border: none; padding-bottom: 1px;"
            )
        self.hud_back2_btn.setStyleSheet(seek_qss)
        self.hud_fwd2_btn.setStyleSheet(seek_qss)

        for btn in (self.hud_theme_btn, self.hud_link_btn, self.hud_style_btn, self.hud_expand_btn):
            btn.set_palette(icon_idle, icon_active, hover_bg)
        active_hover = "#D8C3C6" if dark_fg else LEXICON_THEME["mauve"]
        self.hud_pin_btn.set_palette(
            icon_active if self._auto_follow else icon_muted,
            active_hover if self._auto_follow else icon_muted,
            hover_bg,
        )
        self.hud_pause_btn.set_palette(
            icon_active if self._auto_pause else icon_muted,
            active_hover if self._auto_pause else icon_muted,
            hover_bg,
        )
        if hasattr(self, "hud_ocr_btn"):
            ocr_col = (LEXICON_THEME["mauve"] if not dark_fg else "#D8C3C6") if self._custom_region_active else icon_idle
            self.hud_ocr_btn.set_palette(ocr_col, active_hover, hover_bg)
            self.hud_ocr_btn.setToolTip(
                "已锁定字幕区 OCR（点击重新框选，右键清除选区恢复自动）"
                if self._custom_region_active
                else "框选字幕区域（强制屏幕 OCR，适用 PPT/本地视频；右键清除选区）"
            )
        if hasattr(self, "con_region_link"):
            if self._custom_region_active:
                self.con_region_link.setText("已锁字幕区")
                self.con_region_link.setStyleSheet(
                    "QPushButton { background: transparent; color: #816D70; border: none; "
                    "border-bottom: 1.5px solid #816D70; border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 500; }"
                    "QPushButton:hover { color: #1C1C1C; border-bottom: 1.5px solid #1C1C1C; }"
                )
            else:
                self.con_region_link.setText("框选字幕")
                self.con_region_link.setStyleSheet(
                    "QPushButton { background: transparent; color: #9CA3AF; border: none; "
                    "border-bottom: 1px solid rgba(28,28,28,45); border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: 400; }"
                    "QPushButton:hover { color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }"
                )
        self.hud_close_btn.set_palette(icon_idle, icon_active, close_hover_bg)

    def _update_hud_readability_halo(self):
        self._sync_proximity_watcher()
        if self._is_adaptive_contrast_active():
            s = float(getattr(self, "_auto_halo_strength", 0.0))
            use_halo = s > 0.02
            dark_halo = self._is_dark_hud_foreground()
            self.hud_en_widget.set_shadow_mode(use_halo, strength=s, dark_halo=dark_halo)
            self.hud_cn_label.set_transparent_bg(use_halo, strength=s, dark_halo=dark_halo)
            self._sample_and_apply_backdrop(force_repaint=False)
        else:
            self._auto_halo_strength = 0.0
            self.hud_en_widget.set_shadow_mode(False, strength=0.0, dark_halo=True)
            self.hud_cn_label.set_transparent_bg(False, strength=0.0, dark_halo=True)
        self._apply_hud_top_reveal()

    def _apply_hud_appearance(self, resize_now: bool = True):
        old_size = self.size()
        self.hud_en_widget.set_font_size(self._font_size_pt)
        self.hud_en_widget.set_font_weight(self._font_weight)
        self._update_hud_readability_halo()
        self._apply_hud_theme_chrome()
        self._sync_hud_text_palette()
        self._refresh_hud_text_visibility()
        self._hud_vbox.invalidate()
        self._hud_vbox.activate()
        self._root_layout.invalidate()
        self._root_layout.activate()
        self.update()
        if resize_now and self._view_mode == MODE_INPLACE_HUD:
            self._sync_hud_hover_geometry()
            if self.size() == old_size:
                self._force_hud_full_repaint(flush_dwm=True)

    def _refresh_hud_text_visibility(self):
        has_en = bool(self._active_en)
        has_cn = bool(self._active_cn)
        if self._display_lang == "en_only":
            self.hud_en_widget.show()
            self.hud_cn_label.hide()
        elif self._display_lang == "cn_only":
            if has_cn:
                self.hud_en_widget.hide()
                self.hud_cn_label.show()
            else:
                self.hud_en_widget.show()
                self.hud_cn_label.hide()
        else:
            self.hud_en_widget.show()
            self.hud_cn_label.setVisible(has_cn)
        if not has_en and self._display_lang == "cn_only" and has_cn:
            self.hud_en_widget.hide()

    def set_hud_font_size(self, pt: float):
        self._font_size_pt = max(10.0, min(22.0, float(pt)))
        self._apply_hud_appearance(resize_now=True)

    def set_hud_font_weight(self, weight_key: str):
        wk = str(weight_key or "normal")
        if wk not in HUD_WEIGHT_CHOICES:
            return
        self._font_weight = wk
        self._apply_hud_appearance(resize_now=True)

    def set_hud_bg_opacity(self, pct: int):
        self._bg_opacity_pct = max(0, min(100, int(pct)))
        self._update_hud_readability_halo()
        self._apply_hud_theme_chrome()
        self._sync_hud_text_palette()
        self._force_hud_full_repaint(flush_dwm=True)

    def set_hud_display_lang(self, lang: str):
        if lang not in ("bilingual", "en_only", "cn_only"):
            return
        self._display_lang = lang
        self._apply_hud_appearance(resize_now=True)

    def set_hud_width_preset(self, preset: str):
        if preset not in HUD_WIDTH_PRESETS:
            return
        self._width_preset = preset
        self._apply_hud_width_preset()

    def _apply_hud_width_preset(self):
        if self._view_mode != MODE_INPLACE_HUD:
            return
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        preset_w = min(avail.width() - 16, HUD_WIDTH_PRESETS.get(self._width_preset, 780))
        target_w = max(540, preset_w)
        target_h = self._measure_hud_height_for_width(target_w)
        pad = getattr(self, "_dwm_pad_h", 0)
        if self._auto_follow:
            cx = self.geometry().center().x()
            bot = self.y() + self._hud_base_height()
            tx = max(avail.left() + 4, min(avail.right() - target_w - 4, cx - target_w // 2))
            ty = max(avail.top() + 4, min(avail.bottom() - target_h - pad - 4, bot - target_h))
        else:
            cx = self.geometry().center().x()
            bot = self.y() + self._hud_base_height()
            tx = max(avail.left() + 4, min(avail.right() - target_w - 4, cx - target_w // 2))
            ty = max(avail.top() + 4, min(avail.bottom() - target_h - pad - 4, bot - target_h))
            self._hud_user_pos = QPoint(tx, ty)
        self._hud_anim_timer.stop()
        self.setGeometry(tx, ty, target_w, target_h + pad)
        self._hud_size = QSize(target_w, target_h)

    def _on_popover_theme_changed(self, theme: str):
        self.set_hud_theme(theme, animate=True)
        self._save_hud_settings()

    def _open_style_popover(self):
        if self._style_popover is None:
            self._style_popover = _HudStylePopover(None)
            self._style_popover.font_size_changed.connect(self.set_hud_font_size)
            self._style_popover.font_weight_changed.connect(self.set_hud_font_weight)
            self._style_popover.bg_opacity_changed.connect(self.set_hud_bg_opacity)
            self._style_popover.display_lang_changed.connect(self.set_hud_display_lang)
            self._style_popover.width_preset_changed.connect(self.set_hud_width_preset)
            self._style_popover.hud_theme_changed.connect(self._on_popover_theme_changed)
            self._style_popover.settings_commit_requested.connect(self._save_hud_settings)
            self._style_popover.reset_requested.connect(self._reset_hud_settings)
            self._style_popover.bilibili_login_requested.connect(self._open_bilibili_login)
        self._style_popover.sync_state(self.get_hud_settings())
        self._style_popover.adjustSize()

        btn_center = self.hud_style_btn.mapToGlobal(QPoint(self.hud_style_btn.width() // 2, 0))
        pw = self._style_popover.width()
        ph = self._style_popover.sizeHint().height()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        px = max(avail.left() + 6, min(avail.right() - pw - 6, btn_center.x() - pw // 2))
        py = btn_center.y() - ph - 8
        if py < avail.top() + 6:
            py = min(avail.bottom() - ph - 6, self.hud_style_btn.mapToGlobal(QPoint(0, self.hud_style_btn.height())).y() + 8)
        self._style_popover.move(px, py)
        self._style_popover.show()
        self._style_popover.raise_()

    def _update_pin_indicator(self):
        dark_fg = self._is_dark_hud_foreground()
        active_col = "#F9F8F6" if dark_fg else "#1C1C1C"
        active_hover = "#D8C3C6" if dark_fg else LEXICON_THEME["mauve"]
        muted_col = "#9CA3AF"
        hover_bg = "rgba(249,248,246,18)" if dark_fg else "rgba(28,28,28,12)"
        if self._auto_follow:
            self.hud_pin_btn.set_palette(active_col, active_hover, hover_bg)
            self.hud_pin_btn.setToolTip("自动贴合原生字幕位置：已开启（拖动后可点此恢复）")
            if hasattr(self, "con_pin_btn"):
                self.con_pin_btn.set_palette(LEXICON_THEME["text"], LEXICON_THEME["mauve"], LEXICON_THEME["hover_bg"])
                self.con_pin_btn.setToolTip("自动贴合位置：已开启")
        else:
            self.hud_pin_btn.set_palette(muted_col, muted_col, hover_bg)
            self.hud_pin_btn.setToolTip("当前为固定位置（点击恢复自动贴合原生字幕）")
            if hasattr(self, "con_pin_btn"):
                self.con_pin_btn.set_palette("#6B7280", "#6B7280", LEXICON_THEME["hover_bg"])
                self.con_pin_btn.setToolTip("当前为固定位置（点击恢复自动贴合）")

    def _toggle_auto_pause(self):
        self._auto_pause = not self._auto_pause
        tip = "查词自动暂停视频：已开启" if self._auto_pause else "查词自动暂停视频：已关闭"
        dark_fg = self._is_dark_hud_foreground()
        active_col = "#F9F8F6" if dark_fg else "#1C1C1C"
        active_hover = "#D8C3C6" if dark_fg else LEXICON_THEME["mauve"]
        muted_col = "#9CA3AF"
        hover_bg = "rgba(249,248,246,18)" if dark_fg else "rgba(28,28,28,12)"
        self.hud_pause_btn.set_palette(
            active_col if self._auto_pause else muted_col,
            active_hover if self._auto_pause else muted_col,
            hover_bg,
        )
        self.hud_pause_btn.setToolTip(tip)

        self.con_pause_btn.set_palette(
            LEXICON_THEME["text"] if self._auto_pause else "#6B7280",
            LEXICON_THEME["mauve"] if self._auto_pause else "#6B7280",
            LEXICON_THEME["hover_bg"],
        )
        self.con_pause_btn.setToolTip(tip)
        self._force_hud_full_repaint(flush_dwm=True)

    def _open_custom_region_selector(self):
        if getattr(self, "_selecting_region", False):
            return
        self._selecting_region = True
        if self._region_selector is None:
            self._region_selector = _SubtitleRegionSelectorOverlay()
            self._region_selector.region_selected.connect(self._on_region_selector_picked)
            self._region_selector.cancelled.connect(self._on_region_selector_cancelled)
        self._was_visible_before_region = self.isVisible()
        if self._style_popover is not None and self._style_popover.isVisible():
            self._style_popover.hide()
        if self._was_visible_before_region:
            self.hide()
            QTimer.singleShot(0, self._begin_region_selection_safe)
        else:
            self._begin_region_selection_safe()

    def _begin_region_selection_safe(self):
        if not getattr(self, "_selecting_region", False):
            return
        if self._region_selector is not None:
            self._region_selector.begin_selection()

    def _on_region_selector_picked(self, x1: int, y1: int, x2: int, y2: int):
        self._selecting_region = False
        self._custom_region_active = True
        self._apply_hud_theme_chrome()
        if getattr(self, "_was_visible_before_region", True):
            self.show()
            self.raise_()
        self.custom_region_selected.emit(x1, y1, x2, y2)

    def _on_region_selector_cancelled(self):
        self._selecting_region = False
        if getattr(self, "_was_visible_before_region", False):
            self.show()
            self.raise_()

    def _clear_custom_region_ui(self):
        if not self._custom_region_active:
            self.switch_to_ocr_requested.emit()
            return
        self._custom_region_active = False
        self._apply_hud_theme_chrome()
        self._force_hud_full_repaint(flush_dwm=True)
        self.custom_region_cleared.emit()

    def _open_link_bar_in_console(self):
        self.set_view_mode(MODE_CONSOLE_DRAWER)
        if not self.link_bar.isVisible():
            self._toggle_link_bar()

    def _toggle_link_bar(self):
        vis = not self.link_bar.isVisible()
        self.link_bar.setVisible(vis)
        if vis:
            # 尝试从剪贴板自动识别 YouTube 或 Bilibili 链接预填
            try:
                cb_text = QApplication.clipboard().text().strip()
                if (
                    any(k in cb_text for k in ("youtube.com/", "youtu.be/", "bilibili.com/", "b23.tv/"))
                    and not self.link_input.text().strip()
                ):
                    self.link_input.setText(cb_text)
            except Exception:
                pass
            self._refresh_bili_login_btns()
            self.link_input.setFocus()
        self._force_hud_full_repaint(flush_dwm=True)

    def _refresh_bili_login_btns(self):
        has_auth = BilibiliAuthManager.has_sessdata()
        uname = BilibiliAuthManager.get_display_name() if has_auth else ""
        label_txt = f"B站: {uname[:10]}" if uname else ("B站已登" if has_auth else "B站扫码")
        if hasattr(self, "con_bili_link"):
            self.con_bili_link.setText(label_txt)
            line_col = "rgba(28,28,28,170)" if has_auth else "rgba(28,28,28,76)"
            txt_col = "#1C1C1C" if has_auth else "#6B7280"
            self.con_bili_link.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {txt_col}; border: none; "
                f"border-bottom: 1px solid {line_col}; border-radius: 0px; padding: 1px 0px; font-size: 11.5px; font-weight: {'500' if has_auth else '400'}; }}"
                f"QPushButton:hover {{ color: #1C1C1C; border-bottom: 1px solid #816D70; }}"
            )
            self.con_bili_link.setToolTip(
                f"B站已登录：{uname or '可用'}（点击切换或退出）" if has_auth else "B站扫码登录（获取需登录可见的官方英文外挂字幕）"
            )
        if hasattr(self, "link_bili_btn"):
            self.link_bili_btn.setText(label_txt)
        if hasattr(self, "con_bili_btn"):
            self.con_bili_btn.set_color(LEXICON_THEME["mauve"] if has_auth else "#6B7280")
            self.con_bili_btn.setToolTip(
                f"B站已登录：{uname or '可用'}" if has_auth else "B站扫码登录（获取需登录可见的官方英文外挂字幕）"
            )

    def _open_bilibili_login(self):
        if self._bili_login_dialog is None:
            self._bili_login_dialog = _BilibiliLoginDialog(None)
            self._bili_login_dialog.auth_changed.connect(self._on_bilibili_auth_changed)
        self._bili_login_dialog.adjustSize()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        dw = self._bili_login_dialog.width()
        dh = max(340, self._bili_login_dialog.sizeHint().height())
        anchor = self.geometry().center() if self.isVisible() else avail.center()
        dx = max(avail.left() + 8, min(avail.right() - dw - 8, anchor.x() - dw // 2))
        dy = max(avail.top() + 8, min(avail.bottom() - dh - 8, anchor.y() - dh // 2))
        self._bili_login_dialog.move(dx, dy)
        self._bili_login_dialog.show()
        self._bili_login_dialog.raise_()
        self._bili_login_dialog.refresh_auth_view()

    def _on_bilibili_auth_changed(self):
        self._refresh_bili_login_btns()
        if self._style_popover is not None and self._style_popover.isVisible():
            self._style_popover.sync_state(self.get_hud_settings())
        self.bilibili_auth_changed.emit()

    def set_bilibili_login_hint(self, title: str):
        self._status_notice = "B站外挂字幕需登录 · 设置中可扫码（已开启画面OCR）"
        self.hud_status_label.setText(self._status_notice)
        self.hud_status_label.show()
        if hasattr(self, "hud_notice_label"):
            self.hud_notice_label.setText(self._status_notice)
            self.hud_notice_label.show()
            self._notice_timer.start(3000)
        self.con_mode_badge.hide()
        self._refresh_bili_login_btns()
        if self._view_mode == MODE_INPLACE_HUD and not self._dragging and not self._resizing:
            self._sync_hud_hover_geometry()
        self._force_hud_full_repaint(flush_dwm=True)

    def set_status_notice(self, msg: str):
        notice = (msg or "").strip()
        self._status_notice = notice
        if notice:
            self.hud_status_label.setText(notice)
            self.hud_status_label.show()
            self.con_mode_badge.hide()
            if hasattr(self, "hud_notice_label"):
                self.hud_notice_label.setText(notice)
                self.hud_notice_label.show()
                self._notice_timer.start(2000)
            if self._current_mode != "transcript" and not self._active_en:
                self.hud_en_widget.set_text("等待字幕")
            if self._view_mode == MODE_INPLACE_HUD and not self._dragging and not self._resizing:
                self._sync_hud_hover_geometry()
        else:
            self.hud_status_label.setText("")
            self.hud_status_label.hide()
            self._clear_transient_notice()
        self._force_hud_full_repaint(flush_dwm=True)

    def _clear_transient_notice(self):
        self._notice_timer.stop()
        if hasattr(self, "hud_notice_label") and not self.hud_notice_label.isHidden():
            self.hud_notice_label.setText("")
            self.hud_notice_label.hide()
            if self._view_mode == MODE_INPLACE_HUD and not self._dragging and not self._resizing:
                self._sync_hud_hover_geometry()
                self._force_hud_full_repaint(flush_dwm=True)

    @staticmethod
    def format_bilingual_notes(history: List[Dict[str, Any]], title: str = "", fmt: str = "md") -> str:
        """将当前整节双语字幕格式化为带时间轴的 Markdown 或纯文本学习笔记。"""
        clean_title = (title or "").strip() or "ScreenLex 双语字幕笔记"
        ts_now = time.strftime("%Y-%m-%d %H:%M")
        lines: List[str] = []
        if fmt == "md":
            lines.append(f"# {clean_title}")
            lines.append("")
            lines.append(f"> 导出时间：{ts_now} · 共 {len(history)} 句")
            lines.append("")
            for idx, item in enumerate(history, 1):
                en = (item.get("en_text") or "").strip()
                cn = (item.get("cn_text") or "").strip()
                ts = (item.get("timestamp_str") or "").strip()
                if not en and not cn:
                    continue
                prefix = f"**[{ts}]** " if ts else f"**{idx}.** "
                lines.append(f"- {prefix}{en}")
                if cn:
                    lines.append(f"  {cn}")
                lines.append("")
        else:
            lines.append(f"{clean_title}")
            lines.append(f"导出时间：{ts_now} · 共 {len(history)} 句")
            lines.append("=" * 48)
            lines.append("")
            for idx, item in enumerate(history, 1):
                en = (item.get("en_text") or "").strip()
                cn = (item.get("cn_text") or "").strip()
                ts = (item.get("timestamp_str") or "").strip()
                if not en and not cn:
                    continue
                tag = f"[{ts}] " if ts else f"[{idx:03d}] "
                lines.append(f"{tag}{en}")
                if cn:
                    lines.append(f"{' ' * len(tag)}{cn}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _export_bilingual_notes(self):
        items = list(self._latest_history or [])
        if not items and self._active_en:
            items = [{"timestamp_str": "", "en_text": self._active_en, "cn_text": self._active_cn}]
        if not items:
            QMessageBox.information(self, "提示", "当前暂无字幕记录可导出。")
            return
        safe_title = re.sub(r'[\\/:*?"<>|]+', "_", (self._latest_title or "").strip())[:48].strip("._ ")
        base_name = f"ScreenLex_{safe_title or '双语字幕笔记'}_{time.strftime('%Y%m%d_%H%M')}"
        default_path = os.path.join(os.path.expanduser("~"), f"{base_name}.md")
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出双语字幕笔记",
            default_path,
            "Markdown 笔记 (*.md);;纯文本笔记 (*.txt)",
        )
        if not file_path:
            return
        is_txt = file_path.lower().endswith(".txt") or ("*.txt" in (selected_filter or "") and not file_path.lower().endswith(".md"))
        content = self.format_bilingual_notes(items, title=self._latest_title, fmt="txt" if is_txt else "md")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：\n{exc}")
            return
        QMessageBox.information(self, "导出成功", f"已导出 {len(items)} 条双语字幕笔记至：\n{file_path}")

    def _on_clear_clicked(self):
        self._status_notice = ""
        self._user_scroll_locked = False
        self._clear_transient_notice()
        self.hud_status_label.setText("")
        self.hud_status_label.hide()
        self.con_mode_badge.setText("")
        self.con_mode_badge.hide()
        self.clear_requested.emit()
        self._force_hud_full_repaint(flush_dwm=True)

    def _submit_link(self):
        url = self.link_input.text().strip()
        if not url:
            return
        self.con_mode_badge.hide()
        self.link_submitted.emit(url)

    def set_transcript_error(self, msg: str):
        short_err = (msg or "加载失败")[:42]
        self.con_mode_badge.hide()
        self.hud_status_label.setText(f"链接载入失败: {short_err}")
        self.hud_status_label.show()
        if hasattr(self, "con_url_link"):
            self.con_url_link.setToolTip(msg)
        if self.link_bar.isVisible():
            self.link_input.setToolTip(msg)

    def set_transcript_loaded(self, meta: Dict[str, Any]):
        self.con_mode_badge.hide()
        self._user_scroll_locked = False

    # ---------------- 数据刷新与原位跟随阻尼 ----------------

    def _measure_hud_height_for_width(self, total_w: int) -> int:
        chrome_margin = 12
        inner_w = max(120, total_w - 48 - chrome_margin)
        top_h = (max(24, self.hud_top_widget.sizeHint().height()) + 5) if self.hud_top_widget.isVisible() else 0
        notice_h = (
            (max(16, self.hud_notice_label.sizeHint().height()) + 5)
            if (hasattr(self, "hud_notice_label") and not self.hud_notice_label.isHidden())
            else 0
        )
        en_vis = not self.hud_en_widget.isHidden()
        en_h = self.hud_en_widget.measure_height_for_width(inner_w) if en_vis else 0
        cn_vis = (self._display_lang != "en_only") and bool(self.hud_cn_label.text()) and (not self.hud_cn_label.isHidden() or self._display_lang == "cn_only")
        if cn_vis:
            cn_h = max(self.hud_cn_label.heightForWidth(inner_w), self.hud_cn_label.sizeHint().height())
            gap = 5 if en_vis else 0
            total_h = top_h + notice_h + 26 + chrome_margin + en_h + gap + cn_h + 4
            return max(56 if not en_vis else 74, min(360, total_h))
        total_h = top_h + notice_h + 26 + chrome_margin + en_h + 4
        return max(48, min(300, total_h))

    def compute_hud_target_rect(self, native_box: Tuple[int, int, int, int]) -> QRect:
        """
        根据检测到的原生字幕区域 (nx, ny, nw, nh) 计算原位遮盖舱的目标几何位置。
        无副作用纯计算：透明外边距与磨砂卡片内框均完整包络原生字幕（绝不露边），且严格受屏幕边界约束。
        """
        nx, ny, nw, nh = native_box
        nb = ny + nh
        center_x = nx + nw // 2
        screen = QApplication.screenAt(QPoint(center_x, ny + nh // 2)) or self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

        preset_max = HUD_WIDTH_PRESETS.get(getattr(self, "_width_preset", "medium"), 780)
        max_allowed_w = max(540, min(avail.width() - 16, max(preset_max, nw + 40)))
        target_w = max(540, min(max_allowed_w, max(nw + 84, preset_max if self._width_preset == "wide" else 540)))
        content_h = self._measure_hud_height_for_width(target_w)
        min_cover_h = nh + 30
        target_h = max(content_h, min_cover_h)

        tx = int(center_x - target_w // 2)
        ty = min(int(ny - 14), int(nb + 16 - target_h))
        if ty + target_h > avail.bottom() - 4:
            ty = avail.bottom() - target_h - 4
        tx = max(avail.left() + 4, min(avail.right() - target_w - 4, tx))
        ty = max(avail.top() + 4, ty)
        return QRect(tx, ty, target_w, target_h)

    @staticmethod
    def _lerp_int(cur_v: int, tgt_v: int, alpha: float) -> int:
        if cur_v == tgt_v:
            return tgt_v
        diff = tgt_v - cur_v
        step = int(round(diff * alpha))
        if step == 0:
            step = 1 if diff > 0 else -1
        return cur_v + step

    def _apply_smooth_step(self, target_rect: QRect, native_box: Optional[Tuple[int, int, int, int]] = None):
        cur = self.geometry()
        if cur == target_rect:
            self._hud_anim_timer.stop()
            return
        jump_dist = abs(target_rect.x() - cur.x()) + abs(target_rect.y() - cur.y())
        if jump_dist > 160:
            self.setGeometry(target_rect)
            self._hud_anim_timer.stop()
            return
        sx = self._lerp_int(cur.x(), target_rect.x(), 0.42)
        sy = self._lerp_int(cur.y(), target_rect.y(), 0.42)
        sw = self._lerp_int(cur.width(), target_rect.width(), 0.42)
        sh = self._lerp_int(cur.height(), target_rect.height(), 0.42)
        if native_box is not None:
            nbx, nby, nbw, nbh = native_box
            s_right = sx + sw
            s_bot = sy + sh
            if nbx - 10 < sx:
                sx = nbx - 10
            if nbx + nbw + 10 > s_right:
                s_right = nbx + nbw + 10
            if nby - 6 < sy:
                sy = nby - 6
            if nby + nbh + 8 > s_bot:
                s_bot = nby + nbh + 8
            sw = max(300, s_right - sx)
            sh = max(42, s_bot - sy)
        min_content_h = self._measure_hud_height_for_width(max(300, sw)) + getattr(self, "_dwm_pad_h", 0)
        sh = max(sh, min_content_h)
        step_rect = QRect(sx, sy, max(300, sw), max(min_content_h, sh))
        eff_target = QRect(
            target_rect.x(),
            target_rect.y(),
            max(300, target_rect.width()),
            max(target_rect.height(), min_content_h),
        )
        self.setGeometry(step_rect)
        if step_rect != eff_target and step_rect != cur:
            if not self._hud_anim_timer.isActive():
                self._hud_anim_timer.start()
        else:
            self._hud_anim_timer.stop()
            self._hud_needs_dwm_flush = True
            if self.isVisible() and self._view_mode == MODE_INPLACE_HUD:
                self._force_hud_full_repaint(flush_dwm=True)

    def _on_hud_anim_tick(self):
        if (
            self._view_mode != MODE_INPLACE_HUD
            or not self._auto_follow
            or self._dragging
            or self._resizing
            or self._hud_target_rect is None
            or not self.isVisible()
        ):
            self._hud_anim_timer.stop()
            return
        self._apply_smooth_step(self._hud_target_rect, native_box=None)

    def _follow_native_box(self, native_box: Tuple[int, int, int, int]):
        if self._view_mode != MODE_INPLACE_HUD or not self._auto_follow or self._dragging or self._resizing:
            return
        target_rect = self.compute_hud_target_rect(native_box)
        nx, ny, nw, nh = native_box
        cur_rect = self.geometry()
        cx, cy = target_rect.center().x(), target_rect.center().y()

        uncovered = (
            (nx - 6 < cur_rect.x())
            or (nx + nw + 6 > cur_rect.x() + cur_rect.width())
            or (ny - 4 < cur_rect.y())
            or (ny + nh + 4 > cur_rect.y() + cur_rect.height())
        )

        if self._last_follow_anchor is None:
            pad = getattr(self, "_dwm_pad_h", 0)
            padded_rect = QRect(target_rect.x(), target_rect.y(), target_rect.width(), target_rect.height() + pad)
            self._last_follow_anchor = (padded_rect.center().x(), padded_rect.center().y())
            self._hud_target_rect = padded_rect
            self._hud_anim_timer.stop()
            self.setGeometry(padded_rect)
            return

        dx = abs(cx - self._last_follow_anchor[0])
        dy = abs(cy - self._last_follow_anchor[1])
        pad = getattr(self, "_dwm_pad_h", 0)
        base_h = self._hud_base_height()
        if not uncovered and dx < 28 and dy < 28:
            need_h = max(self._measure_hud_height_for_width(self.width()), nh + 18)
            if not (need_h > base_h or abs(base_h - need_h) >= 8):
                return
            anchor_bottom = cur_rect.y() + base_h
            new_y = anchor_bottom - need_h
            anchor_rect = QRect(cur_rect.x(), new_y, cur_rect.width(), need_h + pad)
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                if anchor_rect.top() < avail.top() + 4:
                    anchor_rect.moveTop(avail.top() + 4)
                if anchor_rect.bottom() > avail.bottom() - 4:
                    anchor_rect.moveTop(max(avail.top() + 4, avail.bottom() - need_h - pad - 4))
            self._hud_target_rect = anchor_rect
            self._apply_smooth_step(anchor_rect, native_box=None)
            return

        self._last_follow_anchor = (cx, cy)
        padded_target = QRect(target_rect.x(), target_rect.y(), target_rect.width(), target_rect.height() + pad)
        self._hud_target_rect = padded_target
        self._apply_smooth_step(padded_target, native_box=native_box if uncovered else None)

    def _follow_player_box(self, player_box: Tuple[int, int, int, int]):
        """当用户关闭 YouTube 原生 CC 字幕时，依据播放器矩形在底部居中位置自动悬浮对齐。"""
        if self._view_mode != MODE_INPLACE_HUD or not self._auto_follow or self._dragging or self._resizing:
            return
        px, py, pw, ph = player_box
        center_x = px + pw // 2
        bottom_y = py + ph
        screen = QApplication.screenAt(QPoint(center_x, bottom_y)) or self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

        preset = getattr(self, "_width_preset", "medium")
        preset_max = HUD_WIDTH_PRESETS.get(preset, 780)
        if preset == "wide":
            target_w = max(540, min(preset_max, avail.width() - 16))
        elif preset == "narrow":
            target_w = max(540, min(preset_max, avail.width() - 16))
        else:
            target_w = max(540, min(preset_max, min(pw + 40, avail.width() - 16)))
        target_h = self._measure_hud_height_for_width(target_w)
        pad = getattr(self, "_dwm_pad_h", 0)
        tx = int(center_x - target_w // 2)
        ty = int(bottom_y - target_h)
        tx = max(avail.left() + 4, min(avail.right() - target_w - 4, tx))
        ty = max(avail.top() + 4, min(avail.bottom() - target_h - pad - 4, ty))
        target_rect = QRect(tx, ty, target_w, target_h + pad)

        if self._last_follow_anchor is None:
            self._last_follow_anchor = (target_rect.center().x(), target_rect.center().y())
            self._hud_target_rect = target_rect
            self._hud_anim_timer.stop()
            self.setGeometry(target_rect)
            return

        cur = self.geometry()
        cur_base_h = self._hud_base_height()
        dx = abs(target_rect.center().x() - cur.center().x())
        dy = abs((cur.y() + cur_base_h) - (ty + target_h))
        if dx < 24 and dy < 24:
            if target_h > cur_base_h or abs(target_h - cur_base_h) >= 8 or abs(target_w - cur.width()) >= 24:
                self._hud_anim_timer.stop()
                self._hud_target_rect = target_rect
                self._last_follow_anchor = (target_rect.center().x(), target_rect.center().y())
                self.setGeometry(target_rect)
            return

        self._last_follow_anchor = (target_rect.center().x(), target_rect.center().y())
        self._hud_target_rect = target_rect
        self._apply_smooth_step(target_rect, native_box=None)

    def update_subtitle_data(self, payload: Dict[str, Any]):
        if not payload:
            return
        snap_seq = payload.get("snapshot_seq")
        if snap_seq is not None:
            eng_id = int(payload.get("engine_id") or 0)
            seq_i = int(snap_seq)
            if eng_id != self._last_snapshot_engine_id:
                self._last_snapshot_engine_id = eng_id
                self._last_snapshot_seq = seq_i
            elif seq_i <= self._last_snapshot_seq:
                return
            else:
                self._last_snapshot_seq = seq_i
        prev_size = self.size()
        top_vis = self.hud_top_widget.isVisible()
        prev_hud_snap = (
            getattr(self.hud_en_widget, "_text", ""),
            self.hud_cn_label.text(),
            self.hud_status_label.text() if top_vis else "",
            self._active_en,
            self._active_cn,
            len(self._row_widgets),
        )
        self._target_hwnd = int(payload.get("target_hwnd") or self._target_hwnd or 0)
        mode = payload.get("mode", "ocr")
        active_en = payload.get("active_en", "").strip()
        hud_en = (payload.get("hud_en") or active_en).strip()
        active_cn = payload.get("active_cn", "").strip()
        history = payload.get("history", [])
        native_box = payload.get("native_box")
        player_box = payload.get("player_box")
        playback_sec = float(payload.get("playback_sec", 0.0) or 0.0)
        transcript_index = int(payload.get("transcript_index", -1))

        self._active_en = active_en
        self._active_cn = active_cn
        self._current_mode = mode
        self._latest_history = list(history) if isinstance(history, list) else []
        if payload.get("transcript_title"):
            self._latest_title = str(payload.get("transcript_title") or "")
        elif mode != "transcript":
            self._latest_title = ""

        # 刷新模式状态（保持期刊标题清爽，不再在标题旁堆积影片名徽章；B站登录按钮仅在登录态变更/展开时刷新，避免每帧查 SQLite）
        if mode == "transcript":
            self._status_notice = ""
            if getattr(self, "_custom_region_active", False):
                self._custom_region_active = False
                self._apply_hud_theme_chrome()
            mm, ss = divmod(max(0, int(playback_sec)), 60)
            self.con_mode_badge.setText("")
            self.con_mode_badge.hide()
            self.hud_back2_btn.show()
            self.hud_fwd2_btn.show()
            self.hud_status_label.setText(f"{mm:02d}:{ss:02d}")
            self.hud_status_label.show()
        else:
            if self._status_notice:
                self.hud_status_label.setText(self._status_notice)
                self.hud_status_label.show()
            else:
                self.hud_status_label.setText("")
                self.hud_status_label.hide()
            self.con_mode_badge.setText("")
            self.con_mode_badge.hide()
            self.hud_back2_btn.hide()
            self.hud_fwd2_btn.hide()

        self.live_focus_box.setVisible(bool(active_en))

        # 刷新当前焦点句（HUD 使用紧凑实时子句并消除闲置/等待译文闪烁，Console 保留完整上下文）
        want_idle_pal = not bool(active_en)
        if want_idle_pal != getattr(self, "_hud_idle_palette", False):
            self._hud_idle_palette = want_idle_pal
            self._sync_hud_text_palette()

        idle_hud = "等待字幕"
        hud_display_en = hud_en if active_en else idle_hud
        con_display_en = active_en if active_en else "等待字幕"
        self.hud_en_widget.set_text(hud_display_en)
        self.con_active_en.set_text(con_display_en)

        if active_cn:
            self.hud_cn_label.setText(active_cn)
            self.con_active_cn.setText(active_cn)
            self.con_active_cn.setStyleSheet(
                f"color: {LEXICON_THEME['text_muted']}; font-size: 12.5px; background: transparent; border: none;"
            )
            self.con_active_cn.show()
        else:
            self.hud_cn_label.setText("")
            self.con_active_cn.setText("")
            self.con_active_cn.hide()
        self._refresh_hud_text_visibility()
        self.hud_en_widget._relayout(max(120, self.width() - 60))
        self._hud_vbox.invalidate()
        self._hud_vbox.activate()
        self._root_layout.invalidate()
        self._root_layout.activate()

        # 增量同步历史流条目（HUD 模式下延迟至切入字幕台时同步，避免后台同步构建数千控件）
        if self._view_mode == MODE_CONSOLE_DRAWER:
            self._pending_history_sync = None
            self._sync_history_rows(history, mode, transcript_index)
        else:
            self._pending_history_sync = (history, mode, transcript_index)

        # 原位随动：开启跟随且有原生字幕/播放器框时自动对齐；固定位置模式下也同步调整字幕框高度防止多行重叠
        if self._auto_follow and native_box and isinstance(native_box, (tuple, list)) and len(native_box) == 4:
            self._follow_native_box(tuple(native_box))
        elif self._auto_follow and player_box and isinstance(player_box, (tuple, list)) and len(player_box) == 4:
            self._follow_player_box(tuple(player_box))
        elif self._view_mode == MODE_INPLACE_HUD and not self._dragging and not self._resizing:
            self._sync_hud_hover_geometry()

        top_vis_after = self.hud_top_widget.isVisible()
        cur_hud_snap = (
            getattr(self.hud_en_widget, "_text", ""),
            self.hud_cn_label.text(),
            self.hud_status_label.text() if top_vis_after else "",
            self._active_en,
            self._active_cn,
            len(self._row_widgets),
        )
        if self.isVisible() and cur_hud_snap != prev_hud_snap:
            if getattr(self, "_use_dwm_glass", False):
                if not self._hud_anim_timer.isActive():
                    self._force_hud_full_repaint(flush_dwm=False)
                else:
                    self.hud_en_widget.update()
                    self.hud_cn_label.update()
                    self.hud_status_label.update()
                    self.update()
            else:
                self._hud_needs_dwm_flush = True
                if self._view_mode == MODE_INPLACE_HUD:
                    if not self._hud_anim_timer.isActive():
                        self._force_hud_full_repaint(flush_dwm=True)
                        self._dwm_post_paint_timer.start(20)
                    else:
                        self.hud_en_widget.update()
                        self.hud_cn_label.update()
                        self.hud_status_label.update()
                        self.update()
                elif self.size() == prev_size:
                    self._force_hud_full_repaint(flush_dwm=True)
                else:
                    self.update()

    def _append_history_row_widget(self, i: int, item: Dict[str, Any], is_transcript: bool, transcript_index: int):
        is_cur = is_transcript and (i == transcript_index)
        eid = int(item.get("entry_id", i + 1))
        ts_str = item.get("timestamp_str", "")
        en_t = item.get("en_text", "")
        cn_t = item.get("cn_text", "")
        row = _TranscriptRowWidget(
            entry_id=eid,
            ts_str=ts_str,
            en_text=en_t,
            cn_text=cn_t,
            is_current=is_cur,
            clickable_row=is_transcript,
        )
        row.word_clicked.connect(self._on_row_word_clicked)
        row.row_activated.connect(self._on_row_activated)
        insert_pos = max(1, self.history_layout.count() - 1)
        self.history_layout.insertWidget(insert_pos, row)
        self._row_widgets.append(row)

    def _invalidate_history_feather(self):
        if hasattr(self.scroll, "_feather_overlay") and self.scroll._feather_overlay is not None:
            try:
                self.scroll._feather_overlay._surface.invalidate(clear_pixmap=True)
                self.scroll._feather_overlay._schedule_prewarm()
            except Exception:
                pass
            if self.scroll.viewport() is not None:
                self.scroll.viewport().update()

    def _mount_next_history_batch(self):
        if self._view_mode != MODE_CONSOLE_DRAWER or self._hist_batch_data is None:
            self._hist_batch_data = None
            return
        history, mode, transcript_index = self._hist_batch_data
        is_transcript = (mode == "transcript")
        cur_count = len(self._row_widgets)
        total = len(history)
        if cur_count >= total:
            self._hist_batch_data = None
            return
        end_idx = min(total, cur_count + self._hist_batch_size)
        for i in range(cur_count, end_idx):
            self._append_history_row_widget(i, history[i], is_transcript, transcript_index)
        self._invalidate_history_feather()
        if not self._user_scroll_locked and is_transcript and cur_count <= transcript_index < end_idx:
            self._last_transcript_idx = transcript_index
            QTimer.singleShot(0, lambda idx=transcript_index: self._scroll_row_index_visible(idx))
        if end_idx < total:
            self._hist_batch_timer.start()
        else:
            self._hist_batch_data = None
            if not self._user_scroll_locked and not is_transcript:
                QTimer.singleShot(0, self._scroll_to_bottom)

    def _sync_history_rows(self, history: List[Dict[str, Any]], mode: str, transcript_index: int):
        self._hist_batch_timer.stop()
        self._hist_batch_data = None

        has_items = bool(history)
        self.empty_hint.setVisible(not has_items)

        is_transcript = (mode == "transcript")
        cur_count = len(self._row_widgets)
        orig_count = cur_count
        new_count = len(history)

        # 若条目变少（如清空或切换视频），立刻隐藏并摘除父级后再异步销毁，杜绝 (0,0) 闪影
        if new_count < cur_count:
            for row in self._row_widgets[new_count:]:
                self.history_layout.removeWidget(row)
                row.hide()
                row.setParent(None)
                row.deleteLater()
            self._row_widgets = self._row_widgets[:new_count]
            cur_count = new_count

        any_row_updated = (new_count != orig_count)
        update_limit = min(cur_count, new_count)
        for i in range(update_limit):
            item = history[i]
            is_cur = is_transcript and (i == transcript_index)
            eid = int(item.get("entry_id", i + 1))
            ts_str = item.get("timestamp_str", "")
            en_t = item.get("en_text", "")
            cn_t = item.get("cn_text", "")
            if self._row_widgets[i].update_content(ts_str, en_t, cn_t, is_cur, is_transcript, entry_id=eid):
                any_row_updated = True

        # 新增行数 <= 60 时同步构建；长视频（>60 句，如 1500+ 句讲座）首批立刻挂 40 行，剩余每帧分批挂 40 行，切入字幕台 0 卡顿
        remaining = new_count - cur_count
        if remaining > 0:
            sync_limit = new_count if remaining <= 60 else (cur_count + self._hist_batch_size)
            for i in range(cur_count, sync_limit):
                self._append_history_row_widget(i, history[i], is_transcript, transcript_index)
            any_row_updated = True
            if sync_limit < new_count:
                self._hist_batch_data = (list(history), mode, transcript_index)
                self._hist_batch_timer.start()

        if any_row_updated:
            self._invalidate_history_feather()

        if not self._user_scroll_locked:
            if is_transcript and 0 <= transcript_index < len(self._row_widgets):
                if transcript_index != self._last_transcript_idx or new_count != orig_count:
                    self._last_transcript_idx = transcript_index
                    QTimer.singleShot(0, lambda idx=transcript_index: self._scroll_row_index_visible(idx))
            elif new_count > orig_count and self._hist_batch_data is None:
                # 仅在确实新增历史记录行时滚到底部，刷新译文绝不抢夺用户浏览位置
                QTimer.singleShot(0, self._scroll_to_bottom)
        if not is_transcript:
            self._last_transcript_idx = -1

    def eventFilter(self, obj, event):
        if shiboken6.isValid(self.scroll) and obj is self.scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                if event.angleDelta().y() > 0 or event.pixelDelta().y() > 0:
                    self._user_scroll_locked = True
        return super().eventFilter(obj, event)

    def _on_row_activated(self, entry_id: int):
        self._user_scroll_locked = False
        self.transcript_item_activated.emit(max(0, entry_id - 1))

    def _scroll_row_index_visible(self, idx: int):
        if not shiboken6.isValid(self.scroll) or idx < 0 or idx >= len(self._row_widgets):
            return
        row = self._row_widgets[idx]
        if shiboken6.isValid(row):
            self._programmatic_scroll = True
            try:
                self.scroll.ensureWidgetVisible(row, 0, 80)
            finally:
                self._programmatic_scroll = False

    def _scroll_to_bottom(self):
        if shiboken6.isValid(self.scroll):
            vbar = self.scroll.verticalScrollBar()
            self._programmatic_scroll = True
            try:
                vbar.setValue(vbar.maximum())
            finally:
                self._programmatic_scroll = False

    def _on_scroll_value_changed(self, value: int):
        if self._programmatic_scroll:
            return
        vbar = self.scroll.verticalScrollBar()
        vmax = vbar.maximum()
        if vmax <= 0:
            self._user_scroll_locked = False
            return
        if self._current_mode == "transcript":
            # 字幕轨同步模式下：用户主动滚动即锁定，若当前高亮行仍在视口可见范围内不强行死锁
            self._user_scroll_locked = True
        else:
            # OCR 流追加模式下：向上回看超过 32px 时锁定，回到底部恢复自动跟随
            self._user_scroll_locked = (vmax - value) > 32

    # ---------------- 查词交互与自动暂停联动 ----------------

    def _on_hud_word_clicked(self, word: str, global_pos: QPoint):
        if self._auto_pause and self._target_hwnd:
            self._playback_ctrl.pause_if_needed(self._target_hwnd)
        self.word_clicked.emit(word, self._active_en, self._active_cn, global_pos)

    def _on_row_word_clicked(self, word: str, en_text: str, cn_text: str, global_pos: QPoint):
        if self._auto_pause and self._target_hwnd:
            self._playback_ctrl.pause_if_needed(self._target_hwnd)
        self.word_clicked.emit(word, en_text, cn_text, global_pos)

    def on_dictionary_card_closed(self):
        """当词典卡片关闭时，若之前因查词自动暂停了视频，自动恢复播放。"""
        if self._auto_pause and self.isVisible():
            self._playback_ctrl.resume_if_paused()

    # ---------------- 窗口拖拽移动与边缘大小调节 ----------------

    def _is_interactive_child(self, pt: QPoint) -> bool:
        w = self.childAt(pt)
        while w is not None and w is not self:
            if isinstance(w, (VectorIconButton, QPushButton, QLineEdit, QScrollArea, QAbstractSlider)):
                return True
            if isinstance(w, InteractiveSubtitleText):
                local_pt = w.mapFrom(self, pt)
                if w._hit_test(local_pt) != -1:
                    return True
            w = w.parentWidget()
        return False

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pt = pts[0].position().toPoint()
                g_pt = pts[0].globalPosition().toPoint()
                et = event.type()
                if et == QEvent.Type.TouchBegin and not self._is_interactive_child(pt):
                    self._touch_press_pos = g_pt
                    if self._in_resize_corner(pt):
                        self._resizing = True
                        self._resize_start_global = g_pt
                        self._resize_start_size = self.size()
                    else:
                        self._dragging = True
                        self._drag_offset = g_pt - self.frameGeometry().topLeft()
                    event.accept()
                    return True
                elif et == QEvent.Type.TouchUpdate and (self._dragging or self._resizing):
                    if self._resizing:
                        delta = g_pt - self._resize_start_global
                        min_w = 300 if self._view_mode == MODE_INPLACE_HUD else 340
                        min_h = 42 if self._view_mode == MODE_INPLACE_HUD else 260
                        nw = max(min_w, min(1100, self._resize_start_size.width() + delta.x()))
                        nh = max(min_h, min(900, self._resize_start_size.height() + delta.y()))
                        self.resize(nw, nh)
                    else:
                        if self._view_mode == MODE_INPLACE_HUD and self._auto_follow:
                            self._auto_follow = False
                            self._update_pin_indicator()
                        self.move(g_pt - self._drag_offset)
                    event.accept()
                    return True
                elif et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel) and (self._dragging or self._resizing):
                    moved = (g_pt - getattr(self, "_touch_press_pos", g_pt)).manhattanLength()
                    if self._view_mode == MODE_INPLACE_HUD and et == QEvent.Type.TouchEnd and moved < 14 and not self._resizing:
                        if not self._hud_hovered:
                            self._show_hud_top()
                    elif self._view_mode == MODE_INPLACE_HUD and not self._auto_follow:
                        self._hud_user_pos = self.pos()
                        self._hud_size = self.size()
                        self._save_hud_settings()
                        if self._style_popover is not None:
                            self._style_popover.sync_state(self.get_hud_settings())
                    elif self._view_mode == MODE_CONSOLE_DRAWER:
                        self._console_user_pos = self.pos()
                        self._console_size = self.size()
                    self._dragging = False
                    self._resizing = False
                    event.accept()
                    return True
        return super().event(event)

    def _in_resize_corner(self, pt: QPoint) -> bool:
        return pt.x() >= self.width() - 18 and pt.y() >= self.height() - 18

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pt = event.position().toPoint()
            if self._in_resize_corner(pt):
                self._resizing = True
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
                event.accept()
                return
            if not self._is_interactive_child(pt):
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        pt = event.position().toPoint()
        if self._resizing and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._resize_start_global
            min_w = 300 if self._view_mode == MODE_INPLACE_HUD else 340
            min_h = 42 if self._view_mode == MODE_INPLACE_HUD else 260
            nw = max(min_w, min(1100, self._resize_start_size.width() + delta.x()))
            nh = max(min_h, min(900, self._resize_start_size.height() + delta.y()))
            self.resize(nw, nh)
            event.accept()
            return
        if self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._view_mode == MODE_INPLACE_HUD and self._auto_follow:
                # 手动拖拽原位字幕舱后切为固定用户位置，避免立刻弹回
                self._auto_follow = False
                self._update_pin_indicator()
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        if self._in_resize_corner(pt):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        elif not self._is_interactive_child(pt):
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging or self._resizing:
            if self._view_mode == MODE_INPLACE_HUD and not self._auto_follow:
                self._hud_user_pos = self.pos()
                self._hud_size = self.size()
                self._save_hud_settings()
                if self._style_popover is not None:
                    self._style_popover.sync_state(self.get_hud_settings())
            elif self._view_mode == MODE_CONSOLE_DRAWER:
                self._console_user_pos = self.pos()
                self._console_size = self.size()
            self._dragging = False
            self._resizing = False
            if self._view_mode == MODE_INPLACE_HUD:
                self._sample_and_apply_backdrop(force_repaint=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
