"""
ScreenLex 统一矢量图标与 Pure White Lexicon 色彩体系
纯 QPainter 抗锯齿矢量渲染，零 Emoji 字符。
"""
from __future__ import annotations

from collections import OrderedDict
import ctypes
from ctypes import wintypes
import math
import os
import re
import sys
import time

import shiboken6
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QMenu, QScrollArea, QAbstractButton
)
from PySide6.QtCore import Qt, QObject, QRect, QRectF, QPoint, QPointF, QEvent, QTimer, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QIcon, QPainterPath,
    QPolygonF, QCursor, QTouchEvent, QMouseEvent, QFont, QFontMetrics, QLinearGradient, QPalette, QRegion
)

LEXICON_THEME = {
    "bg": "#F9F8F6",
    "surface": "#F3F2EE",
    "hover_bg": "#EFECE6",
    "hover_neutral": "#EFECE6",
    "hover_danger": "#F5EFEF",
    "border": "#1C1C1C",
    "spine": "#816D70",
    "mauve": "#816D70",
    "divider": "#E3E2E0",
    "divider_strong": "#A4A3A2",
    "divider_soft": "#E3E2E0",
    "text": "#1C1C1C",
    "text_sec": "#4B4F56",
    "text_muted": "#6B7280",
    "accent": "#1C1C1C",
    "danger": "#816D70",
    "danger_title": "#816D70",
    "ok": "#1C1C1C",
    "warn_bg": "#F9F8F6",
    "warn_border": "#816D70",
}


