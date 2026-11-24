import html
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QFrame, QFileDialog, QMessageBox, QAbstractButton
)
from PySide6.QtCore import Qt, QPoint, QRectF, QEvent, QTimer, Signal
from PySide6.QtGui import QCursor, QColor, QFont, QPainter, QPalette, QPen, QTouchEvent
from .database import get_all_collected, toggle_collection, set_word_collected
from .dictionary_service import speak_word
from .ui_icons import (
    LEXICON_THEME, VectorIconButton, make_vector_qicon, clear_layout,
    LexiconCardFrame, editorial_scrollbar_qss, enhance_scroll_area,
    supports_win11_native_chrome, apply_native_win11_chrome,
)
import csv
import os
import time
from datetime import datetime

FONT_FAMILY_SANS = "\"Segoe UI Variable Text\", \"Segoe UI\", \"Microsoft YaHei UI\", sans-serif"


class _VaultWordCard(QFrame):
    """
    Editorial Hairline Card 生词条目：
    支持 QScroller 电容触屏惯性滑选防误触，顶部 1px 发丝线区隔，悬停加深顶线、点亮左侧 2px 陈玫竖脊并联动书签高亮。
    """
    def __init__(self, word_data, on_clicked, parent=None):
        super().__init__(parent)
        self._word_data = word_data
        self._on_clicked = on_clicked
        self._press_global: QPoint | None = None
        self._hovered = False
        self._bookmark_btn: VectorIconButton | None = None
        self._date_label: QLabel | None = None
        self.setObjectName("WordCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def bind_accent_widgets(self, bookmark_btn: VectorIconButton, date_label: QLabel):
        self._bookmark_btn = bookmark_btn
        self._date_label = date_label

    def _sync_hover_accents(self, active: bool):
        if self._bookmark_btn is not None:
            self._bookmark_btn.set_kind("bookmark_on" if active else "bookmark_off")
            self._bookmark_btn.set_color(LEXICON_THEME["mauve"] if active else LEXICON_THEME["text_muted"])
        if self._date_label is not None:
            col = LEXICON_THEME["text"] if active else LEXICON_THEME["text_muted"]
            self._date_label.setStyleSheet(
                f"color: {col}; font-size: 10.5px; letter-spacing: 1.4px; padding-top: 5px; "
                f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};"
            )

    def enterEvent(self, event):
        if not self._hovered:
            self._hovered = True
            self._sync_hover_accents(True)
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hovered:
            self._hovered = False
            self._sync_hover_accents(self.hasFocus())
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._hovered or self.hasFocus():
            painter.fillRect(self.rect(), QColor(LEXICON_THEME["surface"]))
        else:
            painter.fillRect(self.rect(), QColor(LEXICON_THEME["bg"]))
        if self.width() > 16:
            alpha = 102 if (self._hovered or self.hasFocus()) else 26
            painter.setPen(QPen(QColor(28, 28, 28, alpha), 1.0))
            painter.drawLine(0, 0, self.width(), 0)
        if (self._hovered or self.hasFocus()) and self.height() > 6:
            painter.fillRect(QRectF(0.0, 1.0, 2.0, float(self.height() - 2)), QColor(LEXICON_THEME["mauve"]))
        painter.end()
        super().paintEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._on_clicked:
                self._on_clicked(self._word_data)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            dist = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
            self._press_global = None
            if dist < 12 and self.rect().contains(event.position().toPoint()):
                if self._on_clicked:
                    self._on_clicked(self._word_data)
                event.accept()
                return
        self._press_global = None
        super().mouseReleaseEvent(event)


