import ctypes
import sys
import time
from ctypes import wintypes
import shiboken6
from PySide6.QtWidgets import QWidget, QApplication, QMenu
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, Signal, QEvent, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QMouseEvent, QCursor, QTouchEvent
from .database import get_setting, set_setting
from .ui_icons import (
    draw_vector_icon,
    EditorialPopupMenu,
    LEXICON_THEME,
    make_vector_qicon,
    enforce_window_topmost,
    restore_topmost_if_needed,
    get_taskbar_created_msg_id,
)

DOCK_POS_SETTINGS_KEY = "dock_position"

_u32_dock = None
if sys.platform == "win32":
    try:
        _u32_dock = ctypes.WinDLL("user32", use_last_error=True)
        _u32_dock.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        _u32_dock.SetPropW.restype = wintypes.BOOL
    except Exception:
        _u32_dock = None

TABLET_DISABLE_PRESSANDHOLD = 0x00000001
TABLET_DISABLE_FLICKS = 0x00010000
TABLET_DISABLE_TOUCHSWITCH = 0x00008000

class FloatingZenPill(QWidget):
    """
    屏幕边缘常驻极简灵动小胶囊 (Zen Edge Pill)
    专为 Windows 11 触控平板模式深度定制：
    1. 停靠在屏幕右侧中部（拇指舒适操控区，避开边缘防误触区）；
    2. 指尖轻点胶囊 ➔ 0 延迟定格全屏进入 165Hz 原生触控取词画布；
    3. 支持沿屏幕边缘上下自由拖拽调节位置；
    4. 长按胶囊 ➔ 快速呼出学术生词本。
    """
    triggered = Signal(QPoint)
    open_vault = Signal()
    toggle_subtitle = Signal()
    open_settings = Signal()
    position_changed = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip("左键轻触：屏幕取词（拖动可贴靠屏幕左/右侧）\n中键/右键：双语字幕、生词本与设置")

        # 暗炭磨砂方章停靠微标（34x34 紧凑体量，预留 5px 阴影边距保证触屏热区）
        self.pill_w = 34
        self.pill_h = 34
        self.resize(self.pill_w + 10, self.pill_h + 10)

        self._dock_side = "right"
        self._y_ratio = 0.5
        self._edge_margin = 6
        self._context_menu_open = False

        self.is_hovered = False
        self._is_pressed = False
        self._dragging = False
        self._moved_during_drag = False
        self._subtitle_active = False
        self._drag_offset = QPoint()
        self._press_pos = QPoint()
        self._press_time = 0.0
        self._long_press_fired = False
        self._last_touch_ts = 0.0
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(480)
        self._long_press_timer.timeout.connect(self._on_long_press_timeout)

        # 开机及日常轻量 Z-order 守卫：检测跌出 TOPMOST 或被外部窗口压盖时无感恢复置顶
        self._topmost_guard_timer = QTimer(self)
        self._topmost_guard_timer.setInterval(1200)
        self._topmost_guard_timer.timeout.connect(self._on_topmost_guard_tick)

        # 屏幕工作区/任务栏就绪或 DPI 变化时防抖重贴边与补强置顶
        self._reposition_timer = QTimer(self)
        self._reposition_timer.setSingleShot(True)
        self._reposition_timer.setInterval(180)
        self._reposition_timer.timeout.connect(self.ensure_topmost_and_positioned)
        self._watched_screen = None
        self._bind_screen_signals()

        self._load_dock_position()

    def _bind_screen_signals(self):
        app = QApplication.instance()
        if app is not None:
            for sig in (app.primaryScreenChanged, app.screenAdded, app.screenRemoved):
                try:
                    sig.connect(self._on_screen_topology_changed)
                except Exception:
                    pass
        self._rebind_primary_screen_geom()

    def _rebind_primary_screen_geom(self):
        scr = (self.screen() if shiboken6.isValid(self) else None) or QApplication.primaryScreen()
        if scr is self._watched_screen:
            return
        if self._watched_screen is not None and shiboken6.isValid(self._watched_screen):
            try:
                self._watched_screen.availableGeometryChanged.disconnect(self._schedule_reposition)
            except Exception:
                pass
        self._watched_screen = scr
        if scr is not None and shiboken6.isValid(scr):
            try:
                scr.availableGeometryChanged.connect(self._schedule_reposition)
            except Exception:
                pass

    def _on_screen_topology_changed(self, *_args):
        self._rebind_primary_screen_geom()
        self._schedule_reposition()

    def _schedule_reposition(self, *_args):
        if shiboken6.isValid(self) and self.isVisible() and not self._dragging:
            self._reposition_timer.start()

    def _on_topmost_guard_tick(self):
        if not shiboken6.isValid(self) or not self.isVisible():
            return
        if self._dragging or self._context_menu_open:
            return
        restore_topmost_if_needed(self, force=False)

    def ensure_topmost_and_positioned(self):
        """确保微柄贴合当前可用工作区边缘并恢复 HWND_TOPMOST（隐藏/拖拽/菜单打开期间静默跳过）。"""
        if not shiboken6.isValid(self) or not self.isVisible() or self._dragging or self._context_menu_open:
            return
        self._apply_dock_geometry(self._dock_side, self._y_ratio)
        restore_topmost_if_needed(self, force=True)

    @property
    def dock_side(self) -> str:
        return self._dock_side

    def _get_target_screen_geom(self, ref_global: QPoint | None = None) -> QRect:
        screen = (
            (QApplication.screenAt(ref_global) if ref_global is not None else None)
            or self.screen()
            or QApplication.primaryScreen()
        )
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

    def _apply_dock_geometry(self, side: str, y_ratio: float):
        geom = self._get_target_screen_geom()
        self._dock_side = "left" if side == "left" else "right"
        self._y_ratio = max(0.0, min(1.0, float(y_ratio)))
        min_y = geom.top() + 24
        max_y = max(min_y, geom.bottom() - self.height() - 24)
        target_y = int(round(min_y + (max_y - min_y) * self._y_ratio))
        target_x = (
            geom.left() + self._edge_margin
            if self._dock_side == "left"
            else geom.right() - self.width() - self._edge_margin
        )
        self.move(target_x, target_y)

    def _load_dock_position(self):
        raw = get_setting(DOCK_POS_SETTINGS_KEY, None)
        side = "right"
        y_ratio = 0.5
        if isinstance(raw, dict):
            s = str(raw.get("side", "right") or "right").lower()
            if s in ("left", "right"):
                side = s
            try:
                y_ratio = max(0.0, min(1.0, float(raw.get("y_ratio", 0.5))))
            except Exception:
                y_ratio = 0.5
        self._apply_dock_geometry(side, y_ratio)

    def _save_dock_position(self):
        try:
            set_setting(
                DOCK_POS_SETTINGS_KEY,
                {"side": self._dock_side, "y_ratio": round(self._y_ratio, 4)},
            )
        except Exception:
            pass
        self.position_changed.emit(self._dock_side, self._y_ratio)

    def _snap_to_nearest_edge(self):
        geom = self._get_target_screen_geom()
        cx = self.x() + self.width() // 2
        side = "left" if cx < geom.center().x() else "right"
        min_y = geom.top() + 24
        max_y = max(min_y, geom.bottom() - self.height() - 24)
        span = max(1, max_y - min_y)
        clamped_y = max(min_y, min(max_y, self.y()))
        y_ratio = (clamped_y - min_y) / float(span)
        self._apply_dock_geometry(side, y_ratio)
        self._rebind_primary_screen_geom()
        self._save_dock_position()

    def set_dock_side(self, side: str):
        self._apply_dock_geometry(side, self._y_ratio)
        self._save_dock_position()

    def reset_position(self):
        self._apply_dock_geometry("right", 0.5)
        self._save_dock_position()

    def set_subtitle_active(self, active: bool):
        self._subtitle_active = bool(active)

    def reset_interaction_state(self):
        if hasattr(self, "_long_press_timer"):
            self._long_press_timer.stop()
        was_moved = getattr(self, "_moved_during_drag", False)
        self.is_hovered = False
        self._is_pressed = False
        self._dragging = False
        self._moved_during_drag = False
        self._long_press_fired = False
        if was_moved:
            self._snap_to_nearest_edge()
        self.update()

    def _on_long_press_timeout(self):
        if not self._is_pressed or self._long_press_fired:
            return
        self._long_press_fired = True
        self._dragging = False
        self._is_pressed = False
        self._last_touch_ts = time.monotonic()
        self.update()
        anchor = self._press_pos if not self._press_pos.isNull() else self.mapToGlobal(self.rect().center())
        self._show_context_menu(anchor)

    def hideEvent(self, event):
        if hasattr(self, "_topmost_guard_timer"):
            self._topmost_guard_timer.stop()
        if hasattr(self, "_reposition_timer"):
            self._reposition_timer.stop()
        self.reset_interaction_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "_topmost_guard_timer"):
            self._topmost_guard_timer.stop()
        if hasattr(self, "_reposition_timer"):
            self._reposition_timer.stop()
        super().closeEvent(event)

    def focusOutEvent(self, event):
        if self._dragging or self._is_pressed:
            self.reset_interaction_state()
        super().focusOutEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 禁用胶囊上的系统长按右键白圈（64位安全句柄签名）
        if _u32_dock is not None:
            try:
                hwnd = int(self.winId())
                if hwnd:
                    flags = TABLET_DISABLE_PRESSANDHOLD | TABLET_DISABLE_FLICKS | TABLET_DISABLE_TOUCHSWITCH
                    _u32_dock.SetPropW(wintypes.HWND(hwnd), "MicrosoftTabletPenServiceProperty", wintypes.HANDLE(flags))
            except Exception:
                pass
        enforce_window_topmost(self, activate=False)
        if hasattr(self, "_topmost_guard_timer") and not self._topmost_guard_timer.isActive():
            self._topmost_guard_timer.start()

    def nativeEvent(self, eventType, message):
        msg = wintypes.MSG.from_address(message.__int__())
        if msg.message == 0x02CC: # WM_TABLET_QUERYSYSTEMGESTURESTATUS
            return True, TABLET_DISABLE_PRESSANDHOLD | TABLET_DISABLE_FLICKS
        taskbar_msg = get_taskbar_created_msg_id()
        if (taskbar_msg and msg.message == taskbar_msg) or msg.message == 0x007E or (msg.message == 0x001A and msg.wParam == 0x002F):
            self._schedule_reposition()
        return False, 0

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                evt_type = event.type()
                if evt_type == QEvent.Type.TouchBegin:
                    self._dragging = True
                    self._moved_during_drag = False
                    self._is_pressed = True
                    self._long_press_fired = False
                    self._press_pos = pts[0].globalPosition().toPoint()
                    self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
                    self._press_time = time.time()
                    self._long_press_timer.start()
                    self.update()
                elif evt_type == QEvent.Type.TouchUpdate and self._dragging:
                    cur_g = pts[0].globalPosition().toPoint()
                    if not self._moved_during_drag and (cur_g - self._press_pos).manhattanLength() >= 14:
                        self._long_press_timer.stop()
                        self._moved_during_drag = True
                    if self._moved_during_drag:
                        screen_geom = self._get_target_screen_geom(cur_g)
                        new_pos = cur_g - self._drag_offset
                        new_x = max(screen_geom.left() + self._edge_margin, min(screen_geom.right() - self.width() - self._edge_margin, new_pos.x()))
                        new_y = max(screen_geom.top() + 24, min(screen_geom.bottom() - self.height() - 24, new_pos.y()))
                        self.move(new_x, new_y)
                elif evt_type in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                    self._long_press_timer.stop()
                    self._last_touch_ts = time.monotonic()
                    was_long = self._long_press_fired
                    was_moved = self._moved_during_drag
                    self._dragging = False
                    self._moved_during_drag = False
                    self._is_pressed = False
                    self._long_press_fired = False
                    self.update()
                    if evt_type == QEvent.Type.TouchEnd and not was_long:
                        cur_pos = pts[0].globalPosition().toPoint()
                        if (cur_pos - self._press_pos).manhattanLength() < 24 and not was_moved:
                            if time.time() - self._press_time > 0.48:
                                self._show_context_menu(cur_pos)
                            else:
                                self.triggered.emit(cur_pos)
                        else:
                            self._snap_to_nearest_edge()
                    elif was_moved:
                        self._snap_to_nearest_edge()
                event.accept()
                return True
        return super().event(event)

    def enterEvent(self, event):
        self.is_hovered = True
        self.update()

    def leaveEvent(self, event):
        self.is_hovered = False
        if not self._dragging:
            self._is_pressed = False
        self.update()

    def _show_context_menu(self, global_pos: QPoint):
        menu = EditorialPopupMenu(self)
        sub_label = "实时双语字幕 · 已开启" if self._subtitle_active else "实时双语字幕"
        sub_col = LEXICON_THEME["mauve"] if self._subtitle_active else LEXICON_THEME["text"]
        act_sub = menu.addAction(make_vector_qicon("subtitle", sub_col), sub_label)
        act_sub.triggered.connect(self.toggle_subtitle.emit)

        act_freeze = menu.addAction(make_vector_qicon("reticle", LEXICON_THEME["text_sec"]), "屏幕定格取词")
        act_freeze.triggered.connect(lambda: self.triggered.emit(global_pos))

        vault_col = LEXICON_THEME["mauve"]
        act_vault = menu.addAction(make_vector_qicon("bookmark_off", vault_col), "生词本")
        act_vault.triggered.connect(self.open_vault.emit)

        act_settings = menu.addAction(make_vector_qicon("sliders", LEXICON_THEME["text_sec"]), "设置")
        act_settings.triggered.connect(self.open_settings.emit)

        menu.adjustSize()
        popup_pos = QPoint(global_pos)
        if self._dock_side == "left":
            popup_pos = QPoint(self.frameGeometry().right() + 4, global_pos.y())
        self._context_menu_open = True
        try:
            menu.exec(popup_pos)
        finally:
            self._context_menu_open = False
            menu.deleteLater()
            if self.isVisible():
                enforce_window_topmost(self, activate=False)

    def mousePressEvent(self, event: QMouseEvent):
        if (time.monotonic() - self._last_touch_ts) < 0.45:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._moved_during_drag = False
            self._is_pressed = True
            self._long_press_fired = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._press_time = time.time()
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.toggle_subtitle.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent):
        if (time.monotonic() - self._last_touch_ts) < 0.45:
            event.accept()
            return
        if self._dragging:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self.reset_interaction_state()
                return
            cur_g = event.globalPosition().toPoint()
            if not self._moved_during_drag and (cur_g - self._press_pos).manhattanLength() >= 10:
                self._moved_during_drag = True
            if self._moved_during_drag:
                screen_geom = self._get_target_screen_geom(cur_g)
                new_pos = cur_g - self._drag_offset
                new_x = max(screen_geom.left() + self._edge_margin, min(screen_geom.right() - self.width() - self._edge_margin, new_pos.x()))
                new_y = max(screen_geom.top() + 24, min(screen_geom.bottom() - self.height() - 24, new_pos.y()))
                self.move(new_x, new_y)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if (time.monotonic() - self._last_touch_ts) < 0.45:
            event.accept()
            return
        if self._dragging:
            was_moved = self._moved_during_drag
            self._dragging = False
            self._moved_during_drag = False
            self._is_pressed = False
            self.update()
            cur_pos = event.globalPosition().toPoint()
            press_duration = time.time() - self._press_time

            if (cur_pos - self._press_pos).manhattanLength() < 16 and not was_moved:
                if press_duration > 0.6:
                    self._show_context_menu(cur_pos)
                else:
                    self.triggered.emit(cur_pos)
            else:
                self._snap_to_nearest_edge()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        inset = 5
        if self._is_pressed:
            rect = QRectF(self.rect().adjusted(inset + 1, inset + 1, -(inset - 1), -(inset - 1)))
        else:
            rect = QRectF(self.rect().adjusted(inset, inset, -inset, -inset))

        if self._is_pressed:
            bg_color = QColor("#816D70")
            border_color = QColor("#1C1C1C")
            icon_color = QColor("#F9F8F6")
            border_w = 1.0
        elif self.is_hovered:
            bg_color = QColor("#F9F8F6")
            border_color = QColor("#1C1C1C")
            icon_color = QColor("#1C1C1C")
            border_w = 1.0
        else:
            bg_color = QColor("#1C1C1C")
            border_color = QColor(249, 248, 246, 68)
            icon_color = QColor("#F9F8F6")
            border_w = 1.0

        painter.setPen(QPen(border_color, border_w))
        painter.setBrush(QBrush(bg_color))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # 居中取词准星线框图标
        icon_rect = QRectF(rect.center().x() - 9, rect.center().y() - 9, 18, 18)
        draw_vector_icon(painter, "reticle", icon_rect, icon_color, stroke_width=1.25)