def draw_vector_icon(
    painter: QPainter,
    kind: str,
    rect: QRectF,
    color: QColor,
    stroke_width: float = 1.15,
):
    """在指定矩形内绘制 1.15px Editorial 锐利风格矢量线框图标。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    pen = QPen(
        color,
        stroke_width,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.SquareCap,
        Qt.PenJoinStyle.MiterJoin,
    )
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    cx = rect.center().x()
    cy = rect.center().y()

    if kind == "speaker":
        poly = QPolygonF([
            QPointF(cx - 4.5, cy - 1.8),
            QPointF(cx - 2.0, cy - 1.8),
            QPointF(cx + 1.0, cy - 4.5),
            QPointF(cx + 1.0, cy + 4.5),
            QPointF(cx - 2.0, cy + 1.8),
            QPointF(cx - 4.5, cy + 1.8),
        ])
        painter.drawPolygon(poly)
        painter.drawLine(QPointF(cx + 3.2, cy - 2.4), QPointF(cx + 3.2, cy + 2.4))
        painter.drawLine(QPointF(cx + 5.4, cy - 4.2), QPointF(cx + 5.4, cy + 4.2))

    elif kind in ("bookmark_off", "bookmark_on"):
        path = QPainterPath()
        path.moveTo(cx - 4.0, cy - 5.5)
        path.lineTo(cx + 4.0, cy - 5.5)
        path.lineTo(cx + 4.0, cy + 5.5)
        path.lineTo(cx, cy + 2.2)
        path.lineTo(cx - 4.0, cy + 5.5)
        path.closeSubpath()
        if kind == "bookmark_on":
            painter.setBrush(QBrush(color))
        painter.drawPath(path)

    elif kind == "close":
        d = 4.2
        painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))

    elif kind == "minimize":
        painter.drawLine(QPointF(cx - 4.8, cy + 0.5), QPointF(cx + 4.8, cy + 0.5))

    elif kind == "search":
        painter.drawEllipse(QPointF(cx - 1.3, cy - 1.3), 4.1, 4.1)
        painter.drawLine(QPointF(cx + 1.8, cy + 1.8), QPointF(cx + 5.1, cy + 5.1))

    elif kind == "export":
        painter.drawLine(QPointF(cx - 4.5, cy + 1.5), QPointF(cx - 4.5, cy + 4.5))
        painter.drawLine(QPointF(cx - 4.5, cy + 4.5), QPointF(cx + 4.5, cy + 4.5))
        painter.drawLine(QPointF(cx + 4.5, cy + 4.5), QPointF(cx + 4.5, cy + 1.5))
        painter.drawLine(QPointF(cx, cy + 1.5), QPointF(cx, cy - 5.0))
        painter.drawLine(QPointF(cx - 2.8, cy - 2.2), QPointF(cx, cy - 5.0))
        painter.drawLine(QPointF(cx + 2.8, cy - 2.2), QPointF(cx, cy - 5.0))

    elif kind == "reticle":
        r = 6.2
        k = 2.6
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            px = cx + dx * r
            py = cy + dy * r
            painter.drawLine(QPointF(px, py), QPointF(px - dx * k, py))
            painter.drawLine(QPointF(px, py), QPointF(px, py - dy * k))
        painter.drawLine(QPointF(cx - 2.0, cy - 3.0), QPointF(cx + 2.0, cy - 3.0))
        painter.drawLine(QPointF(cx, cy - 3.0), QPointF(cx, cy + 3.0))
        painter.drawLine(QPointF(cx - 2.0, cy + 3.0), QPointF(cx + 2.0, cy + 3.0))

    elif kind == "subtitle":
        # 直角字幕屏幕框 + 居中双行对白线
        painter.drawRect(QRectF(cx - 6.0, cy - 4.5, 12.0, 9.0))
        painter.drawLine(QPointF(cx - 3.4, cy + 0.6), QPointF(cx + 3.4, cy + 0.6))
        painter.drawLine(QPointF(cx - 2.4, cy + 2.5), QPointF(cx + 2.4, cy + 2.5))

    elif kind == "dock":
        # 3 条纯直平行线（对齐 Editorial 图二 ≡ 图标）
        for dy in (-3.5, 0.0, 3.5):
            painter.drawLine(QPointF(cx - 5.5, cy + dy), QPointF(cx + 5.5, cy + dy))

    elif kind == "pin":
        # 方形准星靶标（对齐 Editorial 图二 ⌖ 图标，无圆环无实点）
        painter.drawRect(QRectF(cx - 4.2, cy - 4.2, 8.4, 8.4))
        painter.drawLine(QPointF(cx - 6.6, cy), QPointF(cx - 3.2, cy))
        painter.drawLine(QPointF(cx + 3.2, cy), QPointF(cx + 6.6, cy))
        painter.drawLine(QPointF(cx, cy - 6.6), QPointF(cx, cy - 3.2))
        painter.drawLine(QPointF(cx, cy + 3.2), QPointF(cx, cy + 6.6))

    elif kind == "pause_play":
        # 极简双竖线暂停（对齐 Editorial 图二 || 图标，无外圆环）
        painter.drawLine(QPointF(cx - 2.3, cy - 4.4), QPointF(cx - 2.3, cy + 4.4))
        painter.drawLine(QPointF(cx + 2.3, cy - 4.4), QPointF(cx + 2.3, cy + 4.4))

    elif kind == "style":
        # Georgia 衬线 Aa 排版样式图标（对齐 Editorial 图二 Aa 图标）
        tf = QFont()
        tf.setFamilies(["Georgia", "Times New Roman", "serif"])
        tf.setPointSizeF(9.5)
        tf.setWeight(QFont.Weight.Normal)
        painter.setFont(tf)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Aa")

    elif kind == "link":
        painter.translate(cx, cy)
        painter.rotate(-42.0)
        painter.drawArc(QRectF(-5.5, -2.3, 5.2, 4.6), 45 * 16, 270 * 16)
        painter.drawArc(QRectF(0.3, -2.3, 5.2, 4.6), 225 * 16, 270 * 16)
        painter.drawLine(QPointF(-1.8, 0.0), QPointF(1.8, 0.0))

    elif kind == "trash":
        painter.drawLine(QPointF(cx - 4.4, cy - 3.2), QPointF(cx + 4.4, cy - 3.2))
        painter.drawLine(QPointF(cx - 1.6, cy - 4.8), QPointF(cx + 1.6, cy - 4.8))
        painter.drawRect(QRectF(cx - 3.4, cy - 3.2, 6.8, 7.6))
        painter.drawLine(QPointF(cx - 1.1, cy - 1.2), QPointF(cx - 1.1, cy + 2.6))
        painter.drawLine(QPointF(cx + 1.1, cy - 1.2), QPointF(cx + 1.1, cy + 2.6))

    elif kind == "sliders":
        for dy, kx in ((-3.5, -1.6), (0.0, 2.0), (3.5, -0.5)):
            y = cy + dy
            if kx - 1.6 > -5.2:
                painter.drawLine(QPointF(cx - 5.2, y), QPointF(cx + kx - 1.6, y))
            if kx + 1.6 < 5.2:
                painter.drawLine(QPointF(cx + kx + 1.6, y), QPointF(cx + 5.2, y))
            painter.drawEllipse(QPointF(cx + kx, y), 1.5, 1.5)

    elif kind == "qrcode":
        for ox, oy in ((-4.8, -4.8), (1.2, -4.8), (-4.8, 1.2)):
            painter.drawRect(QRectF(cx + ox, cy + oy, 3.6, 3.6))
        painter.setBrush(QBrush(color))
        painter.drawRect(QRectF(cx + 1.4, cy + 1.4, 1.3, 1.3))
        painter.drawRect(QRectF(cx + 3.5, cy + 1.4, 1.3, 1.3))
        painter.drawRect(QRectF(cx + 3.5, cy + 3.5, 1.3, 1.3))
        painter.drawRect(QRectF(cx + 1.4, cy + 3.5, 1.3, 1.3))

    elif kind == "sun":
        # 暖米纸主题切换图标（1.15px 极简太阳）
        painter.drawEllipse(QPointF(cx, cy), 2.3, 2.3)
        for i in range(8):
            ang = i * (math.pi / 4.0)
            ca, sa = math.cos(ang), math.sin(ang)
            painter.drawLine(
                QPointF(cx + ca * 3.9, cy + sa * 3.9),
                QPointF(cx + ca * 5.4, cy + sa * 5.4),
            )

    elif kind == "moon":
        # 墨黑主题切换图标（1.15px 极简新月）
        outer = QPainterPath()
        outer.addEllipse(QPointF(cx + 0.3, cy), 4.5, 4.5)
        cut = QPainterPath()
        cut.addEllipse(QPointF(cx + 2.6, cy - 1.7), 3.6, 3.6)
        crescent = outer.subtracted(cut)
        painter.drawPath(crescent)

    painter.restore()


def make_vector_qicon(kind: str, color: str, size: int = 16, dpr: float = 2.0) -> QIcon:
    """将矢量图标光栅化为高分屏 QIcon，供 QPushButton / QLineEdit Action 使用。"""
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    draw_vector_icon(painter, kind, QRectF(0, 0, size, size), QColor(color))
    painter.end()
    return QIcon(pm)


_ICON_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


class VectorIconButton(QWidget):
    """
    轻量纯矢量图形微按钮（可选配简短文字标签），支持触屏与鼠标悬停状态，支持 rgba(...) 半透明底色。
    """
    clicked = Signal()

    @staticmethod
    def _parse_qcolor(val: str) -> QColor:
        if not val:
            return QColor(0, 0, 0, 0)
        s = val.strip()
        if s.lower() == "transparent":
            return QColor(0, 0, 0, 0)
        m = re.match(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
        if m:
            return QColor(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        return QColor(s)

    def __init__(
        self,
        kind: str,
        color: str = LEXICON_THEME["text_muted"],
        hover_color: str = LEXICON_THEME["accent"],
        hover_bg: str = LEXICON_THEME["hover_neutral"],
        size: int = 24,
        radius: int = 4,
        label: str = "",
        bg_color: str = "transparent",
        parent=None,
    ):
        super().__init__(parent)
        self.kind = kind
        self._base_size = int(size)
        self._label = (label or "").strip()
        self._color = self._parse_qcolor(color)
        self._hover_color = self._parse_qcolor(hover_color)
        self._hover_bg = self._parse_qcolor(hover_bg)
        self._bg_color = self._parse_qcolor(bg_color)
        self._radius = radius
        self.is_hovered = False
        self._pressed = False
        self._opacity: float = 1.0
        self._last_touch_ts: float = 0.0
        self._update_fixed_size()
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    def label(self) -> str:
        return self._label

    def set_label(self, label: str):
        self._label = (label or "").strip()
        self._update_fixed_size()
        self.update()

    def _update_fixed_size(self):
        if not self._label:
            self.setFixedSize(self._base_size, self._base_size)
            return
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(font)
        icon_box = max(12, min(15, self._base_size - 6))
        text_w = fm.horizontalAdvance(self._label)
        total_w = 6 + icon_box + 3 + text_w + 7
        self.setFixedSize(total_w, self._base_size)

    def set_color(self, color: str):
        self._color = self._parse_qcolor(color)
        self.update()

    def set_opacity(self, alpha: float):
        a = max(0.0, min(1.0, float(alpha)))
        if abs(self._opacity - a) > 0.005:
            self._opacity = a
            self.update()

    def set_kind(self, kind: str):
        if self.kind != kind:
            self.kind = kind
            self.update()

    def set_palette(self, color: str, hover_color: str, hover_bg: str):
        self._color = self._parse_qcolor(color)
        self._hover_color = self._parse_qcolor(hover_color)
        self._hover_bg = self._parse_qcolor(hover_bg)
        self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self.update()

    def leaveEvent(self, event):
        self.is_hovered = False
        self._pressed = False
        self.update()

    def mousePressEvent(self, event):
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and (time.monotonic() - self._last_touch_ts) < 0.45:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and (time.monotonic() - self._last_touch_ts) < 0.45:
            self._pressed = False
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
            return
        self._pressed = False
        self.update()

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pt = pts[0].position().toPoint()
                if event.type() == QEvent.Type.TouchBegin:
                    self._pressed = True
                    self.update()
                    event.accept()
                    return True
                elif event.type() == QEvent.Type.TouchEnd:
                    was_pressed = self._pressed
                    self._pressed = False
                    self.update()
                    if was_pressed and self.rect().adjusted(-4, -4, 4, 4).contains(pt):
                        self._last_touch_ts = time.monotonic()
                        self.clicked.emit()
                    event.accept()
                    return True
                elif event.type() == QEvent.Type.TouchCancel:
                    self._pressed = False
                    self.update()
                    event.accept()
                    return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._opacity < 0.999:
            painter.setOpacity(self._opacity)
        if self.is_hovered or self._pressed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._hover_bg)
            if self._radius <= 0:
                painter.drawRect(self.rect())
            else:
                painter.drawRoundedRect(self.rect(), self._radius, self._radius)
        elif self._bg_color.alpha() > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._bg_color)
            if self._radius <= 0:
                painter.drawRect(self.rect())
            else:
                painter.drawRoundedRect(self.rect(), self._radius, self._radius)
        icon_color = self._hover_color if (self.is_hovered or self._pressed) else self._color
        dpr = self.devicePixelRatioF()
        key = (self.kind, self._label, icon_color.rgba(), self.width(), self.height(), round(dpr, 2))
        pix = _ICON_PIXMAP_CACHE.get(key)
        if pix is None:
            if len(_ICON_PIXMAP_CACHE) > 96:
                _ICON_PIXMAP_CACHE.clear()
            pix = QPixmap(int(round(self.width() * dpr)), int(round(self.height() * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            ip = QPainter(pix)
            ip.setRenderHint(QPainter.RenderHint.Antialiasing)
            ip.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            if self._label:
                icon_box = max(12, min(15, self._base_size - 6))
                icon_rect = QRectF(6.0, (self.height() - icon_box) / 2.0, float(icon_box), float(icon_box))
                draw_vector_icon(ip, self.kind, icon_rect, icon_color, stroke_width=1.15)
                font = QFont("Microsoft YaHei UI")
                font.setPixelSize(11)
                font.setWeight(QFont.Weight.DemiBold)
                ip.setFont(font)
                ip.setPen(icon_color)
                text_x = 6.0 + icon_box + 3.0
                text_rect = QRectF(text_x, 0.0, float(self.width()) - text_x - 5.0, float(self.height()))
                ip.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)
            else:
                draw_vector_icon(ip, self.kind, QRectF(0, 0, self.width(), self.height()), icon_color)
            ip.end()
            _ICON_PIXMAP_CACHE[key] = pix
        painter.drawPixmap(0, 0, pix)


OVERLAY_COLORS = {
    "navy_border": QColor(26, 54, 93, 245),
    "navy_fill": QColor(26, 54, 93, 42),
    "navy_fill_locked": QColor(26, 54, 93, 52),
    "navy_stroke": QColor(26, 54, 93, 215),
    "halo_light": QColor(255, 255, 255, 170),
    "save_border": QColor(27, 94, 68, 245),
    "save_fill": QColor(27, 94, 68, 52),
    "save_hud_bg": QColor(27, 94, 68, 245),
    "save_hud_border": QColor(45, 134, 98, 220),
    "cancel_border": QColor(161, 40, 59, 245),
    "cancel_fill": QColor(161, 40, 59, 48),
    "cancel_strike": QColor(255, 228, 232, 220),
    "cancel_hud_bg": QColor(139, 38, 53, 245),
    "cancel_hud_border": QColor(186, 62, 80, 220),
    "hud_bg": QColor(24, 24, 27, 242),
    "hud_border": QColor(255, 255, 255, 38),
    "hud_divider": QColor(255, 255, 255, 36),
    "hud_text": QColor(244, 244, 245),
    "hud_text_muted": QColor(212, 212, 216),
    "hud_text_cancel": QColor(244, 163, 174),
    "hud_text_save": QColor(134, 214, 180),
}


def draw_hud_bar(
    painter: QPainter,
    rect: QRect,
    text: str,
    bg: QColor = None,
    border: QColor = None,
    fg: QColor = None,
    radius: int = 5,
    font_size: int = 9,
    bold: bool = False,
):
    """绘制与图标弧度协调的柔和矩形 HUD 提示条（替代暗色大圆角胶囊）。"""
    bg_c = bg or OVERLAY_COLORS["hud_bg"]
    border_c = border or OVERLAY_COLORS["hud_border"]
    fg_c = fg or OVERLAY_COLORS["hud_text"]
    painter.setPen(QPen(border_c, 1))
    painter.setBrush(QBrush(bg_c))
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(fg_c)
    weight = QFont.Weight.DemiBold if bold else QFont.Weight.Normal
    painter.setFont(QFont("Microsoft YaHei UI", font_size, weight))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def draw_triage_split_hud(painter: QPainter, rect: QRect):
    """绘制锁定态三段式分流 HUD 提示条。"""
    painter.setPen(QPen(OVERLAY_COLORS["hud_border"], 1))
    painter.setBrush(QBrush(OVERLAY_COLORS["hud_bg"]))
    painter.drawRoundedRect(rect, 5, 5)

    w = rect.width()
    seg1_w = int(w * 0.30)
    seg2_w = int(w * 0.40)
    seg3_w = w - seg1_w - seg2_w

    left_seg = QRect(rect.left(), rect.top(), seg1_w, rect.height())
    mid_seg = QRect(rect.left() + seg1_w, rect.top(), seg2_w, rect.height())
    right_seg = QRect(rect.left() + seg1_w + seg2_w, rect.top(), seg3_w, rect.height())

    painter.setPen(QPen(OVERLAY_COLORS["hud_divider"], 1))
    painter.drawLine(left_seg.right(), rect.top() + 6, left_seg.right(), rect.bottom() - 6)
    painter.drawLine(mid_seg.right(), rect.top() + 6, mid_seg.right(), rect.bottom() - 6)

    painter.setFont(QFont("Microsoft YaHei UI", 9))
    painter.setPen(OVERLAY_COLORS["hud_text_cancel"])
    painter.drawText(left_seg, Qt.AlignmentFlag.AlignCenter, "上滑 取消")
    painter.setPen(OVERLAY_COLORS["hud_text"])
    painter.drawText(mid_seg, Qt.AlignmentFlag.AlignCenter, "松手查词")
    painter.setPen(OVERLAY_COLORS["hud_text_save"])
    painter.drawText(right_seg, Qt.AlignmentFlag.AlignCenter, "下滑 收藏")


def clear_layout(layout):
    """立即从布局中摘除并隐藏旧子控件，避免 deleteLater() 延迟析构导致重绘残影重叠。"""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


class LexiconCardFrame(QFrame):
    """
    带平滑抗锯齿圆角边框的 Pure White Lexicon 容器，
    统一绘制纯白纸面底色与平滑 1px 外框，消除暗色背景下顶边错位观感。
    """

    def __init__(self, radius: int = 10, spine_height: float = 0.0, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._spine_height = spine_height

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)

        painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
        painter.setBrush(QBrush(QColor(LEXICON_THEME["bg"])))
        if self._radius <= 0:
            painter.drawRect(rect)
        else:
            painter.drawRoundedRect(rect, self._radius, self._radius)

        if self._spine_height > 0:
            clip = QPainterPath()
            if self._radius <= 0:
                clip.addRect(rect)
            else:
                clip.addRoundedRect(rect, self._radius, self._radius)
            painter.save()
            painter.setClipPath(clip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(LEXICON_THEME["spine"]))
            painter.drawRect(QRectF(0, 0, self.width(), self._spine_height))
            painter.restore()


class EditorialPullQuoteFrame(QFrame):
    """Editorial 引文 Pull Quote 容器：无外框无阴影，仅绘制左侧 2px 陈玫竖脊与可选大号衬线左引号。"""

    def __init__(self, parent=None, *, show_quote_mark: bool = True, top_pad: float = 4.0):
        super().__init__(parent)
        self._show_quote_mark = show_quote_mark
        self._top_pad = top_pad

    def set_quote_mark_visible(self, visible: bool):
        self._show_quote_mark = bool(visible)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        h = float(self.height())
        if h > 8.0:
            painter.fillRect(
                QRectF(2.0, self._top_pad, 2.0, max(12.0, h - self._top_pad * 2.0)),
                QColor(LEXICON_THEME["mauve"]),
            )
            if self._show_quote_mark:
                q_font = QFont()
                q_font.setFamilies(["Georgia", "Noto Serif SC", "STSong", "Microsoft YaHei UI"])
                q_font.setPointSizeF(22.0)
                painter.setFont(q_font)
                painter.setPen(QColor(LEXICON_THEME["mauve"]))
                painter.drawText(QPointF(14.0, self._top_pad + 20.0), "“")
        painter.end()
        super().paintEvent(event)


def editorial_scrollbar_qss(bg_color: str = LEXICON_THEME["bg"], width: int = 4) -> str:
    """Editorial 极细直角 Ink 滚动条样式（零圆角、暖米纸轨、2px 视觉墨黑滑块）。"""
    return f"""
        QScrollArea {{ border: none; background: {bg_color}; }}
        QScrollBar:vertical {{
            width: {width}px;
            background: {bg_color};
            padding: 2px 1px 2px 1px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: #1C1C1C;
            border-radius: 0px;
            min-height: 26px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #816D70;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
            background: {bg_color};
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: {bg_color};
        }}
    """


class EditorialPopupMenu(QMenu):
    """Editorial 直角无阴影右键/托盘弹出菜单（在 Win11 强制关闭系统圆角并绘制单层 1px 墨黑外框）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native_chrome = supports_win11_native_chrome()
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setStyleSheet(f"""
            QMenu {{
                background-color: {LEXICON_THEME["bg"]};
                color: {LEXICON_THEME["text"]};
                border: none;
                border-radius: 0px;
                padding: 5px 1px;
                font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", sans-serif;
                font-size: 12.5px;
                font-weight: 400;
            }}
            QMenu::icon {{
                padding-left: 12px;
            }}
            QMenu::indicator {{
                width: 0px;
                height: 0px;
                margin: 0px;
                padding: 0px;
            }}
            QMenu::item {{
                padding: 7px 20px 7px 14px;
                border-radius: 0px;
                color: {LEXICON_THEME["text"]};
            }}
            QMenu::item:selected {{
                background-color: {LEXICON_THEME["hover_bg"]};
                color: {LEXICON_THEME["text"]};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {LEXICON_THEME["divider"]};
                margin: 4px 0px;
            }}
        """)

    def showEvent(self, event):
        super().showEvent(event)
        if self._native_chrome:
            apply_native_win11_chrome(self, corner_pref=1, border_color=LEXICON_THEME["border"])

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.75, 0.75, max(1.0, self.width() - 1.5), max(1.0, self.height() - 1.5)))
        painter.end()


