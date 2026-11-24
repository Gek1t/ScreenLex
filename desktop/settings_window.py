"""
ScreenLex 设置窗口 (Editorial Settings Window)
风格对齐 Pure White Lexicon (#F9F8F6 暖米纸底 + 1.5px #1C1C1C 直角线框 + #816D70 陈玫点缀)
支持：
1. 全局快捷键总开关与 4 组快捷键实时配置（即时生效、冲突校验、持久化至 SQLite app_settings）
2. 停靠微柄左右贴边选择与一键重置位置
3. 开机自启动联动（与托盘菜单双向同步）
4. 离线全词库状态展示与一键打开日志目录
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt, QPoint, QRectF, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QKeyEvent, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget, QAbstractButton,
)
from .input_hook import DEFAULT_HOTKEYS, HOTKEY_LABELS, HOTKEY_PRESETS, normalize_hotkey_spec
from .logger import open_log_dir, get_log_file
from .offline_dict import get_offline_dict_count
from .ui_icons import (
    LEXICON_THEME,
    VectorIconButton,
    apply_native_win11_chrome,
    supports_win11_native_chrome,
    enforce_window_topmost,
)

FONT_FAMILY_SANS = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'
_WDA_EXCLUDEFROMCAPTURE = 0x00000011

_u32_local = None
if sys.platform == "win32":
    try:
        _u32_local = ctypes.WinDLL("user32", use_last_error=True)
        _u32_local.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        _u32_local.SetWindowDisplayAffinity.restype = wintypes.BOOL
    except Exception:
        _u32_local = None


def _exclude_from_capture(widget: QWidget):
    if _u32_local is None:
        return
    try:
        hwnd = int(widget.winId())
        if hwnd:
            _u32_local.SetWindowDisplayAffinity(wintypes.HWND(hwnd), wintypes.DWORD(_WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        pass


class SettingsWindow(QWidget):
    hotkeys_changed = Signal(dict)
    dock_side_changed = Signal(str)
    dock_reset_requested = Signal()
    autostart_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ScreenLex — 设置")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._use_native_chrome = supports_win11_native_chrome()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(LEXICON_THEME["bg"]))
        self.setPalette(pal)
        self.setFixedWidth(452)

        self._dragging = False
        self._drag_offset = QPoint()
        self._hotkey_combos: Dict[str, QComboBox] = {}
        self._dock_side_btns: Dict[str, QPushButton] = {}
        self._current_dock_side: str = "right"
        self._current_hotkeys: Dict[str, Any] = dict(DEFAULT_HOTKEYS)

        self._init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if self._use_native_chrome:
            apply_native_win11_chrome(self, corner_pref=1, border_color=LEXICON_THEME["border"])
        _exclude_from_capture(self)
        enforce_window_topmost(self, activate=False)
        self._refresh_status_info()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(LEXICON_THEME["bg"]))
        painter.setPen(QPen(QColor(LEXICON_THEME["border"]), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.75, 0.75, max(1.0, self.width() - 1.5), max(1.0, self.height() - 1.5)))
        painter.end()
        super().paintEvent(event)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 20)
        root.setSpacing(14)

        # 1. 刊头标题栏
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("设置")
        tf = QFont("Georgia", 16)
        tf.setFamilies(["Georgia", "Noto Serif SC", "STSong", "serif"])
        title.setFont(tf)
        title.setStyleSheet(f"color: {LEXICON_THEME['text']}; background: transparent; border: none;")
        head.addWidget(title)

        sub = QLabel("PREFERENCES")
        sub.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 9.5px; letter-spacing: 2px; "
            f"padding-top: 6px; background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        head.addWidget(sub)
        head.addStretch(1)

        close_btn = VectorIconButton(
            kind="close",
            color=LEXICON_THEME["text"],
            hover_color=LEXICON_THEME["mauve"],
            hover_bg=LEXICON_THEME["hover_danger"],
            size=22,
            radius=0,
            parent=self,
        )
        close_btn.clicked.connect(self.close)
        head.addWidget(close_btn)
        root.addLayout(head)

        root.addWidget(self._make_divider())

        # 2. 全局快捷键区
        hk_head = QHBoxLayout()
        hk_head.setSpacing(8)
        hk_title = QLabel("全局快捷键")
        hk_title.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 13px; font-weight: 600; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        hk_head.addWidget(hk_title)
        hk_head.addStretch(1)

        self.hk_enable_cb = QCheckBox("启用全局热键")
        self.hk_enable_cb.setChecked(True)
        self.hk_enable_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.hk_enable_cb.setStyleSheet(self._checkbox_qss())
        self.hk_enable_cb.toggled.connect(self._on_hotkeys_enable_toggled)
        hk_head.addWidget(self.hk_enable_cb)
        root.addLayout(hk_head)

        combo_qss = (
            f"QComboBox {{ background: #FFFFFF; color: {LEXICON_THEME['text']}; "
            f"border: 1px solid rgba(28, 28, 28, 80); border-radius: 0px; "
            f"padding: 4px 8px; font-size: 12px; font-family: {FONT_FAMILY_SANS}; }}"
            f"QComboBox:hover {{ border-color: {LEXICON_THEME['text']}; }}"
            f"QComboBox:disabled {{ background: #F3F2EE; color: #9CA3AF; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background: #F9F8F6; color: #1C1C1C; "
            f"border: 1px solid #1C1C1C; selection-background-color: #1C1C1C; selection-color: #F9F8F6; }}"
        )

        for action_key, label_text in HOTKEY_LABELS:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color: {LEXICON_THEME['text_sec']}; font-size: 12px; "
                f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
            )
            row.addWidget(lbl)
            row.addStretch(1)

            combo = QComboBox(self)
            combo.setFixedWidth(136)
            combo.setStyleSheet(combo_qss)
            presets = list(HOTKEY_PRESETS.get(action_key, []))
            for p in presets:
                combo.addItem(p if p else "禁用", p)
            combo.currentIndexChanged.connect(lambda _idx, ak=action_key: self._on_hotkey_combo_changed())
            self._hotkey_combos[action_key] = combo
            row.addWidget(combo)
            root.addLayout(row)

        self.hk_status_lbl = QLabel("")
        self.hk_status_lbl.setWordWrap(True)
        self.hk_status_lbl.setStyleSheet(
            f"color: {LEXICON_THEME['mauve']}; font-size: 11px; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        self.hk_status_lbl.hide()
        root.addWidget(self.hk_status_lbl)

        root.addWidget(self._make_divider())

        # 3. 停靠微柄设置区
        dock_row = QHBoxLayout()
        dock_row.setSpacing(10)
        dock_lbl = QLabel("停靠微柄贴边")
        dock_lbl.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 12.5px; font-weight: 500; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        dock_row.addWidget(dock_lbl)
        dock_row.addStretch(1)

        seg_box = QHBoxLayout()
        seg_box.setSpacing(0)
        for idx, (side_key, side_label) in enumerate((("left", "左侧"), ("right", "右侧"))):
            btn = QPushButton(side_label)
            btn.setCheckable(True)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFixedHeight(27)
            left_rule = "border-left: none;" if idx > 0 else "border-left: 1px solid rgba(28,28,28,70);"
            btn.setStyleSheet(
                f"QPushButton {{ background: #F9F8F6; color: #1C1C1C; border: 1px solid rgba(28,28,28,70); {left_rule} "
                f"border-radius: 0px; padding: 0 12px; font-size: 11.5px; font-weight: 500; font-family: {FONT_FAMILY_SANS}; }}"
                f"QPushButton:hover {{ background: #F3F2EE; }}"
                f"QPushButton:checked {{ background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; }}"
            )
            btn.clicked.connect(lambda _chk=False, sk=side_key: self._on_dock_side_clicked(sk))
            self._dock_side_btns[side_key] = btn
            seg_box.addWidget(btn)
        dock_row.addLayout(seg_box)

        self.dock_reset_btn = QPushButton("重置位置")
        self.dock_reset_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.dock_reset_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {LEXICON_THEME['text_sec']}; border: none; "
            f"border-bottom: 1px solid rgba(28,28,28,90); border-radius: 0px; padding: 2px 1px; "
            f"font-size: 11.5px; font-family: {FONT_FAMILY_SANS}; }}"
            f"QPushButton:hover {{ color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }}"
        )
        self.dock_reset_btn.clicked.connect(self.dock_reset_requested.emit)
        dock_row.addWidget(self.dock_reset_btn)
        root.addLayout(dock_row)

        dock_tip = QLabel("提示：也可直接按住微柄拖向屏幕左/右边缘自动吸附并记忆位置。")
        dock_tip.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11px; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        root.addWidget(dock_tip)

        root.addWidget(self._make_divider())

        # 4. 系统与词库状态区
        sys_row = QHBoxLayout()
        sys_row.setSpacing(10)
        self.autostart_cb = QCheckBox("开机自动启动 ScreenLex")
        self.autostart_cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.autostart_cb.setStyleSheet(self._checkbox_qss())
        self.autostart_cb.toggled.connect(self._on_autostart_toggled)
        sys_row.addWidget(self.autostart_cb)
        sys_row.addStretch(1)

        self.log_btn = QPushButton("打开日志目录")
        self.log_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.log_btn.setToolTip(get_log_file())
        self.log_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {LEXICON_THEME['text_sec']}; border: none; "
            f"border-bottom: 1px solid rgba(28,28,28,90); border-radius: 0px; padding: 2px 1px; "
            f"font-size: 11.5px; font-family: {FONT_FAMILY_SANS}; }}"
            f"QPushButton:hover {{ color: #1C1C1C; border-bottom: 1px solid #1C1C1C; }}"
        )
        self.log_btn.clicked.connect(lambda: open_log_dir())
        sys_row.addWidget(self.log_btn)
        root.addLayout(sys_row)

        self.dict_status_lbl = QLabel("")
        self.dict_status_lbl.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11px; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        root.addWidget(self.dict_status_lbl)

        # 5. 底栏按钮
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.reset_hk_btn = QPushButton("恢复默认热键")
        self.reset_hk_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.reset_hk_btn.setStyleSheet(
            f"QPushButton {{ background: #F9F8F6; color: #1C1C1C; border: 1px solid rgba(28,28,28,70); "
            f"border-radius: 0px; padding: 6px 12px; font-size: 11.5px; font-family: {FONT_FAMILY_SANS}; }}"
            f"QPushButton:hover {{ background: #F3F2EE; border-color: #1C1C1C; }}"
        )
        self.reset_hk_btn.clicked.connect(self._reset_default_hotkeys)
        foot.addWidget(self.reset_hk_btn)
        foot.addStretch(1)

        done_btn = QPushButton("完成")
        done_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        done_btn.setStyleSheet(
            f"QPushButton {{ background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; "
            f"border-radius: 0px; padding: 6px 20px; font-size: 11.5px; font-weight: 500; font-family: {FONT_FAMILY_SANS}; }}"
            f"QPushButton:hover {{ background: #F9F8F6; color: #1C1C1C; }}"
        )
        done_btn.clicked.connect(self.close)
        foot.addWidget(done_btn)
        root.addLayout(foot)

    @staticmethod
    def _make_divider() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {LEXICON_THEME['divider']}; border: none;")
        return line

    @staticmethod
    def _checkbox_qss() -> str:
        return (
            f"QCheckBox {{ color: {LEXICON_THEME['text']}; font-size: 12px; spacing: 7px; "
            f"background: transparent; font-family: {FONT_FAMILY_SANS}; }}"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #1C1C1C; "
            f"border-radius: 0px; background: #FFFFFF; }}"
            f"QCheckBox::indicator:checked {{ background: #1C1C1C; border: 1px solid #1C1C1C; }}"
        )

    def _refresh_status_info(self):
        cnt = get_offline_dict_count()
        if cnt > 0:
            self.dict_status_lbl.setText(f"离线全词库已就绪：已内置 {cnt:,} 条核心英文与学术词汇（无网 0ms 查词）")
        else:
            self.dict_status_lbl.setText("离线词库：使用内置高频学术核心词表")

    def sync_state(
        self,
        hotkeys: Optional[Dict[str, Any]] = None,
        dock_side: str = "right",
        autostart: bool = False,
        failed_actions: Optional[List[str]] = None,
    ):
        if isinstance(hotkeys, dict):
            self._current_hotkeys = dict(DEFAULT_HOTKEYS)
            self._current_hotkeys.update(hotkeys)
        enabled = bool(self._current_hotkeys.get("enabled", True))
        self.hk_enable_cb.blockSignals(True)
        self.hk_enable_cb.setChecked(enabled)
        self.hk_enable_cb.blockSignals(False)

        for action_key, combo in self._hotkey_combos.items():
            val = normalize_hotkey_spec(str(self._current_hotkeys.get(action_key, "") or ""))
            combo.blockSignals(True)
            combo.setEnabled(enabled)
            idx = combo.findData(val)
            if idx < 0 and val:
                combo.addItem(val, val)
                idx = combo.findData(val)
            if idx < 0:
                idx = combo.findData("")
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        self._current_dock_side = "left" if dock_side == "left" else "right"
        for k, btn in self._dock_side_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == self._current_dock_side)
            btn.blockSignals(False)

        self.autostart_cb.blockSignals(True)
        self.autostart_cb.setChecked(bool(autostart))
        self.autostart_cb.blockSignals(False)

        self.set_hotkey_feedback(failed_actions or [])
        self._refresh_status_info()

    def set_hotkey_feedback(self, failed_actions: List[str]):
        if failed_actions:
            label_map = dict(HOTKEY_LABELS)
            names = "、".join(label_map.get(a, a) for a in failed_actions)
            self.hk_status_lbl.setText(f"以下热键可能被其他软件占用，建议更换组合：{names}")
            self.hk_status_lbl.show()
        else:
            self.hk_status_lbl.hide()
        self.adjustSize()

    def _collect_hotkeys_config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {"enabled": bool(self.hk_enable_cb.isChecked())}
        for action_key, combo in self._hotkey_combos.items():
            cfg[action_key] = str(combo.currentData() or "")
        return cfg

    def _on_hotkeys_enable_toggled(self, checked: bool):
        for combo in self._hotkey_combos.values():
            combo.setEnabled(checked)
        self._emit_hotkeys_if_valid()

    def _on_hotkey_combo_changed(self):
        self._emit_hotkeys_if_valid()

    def _emit_hotkeys_if_valid(self):
        cfg = self._collect_hotkeys_config()
        if not cfg.get("enabled", True):
            # 关闭全局热键总开关时允许直接生效并持久化，不因冲突提示阻断关闭操作
            self.hk_status_lbl.hide()
            self.adjustSize()
            disabled_cfg = dict(self._current_hotkeys)
            disabled_cfg["enabled"] = False
            self._current_hotkeys = disabled_cfg
            self.hotkeys_changed.emit(dict(disabled_cfg))
            return
        seen: Dict[str, str] = {}
        conflicts: List[str] = []
        label_map = dict(HOTKEY_LABELS)
        for action_key, _ in HOTKEY_LABELS:
            spec = (cfg.get(action_key) or "").strip()
            if not spec:
                continue
            if spec in seen:
                conflicts.append(f"{label_map.get(seen[spec], seen[spec])} 与 {label_map.get(action_key, action_key)} ({spec})")
            else:
                seen[spec] = action_key
        if conflicts:
            self.hk_status_lbl.setText(f"快捷键冲突，未保存：{'；'.join(conflicts)}")
            self.hk_status_lbl.show()
            self.adjustSize()
            return
        self.hk_status_lbl.hide()
        self.adjustSize()
        self._current_hotkeys = dict(cfg)
        self.hotkeys_changed.emit(dict(cfg))

    def _reset_default_hotkeys(self):
        self.sync_state(
            hotkeys=dict(DEFAULT_HOTKEYS),
            dock_side=self._current_dock_side,
            autostart=self.autostart_cb.isChecked(),
        )
        self.hotkeys_changed.emit(dict(DEFAULT_HOTKEYS))

    def _on_dock_side_clicked(self, side: str):
        self._current_dock_side = "left" if side == "left" else "right"
        for k, btn in self._dock_side_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == self._current_dock_side)
            btn.blockSignals(False)
        self.dock_side_changed.emit(self._current_dock_side)

    def _on_autostart_toggled(self, checked: bool):
        self.autostart_toggled.emit(bool(checked))

    def _is_interactive_child(self, pt: QPoint) -> bool:
        w = self.childAt(pt)
        while w is not None and w is not self:
            if isinstance(w, (QAbstractButton, QComboBox, VectorIconButton)):
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and not self._is_interactive_child(event.position().toPoint()):
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
        if self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