class VocabularyVaultWindow(QWidget):
    """
    学术生词本独立沉淀视窗 (Vocabulary Vault)
    高奢静奢暖白纸质风重构 (Warm Cream Zen Paper & Editorial Luxury)
    1. 彻底去除一切杂乱的内部多重灰框与表格感，文字纯净呼吸留白；
    2. 采用 Frameless 无缝极简圆角画框，彻底根除 Windows 原生白顶栏的割裂感；
    3. 全面统一至温润暖白原浆纸色彩规范 (#FAF8F5 底座，#FFFFFF 纯白卡片，#1C1917 深度石墨黑)；
    4. 彻底剔除底层虚假学术标签（'系统工程'、'心理学' 等干扰项全部清除）；
    5. 搭载 QScroller 电容触屏动力学滑选手势与一键 Anki/CSV 导出。
    """
    word_removed = Signal(str)
    word_restored = Signal(str)

    def __init__(self, parent=None, on_word_clicked=None):
        super().__init__(parent)
        self.on_word_clicked = on_word_clicked
        self.words = []
        self._drag_pos = QPoint()
        self._is_dragging_window = False
        self._active_filter = "all"  # "all" | "word" | "sentence"
        self._filter_btns = {}
        self._render_gen = 0
        self._pending_render_items = []
        self._render_batch_size = 40
        self._last_removed_id = ""
        self._last_removed_word = ""

        self.setWindowTitle("ScreenLex — 课堂生词本 (Vocabulary Vault)")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self._use_native_chrome = supports_win11_native_chrome()
        if self._use_native_chrome:
            self.setAutoFillBackground(True)
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor(LEXICON_THEME["bg"]))
            self.setPalette(pal)
            self.setStyleSheet(f"VocabularyVaultWindow {{ background-color: {LEXICON_THEME['bg']}; }}")
            self.resize(656, 696)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.resize(680, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        _vfont = QFont()
        _vfont.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
        _vfont.setPointSize(10)
        self.setFont(_vfont)

        self._dirty = True
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self._flush_search_filter)

        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(0)
        self._batch_timer.timeout.connect(self._mount_next_batch)

        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(4000)
        self._undo_timer.timeout.connect(self._hide_undo_bar)

        self._init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_use_native_chrome", False):
            apply_native_win11_chrome(self, corner_pref=1, border_color=LEXICON_THEME["border"])
        if getattr(self, "_dirty", True):
            self.refresh_list(force=True)
        elif self._pending_render_items and not self._batch_timer.isActive():
            self._batch_timer.start()

    def hideEvent(self, event):
        self._batch_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._batch_timer.stop()
        super().closeEvent(event)

    def paintEvent(self, event):
        if getattr(self, "_use_native_chrome", False):
            super().paintEvent(event)
            return
        painter = QPainter(self)
        clip = event.rect()
        painter.setClipRect(clip)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.eraseRect(clip)
        painter.end()
        super().paintEvent(event)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        if self._use_native_chrome:
            root_layout.setContentsMargins(0, 0, 0, 0)
            self.container = LexiconCardFrame(radius=0, parent=self)
            self.container.setObjectName("VaultContainer")
            self.container.setStyleSheet("""
                QFrame#VaultContainer {
                    background: transparent;
                    border: none;
                }
            """)
        else:
            root_layout.setContentsMargins(2, 2, 2, 2)
            # Editorial 直角暖米纸主容器
            self.container = LexiconCardFrame(radius=0, parent=self)
            self.container.setObjectName("VaultContainer")
            self.container.setStyleSheet("""
                QFrame#VaultContainer {
                    background: transparent;
                    border: none;
                }
            """)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(28, 14, 28, 20)
        layout.setSpacing(10)

        # 0. 右上角极简窗口控制栏 (- / ×)
        win_ctrl = QHBoxLayout()
        win_ctrl.setContentsMargins(0, 0, 0, 0)
        win_ctrl.setSpacing(4)
        win_ctrl.addStretch()

        self.min_btn = VectorIconButton(
            kind="minimize",
            color=LEXICON_THEME["text_muted"],
            hover_color=LEXICON_THEME["text"],
            hover_bg=LEXICON_THEME["hover_neutral"],
            size=22,
            radius=0,
            parent=self.container,
        )
        self.min_btn.clicked.connect(self.showMinimized)
        win_ctrl.addWidget(self.min_btn)

        self.close_btn = VectorIconButton(
            kind="close",
            color=LEXICON_THEME["text"],
            hover_color=LEXICON_THEME["mauve"],
            hover_bg=LEXICON_THEME["hover_danger"],
            size=22,
            radius=0,
            parent=self.container,
        )
        self.close_btn.clicked.connect(self.close)
        win_ctrl.addWidget(self.close_btn)
        layout.addLayout(win_ctrl)

        # 1. 顶部刊头栏（衬线大标题 "生词本" + 墨黑直角主按钮 "导出 CSV"）
        top_bar = QHBoxLayout()
        top_bar.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        top_bar.setSpacing(12)

        title = QLabel("生词本", self.container)
        title.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 25px; font-weight: 400; "
            f"font-family: 'Georgia', 'Noto Serif SC', 'STSong', serif; border: none; background: transparent;"
        )
        top_bar.addWidget(title)

        self.subtitle = QLabel("0 词", self.container)
        self.subtitle.hide()
        top_bar.addStretch()

        # Contrast Lockup Primary Button: 导出 CSV (#1C1C1C fill + #F9F8F6 text, 0px radius)
        self.export_btn = QPushButton("导出 CSV", self.container)
        self.export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.export_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1C1C1C;
                color: #F9F8F6;
                border: 1px solid #1C1C1C;
                border-radius: 0px;
                padding: 7px 16px;
                font-size: 11.5px;
                font-weight: 500;
                letter-spacing: 1px;
                font-family: {FONT_FAMILY_SANS};
            }}
            QPushButton:hover {{
                background: #F9F8F6;
                color: #1C1C1C;
            }}
        """)
        self.export_btn.clicked.connect(self._export_anki)
        top_bar.addWidget(self.export_btn)

        layout.addLayout(top_bar)

        # 2. 极简底线检索框 ("搜索...")
        self.search_input = QLineEdit(self.container)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.addAction(
            make_vector_qicon("search", LEXICON_THEME["text_muted"], 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_input.setPlaceholderText("搜索...")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {LEXICON_THEME["text"]};
                border-radius: 0px;
                padding: 7px 4px;
                color: {LEXICON_THEME["text"]};
                font-size: 12.5px;
                font-family: {FONT_FAMILY_SANS};
            }}
            QLineEdit:focus {{
                border-bottom: 1.5px solid {LEXICON_THEME["mauve"]};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        layout.addWidget(self.search_input)

        # 2.5 顶部快速筛选栏（全部 / 单词 / 长句）
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 2, 0, 2)
        filter_row.setSpacing(6)
        for f_key, f_label in (("all", "全部"), ("word", "单词"), ("sentence", "长句")):
            btn = QPushButton(f_label, self.container)
            btn.setCheckable(True)
            btn.setChecked(f_key == self._active_filter)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {LEXICON_THEME['text_muted']}; "
                f"border: 1px solid rgba(28,28,28,45); border-radius: 0px; padding: 0 10px; "
                f"font-size: 11px; font-weight: 500; font-family: {FONT_FAMILY_SANS}; }}"
                f"QPushButton:hover {{ color: {LEXICON_THEME['text']}; border-color: {LEXICON_THEME['text']}; }}"
                f"QPushButton:checked {{ background: #1C1C1C; color: #F9F8F6; border: 1px solid #1C1C1C; }}"
            )
            btn.clicked.connect(lambda _chk=False, k=f_key: self._set_filter_mode(k))
            self._filter_btns[f_key] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch(1)
        self.count_label = QLabel("0 条", self.container)
        self.count_label.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11px; "
            f"background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        filter_row.addWidget(self.count_label)
        layout.addLayout(filter_row)

        # 3. 词汇列表滚动区（不透明暖米纸优化 + 极细直角墨黑滚动条）
        scroll = QScrollArea(self.container)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(editorial_scrollbar_qss(LEXICON_THEME["bg"], width=4))

        self.scroll_widget = QWidget()
        self.scroll_widget.setObjectName("VaultScrollContent")
        self.scroll_widget.setStyleSheet(f"""
            QWidget#VaultScrollContent {{
                background-color: {LEXICON_THEME["bg"]};
            }}
            QFrame#WordCard {{
                background-color: transparent;
                border: none;
                border-radius: 0px;
            }}
        """)
        self.list_layout = QVBoxLayout(self.scroll_widget)
        self.list_layout.setContentsMargins(0, 4, 6, 8)
        self.list_layout.setSpacing(0)
        scroll.setWidget(self.scroll_widget)
        enhance_scroll_area(scroll, top_feather=24, bottom_feather=20, feather_color=LEXICON_THEME["bg"])
        layout.addWidget(scroll, stretch=1)

        # 4. 底部 4 秒误删撤销条（Editorial 墨黑直角底条）
        self.undo_bar = QFrame(self.container)
        self.undo_bar.setObjectName("VaultUndoBar")
        self.undo_bar.setStyleSheet(
            "QFrame#VaultUndoBar { background: #1C1C1C; border: 1px solid #1C1C1C; border-radius: 0px; }"
        )
        undo_h = QHBoxLayout(self.undo_bar)
        undo_h.setContentsMargins(14, 8, 12, 8)
        undo_h.setSpacing(10)
        self.undo_label = QLabel("", self.undo_bar)
        self.undo_label.setStyleSheet(
            f"color: #F9F8F6; font-size: 12px; background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        undo_h.addWidget(self.undo_label, stretch=1)
        self.undo_btn = QPushButton("撤销", self.undo_bar)
        self.undo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.undo_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: #E5D4D7; border: none; "
            f"border-bottom: 1px solid #E5D4D7; border-radius: 0px; padding: 1px 2px; "
            f"font-size: 12px; font-weight: 600; font-family: {FONT_FAMILY_SANS}; }}"
            f"QPushButton:hover {{ color: #FFFFFF; border-bottom: 1px solid #FFFFFF; }}"
        )
        self.undo_btn.clicked.connect(self._undo_last_remove)
        undo_h.addWidget(self.undo_btn)
        self.undo_bar.hide()
        layout.addWidget(self.undo_bar)

        root_layout.addWidget(self.container)

    def _is_interactive_child(self, pt: QPoint) -> bool:
        w = self.childAt(pt)
        while w is not None and w is not self:
            if isinstance(w, (QAbstractButton, QLineEdit, VectorIconButton)):
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, event):
        pt = event.position().toPoint()
        drag_limit = 86 if getattr(self, "_use_native_chrome", False) else 96
        if event.button() == Qt.MouseButton.LeftButton and pt.y() <= drag_limit and not self._is_interactive_child(pt):
            self._is_dragging_window = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_dragging_window and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._is_dragging_window = False
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pos = pts[0].position()
                pt = pos.toPoint()
                drag_limit = 86 if getattr(self, "_use_native_chrome", False) else 96
                if event.type() == QEvent.Type.TouchBegin and pos.y() <= drag_limit and not self._is_interactive_child(pt):
                    self._is_dragging_window = True
                    self._drag_pos = pts[0].globalPosition().toPoint() - self.frameGeometry().topLeft()
                    event.accept()
                    return True
                elif event.type() == QEvent.Type.TouchUpdate and self._is_dragging_window:
                    self.move(pts[0].globalPosition().toPoint() - self._drag_pos)
                    event.accept()
                    return True
                elif event.type() in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                    if self._is_dragging_window:
                        self._is_dragging_window = False
                        event.accept()
                        return True
        return super().event(event)

    def _set_filter_mode(self, mode: str):
        if mode not in ("all", "word", "sentence"):
            return
        self._active_filter = mode
        for k, b in self._filter_btns.items():
            b.blockSignals(True)
            b.setChecked(k == mode)
            b.blockSignals(False)
        cur_query = self.search_input.text() if hasattr(self, "search_input") else ""
        self._filter_list(cur_query)

    def _on_search_text_changed(self, _text: str):
        self._search_timer.start()

    def _flush_search_filter(self):
        cur_query = self.search_input.text() if hasattr(self, "search_input") else ""
        self._filter_list(cur_query)

    def _update_filter_counts(self):
        total = len(self.words)
        sent_cnt = sum(1 for w in self.words if len((w.get("word") or "").strip().split()) > 3)
        word_cnt = total - sent_cnt
        labels = {"all": f"全部 {total}", "word": f"单词 {word_cnt}", "sentence": f"长句 {sent_cnt}"}
        for k, btn in self._filter_btns.items():
            btn.setText(labels.get(k, k))

    def refresh_list(self, force: bool = False):
        # 窗口隐藏时仅置脏，避免每次收藏/查词在后台全量销毁重建数千个卡片控件
        if not force and not self.isVisible():
            self._dirty = True
            return
        self._dirty = False
        self.words = [w for w in get_all_collected() if any(c.isalnum() for c in (w.get("word") or ""))]
        self.subtitle.setText(f"{len(self.words)} 词")
        self._update_filter_counts()
        cur_query = self.search_input.text().strip() if hasattr(self, "search_input") else ""
        self._filter_list(cur_query)

    def _filter_list(self, query: str):
        q = (query or "").strip().lower()
        mode = self._active_filter
        filtered = []
        for w in self.words:
            clean_w = (w.get("word") or "").strip()
            is_sent = len(clean_w.split()) > 3
            if mode == "word" and is_sent:
                continue
            if mode == "sentence" and not is_sent:
                continue
            if q:
                if not (
                    q in clean_w.lower()
                    or q in (w.get("context_sentence") or "").lower()
                    or any(q in (d.get("cn") or "").lower() for d in (w.get("definitions") or []))
                ):
                    continue
            filtered.append(w)
        if hasattr(self, "count_label"):
            self.count_label.setText(f"{len(filtered)} 条")
        self._render_words(filtered)

    @staticmethod
    def _format_vault_date(item: dict) -> str:
        ts_raw = item.get("collected_at") or item.get("created_at")
        if ts_raw:
            try:
                ts_sec = float(ts_raw)
                if ts_sec > 1e11:
                    ts_sec /= 1000.0
                return datetime.fromtimestamp(ts_sec).strftime("%b %d").upper()
            except Exception:
                pass
        return "SAVED"

    def _create_word_card(self, item: dict) -> _VaultWordCard | None:
        clean_word = (item.get("word") or "").strip()
        if not clean_word:
            return None

        row = _VaultWordCard(item, self._handle_item_clicked, self.scroll_widget)
        row_h = QHBoxLayout(row)
        row_h.setContentsMargins(14, 16, 10, 16)
        row_h.setSpacing(14)

        # 左栏：全大写杂志日期 (如 NOV 17)
        date_lbl = QLabel(self._format_vault_date(item), row)
        date_lbl.setFixedWidth(56)
        date_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        date_lbl.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 10.5px; letter-spacing: 1.4px; padding-top: 5px; "
            f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};"
        )
        row_h.addWidget(date_lbl, 0, Qt.AlignmentFlag.AlignTop)

        # 中栏：衬线词头 + 斜体音标 + 简明中文释义
        body_v = QVBoxLayout()
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(5)

        head_h = QHBoxLayout()
        head_h.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        head_h.setSpacing(10)

        w_lbl = QLabel(clean_word, row)
        w_lbl.setTextFormat(Qt.TextFormat.PlainText)
        w_count = len(clean_word.split())
        is_long_entry = w_count > 3
        if is_long_entry:
            w_lbl.setWordWrap(True)
            w_lbl.setStyleSheet(
                f"color: {LEXICON_THEME['text']}; font-size: 14.5px; font-weight: 400; "
                f"font-family: 'Georgia', 'Noto Serif SC', serif; border: none; background: transparent;"
            )
            head_h.addWidget(w_lbl, stretch=1)
        else:
            w_lbl.setStyleSheet(
                f"color: {LEXICON_THEME['text']}; font-size: 18.5px; font-weight: 400; "
                f"font-family: 'Georgia', serif; border: none; background: transparent;"
            )
            head_h.addWidget(w_lbl)

            p_uk = (item.get("phonetic_uk") or "").strip()
            p_us = (item.get("phonetic_us") or "").strip()
            phonetic_display = p_uk or p_us
            if phonetic_display:
                p_lbl = QLabel(phonetic_display, row)
                p_lbl.setTextFormat(Qt.TextFormat.PlainText)
                p_lbl.setStyleSheet(
                    f"color: {LEXICON_THEME['text_muted']}; font-size: 12px; font-style: italic; "
                    f"font-family: 'Georgia', serif; border: none; background: transparent;"
                )
                head_h.addWidget(p_lbl)
            head_h.addStretch()

        body_v.addLayout(head_h)

        defs = item.get("definitions", [])
        if defs:
            def_parts = []
            for d in defs[:2]:
                pos = html.escape((d.get('pos') or '').strip(), quote=False).replace("&#x27;", "'")
                cn = html.escape((d.get('cn') or '').strip(), quote=False).replace("&#x27;", "'")
                pos_str = (
                    f"<span style='color:{LEXICON_THEME['text_muted']}; font-family:Georgia, serif; font-style:italic;'>{pos}</span>&nbsp;&nbsp;"
                    if pos and not any(k in pos for k in ("短语", "释义", "长句", "译文"))
                    else ""
                )
                def_parts.append(f"{pos_str}<span style='color:{LEXICON_THEME['text_muted']};'>{cn}</span>")
            d_lbl = QLabel(" · ".join(def_parts), row)
            d_lbl.setTextFormat(Qt.TextFormat.RichText)
            d_lbl.setWordWrap(True)
            d_lbl.setStyleSheet(f"font-size: 12.5px; border: none; background: transparent; font-family: {FONT_FAMILY_SANS};")
            body_v.addWidget(d_lbl)

        row_h.addLayout(body_v, stretch=1)

        # 右栏：发声按钮 + 书签取消收藏按钮
        actions_h = QHBoxLayout()
        actions_h.setContentsMargins(0, 2, 0, 0)
        actions_h.setSpacing(4)
        actions_h.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        if not is_long_entry or w_count <= 8:
            spk = VectorIconButton(
                kind="speaker",
                color=LEXICON_THEME["text_muted"],
                hover_color=LEXICON_THEME["text"],
                hover_bg=LEXICON_THEME["hover_bg"],
                size=24,
                radius=0,
                parent=row,
            )
            word_str = clean_word
            spk.clicked.connect(lambda w=word_str: speak_word(w))
            actions_h.addWidget(spk)

        del_btn = VectorIconButton(
            kind="bookmark_off",
            color=LEXICON_THEME["text_muted"],
            hover_color=LEXICON_THEME["mauve"],
            hover_bg=LEXICON_THEME["hover_danger"],
            size=24,
            radius=0,
            parent=row,
        )
        del_btn.setToolTip("移出生词本")
        w_id = item["id"]
        del_btn.clicked.connect(lambda wid=w_id, wtxt=clean_word: self._remove_word(wid, wtxt))
        actions_h.addWidget(del_btn)
        row.bind_accent_widgets(del_btn, date_lbl)

        row_h.addLayout(actions_h)
        return row

    def _finish_list_footer(self):
        if self.list_layout.count() > 0:
            bottom_rule = QFrame(self.scroll_widget)
            bottom_rule.setFixedHeight(1)
            bottom_rule.setStyleSheet("background: rgba(28, 28, 28, 26); border: none;")
            self.list_layout.addWidget(bottom_rule)
            self.list_layout.addStretch(1)

    def _mount_next_batch(self):
        if not self._pending_render_items:
            self._finish_list_footer()
            return
        batch = self._pending_render_items[: self._render_batch_size]
        self._pending_render_items = self._pending_render_items[self._render_batch_size :]
        for item in batch:
            card = self._create_word_card(item)
            if card is not None:
                self.list_layout.addWidget(card)
        if self._pending_render_items:
            self._batch_timer.start()
        else:
            self._finish_list_footer()

    def _render_words(self, words_to_show):
        self._batch_timer.stop()
        self._render_gen += 1
        self._pending_render_items = []
        clear_layout(self.list_layout)

        if not words_to_show:
            has_filter = bool(
                (hasattr(self, "search_input") and self.search_input.text().strip())
                or self._active_filter != "all"
            )
            hint_msg = (
                "未找到匹配的生词记录。\n请尝试切换筛选标签或更换关键词检索。"
                if has_filter
                else "暂无收录生词。\n定格取词后向下滑动，或点击词典卡右上角书签即可加入。"
            )
            empty = QLabel(hint_msg, self.scroll_widget)
            empty.setStyleSheet(
                f"color: #888888; font-size: 13px; padding: 60px; border: none; "
                f"background: transparent; font-family: {FONT_FAMILY_SANS};"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(empty)
            return

        # 首批立即可见区 40 张卡片同步挂载，剩余按帧分批挂载，300+ 词 0 卡顿
        first_batch = words_to_show[: self._render_batch_size]
        self._pending_render_items = list(words_to_show[self._render_batch_size :])
        for item in first_batch:
            card = self._create_word_card(item)
            if card is not None:
                self.list_layout.addWidget(card)
        if self._pending_render_items:
            self._batch_timer.start()
        else:
            self._finish_list_footer()

    def _hide_undo_bar(self):
        self._undo_timer.stop()
        self._last_removed_id = ""
        self._last_removed_word = ""
        if hasattr(self, "undo_bar"):
            self.undo_bar.hide()

    def _undo_last_remove(self):
        wid = self._last_removed_id
        raw_w = self._last_removed_word
        self._hide_undo_bar()
        if not wid:
            return
        restored = set_word_collected(wid, True)
        if restored:
            self.word_restored.emit(raw_w)
            self.refresh_list(force=True)

    def _remove_word(self, word_id: str, display_word: str = ""):
        toggle_collection(word_id)
        raw_w = display_word or (word_id[5:] if word_id.startswith("word_") else word_id)
        self._last_removed_id = word_id
        self._last_removed_word = raw_w
        short_w = raw_w if len(raw_w) <= 28 else (raw_w[:27] + "…")
        if hasattr(self, "undo_label") and hasattr(self, "undo_bar"):
            self.undo_label.setText(f"已移出「{short_w}」")
            self.undo_bar.show()
            self._undo_timer.start()
        self.word_removed.emit(raw_w)
        self.refresh_list()

    def _handle_item_clicked(self, word_data):
        if self.on_word_clicked:
            self.on_word_clicked(word_data)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def _export_anki(self):
        if not self.words:
            QMessageBox.information(self, "提示", "生词本暂无词汇可导出。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出生词本为 Anki/CSV",
            os.path.join(os.path.expanduser("~"), f"ScreenLex_生词本_{time.strftime('%Y%m%d')}.csv"),
            "CSV 文本 (*.csv)"
        )
        if not file_path:
            return

        pseudo_pos = {"释义", "短语", "长句", "译文"}
        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Word", "PhoneticUK", "PhoneticUS", "Definitions", "ContextSentence", "ContextTranslation"])
                for w in self.words:
                    def_items = []
                    for d in w.get("definitions", []):
                        pos = (d.get("pos") or "").strip()
                        cn = (d.get("cn") or "").strip()
                        if not cn:
                            continue
                        def_items.append(f"[{pos}] {cn}" if (pos and pos not in pseudo_pos) else cn)
                    defs_str = "; ".join(def_items)
                    ctx_trans_clean = (w.get("context_translation") or "").replace("↳ 译文：", "").strip()
                    writer.writerow([
                        w["word"],
                        w.get("phonetic_uk", ""),
                        w.get("phonetic_us", ""),
                        defs_str,
                        w.get("context_sentence", ""),
                        ctx_trans_clean
                    ])
        except OSError as e:
            QMessageBox.warning(self, "导出失败", f"无法写入文件（可能正被 Excel 或其他程序占用）：\n{e}")
            return

        QMessageBox.information(self, "导出成功", f"生词已成功导出至：\n{file_path}\n支持一键导入 Anki 或 Excel。")