def draw_ambient_card_shadow(
    painter: QPainter,
    card_rect: QRect,
    radius: int = 10,
    layers: int = 6,
    offset_y: float = 2.0,
):
    """
    在顶层透明窗口边缘直接绘制轻量多层柔光阴影，
    替代阻塞每帧滚动画面的 CPU 级 QGraphicsDropShadowEffect 全卡片高斯模糊。
    """
    if card_rect.isEmpty() or card_rect.left() < 2:
        return
    max_spread = min(8.5, float(max(3, card_rect.left() - 1)))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.setPen(Qt.PenStyle.NoPen)
    base = QRectF(card_rect)
    for step in range(layers, 0, -1):
        ratio = step / float(layers)
        spread = max_spread * ratio
        dy = min(max_spread * 0.45, offset_y * ratio)
        shadow_rect = base.adjusted(-spread, -spread * 0.45 + dy, spread, spread * 0.85 + dy)
        alpha = max(1, int(round((1.0 - ratio * 0.72) * 5.0)))
        painter.setBrush(QColor(15, 23, 42, alpha))
        painter.drawRoundedRect(shadow_rect, radius + spread * 0.55, radius + spread * 0.55)
    painter.restore()


class _DWM_MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class _DWM_UNSIGNED_RATIO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("uiNumerator", ctypes.c_uint32), ("uiDenominator", ctypes.c_uint32)]


class _DWM_TIMING_INFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("rateRefresh", _DWM_UNSIGNED_RATIO),
        ("qpcRefreshPeriod", ctypes.c_uint64),
        ("rateCompose", _DWM_UNSIGNED_RATIO),
        ("qpcVBlank", ctypes.c_uint64),
        ("cRefresh", ctypes.c_uint64),
        ("cDXRefresh", ctypes.c_uint32),
        ("qpcCompose", ctypes.c_uint64),
        ("cFrame", ctypes.c_uint64),
        ("cDXPresent", ctypes.c_uint32),
        ("cRefreshFrame", ctypes.c_uint64),
        ("cFrameSubmitted", ctypes.c_uint64),
        ("cDXPresentSubmitted", ctypes.c_uint32),
        ("cFrameConfirmed", ctypes.c_uint64),
        ("cDXPresentConfirmed", ctypes.c_uint32),
        ("cRefreshConfirmed", ctypes.c_uint64),
        ("cDXRefreshConfirmed", ctypes.c_uint32),
        ("cFramesLate", ctypes.c_uint64),
        ("cFramesOutstanding", ctypes.c_uint32),
        ("cFrameDisplayed", ctypes.c_uint64),
        ("qpcFrameDisplayed", ctypes.c_uint64),
        ("cRefreshFrameDisplayed", ctypes.c_uint64),
        ("cFrameComplete", ctypes.c_uint64),
        ("qpcFrameComplete", ctypes.c_uint64),
        ("cFramePending", ctypes.c_uint64),
        ("qpcFramePending", ctypes.c_uint64),
        ("cFramesDisplayed", ctypes.c_uint64),
        ("cFramesComplete", ctypes.c_uint64),
        ("cFramesPending", ctypes.c_uint64),
        ("cFramesAvailable", ctypes.c_uint64),
        ("cFramesDropped", ctypes.c_uint64),
        ("cFramesMissed", ctypes.c_uint64),
        ("cRefreshNextDisplayed", ctypes.c_uint64),
        ("cRefreshNextPresented", ctypes.c_uint64),
        ("cRefreshesDisplayed", ctypes.c_uint64),
        ("cRefreshesPresented", ctypes.c_uint64),
        ("cRefreshStarted", ctypes.c_uint64),
        ("cPixelsReceived", ctypes.c_uint64),
        ("cPixelsDrawn", ctypes.c_uint64),
        ("cBuffersEmpty", ctypes.c_uint64),
    ]


_QPC_FREQ: float | None = None


def _get_dwm_timing_sec() -> tuple[float, float, float] | None:
    """返回 Windows DWM (qpcVBlank_sec, refresh_period_sec, qpc_now_sec)，用于精确锁定 VBlank 相位。"""
    global _QPC_FREQ
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        if _QPC_FREQ is None:
            fq = ctypes.c_int64()
            if not kernel32.QueryPerformanceFrequency(ctypes.byref(fq)) or fq.value <= 0:
                return None
            _QPC_FREQ = float(fq.value)
        ti = _DWM_TIMING_INFO()
        ti.cbSize = ctypes.sizeof(_DWM_TIMING_INFO)
        if ctypes.windll.dwmapi.DwmGetCompositionTimingInfo(None, ctypes.byref(ti)) != 0:
            return None
        if ti.qpcRefreshPeriod <= 0:
            return None
        qnow = ctypes.c_int64()
        kernel32.QueryPerformanceCounter(ctypes.byref(qnow))
        return (
            float(ti.qpcVBlank) / _QPC_FREQ,
            float(ti.qpcRefreshPeriod) / _QPC_FREQ,
            float(qnow.value) / _QPC_FREQ,
        )
    except Exception:
        return None


def supports_win11_native_chrome() -> bool:
    """检测是否为 Windows 11 (Build >= 22000)，可启用硬件圆角与 DWM 原生阴影以彻底摆脱 UpdateLayeredWindow CPU 拷贝瓶颈。"""
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False


def apply_native_win11_chrome(
    widget: QWidget,
    corner_pref: int = 2,
    border_color: str = LEXICON_THEME["border"],
) -> bool:
    """为非分层顶层无边框窗口启用 Win11 DWM 原生抗锯齿圆角、1px 细边框与硬件合成投影（64位句柄安全）。"""
    if not supports_win11_native_chrome() or not shiboken6.isValid(widget):
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        dwmapi = _Z_DWMAPI or ctypes.WinDLL("dwmapi", use_last_error=True)
        if not getattr(dwmapi.DwmSetWindowAttribute, "argtypes", None):
            dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        if not getattr(dwmapi.DwmExtendFrameIntoClientArea, "argtypes", None):
            dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(_DWM_MARGINS)]
            dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
        hw = wintypes.HWND(hwnd)
        pref = ctypes.c_int(corner_pref)
        dwmapi.DwmSetWindowAttribute(hw, wintypes.DWORD(33), ctypes.byref(pref), wintypes.DWORD(ctypes.sizeof(pref)))
        c = QColor(border_color)
        if c.isValid():
            colorref = ctypes.c_uint32((c.blue() << 16) | (c.green() << 8) | c.red())
            dwmapi.DwmSetWindowAttribute(hw, wintypes.DWORD(34), ctypes.byref(colorref), wintypes.DWORD(ctypes.sizeof(colorref)))
        margins = _DWM_MARGINS(0, 0, 0, 1)
        dwmapi.DwmExtendFrameIntoClientArea(hw, ctypes.byref(margins))
        return True
    except Exception:
        return False


def normalize_win32_process_priority():
    """若进程被任务计划程序或后台启动继承为 BelowNormal/Idle，自动恢复至 Normal 优先级保障 165Hz UI 调度。"""
    if sys.platform != "win32":
        return
    try:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
        kernel32.GetPriorityClass.restype = wintypes.DWORD
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        hproc = kernel32.GetCurrentProcess()
        prio = kernel32.GetPriorityClass(hproc)
        if prio in (0x00004000, 0x00000040):  # BELOW_NORMAL / IDLE
            kernel32.SetPriorityClass(hproc, 0x00000020)  # NORMAL_PRIORITY_CLASS
    except Exception:
        pass


_Z_USER32 = None
_Z_DWMAPI = None
if sys.platform == "win32":
    try:
        _Z_USER32 = ctypes.WinDLL("user32", use_last_error=True)
        _Z_USER32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
        ]
        _Z_USER32.SetWindowPos.restype = wintypes.BOOL
        _Z_USER32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        _Z_USER32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        _Z_USER32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        _Z_USER32.GetWindow.restype = wintypes.HWND
        _Z_USER32.IsWindowVisible.argtypes = [wintypes.HWND]
        _Z_USER32.IsWindowVisible.restype = wintypes.BOOL
        _Z_USER32.IsIconic.argtypes = [wintypes.HWND]
        _Z_USER32.IsIconic.restype = wintypes.BOOL
        _Z_USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        _Z_USER32.ShowWindow.restype = wintypes.BOOL
        _Z_USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _Z_USER32.GetWindowRect.restype = wintypes.BOOL
        _Z_USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _Z_USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _Z_USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _Z_USER32.GetClassNameW.restype = ctypes.c_int
        _Z_USER32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        _Z_USER32.RegisterWindowMessageW.restype = wintypes.UINT
    except Exception:
        _Z_USER32 = None
    try:
        _Z_DWMAPI = ctypes.WinDLL("dwmapi", use_last_error=True)
        _Z_DWMAPI.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
        ]
        _Z_DWMAPI.DwmGetWindowAttribute.restype = ctypes.c_long
        _Z_DWMAPI.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
        ]
        _Z_DWMAPI.DwmSetWindowAttribute.restype = ctypes.c_long
        _Z_DWMAPI.DwmExtendFrameIntoClientArea.argtypes = [
            wintypes.HWND, ctypes.POINTER(_DWM_MARGINS)
        ]
        _Z_DWMAPI.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    except Exception:
        _Z_DWMAPI = None

_HWND_TOPMOST = ctypes.c_void_p(-1)
_WS_EX_TOPMOST = 0x00000008
_WS_EX_LAYERED_TRANSPARENT = 0x00080020
_DWMWA_CLOAKED = 14
_GWL_EXSTYLE = -20
_GW_HWNDPREV = 3
_SW_SHOWNOACTIVATE = 4
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200
_OWN_PID = os.getpid()
_TASKBAR_CREATED_MSG_ID: int = 0
_SHELL_POPUP_CLASSES = frozenset({
    "#32768",
    "Windows.UI.Core.CoreWindow",
    "XamlExplorerHostIslandWindow",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
    "MultitaskingViewFrame",
    "TaskListThumbnailWnd",
    "tooltips_class32",
    "MSCTFIME UI",
    "IME",
    "EdgeUiInputTopWndClass",
    "Windows.UI.Input.InputSite.WindowClass",
})


def get_taskbar_created_msg_id() -> int:
    """缓存 Windows TaskbarCreated 广播消息 ID，用于在资源管理器重启/开机延迟初始化后恢复微柄置顶。"""
    global _TASKBAR_CREATED_MSG_ID
    if _TASKBAR_CREATED_MSG_ID == 0 and _Z_USER32 is not None:
        try:
            _TASKBAR_CREATED_MSG_ID = int(_Z_USER32.RegisterWindowMessageW("TaskbarCreated") or 0)
        except Exception:
            _TASKBAR_CREATED_MSG_ID = 0
    return _TASKBAR_CREATED_MSG_ID


def enforce_window_topmost(widget: QWidget, activate: bool = False) -> bool:
    """通过 64 位安全的 Win32 SetWindowPos(HWND_TOPMOST) 强制置顶窗口，默认绝不抢占焦点。"""
    if sys.platform != "win32" or _Z_USER32 is None or not shiboken6.isValid(widget):
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOOWNERZORDER
        if not activate:
            flags |= _SWP_NOACTIVATE
        return bool(_Z_USER32.SetWindowPos(wintypes.HWND(hwnd), _HWND_TOPMOST, 0, 0, 0, 0, flags))
    except Exception:
        return False


def _is_dwm_cloaked(hwnd) -> bool:
    if _Z_DWMAPI is None or not hwnd:
        return False
    try:
        cloaked = wintypes.DWORD(0)
        hr = int(_Z_DWMAPI.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)))
        return hr == 0 and int(cloaked.value) != 0
    except Exception:
        return False


def _find_topmost_blocker(widget: QWidget) -> tuple[bool, int]:
    """
    返回 (need_restore, blocker_hwnd)：
    - need_restore=True 且 blocker_hwnd=0：窗口本身已最小化/不可见或失去 WS_EX_TOPMOST，必须立即置顶。
    - need_restore=True 且 blocker_hwnd!=0：存在可见外部窗口压在本窗之上（blocker_hwnd 为最顶层遮挡句柄；若为高权限 TOPMOST 窗用于抗 UIPI 自旋）。
    """
    if sys.platform != "win32" or _Z_USER32 is None or not shiboken6.isValid(widget) or not widget.isVisible():
        return False, 0
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False, 0
        hw = wintypes.HWND(hwnd)
        if _Z_USER32.IsIconic(hw) or not _Z_USER32.IsWindowVisible(hw):
            return True, 0
        ex_style = int(_Z_USER32.GetWindowLongPtrW(hw, _GWL_EXSTYLE))
        if not (ex_style & _WS_EX_TOPMOST):
            return True, 0

        self_rc = wintypes.RECT()
        if not _Z_USER32.GetWindowRect(hw, ctypes.byref(self_rc)):
            return False, 0
        if self_rc.right <= self_rc.left or self_rc.bottom <= self_rc.top:
            return False, 0

        cur = _Z_USER32.GetWindow(hw, _GW_HWNDPREV)
        steps = 0
        other_pid = wintypes.DWORD()
        other_rc = wintypes.RECT()
        cls_buf = ctypes.create_unicode_buffer(128)
        while cur and steps < 64:
            steps += 1
            if _Z_USER32.IsWindowVisible(cur) and not _Z_USER32.IsIconic(cur) and not _is_dwm_cloaked(cur):
                other_pid.value = 0
                tid = int(_Z_USER32.GetWindowThreadProcessId(cur, ctypes.byref(other_pid)) or 0)
                if tid and other_pid.value and int(other_pid.value) != _OWN_PID:
                    cls_buf.value = ""
                    cls_len = int(_Z_USER32.GetClassNameW(cur, cls_buf, 128) or 0)
                    cls_name = cls_buf.value if cls_len > 0 else ""
                    if cls_name not in _SHELL_POPUP_CLASSES:
                        other_ex = int(_Z_USER32.GetWindowLongPtrW(cur, _GWL_EXSTYLE))
                        if (other_ex & _WS_EX_LAYERED_TRANSPARENT) != _WS_EX_LAYERED_TRANSPARENT:
                            other_rc.left = other_rc.top = other_rc.right = other_rc.bottom = 0
                            if _Z_USER32.GetWindowRect(cur, ctypes.byref(other_rc)):
                                has_area = (other_rc.right > other_rc.left) and (other_rc.bottom > other_rc.top)
                                intersects = has_area and (
                                    other_rc.right > self_rc.left
                                    and other_rc.left < self_rc.right
                                    and other_rc.bottom > self_rc.top
                                    and other_rc.top < self_rc.bottom
                                )
                                if intersects:
                                    # 若上方是非置顶普通窗，绝不缓存 UIPI 阻断；若上方本身是 TOPMOST，返回其句柄供抗 UIPI 自旋校验
                                    blocker_id = int(ctypes.cast(cur, ctypes.c_void_p).value or 0) if (other_ex & _WS_EX_TOPMOST) else 0
                                    return True, blocker_id
            cur = _Z_USER32.GetWindow(cur, _GW_HWNDPREV)
        return False, 0
    except Exception:
        return False, 0


def is_window_topmost_obscured(widget: QWidget) -> bool:
    """检测已显示置顶窗口是否发生 WS_EX_TOPMOST 脱落或被外部可见非幽灵窗口在 Z-order 中压盖。"""
    need, _ = _find_topmost_blocker(widget)
    return need


def restore_topmost_if_needed(widget: QWidget, force: bool = False) -> bool:
    """
    仅当可见窗口发生降层/遮挡（或 force=True）时无感恢复置顶：
    - 已在最顶层时零开销返回 False；
    - 对管理员权限 TOPMOST 遮挡窗内置抗 UIPI 自旋：若首次 SetWindowPos 后仍无法越过同一高权限句柄，缓存跳过直至遮挡变化或自身丢失 TOPMOST。
    """
    if sys.platform != "win32" or _Z_USER32 is None or not shiboken6.isValid(widget) or not widget.isVisible():
        return False
    need, blocker_hwnd = _find_topmost_blocker(widget)
    if not force:
        if not need:
            if getattr(widget, "_uipi_blocked_hwnd", 0):
                widget._uipi_blocked_hwnd = 0
            return False
        if blocker_hwnd and blocker_hwnd == getattr(widget, "_uipi_blocked_hwnd", 0):
            return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        hw = wintypes.HWND(hwnd)
        if _Z_USER32.IsIconic(hw) or not _Z_USER32.IsWindowVisible(hw):
            _Z_USER32.ShowWindow(hw, _SW_SHOWNOACTIVATE)
        ok = enforce_window_topmost(widget, activate=False)
        if not force and blocker_hwnd:
            still_need, new_blocker = _find_topmost_blocker(widget)
            if still_need and new_blocker == blocker_hwnd:
                widget._uipi_blocked_hwnd = blocker_hwnd
            elif not still_need or new_blocker != blocker_hwnd:
                widget._uipi_blocked_hwnd = 0
        else:
            widget._uipi_blocked_hwnd = 0
        return ok
    except Exception:
        return False


def _set_winmm_timer_period(enable: bool):
    if sys.platform != "win32":
        return
    try:
        winmm = ctypes.windll.winmm
        if enable:
            winmm.timeBeginPeriod(1)
        else:
            winmm.timeEndPeriod(1)
    except Exception:
        pass


class SmoothScrollController(QObject):
    """
    屏幕刷新率自适应 + 相位锁定高精度帧步进的阻尼平滑滚动控制器：
    1. 支持滚轮 / 精密触控板 / 触屏滑动 / 鼠标左键拖拽与惯性滑行，统一跑满显示器原生刷新率（如 165Hz / 6.06ms）；
    2. 动画期间临时启用 1ms Windows 系统时钟精度并使用 perf_counter 截止时间相位锁定，消除 6ms QTimer 抖动丢帧；
    3. 彻底绕开 Qt 默认 60Hz QScroller/QUnifiedTimer 帧率上限，并保证 <8px 点击/轻触 100% 原生透传。
    """

    def __init__(self, scroll_area: QScrollArea, wheel_step: float = 82.0):
        super().__init__(scroll_area)
        self._scroll = scroll_area
        self._viewport = scroll_area.viewport()
        self._vbar = scroll_area.verticalScrollBar()
        self._wheel_step = wheel_step
        self._pos_y = float(self._vbar.value())
        self._target_y = float(self._vbar.value())
        self._rate = 16.0
        self._vel_y = 0.0
        self._anim_mode = "idle"  # "idle" | "wheel" | "inertia"
        self._frame_period = 1.0 / 165.0
        self._next_frame_ts = 0.0
        self._last_ts = 0.0
        self._last_drag_paint_ts = 0.0
        self._syncing = False
        self._winmm_active = False
        self._overlay = None

        # 165Hz 鼠标左键/触屏拖拽状态
        self._drag_source: str | None = None
        self._mouse_press_gy: float | None = None
        self._last_drag_gy: float = 0.0
        self._drag_samples: list[tuple[float, float]] = []
        self._touch_point_id: int | None = None
        self._touch_press_gy: float = 0.0
        self._touch_press_local: QPointF | None = None
        self._touch_moved: bool = False

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._on_tick)

        self._vbar.valueChanged.connect(self._on_bar_value_changed)
        self._vbar.rangeChanged.connect(self._on_range_changed)
        self.destroyed.connect(self._on_destroyed)
        if self._viewport is not None:
            self._viewport.installEventFilter(self)

    def _on_destroyed(self, *_args):
        if self._winmm_active:
            self._winmm_active = False
            _set_winmm_timer_period(False)

    def _sync_refresh_period(self):
        hz = 165.0
        try:
            win = self._scroll.window() if shiboken6.isValid(self._scroll) else None
            scr = win.screen() if win is not None else None
            if scr is not None:
                r = float(scr.refreshRate())
                if 50.0 <= r <= 240.0:
                    hz = r
        except Exception:
            hz = 165.0
        self._frame_period = 1.0 / hz

    def _start_anim(self, mode: str = "wheel"):
        self._sync_refresh_period()
        self._anim_mode = mode
        if not self._winmm_active:
            self._winmm_active = True
            _set_winmm_timer_period(True)
        # 先激活合成层（避免冷启动首帧切片渲染耗时被计入下一帧 dt 造成瞬跳）
        if self._overlay is not None and shiboken6.isValid(self._overlay):
            self._overlay.on_wheel_anim_state(True, self._pos_y)
        now = time.perf_counter()
        self._last_ts = now
        dwm = _get_dwm_timing_sec()
        if dwm is not None:
            vblank_s, period_s, qnow_s = dwm
            self._frame_period = period_s
            lead = min(0.0024, period_s * 0.4)
            phase = (qnow_s - vblank_s) % period_s
            wait_s = (period_s - phase - lead) % period_s
            if wait_s < 0.0005:
                wait_s += period_s
            self._next_frame_ts = now + wait_s
        else:
            self._next_frame_ts = now + self._frame_period
        if not self._timer.isActive():
            self._timer.start()

    def _stop_anim(self):
        was_active = self._timer.isActive() or self._anim_mode != "idle"
        self._anim_mode = "idle"
        self._vel_y = 0.0
        if self._timer.isActive():
            self._timer.stop()
        if self._winmm_active:
            self._winmm_active = False
            _set_winmm_timer_period(False)
        if was_active and self._overlay is not None and shiboken6.isValid(self._overlay):
            if self._drag_source is None:
                self._overlay.on_wheel_anim_state(False, self._pos_y)

    def _on_bar_value_changed(self, value: int):
        if self._syncing:
            return
        # 外部接管滚动条（如代码直接定位、键盘翻页或拖曳滑块）时立即同步目标位置
        self._stop_anim()
        self._pos_y = float(value)
        self._target_y = float(value)

    def _get_dpr(self) -> float:
        try:
            if shiboken6.isValid(self._viewport):
                return max(1.0, float(self._viewport.devicePixelRatioF()))
        except Exception:
            pass
        return 1.0

    def _cancel_drag(self):
        self._mouse_press_gy = None
        self._touch_point_id = None
        self._touch_moved = False
        self._touch_press_local = None
        was_drag = self._drag_source in ("mouse", "touch")
        self._drag_source = None
        self._drag_samples = []
        try:
            if shiboken6.isValid(self._viewport) and self._viewport.mouseGrabber() is self._viewport:
                self._viewport.releaseMouse()
        except Exception:
            pass
        if (
            was_drag
            and self._anim_mode == "idle"
            and self._overlay is not None
            and shiboken6.isValid(self._overlay)
        ):
            self._overlay.on_wheel_anim_state(False, self._pos_y)

    def _on_range_changed(self, min_val: int, max_val: int):
        self._pos_y = max(float(min_val), min(float(max_val), self._pos_y))
        self._target_y = max(float(min_val), min(float(max_val), self._target_y))
        if max_val <= min_val and (self._timer.isActive() or self._anim_mode != "idle"):
            self._stop_anim()
        elif self._timer.isActive() and self._anim_mode == "wheel" and abs(self._target_y - self._pos_y) < 0.25:
            self._stop_anim()

    def _record_drag_sample(self, now: float, pos_y: float):
        self._drag_samples.append((now, pos_y))
        cutoff = now - 0.09
        while len(self._drag_samples) > 2 and self._drag_samples[0][0] < cutoff:
            self._drag_samples.pop(0)

    def _compute_release_velocity(self, now: float) -> float:
        if len(self._drag_samples) < 2:
            return 0.0
        last_t, last_y = self._drag_samples[-1]
        if now - last_t > 0.065:
            return 0.0
        first_t, first_y = self._drag_samples[0]
        dt = last_t - first_t
        if dt < 0.008:
            return 0.0
        vel = (last_y - first_y) / dt
        return max(-3200.0, min(3200.0, vel))

    def _dispatch_touch_tap(self, local_pt: QPoint):
        if not shiboken6.isValid(self._viewport):
            return
        child = self._viewport.childAt(local_pt)
        target = child or self._viewport
        w = target
        while w is not None and w is not self._scroll:
            if isinstance(w, QAbstractButton):
                w.click()
                return
            if hasattr(w, "_on_clicked") and hasattr(w, "_word_data"):
                gpos = QPointF(self._viewport.mapToGlobal(local_pt))
                lpos = QPointF(w.mapFrom(self._viewport, local_pt))
                press = QMouseEvent(
                    QEvent.Type.MouseButtonPress, lpos, gpos,
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                )
                rel = QMouseEvent(
                    QEvent.Type.MouseButtonRelease, lpos, gpos,
                    Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(w, press)
                QApplication.sendEvent(w, rel)
                return
            w = w.parentWidget()

    def _targets_gesture_widget(self, local_pt: QPoint) -> bool:
        if not shiboken6.isValid(self._viewport):
            return False
        w = self._viewport.childAt(local_pt)
        while w is not None and w is not self._scroll:
            if getattr(w, "_captures_gesture", False):
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, event):
        if obj is not self._viewport:
            return False
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._viewport) or not shiboken6.isValid(self._vbar):
            return False

        et = event.type()
        vmax = self._vbar.maximum()
        vmin = self._vbar.minimum()

        if et == QEvent.Type.Wheel:
            if self._drag_source in ("mouse", "touch"):
                event.accept()
                return True
            pixel_dy = event.pixelDelta().y()
            angle_dy = event.angleDelta().y()
            if pixel_dy == 0 and angle_dy == 0:
                return False
            if vmax <= vmin:
                return False

            if pixel_dy != 0:
                delta = -float(pixel_dy) * 1.05
                self._rate = 26.0
            elif abs(angle_dy) < 120:
                delta = -(float(angle_dy) / 120.0) * self._wheel_step
                self._rate = 22.0
            else:
                delta = -(float(angle_dy) / 120.0) * self._wheel_step
                self._rate = 16.0

            if not self._timer.isActive() or self._anim_mode != "wheel":
                cur = self._pos_y if self._anim_mode == "inertia" else float(self._vbar.value())
                self._pos_y = cur
                self._target_y = cur

            # 反向滚动时从当前视觉位置起算，防止折返迟滞
            if delta * (self._target_y - self._pos_y) < 0:
                self._target_y = self._pos_y

            self._target_y = max(float(vmin), min(float(vmax), self._target_y + delta))
            if not self._timer.isActive() or self._anim_mode != "wheel":
                if abs(self._target_y - self._pos_y) < 0.05:
                    event.accept()
                    return True
                self._start_anim("wheel")
            event.accept()
            return True

        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self._targets_gesture_widget(event.position().toPoint()):
                self._mouse_press_gy = None
                return False
            if vmax <= vmin:
                return False
            if self._timer.isActive():
                self._stop_anim()
            self._mouse_press_gy = float(event.globalPosition().y())
            self._last_drag_gy = self._mouse_press_gy
            self._drag_samples = []
            # 绝不拦截 Press，确保 <6px 普通点击由子卡片 100% 原生触发
            return False

        if et == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._mouse_press_gy is None or vmax <= vmin:
                return False
            gy = float(event.globalPosition().y())
            if self._drag_source != "mouse":
                if abs(gy - self._mouse_press_gy) < 6.0:
                    return False
                self._drag_source = "mouse"
                self._last_drag_gy = gy
                self._pos_y = float(self._vbar.value())
                self._target_y = self._pos_y
                now = time.perf_counter()
                self._last_drag_paint_ts = now
                self._drag_samples = [(now, self._pos_y)]
                self._viewport.grabMouse()
                if self._overlay is not None and shiboken6.isValid(self._overlay):
                    self._overlay.on_wheel_anim_state(True, self._pos_y)
                event.accept()
                return True

            dy = self._last_drag_gy - gy
            self._last_drag_gy = gy
            self._pos_y = max(float(vmin), min(float(vmax), self._pos_y + dy))
            self._target_y = self._pos_y
            now = time.perf_counter()
            self._record_drag_sample(now, self._pos_y)
            self._set_bar_value(int(round(self._pos_y)))
            imm = (now - self._last_drag_paint_ts) >= (self._frame_period * 0.78)
            if imm:
                self._last_drag_paint_ts = now
            if self._overlay is not None and shiboken6.isValid(self._overlay):
                self._overlay.on_wheel_anim_pos(self._pos_y, immediate=imm)
            event.accept()
            return True

        if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._mouse_press_gy = None
            if self._drag_source == "mouse":
                self._drag_source = None
                try:
                    if self._viewport.mouseGrabber() is self._viewport:
                        self._viewport.releaseMouse()
                except Exception:
                    pass
                vel = self._compute_release_velocity(time.perf_counter())
                self._drag_samples = []
                if abs(vel) > 45.0 and float(vmin) < self._pos_y < float(vmax):
                    self._vel_y = vel
                    self._start_anim("inertia")
                elif self._overlay is not None and shiboken6.isValid(self._overlay):
                    self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)
                    self._overlay.on_wheel_anim_state(False, self._pos_y)
                event.accept()
                return True
            return False

        if isinstance(event, QTouchEvent):
            pts = event.points()
            if not pts or vmax <= vmin:
                return False
            if et == QEvent.Type.TouchBegin:
                if self._targets_gesture_widget(pts[0].position().toPoint()):
                    return False
                if self._timer.isActive():
                    self._stop_anim()
                pt = pts[0]
                self._touch_point_id = pt.id()
                self._touch_press_gy = float(pt.globalPosition().y())
                self._touch_press_local = pt.position()
                self._last_drag_gy = self._touch_press_gy
                self._touch_moved = False
                self._drag_source = "touch_pending"
                self._drag_samples = [(time.perf_counter(), float(self._vbar.value()))]
                event.accept()
                return True
            elif et == QEvent.Type.TouchUpdate and self._drag_source in ("touch_pending", "touch"):
                pt = next((p for p in pts if p.id() == self._touch_point_id), pts[0])
                gy = float(pt.globalPosition().y())
                if not self._touch_moved:
                    if (
                        abs(gy - self._touch_press_gy) < 6.0
                        and self._touch_press_local is not None
                        and (pt.position() - self._touch_press_local).manhattanLength() < 8.0
                    ):
                        event.accept()
                        return True
                    self._touch_moved = True
                    self._drag_source = "touch"
                    self._last_drag_gy = gy
                    self._pos_y = float(self._vbar.value())
                    self._target_y = self._pos_y
                    self._last_drag_paint_ts = time.perf_counter()
                    if self._overlay is not None and shiboken6.isValid(self._overlay):
                        self._overlay.on_wheel_anim_state(True, self._pos_y)
                dy = self._last_drag_gy - gy
                self._last_drag_gy = gy
                self._pos_y = max(float(vmin), min(float(vmax), self._pos_y + dy))
                self._target_y = self._pos_y
                now = time.perf_counter()
                self._record_drag_sample(now, self._pos_y)
                self._set_bar_value(int(round(self._pos_y)))
                imm = (now - self._last_drag_paint_ts) >= (self._frame_period * 0.78)
                if imm:
                    self._last_drag_paint_ts = now
                if self._overlay is not None and shiboken6.isValid(self._overlay):
                    self._overlay.on_wheel_anim_pos(self._pos_y, immediate=imm)
                event.accept()
                return True
            elif et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel) and self._drag_source in ("touch_pending", "touch"):
                was_moved = self._touch_moved
                tap_local = self._touch_press_local
                self._touch_point_id = None
                self._touch_moved = False
                self._touch_press_local = None
                self._drag_source = None
                if was_moved:
                    vel = self._compute_release_velocity(time.perf_counter()) if et == QEvent.Type.TouchEnd else 0.0
                    self._drag_samples = []
                    if abs(vel) > 45.0 and float(vmin) < self._pos_y < float(vmax):
                        self._vel_y = vel
                        self._start_anim("inertia")
                    elif self._overlay is not None and shiboken6.isValid(self._overlay):
                        self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)
                        self._overlay.on_wheel_anim_state(False, self._pos_y)
                else:
                    self._drag_samples = []
                    if et == QEvent.Type.TouchEnd and tap_local is not None:
                        self._dispatch_touch_tap(tap_local.toPoint())
                event.accept()
                return True

        if et in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
            if self._drag_source is not None or self._mouse_press_gy is not None:
                self._cancel_drag()
            if et == QEvent.Type.Hide:
                self._stop_anim()

        return False

    def _on_tick(self):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._vbar):
            self._stop_anim()
            return
        vmin = float(self._vbar.minimum())
        vmax = float(self._vbar.maximum())
        if vmax <= vmin:
            self._stop_anim()
            return

        now = time.perf_counter()
        if now + 0.0003 < self._next_frame_ts:
            return
        while self._next_frame_ts <= now + 0.0003:
            self._next_frame_ts += self._frame_period
        dwm = _get_dwm_timing_sec()
        if dwm is not None:
            vblank_s, period_s, qnow_s = dwm
            self._frame_period = period_s
            lead = min(0.0024, period_s * 0.4)
            phase = (qnow_s - vblank_s) % period_s
            ideal_wait = (period_s - phase - lead) % period_s
            if ideal_wait < 0.0005:
                ideal_wait += period_s
            err = (now + ideal_wait) - self._next_frame_ts
            while err > period_s * 0.5:
                err -= period_s
            while err < -period_s * 0.5:
                err += period_s
            self._next_frame_ts += max(-0.00025, min(0.00025, err))

        dt = min(0.05, max(0.001, now - self._last_ts)) if self._last_ts > 0 else self._frame_period
        self._last_ts = now
        dpr = self._get_dpr()

        if self._anim_mode == "inertia":
            self._pos_y += self._vel_y * dt
            self._vel_y *= math.exp(-4.2 * dt)
            hit_edge = False
            if self._pos_y <= vmin:
                self._pos_y = vmin
                hit_edge = True
            elif self._pos_y >= vmax:
                self._pos_y = vmax
                hit_edge = True
            min_inertia_vel = 1.05 / (dpr * max(0.004, self._frame_period))
            stopped = hit_edge or abs(self._vel_y) < min_inertia_vel
            if stopped and not hit_edge:
                self._pos_y = max(vmin, min(vmax, float(int(round(self._pos_y)))))
            self._target_y = self._pos_y
            self._set_bar_value(int(round(self._pos_y)))
            if self._overlay is not None and shiboken6.isValid(self._overlay):
                self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)
            if stopped:
                self._stop_anim()
            return

        self._target_y = max(vmin, min(vmax, self._target_y))
        diff = self._target_y - self._pos_y
        stop_eps = 0.45 / dpr
        if abs(diff) <= stop_eps:
            self._pos_y = float(int(round(self._target_y)))
            self._set_bar_value(int(round(self._pos_y)))
            if self._overlay is not None and shiboken6.isValid(self._overlay):
                self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)
            self._stop_anim()
            return

        alpha = 1.0 - math.exp(-self._rate * dt)
        step = diff * alpha
        min_step = 1.05 / dpr
        if abs(step) < min_step:
            step = math.copysign(min(abs(diff), min_step), diff)
        self._pos_y += step

        if abs(self._target_y - self._pos_y) <= stop_eps:
            self._pos_y = float(int(round(self._target_y)))
            self._set_bar_value(int(round(self._pos_y)))
            if self._overlay is not None and shiboken6.isValid(self._overlay):
                self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)
            self._stop_anim()
        else:
            self._set_bar_value(int(round(self._pos_y)))
            if self._overlay is not None and shiboken6.isValid(self._overlay):
                self._overlay.on_wheel_anim_pos(self._pos_y, immediate=True)

    def _set_bar_value(self, value: int):
        if self._vbar.value() == value:
            return
        self._syncing = True
        try:
            self._vbar.setValue(value)
        finally:
            self._syncing = False


def _draw_feather_gradient(
    painter: QPainter,
    rect: QRectF,
    is_top: bool,
    color: QColor,
    strength: float,
):
    """在顶部或底部边界区域绘制平滑四段式透明渐变羽化遮罩。"""
    if strength <= 0.01 or rect.width() <= 1 or rect.height() <= 1:
        return
    r, g, b = color.red(), color.green(), color.blue()
    grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
    if is_top:
        grad.setColorAt(0.00, QColor(r, g, b, int(round(255 * strength))))
        grad.setColorAt(0.30, QColor(r, g, b, int(round(215 * strength))))
        grad.setColorAt(0.68, QColor(r, g, b, int(round(90 * strength))))
        grad.setColorAt(1.00, QColor(r, g, b, 0))
    else:
        grad.setColorAt(0.00, QColor(r, g, b, 0))
        grad.setColorAt(0.32, QColor(r, g, b, int(round(90 * strength))))
        grad.setColorAt(0.70, QColor(r, g, b, int(round(215 * strength))))
        grad.setColorAt(1.00, QColor(r, g, b, int(round(255 * strength))))
    painter.fillRect(rect, grad)


class _FeatherStrip(QWidget):
    """静止态独立边缘羽化渐变条（顶部或底部），不干扰静止时子控件悬停与点击。"""

    def __init__(self, parent: QScrollArea, is_top: bool, color: QColor):
        super().__init__(parent)
        self._is_top = is_top
        self._color = color
        self._strength = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_strength(self, strength: float):
        s = max(0.0, min(1.0, strength))
        if s <= 0.01:
            if self.isVisible():
                self.hide()
            self._strength = 0.0
            return
        was_hidden = not self.isVisible()
        changed = abs(s - self._strength) > 0.02
        self._strength = s
        if was_hidden:
            self.show()
            self.raise_()
            self.update()
        elif changed:
            self.update()

    def paintEvent(self, event):
        if self._strength <= 0.01 or self.width() <= 2 or self.height() <= 2:
            return
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        _draw_feather_gradient(
            painter,
            QRectF(0.0, 0.0, float(self.width()), float(self.height())),
            self._is_top,
            self._color,
            self._strength,
        )


class _ScrollSurfaceOverlay(QWidget):
    """
    高速滚动合成层：在滚轮/惯性滑动期间以 128px 水平分片条带缓存接管视口绘制，
    避免重叠条破坏 bitblt 后触发海量 RichText/QSS 子控件重绘，锁定原生 165Hz 帧率并杜绝透明孔洞与跨片卡顿。
    """

    def __init__(
        self,
        parent: QScrollArea,
        viewport: QWidget,
        vbar,
        top_feather: int,
        bottom_feather: int,
        color: QColor,
    ):
        super().__init__(parent)
        self._scroll = parent
        self._vp = viewport
        self._vbar = vbar
        self._top_h = top_feather
        self._bottom_h = bottom_feather
        self._bg = color
        self._pm: QPixmap | None = None
        self._pm_dirty = True
        self._slice_y = 0
        self._slice_h = 0
        self._dpr = 1.0
        self._active = False
        self._scroll_y = 0.0
        self._last_draw_px = -1
        self._tile_h = 128
        self._max_tiles = 48
        self._prebake_limit = 32
        self._tiles: OrderedDict[int, tuple[QPixmap, int, float]] = OrderedDict()
        self._cached_vp_w = 0
        self._cached_c_h = 0
        self._cached_dpr = 1.0

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def invalidate(self, clear_pixmap: bool = True):
        self._pm_dirty = True
        self._last_draw_px = -1
        if clear_pixmap:
            self._tiles.clear()
            self._pm = None
            self._slice_y = 0
            self._slice_h = 0
            self._cached_vp_w = 0
            self._cached_c_h = 0

    def _reset_cache_if_needed(self, vp_w: int, c_h: int, dpr: float):
        if (
            self._cached_vp_w != vp_w
            or self._cached_c_h != c_h
            or abs(self._cached_dpr - dpr) >= 0.01
        ):
            self._tiles.clear()
            self._pm = None
            self._pm_dirty = True
            self._slice_y = 0
            self._slice_h = 0
            self._cached_vp_w = vp_w
            self._cached_c_h = c_h
            self._cached_dpr = dpr
            self._dpr = dpr

    def _render_tile(
        self, tile_idx: int, content: QWidget, vp_w: int, c_h: int, dpr: float
    ) -> tuple[QPixmap, int, float] | None:
        if tile_idx < 0:
            return None
        ty = tile_idx * self._tile_h
        if ty >= c_h:
            return None
        th = min(self._tile_h, c_h - ty)
        pm = QPixmap(max(1, int(round(vp_w * dpr))), max(1, int(round(th * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(self._bg)
        content.render(pm, QPoint(0, 0), QRegion(0, ty, vp_w, th))
        entry = (pm, th, dpr)
        self._tiles[tile_idx] = entry
        self._tiles.move_to_end(tile_idx)
        while len(self._tiles) > self._max_tiles:
            self._tiles.popitem(last=False)
        if self._pm is None or self._pm_dirty:
            self._pm = pm
        return entry

    def prewarm(self):
        if (
            self._active
            or not shiboken6.isValid(self._vp)
            or not shiboken6.isValid(self._scroll)
            or not shiboken6.isValid(self._vbar)
        ):
            return
        if not self._scroll.isVisible() or not self._vp.isVisible():
            return
        if self._vbar.maximum() <= self._vbar.minimum():
            return
        self._ensure_slice(max(0, int(self._vbar.value())))

    def prebake_next(self) -> bool:
        if (
            self._active
            or not shiboken6.isValid(self._vp)
            or not shiboken6.isValid(self._scroll)
            or not shiboken6.isValid(self._vbar)
        ):
            return False
        if not self._scroll.isVisible() or not self._vp.isVisible():
            return False
        if self._vbar.maximum() <= self._vbar.minimum():
            return False
        content = self._scroll.widget()
        if content is None or not shiboken6.isValid(content):
            return False
        vp_w = self._vp.width()
        vp_h = self._vp.height()
        c_h = content.height()
        if vp_w <= 2 or vp_h <= 2 or c_h <= 2:
            return False
        dpr = max(1.0, float(self._vp.devicePixelRatioF()))
        self._reset_cache_if_needed(vp_w, c_h, dpr)

        total_tiles = (c_h + self._tile_h - 1) // self._tile_h
        max_bake = min(total_tiles, min(self._prebake_limit, self._max_tiles - 4))
        cur_y = max(0, int(self._vbar.value()))
        anchor_idx = cur_y // self._tile_h
        vis_span = ((vp_h + self._tile_h - 1) // self._tile_h) + 2

        candidate = None
        for dist in range(0, total_tiles):
            for idx in (anchor_idx + dist, anchor_idx - dist):
                if 0 <= idx < total_tiles and idx not in self._tiles:
                    if len(self._tiles) < max_bake or dist <= vis_span:
                        candidate = idx
                        break
            if candidate is not None:
                break
        if candidate is None:
            return False

        was_vp_upd = self._vp.updatesEnabled()
        was_content_upd = content.updatesEnabled()
        if not was_vp_upd:
            self._vp.setUpdatesEnabled(True)
        if not was_content_upd:
            content.setUpdatesEnabled(True)
        try:
            self._render_tile(candidate, content, vp_w, c_h, dpr)
            self._pm_dirty = False
            if self._pm is None and self._tiles:
                self._pm = next(iter(self._tiles.values()))[0]
        finally:
            if not was_content_upd:
                content.setUpdatesEnabled(False)
            if not was_vp_upd:
                self._vp.setUpdatesEnabled(False)

        if len(self._tiles) >= max_bake:
            return False
        for dist in range(0, total_tiles):
            for idx in (anchor_idx + dist, anchor_idx - dist):
                if 0 <= idx < total_tiles and idx not in self._tiles:
                    return True
        return False

    def _ensure_slice(self, view_y: float | int, pad: int | None = None):
        content = self._scroll.widget() if shiboken6.isValid(self._scroll) else None
        if content is None or not shiboken6.isValid(content) or not shiboken6.isValid(self._vp):
            return
        vp_w = self._vp.width()
        vp_h = self._vp.height()
        c_h = content.height()
        if vp_w <= 2 or vp_h <= 2 or c_h <= 2:
            return
        dpr = max(1.0, float(self._vp.devicePixelRatioF()))
        self._reset_cache_if_needed(vp_w, c_h, dpr)

        vy = max(0.0, float(view_y))
        view_end = min(float(c_h), vy + float(vp_h))
        if pad is not None and pad > 0:
            vy = max(0.0, vy - float(pad))
            view_end = min(float(c_h), view_end + float(pad))

        first_idx = max(0, int(math.floor(vy)) // self._tile_h)
        last_y = min(max(0, c_h - 1), max(int(math.floor(vy)), int(math.ceil(view_end)) - 1))
        last_idx = max(first_idx, last_y // self._tile_h)

        missing = [idx for idx in range(first_idx, last_idx + 1) if idx not in self._tiles]
        if not missing and not self._pm_dirty and self._pm is not None:
            self._dpr = dpr
            for idx in range(first_idx, last_idx + 1):
                if idx in self._tiles:
                    self._tiles.move_to_end(idx)
            return

        if missing:
            # 在 render 前短暂恢复 vp 与 content 的 updatesEnabled，防止父级禁用更新阻断子控件绘制导致切片空白
            was_vp_upd = self._vp.updatesEnabled()
            was_content_upd = content.updatesEnabled()
            if not was_vp_upd:
                self._vp.setUpdatesEnabled(True)
            if not content.updatesEnabled():
                content.setUpdatesEnabled(True)
            try:
                for idx in missing:
                    self._render_tile(idx, content, vp_w, c_h, dpr)
            finally:
                if not was_content_upd:
                    content.setUpdatesEnabled(False)
                if not was_vp_upd:
                    self._vp.setUpdatesEnabled(False)

        first_entry = self._tiles.get(first_idx)
        if first_entry is not None:
            self._pm = first_entry[0]
        elif self._tiles:
            self._pm = next(iter(self._tiles.values()))[0]
        self._slice_y = first_idx * self._tile_h
        self._slice_h = min(c_h - self._slice_y, (last_idx - first_idx + 1) * self._tile_h)
        self._dpr = dpr
        self._pm_dirty = False

    def begin(self, y_pos: float):
        if not shiboken6.isValid(self._vp) or not shiboken6.isValid(self._scroll):
            return
        content = self._scroll.widget()
        if content is None or not shiboken6.isValid(content):
            return
        self._scroll_y = max(0.0, float(y_pos))
        self._ensure_slice(self._scroll_y)
        if not self._active:
            self._active = True
            self._vp.setUpdatesEnabled(False)
            content.setUpdatesEnabled(False)
            self._last_draw_px = -1
        self.setGeometry(self._vp.geometry())
        self.show()
        self.raise_()
        self.update()

    def set_scroll_y(self, y_pos: float, immediate: bool = False):
        if not self._active:
            return
        self._scroll_y = max(0.0, float(y_pos))
        self._ensure_slice(self._scroll_y)
        cur_px = int(round(self._scroll_y * self._dpr))
        if cur_px != self._last_draw_px:
            if immediate:
                if shiboken6.isValid(self._scroll) and self._scroll.isVisible():
                    self.update()
                    self._scroll.repaint()
                else:
                    self.repaint()
            else:
                self.update()

    def end(self, invalidate_pixmap: bool = False):
        if invalidate_pixmap:
            self.invalidate(clear_pixmap=True)
        was_active = self._active
        self._active = False
        self._last_draw_px = -1
        if shiboken6.isValid(self._vp):
            self._vp.setUpdatesEnabled(True)
        content = self._scroll.widget() if shiboken6.isValid(self._scroll) else None
        if content is not None and shiboken6.isValid(content):
            content.setUpdatesEnabled(True)
        if self.isVisible():
            self.hide()
        if was_active:
            if shiboken6.isValid(self._vp):
                self._vp.update()
            if content is not None and shiboken6.isValid(content):
                content.update()

    def paintEvent(self, event):
        scroll_y = max(0.0, float(self._scroll_y))
        self._ensure_slice(scroll_y)
        painter = QPainter(self)
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2 or not self._tiles or self._cached_c_h <= 0:
            painter.fillRect(event.rect(), self._bg)
        else:
            dpr = self._dpr
            src_y_px = int(round(scroll_y * dpr))
            self._last_draw_px = src_y_px
            scroll_y_aligned = float(src_y_px) / dpr
            view_h_px = int(round(float(h) * dpr))
            view_end_px = src_y_px + view_h_px

            c_h = self._cached_c_h
            first_idx = max(0, int(math.floor(scroll_y_aligned)) // self._tile_h)
            last_y = min(max(0, c_h - 1), int(math.ceil(scroll_y_aligned + float(h))) - 1)
            last_idx = max(first_idx, last_y // self._tile_h)

            prev_bottom = 0.0
            for tidx in range(first_idx, last_idx + 1):
                entry = self._tiles.get(tidx)
                if entry is None:
                    continue
                self._tiles.move_to_end(tidx)
                pm, _th, tdpr = entry
                ty = tidx * self._tile_h
                tile_y0_px = int(round(float(ty) * dpr))
                tile_y1_px = tile_y0_px + pm.height()
                vis_y0_px = max(src_y_px, tile_y0_px)
                vis_y1_px = min(view_end_px, tile_y1_px)
                if vis_y1_px <= vis_y0_px:
                    continue
                src_top_px = vis_y0_px - tile_y0_px
                src_h_px = vis_y1_px - vis_y0_px
                dst_y = float(vis_y0_px - src_y_px) / dpr
                dst_h = float(src_h_px) / dpr
                if dst_y > prev_bottom:
                    painter.fillRect(QRectF(0.0, prev_bottom, float(w), dst_y - prev_bottom), self._bg)
                painter.drawPixmap(
                    QRectF(0.0, dst_y, float(w), dst_h),
                    pm,
                    QRectF(0.0, float(src_top_px), float(w) * tdpr, float(src_h_px)),
                )
                prev_bottom = max(prev_bottom, dst_y + dst_h)

            if prev_bottom < float(h):
                painter.fillRect(QRectF(0.0, prev_bottom, float(w), float(h) - prev_bottom), self._bg)
        vmax = self._vbar.maximum() if shiboken6.isValid(self._vbar) else 0
        if vmax > 0:
            y = self._scroll_y
            top_s = min(1.0, max(0.0, y / 18.0)) if y > 0.0 else 0.0
            bot_s = min(1.0, max(0.0, (float(vmax) - y) / 18.0)) if y < float(vmax) else 0.0
            painter.setPen(Qt.PenStyle.NoPen)
            if self._top_h > 0 and top_s > 0.01:
                th = min(float(self._top_h), float(h) * 0.35)
                _draw_feather_gradient(painter, QRectF(0.0, 0.0, float(w), th), True, self._bg, top_s)
            if self._bottom_h > 0 and bot_s > 0.01:
                bh = min(float(self._bottom_h), float(h) * 0.35)
                _draw_feather_gradient(painter, QRectF(0.0, float(h) - bh, float(w), bh), False, self._bg, bot_s)


class ScrollEdgeFeatherOverlay(QObject):
    """
    滚动视口羽化与高刷调度管理器：
    - 静止态在视口顶部/底部放置分离渐变条，确保不透明无空洞且按钮/悬停交互 100% 原生可用；
    - 滚动/拖拽期间自动切换至 _ScrollSurfaceOverlay 单缓冲合成渲染，锁定 165Hz 流畅滚动。
    """

    def __init__(
        self,
        scroll_area: QScrollArea,
        top_feather: int = 26,
        bottom_feather: int = 22,
        color: str = LEXICON_THEME["bg"],
        ctrl: SmoothScrollController | None = None,
    ):
        super().__init__(scroll_area)
        self._scroll = scroll_area
        self._viewport = scroll_area.viewport()
        self._vbar = scroll_area.verticalScrollBar()
        self._content = scroll_area.widget()
        self._ctrl = ctrl
        self._top_h = top_feather
        self._bottom_h = bottom_feather
        bg = QColor(color)

        self._top_strip = _FeatherStrip(scroll_area, True, bg) if top_feather > 0 else None
        self._bottom_strip = _FeatherStrip(scroll_area, False, bg) if bottom_feather > 0 else None
        self._surface = _ScrollSurfaceOverlay(scroll_area, self._viewport, self._vbar, top_feather, bottom_feather, bg)
        self._anim_driving = False
        self._slider_active = False
        self._last_slider_paint_ts = 0.0

        self._rest_timer = QTimer(self)
        self._rest_timer.setSingleShot(True)
        self._rest_timer.setInterval(140)
        self._rest_timer.timeout.connect(self._exit_fast_scroll)

        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.setInterval(45)
        self._prewarm_timer.timeout.connect(self._prewarm_surface)

        self._prebake_timer = QTimer(self)
        self._prebake_timer.setSingleShot(True)
        self._prebake_timer.setInterval(8)
        self._prebake_timer.timeout.connect(self._prebake_step)

        self._vbar.valueChanged.connect(self._schedule_update)
        self._vbar.rangeChanged.connect(self._on_content_invalidated)
        self._vbar.sliderPressed.connect(self._on_slider_pressed)
        self._vbar.sliderReleased.connect(self._on_slider_released)
        self._scroll.installEventFilter(self)
        if self._viewport is not None:
            self._viewport.installEventFilter(self)
        if self._content is not None:
            self._content.installEventFilter(self)
        self.destroyed.connect(self._on_destroyed)

        self._sync_geometry()
        self._schedule_update()
        self._schedule_prewarm()

    def _on_destroyed(self, *_args):
        if shiboken6.isValid(self._rest_timer) and self._rest_timer.isActive():
            self._rest_timer.stop()
        if shiboken6.isValid(self._prewarm_timer) and self._prewarm_timer.isActive():
            self._prewarm_timer.stop()
        if shiboken6.isValid(self._prebake_timer) and self._prebake_timer.isActive():
            self._prebake_timer.stop()

    def _schedule_prewarm(self):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._prewarm_timer):
            return
        if self._anim_driving or self._slider_active or self._surface._active:
            return
        if shiboken6.isValid(self._prebake_timer) and self._prebake_timer.isActive():
            self._prebake_timer.stop()
        if shiboken6.isValid(self._vbar) and self._vbar.maximum() > self._vbar.minimum():
            self._prewarm_timer.start()

    def _prewarm_surface(self):
        if not shiboken6.isValid(self) or self._anim_driving or self._slider_active:
            return
        if shiboken6.isValid(self._surface):
            self._surface.prewarm()
            self._schedule_prebake()

    def _schedule_prebake(self):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._prebake_timer):
            return
        if self._anim_driving or self._slider_active or self._surface._active:
            return
        if shiboken6.isValid(self._vbar) and self._vbar.maximum() > self._vbar.minimum():
            self._prebake_timer.start()

    def _prebake_step(self):
        if not shiboken6.isValid(self) or self._anim_driving or self._slider_active or self._surface._active:
            return
        if shiboken6.isValid(self._surface) and self._surface.prebake_next():
            self._schedule_prebake()

    def _set_rest_strips_visible(self, show: bool):
        if not show:
            if self._top_strip is not None and shiboken6.isValid(self._top_strip) and self._top_strip.isVisible():
                self._top_strip.hide()
            if self._bottom_strip is not None and shiboken6.isValid(self._bottom_strip) and self._bottom_strip.isVisible():
                self._bottom_strip.hide()
        else:
            self._update_rest_strengths()

    def _enter_fast_scroll(self, y_pos: float):
        if self._rest_timer.isActive():
            self._rest_timer.stop()
        if self._prewarm_timer.isActive():
            self._prewarm_timer.stop()
        if self._prebake_timer.isActive():
            self._prebake_timer.stop()
        self._set_rest_strips_visible(False)
        self._surface.begin(y_pos)

    def _schedule_rest(self):
        if not self._anim_driving and not self._slider_active:
            if shiboken6.isValid(self._rest_timer):
                self._rest_timer.start()

    def _exit_fast_scroll(self):
        if self._anim_driving or self._slider_active:
            return
        if not shiboken6.isValid(self):
            return
        self._surface.end(invalidate_pixmap=False)
        self._update_rest_strengths()
        self._schedule_prebake()

    def _on_content_invalidated(self, *_args):
        if not shiboken6.isValid(self):
            return
        if self._anim_driving or self._slider_active:
            self._surface.invalidate(clear_pixmap=True)
            self._sync_geometry()
            if self._surface._active and shiboken6.isValid(self._vbar):
                self._surface.set_scroll_y(float(self._vbar.value()))
            return
        if self._rest_timer.isActive():
            self._rest_timer.stop()
        if self._prebake_timer.isActive():
            self._prebake_timer.stop()
        self._surface.end(invalidate_pixmap=True)
        self._sync_geometry()
        self._update_rest_strengths()
        self._schedule_prewarm()

    def on_wheel_anim_state(self, active: bool, pos_y: float):
        self._anim_driving = active
        if active:
            self._enter_fast_scroll(pos_y)
        else:
            self._schedule_rest()

    def on_wheel_anim_pos(self, pos_y: float, immediate: bool = False):
        if shiboken6.isValid(self._surface) and self._surface._active:
            self._surface.set_scroll_y(pos_y, immediate=immediate)

    def _on_slider_pressed(self):
        self._slider_active = True
        self._enter_fast_scroll(float(self._vbar.value()))

    def _on_slider_released(self):
        self._slider_active = False
        self._schedule_rest()

    def _sync_geometry(self):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._viewport):
            return
        geo = self._viewport.geometry()
        w, h = geo.width(), geo.height()
        if w <= 4 or h <= 4:
            return
        if self._surface.isVisible() and self._surface.geometry() != geo:
            self._surface.setGeometry(geo)
        if self._top_strip is not None and shiboken6.isValid(self._top_strip):
            th = min(self._top_h, int(h * 0.35))
            tg = QRect(geo.x(), geo.y(), w, th)
            if self._top_strip.geometry() != tg:
                self._top_strip.setGeometry(tg)
            if not self._surface._active:
                self._top_strip.raise_()
        if self._bottom_strip is not None and shiboken6.isValid(self._bottom_strip):
            bh = min(self._bottom_h, int(h * 0.35))
            bg_rect = QRect(geo.x(), geo.y() + h - bh, w, bh)
            if self._bottom_strip.geometry() != bg_rect:
                self._bottom_strip.setGeometry(bg_rect)
            if not self._surface._active:
                self._bottom_strip.raise_()

    def _update_rest_strengths(self):
        if self._surface._active or not shiboken6.isValid(self) or not shiboken6.isValid(self._vbar):
            return
        vmax = self._vbar.maximum()
        val = self._vbar.value()
        top_s = min(1.0, float(val) / 18.0) if (vmax > 0 and val > 0) else 0.0
        bot_s = min(1.0, float(vmax - val) / 18.0) if (vmax > 0 and val < vmax) else 0.0
        if self._top_strip is not None and shiboken6.isValid(self._top_strip):
            self._top_strip.set_strength(top_s)
        if self._bottom_strip is not None and shiboken6.isValid(self._bottom_strip):
            self._bottom_strip.set_strength(bot_s)

    def _schedule_update(self, *_args):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._vbar):
            return
        if self._surface._active:
            if not self._anim_driving:
                imm = False
                if self._slider_active:
                    now = time.perf_counter()
                    period = self._ctrl._frame_period if (self._ctrl is not None and shiboken6.isValid(self._ctrl)) else (1.0 / 165.0)
                    if (now - self._last_slider_paint_ts) >= (period * 0.78):
                        self._last_slider_paint_ts = now
                        imm = True
                self._surface.set_scroll_y(float(self._vbar.value()), immediate=imm)
            return
        self._update_rest_strengths()

    def eventFilter(self, obj, event):
        et = event.type()
        if not shiboken6.isValid(self) or not shiboken6.isValid(self._scroll):
            return False
        if obj is self._content:
            if et in (QEvent.Type.ChildAdded, QEvent.Type.ChildRemoved):
                self._surface.invalidate(clear_pixmap=True)
                if not self._surface._active:
                    self._schedule_prewarm()
            elif et in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
                self._surface.invalidate(clear_pixmap=True)
                if self._surface._active:
                    self._surface.update()
                else:
                    self._schedule_prewarm()
        elif et in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            if et == QEvent.Type.Resize:
                if self._anim_driving or self._slider_active:
                    self._surface.invalidate(clear_pixmap=True)
                    self._sync_geometry()
                    if self._surface._active and shiboken6.isValid(self._vbar):
                        self._surface.set_scroll_y(float(self._vbar.value()), immediate=False)
                else:
                    if self._prebake_timer.isActive():
                        self._prebake_timer.stop()
                    self._surface.end(invalidate_pixmap=True)
                    self._sync_geometry()
                    self._update_rest_strengths()
                    self._schedule_prewarm()
            else:
                self._sync_geometry()
                self._update_rest_strengths()
                if et == QEvent.Type.Show:
                    self._schedule_prewarm()
        elif et == QEvent.Type.Hide:
            if self._rest_timer.isActive():
                self._rest_timer.stop()
            if self._prewarm_timer.isActive():
                self._prewarm_timer.stop()
            if self._prebake_timer.isActive():
                self._prebake_timer.stop()
            self._surface.end(invalidate_pixmap=True)
        return False


def enhance_scroll_area(
    scroll_area: QScrollArea,
    *,
    top_feather: int = 26,
    bottom_feather: int = 22,
    feather_color: str = LEXICON_THEME["bg"],
    wheel_step: float = 82.0,
):
    """
    为 QScrollArea 统一装配：
    1. 滚动区/视口/内容容器强制不透明纯白底色（禁止 WA_OpaquePaintEvent 导致的透明空洞）；
    2. 原生高刷触摸/拖拽惯性 + 刷新率自适应锁相平滑滚轮控制器（对齐显示器 165Hz 原生周期）；
    3. 动静分离式预热切片缓冲合成与上下边缘羽化渐隐遮罩。
    """
    vp = scroll_area.viewport()
    vbar = scroll_area.verticalScrollBar()
    content = scroll_area.widget()
    bg_c = QColor(feather_color)
    bg_hex = bg_c.name()

    for w in (scroll_area, vbar, vp, content):
        if w is None:
            continue
        w.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        if w is content:
            w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pal = w.palette()
        pal.setColor(QPalette.ColorRole.Window, bg_c)
        pal.setColor(QPalette.ColorRole.Base, bg_c)
        w.setPalette(pal)
        w.setAutoFillBackground(True)

    if vp is not None:
        if not vp.objectName():
            vp.setObjectName("qt_scrollarea_viewport")
        cur_qss = scroll_area.styleSheet() or ""
        vp_rule = f"QWidget#{vp.objectName()} {{ background-color: {bg_hex}; border: none; }}"
        if vp.objectName() not in cur_qss:
            scroll_area.setStyleSheet(f"{cur_qss}\n{vp_rule}\n")
        vp.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    ctrl = SmoothScrollController(scroll_area, wheel_step=wheel_step)
    overlay = ScrollEdgeFeatherOverlay(
        scroll_area,
        top_feather=top_feather,
        bottom_feather=bottom_feather,
        color=feather_color,
        ctrl=ctrl,
    )
    ctrl._overlay = overlay
    scroll_area._smooth_scroll_ctrl = ctrl
    scroll_area._feather_overlay = overlay
    return ctrl, overlay
