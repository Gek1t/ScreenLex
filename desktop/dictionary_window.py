import html
import re
import threading
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame,
    QApplication, QAbstractButton
)
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QSize, Signal, QEvent, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QCursor, QKeyEvent, QMouseEvent, QPalette, QTouchEvent,
    QPolygon, QTextDocument, QTextBlockFormat, QTextCursor, QTextOption, QAbstractTextDocumentLayout
)
from .dictionary_service import speak_word, enrich_lookup_sync, LOADING_PLACEHOLDERS, CTX_LOADING_PLACEHOLDERS
from .freeze_overlay import select_word_indices_by_gesture
from .ocr_helpers import clean_word_text
from .database import toggle_collection, get_word, save_word, update_enrichment_if_collected
from .ui_icons import (
    EditorialPullQuoteFrame, LEXICON_THEME, draw_vector_icon, VectorIconButton, clear_layout,
    LexiconCardFrame, enhance_scroll_area,
    supports_win11_native_chrome, apply_native_win11_chrome, enforce_window_topmost,
)
from typing import Dict, Any, Callable, Optional, List

def make_word_id(word: str) -> str:
    return f"word_{word.strip().lower()}"

FONT_FAMILY_SANS = "\"Segoe UI Variable Text\", \"Segoe UI\", \"Microsoft YaHei UI\", sans-serif"


class PhoneticButton(QWidget):
    """
    纯粹内联排印音标按钮（无灰底胶囊、无 Emoji）：
    左侧以斜体展示 BrE/AmE，中段展示音标，右侧绘制 14px 矢量扬声器图标。
    保持 .text() / .setText() 接口兼容以维持现有测试与外部读取契约。
    """
    clicked = Signal()

    def __init__(self, accent_tag: str = "BrE", parent=None):
        super().__init__(parent)
        self._tag = accent_tag
        self._ipa = ""
        self._raw_text = accent_tag
        self.is_hovered = False
        self._pressed = False
        self._last_touch_ts: float = 0.0
        self.setFixedHeight(22)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._update_metrics()

    def set_phonetic(self, tag: str, ipa: str):
        self._tag = tag
        self._ipa = ipa.strip()
        self._raw_text = f"{tag} {self._ipa}".strip()
        self._update_metrics()
        self.update()

    def setText(self, text: str):
        self._raw_text = text
        parts = text.split(None, 1)
        if len(parts) == 2:
            self._tag, self._ipa = parts[0], parts[1].strip()
        else:
            self._tag, self._ipa = text, ""
        self._update_metrics()
        self.update()

    def text(self) -> str:
        return self._raw_text

    def _update_metrics(self):
        tag_font = QFont("Georgia", 9)
        tag_font.setItalic(True)
        ipa_font = QFont("Segoe UI", 9)
        w = 8 + QFontMetrics(tag_font).horizontalAdvance(self._tag)
        if self._ipa:
            w += 5 + QFontMetrics(ipa_font).horizontalAdvance(self._ipa)
        w += 5 + 14 + 6
        self.setFixedWidth(max(48, w))

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 22)

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
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.is_hovered or self._pressed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(LEXICON_THEME["hover_bg"]))
            p.drawRect(self.rect())

        x = 2
        tag_font = QFont("Georgia", 9.2)
        tag_font.setItalic(True)
        p.setFont(tag_font)
        p.setPen(QColor(LEXICON_THEME["mauve"] if self._tag == "BrE" else LEXICON_THEME["text_muted"]))
        fm_tag = QFontMetrics(tag_font)
        tag_w = fm_tag.horizontalAdvance(self._tag)
        p.drawText(QRect(x, 0, tag_w, self.height()), Qt.AlignmentFlag.AlignVCenter, self._tag)
        x += tag_w + 5

        if self._ipa:
            ipa_font = QFont("Segoe UI", 9)
            p.setFont(ipa_font)
            p.setPen(QColor(LEXICON_THEME["text_muted"]))
            fm_ipa = QFontMetrics(ipa_font)
            ipa_w = fm_ipa.horizontalAdvance(self._ipa)
            p.drawText(QRect(x, 0, ipa_w, self.height()), Qt.AlignmentFlag.AlignVCenter, self._ipa)
            x += ipa_w + 5

        icon_rect = QRectF(x, (self.height() - 14) / 2.0, 14, 14)
        draw_vector_icon(p, "speaker", icon_rect, QColor(LEXICON_THEME["text"]), stroke_width=1.2)


class ZenCollectButton(QWidget):
    """
    极简矢量书签收藏按钮（24x24 微圆角方形热区，零圆环、零星号 Emoji）。
    """
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.is_collected = False
        self.is_hovered = False
        self._pressed = False
        self._last_touch_ts: float = 0.0

    def set_collected(self, state: bool):
        self.is_collected = state
        self.setToolTip("已存入生词本 (点击移出)" if state else "未加入生词本 (点击加入)")
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

        if self.is_collected:
            if self.is_hovered or self._pressed:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(LEXICON_THEME["hover_danger"]))
                painter.drawRect(self.rect())
                icon_color = QColor(LEXICON_THEME["text"])
            else:
                icon_color = QColor(LEXICON_THEME["mauve"])
            draw_vector_icon(painter, "bookmark_on", QRectF(self.rect()), icon_color, stroke_width=1.25)
        else:
            if self.is_hovered or self._pressed:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(LEXICON_THEME["hover_bg"]))
                painter.drawRect(self.rect())
                icon_color = QColor(LEXICON_THEME["text"])
            else:
                icon_color = QColor(LEXICON_THEME["text_muted"])
            draw_vector_icon(painter, "bookmark_off", QRectF(self.rect()), icon_color, stroke_width=1.25)


class InteractiveSentenceLabel(QLabel):
    """
    支持手势二次选词的交互式原文标签：
    1. 保持 QLabel 全部公开接口（text / setText / setTextFormat / setWordWrap / setStyleSheet）兼容；
    2. 基于 QTextDocument 精确测量所有英文单词坐标，复用定格层几何选词核心；
    3. 支持轻点单词、横向划线、矩形拉框及闭合画圈手势，松手即发射 word_selected 信号。
    """
    word_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._captures_gesture = True
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))

        self.all_words: List[Dict[str, Any]] = []
        self._boxes_dirty: bool = True
        self._last_layout_key = None
        self._cached_doc_key = None
        self._cached_doc: Optional[QTextDocument] = None
        self._cached_doc_h: float = 0.0

        self._pressing: bool = False
        self.touch_start = QPoint()
        self.touch_cur = QPoint()
        self.stroke_points: List[QPoint] = []
        self.min_sx: Optional[int] = None
        self.max_sx: Optional[int] = None
        self.min_sy: Optional[int] = None
        self.max_sy: Optional[int] = None
        self.path_len: float = 0.0
        self.initial_idx: int = -1
        self.selected_indices: List[int] = []
        self.selected_boxes: List[QRect] = []
        self.selected_text: str = ""

    def _invalidate_doc_cache(self):
        self._boxes_dirty = True
        self._cached_doc_key = None
        self._cached_doc = None
        self._cached_doc_h = 0.0
        self.updateGeometry()

    def _reset_selection_state(self):
        self._pressing = False
        self.stroke_points = []
        self.min_sx = None
        self.max_sx = None
        self.min_sy = None
        self.max_sy = None
        self.path_len = 0.0
        self.initial_idx = -1
        self.selected_indices = []
        self.selected_boxes = []
        self.selected_text = ""

    def setText(self, text: str):
        super().setText(text)
        self._invalidate_doc_cache()
        self._reset_selection_state()

    def setTextFormat(self, fmt):
        super().setTextFormat(fmt)
        self._invalidate_doc_cache()

    def setWordWrap(self, on: bool):
        super().setWordWrap(on)
        self._invalidate_doc_cache()

    def setStyleSheet(self, styleSheet: str):
        super().setStyleSheet(styleSheet)
        self._invalidate_doc_cache()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._boxes_dirty = True

    def _resolved_text_color(self) -> QColor:
        qss = self.styleSheet() or ""
        m = re.search(r"(?:^|[;\s])color\s*:\s*(#[0-9A-Fa-f]{6,8})", qss)
        if m:
            col = QColor(m.group(1))
            if col.isValid():
                return col
        return self.palette().color(QPalette.ColorRole.WindowText)

    def _build_text_doc(self, avail_w: float, store_cache: bool = True) -> QTextDocument:
        if store_cache:
            self.ensurePolished()
        raw_text = self.text() or ""
        fmt = self.textFormat()
        key = (raw_text, fmt, round(float(avail_w), 1), self.font().toString(), int(self.alignment()))
        if self._cached_doc is not None and self._cached_doc_key == key:
            return self._cached_doc

        doc_font = QFont(self.font())
        doc_font.setWordSpacing(0.8)
        doc_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102.0)

        doc = QTextDocument()
        doc.setDefaultFont(doc_font)
        doc.setDocumentMargin(0)
        opt = doc.defaultTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(opt)

        if fmt == Qt.TextFormat.RichText or (fmt == Qt.TextFormat.AutoText and "<" in raw_text and ">" in raw_text):
            doc.setHtml(raw_text)
        else:
            doc.setPlainText(raw_text)

        if raw_text:
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            blk_fmt = QTextBlockFormat()
            blk_fmt.setLineHeight(162.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.mergeBlockFormat(blk_fmt)

        doc.setTextWidth(float(max(40.0, avail_w)))
        if store_cache:
            self._cached_doc = doc
            self._cached_doc_key = key
            self._cached_doc_h = float(doc.size().height())
        return doc

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        raw_text = self.text()
        if not raw_text:
            return super().heightForWidth(width)
        m = self.contentsMargins()
        avail_w = max(40.0, float(width - m.left() - m.right()))
        doc = self._build_text_doc(avail_w, store_cache=False)
        return max(18, int(round(float(doc.size().height()))) + m.top() + m.bottom() + 2)

    def sizeHint(self) -> QSize:
        if self.text():
            w = self.width() if self.width() >= 40 else 340
            return QSize(w, self.heightForWidth(w))
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        if self.text():
            w = self.width() if self.width() >= 40 else 180
            return QSize(40, self.heightForWidth(w))
        return super().minimumSizeHint()

    def _rebuild_word_boxes(self):
        self.ensurePolished()
        cr = self.contentsRect()
        w = max(40, cr.width())
        key = (self.text(), self.textFormat(), w, self.font().toString(), int(self.alignment()))
        if not self._boxes_dirty and self._last_layout_key == key:
            return
        self._boxes_dirty = False
        self._last_layout_key = key
        self.all_words = []

        raw_text = self.text()
        if not raw_text:
            return

        doc = self._build_text_doc(float(w))
        doc_h = float(doc.size().height())

        align = self.alignment()
        if align & Qt.AlignmentFlag.AlignVCenter:
            y_base = float(cr.top()) + max(0.0, (cr.height() - doc_h) / 2.0)
        elif align & Qt.AlignmentFlag.AlignBottom:
            y_base = float(cr.top()) + max(0.0, cr.height() - doc_h)
        else:
            y_base = float(cr.top())

        doc_layout = doc.documentLayout()
        blk = doc.begin()
        global_line_id = 0
        while blk.isValid():
            blk_rect = doc_layout.blockBoundingRect(blk)
            layout = blk.layout()
            blk_text = blk.text()
            for m in re.finditer(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*", blk_text):
                token = m.group(0)
                line_start = layout.lineForTextPosition(m.start())
                if not line_start.isValid():
                    continue
                line_end = layout.lineForTextPosition(m.end())
                x1_raw = line_start.cursorToX(m.start())
                x1 = float(cr.left()) + float(blk_rect.x()) + float(x1_raw[0] if isinstance(x1_raw, tuple) else x1_raw)
                if line_end.isValid() and line_end.lineNumber() == line_start.lineNumber():
                    x2_raw = line_start.cursorToX(m.end())
                    x2 = float(cr.left()) + float(blk_rect.x()) + float(x2_raw[0] if isinstance(x2_raw, tuple) else x2_raw)
                else:
                    x2 = float(cr.left()) + float(blk_rect.x()) + float(line_start.naturalTextWidth())
                y1 = y_base + float(blk_rect.y()) + float(line_start.y())
                lh = max(10.0, float(line_start.height()))
                bw = max(4.0, x2 - x1)
                self.all_words.append({
                    "text": token,
                    "clean": token,
                    "box": {"x": x1, "y": y1, "w": bw, "h": lh},
                    "line_id": global_line_id + line_start.lineNumber(),
                })
            global_line_id += max(1, layout.lineCount())
            blk = blk.next()

    def _find_word_at_point(self, pt: QPoint, tolerance: int = 8) -> int:
        if not self.all_words:
            return -1
        px, py = float(pt.x()), float(pt.y())
        best_idx = -1
        min_dist_sq = float("inf")
        for idx, w in enumerate(self.all_words):
            box = w["box"]
            bx, by, bw, bh = box["x"], box["y"], box["w"], box["h"]
            if (bx - tolerance) <= px <= (bx + bw + tolerance) and (by - tolerance) <= py <= (by + bh + tolerance):
                cx = bx + bw / 2.0
                cy = by + bh / 2.0
                dist_sq = (px - cx) ** 2 + (py - cy) ** 2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    best_idx = idx
        return best_idx

    def _apply_selected_indices(self, indices: List[int]):
        if not indices or not self.all_words:
            self.selected_indices = []
            self.selected_boxes = []
            self.selected_text = ""
            return
        clean_indices = sorted(
            {i for i in indices if 0 <= i < len(self.all_words)},
            key=lambda idx: (self.all_words[idx].get("line_id", 0), self.all_words[idx]["box"]["x"]),
        )
        self.selected_indices = clean_indices
        self.selected_text = " ".join(self.all_words[i]["text"] for i in clean_indices)

        lines_dict: Dict[int, List[Dict[str, Any]]] = {}
        for idx in clean_indices:
            w = self.all_words[idx]
            lines_dict.setdefault(w.get("line_id", 0), []).append(w)

        boxes: List[QRect] = []
        for lid in sorted(lines_dict.keys()):
            lw_list = lines_dict[lid]
            min_bx = min(w["box"]["x"] for w in lw_list)
            min_by = min(w["box"]["y"] for w in lw_list)
            max_bx2 = max(w["box"]["x"] + w["box"]["w"] for w in lw_list)
            max_by2 = max(w["box"]["y"] + w["box"]["h"] for w in lw_list)
            boxes.append(QRect(
                int(round(min_bx)),
                int(round(min_by)),
                max(8, int(round(max_bx2 - min_bx))),
                max(10, int(round(max_by2 - min_by))),
            ))
        self.selected_boxes = boxes

    def _update_selection_from_stroke(self, cur_pos: QPoint):
        if not self.all_words or self.min_sx is None or self.max_sx is None:
            return
        sw = self.max_sx - self.min_sx
        sh = self.max_sy - self.min_sy
        diag = (sw * sw + sh * sh) ** 0.5

        is_closed_loop = False
        if len(self.stroke_points) >= 4 and diag > 0:
            start_pt = self.stroke_points[0]
            end_pt = self.stroke_points[-1]
            closing_dist = ((end_pt.x() - start_pt.x()) ** 2 + (end_pt.y() - start_pt.y()) ** 2) ** 0.5
            half_len = max(3, len(self.stroke_points) // 2)
            min_dist_to_head = min(
                ((end_pt.x() - p.x()) ** 2 + (end_pt.y() - p.y()) ** 2) ** 0.5
                for p in self.stroke_points[:half_len]
            )
            is_closed_loop = (
                self.path_len >= 1.2 * diag and (
                    closing_dist <= 60.0 or closing_dist <= 0.35 * diag or
                    min_dist_to_head <= 50.0 or min_dist_to_head <= 0.35 * diag
                )
            )
        same_line_as_init = False
        if self.initial_idx != -1:
            init_b = self.all_words[self.initial_idx]["box"]
            same_line_as_init = (init_b["y"] - 4.0) <= cur_pos.y() <= (init_b["y"] + init_b["h"] + 2.0)
        is_horiz_swipe = (
            (self.initial_idx == -1 or same_line_as_init)
            and sw >= 16.0
            and (sw >= 2.2 * max(1, sh) or (sh <= 14.0 and sw >= 18.0))
        )

        if self.initial_idx == -1 or is_closed_loop or is_horiz_swipe:
            indices = select_word_indices_by_gesture(
                self.all_words,
                self.min_sx,
                self.min_sy,
                self.max_sx,
                self.max_sy,
                stroke_points=self.stroke_points,
                path_len=self.path_len,
                dpr=1.0,
                pad_tol=4.0,
                swipe_min_w=16.0,
                swipe_flat_min_w=18.0,
                swipe_v_pad=6.0,
                swipe_max_h=14.0,
                lasso_close_px=60.0,
                lasso_head_px=50.0,
                allow_vertical_bracket=False,
            )
            if indices:
                self._apply_selected_indices(indices)
                return

        if self.initial_idx != -1:
            lines_map: Dict[int, List[tuple[int, Dict[str, Any]]]] = {}
            for idx, w in enumerate(self.all_words):
                lines_map.setdefault(w.get("line_id", 0), []).append((idx, w))
            best_lid = None
            min_dy = float("inf")
            for lid, items in lines_map.items():
                top = min(w["box"]["y"] for _, w in items)
                bottom = max(w["box"]["y"] + w["box"]["h"] for _, w in items)
                if (top - 4) <= cur_pos.y() <= (bottom + 4):
                    dy = 0.0
                elif cur_pos.y() < (top - 4):
                    dy = (top - 4) - cur_pos.y()
                else:
                    dy = cur_pos.y() - (bottom + 4)
                if dy < min_dy:
                    min_dy = dy
                    best_lid = lid
            if best_lid is not None:
                chosen_items = lines_map[best_lid]
                closest_idx = chosen_items[0][0]
                min_dx = float("inf")
                for idx, w in chosen_items:
                    cx = w["box"]["x"] + w["box"]["w"] / 2.0
                    dx = abs(cur_pos.x() - cx)
                    if dx < min_dx:
                        min_dx = dx
                        closest_idx = idx
                lo = min(self.initial_idx, closest_idx)
                hi = max(self.initial_idx, closest_idx)
                self._apply_selected_indices(list(range(lo, hi + 1)))

    def _handle_pointer_down(self, pos: QPoint):
        self._rebuild_word_boxes()
        self._pressing = True
        self.touch_start = pos
        self.touch_cur = pos
        self.stroke_points = [pos]
        self.min_sx = self.max_sx = pos.x()
        self.min_sy = self.max_sy = pos.y()
        self.path_len = 0.0
        self.initial_idx = self._find_word_at_point(pos, tolerance=8)
        if self.initial_idx != -1:
            self._apply_selected_indices([self.initial_idx])
        else:
            self._apply_selected_indices([])
        self.update()

    def _handle_pointer_move(self, pos: QPoint):
        if not self._pressing or not self.stroke_points:
            return
        last_pt = self.stroke_points[-1]
        pdx = pos.x() - last_pt.x()
        pdy = pos.y() - last_pt.y()
        if pdx * pdx + pdy * pdy < 4:
            return
        self.path_len += (pdx * pdx + pdy * pdy) ** 0.5
        self.touch_cur = pos
        self.stroke_points.append(pos)
        self.min_sx = min(self.min_sx, pos.x())
        self.max_sx = max(self.max_sx, pos.x())
        self.min_sy = min(self.min_sy, pos.y())
        self.max_sy = max(self.max_sy, pos.y())
        sw = self.max_sx - self.min_sx
        sh = self.max_sy - self.min_sy
        if sw >= 8 or sh >= 6 or self.path_len >= 10:
            self._update_selection_from_stroke(pos)
        self.update()

    def _handle_pointer_up(self, pos: QPoint):
        if not self._pressing:
            return
        self._pressing = False
        if pos and (not self.stroke_points or (pos - self.stroke_points[-1]).manhattanLength() >= 2):
            if self.stroke_points:
                last_pt = self.stroke_points[-1]
                self.path_len += ((pos.x() - last_pt.x()) ** 2 + (pos.y() - last_pt.y()) ** 2) ** 0.5
                self.min_sx = pos.x() if self.min_sx is None else min(self.min_sx, pos.x())
                self.max_sx = pos.x() if self.max_sx is None else max(self.max_sx, pos.x())
                self.min_sy = pos.y() if self.min_sy is None else min(self.min_sy, pos.y())
                self.max_sy = pos.y() if self.max_sy is None else max(self.max_sy, pos.y())
            else:
                self.min_sx = self.max_sx = pos.x()
                self.min_sy = self.max_sy = pos.y()
            self.stroke_points.append(pos)

        sw = (self.max_sx - self.min_sx) if (self.max_sx is not None and self.min_sx is not None) else 0
        sh = (self.max_sy - self.min_sy) if (self.max_sy is not None and self.min_sy is not None) else 0
        is_tap = sw < 10 and sh < 10 and self.path_len < 16
        if is_tap:
            if not self.selected_indices:
                idx = self._find_word_at_point(pos, tolerance=8)
                if idx != -1:
                    self._apply_selected_indices([idx])
        elif sw >= 8 or sh >= 6 or self.path_len >= 12:
            self._update_selection_from_stroke(pos)

        chosen = self.selected_text.strip()
        self._reset_selection_state()
        self.update()
        if chosen:
            self.word_selected.emit(chosen)

    def mousePressEvent(self, event: QMouseEvent):
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and (time.monotonic() - getattr(self, "_last_touch_ts", 0.0)) < 0.45:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._handle_pointer_down(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and (time.monotonic() - getattr(self, "_last_touch_ts", 0.0)) < 0.45:
            event.accept()
            return
        if self._pressing and (event.buttons() & Qt.MouseButton.LeftButton):
            self._handle_pointer_move(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized and (time.monotonic() - getattr(self, "_last_touch_ts", 0.0)) < 0.45:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pressing:
            self._handle_pointer_up(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            self._last_touch_ts = time.monotonic()
            pts = event.points()
            if pts:
                pos = pts[0].position().toPoint()
                et = event.type()
                if et == QEvent.Type.TouchBegin:
                    self._handle_pointer_down(pos)
                    event.accept()
                    return True
                elif et == QEvent.Type.TouchUpdate and self._pressing:
                    self._handle_pointer_move(pos)
                    event.accept()
                    return True
                elif et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel) and self._pressing:
                    self._handle_pointer_up(pos)
                    event.accept()
                    return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.selected_boxes:
            painter.setPen(QPen(QColor(129, 109, 112, 170), 1.0))
            painter.setBrush(QBrush(QColor(129, 109, 112, 34)))
            for b in self.selected_boxes:
                painter.drawRect(b.adjusted(-2, -1, 2, 1))

        raw_text = self.text()
        if raw_text:
            cr = self.contentsRect()
            doc = self._build_text_doc(float(max(40, cr.width())))
            doc_h = float(doc.size().height())
            align = self.alignment()
            if align & Qt.AlignmentFlag.AlignVCenter:
                y_base = float(cr.top()) + max(0.0, (cr.height() - doc_h) / 2.0)
            elif align & Qt.AlignmentFlag.AlignBottom:
                y_base = float(cr.top()) + max(0.0, cr.height() - doc_h)
            else:
                y_base = float(cr.top())

            painter.save()
            painter.translate(float(cr.left()), y_base)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            ctx.palette.setColor(QPalette.ColorRole.Text, self._resolved_text_color())
            doc.documentLayout().draw(painter, ctx)
            painter.restore()

        if self._pressing and len(self.stroke_points) >= 2:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(129, 109, 112, 220), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygon(self.stroke_points))


class PhraseItemWidget(QPushButton):
    """重点短语可点击条目：支持自适应换行及触屏点击钻取查词。"""

    def __init__(self, phrase: str, pos_tag: str = "", cn: str = "", parent=None):
        super().__init__(parent)
        self.phrase = phrase
        self.setFlat(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-top: 1px solid {LEXICON_THEME["divider"]};
                border-radius: 0px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {LEXICON_THEME["surface"]};
                border-top: 1px solid {LEXICON_THEME["divider_strong"]};
            }}
            QPushButton:pressed {{
                background-color: {LEXICON_THEME["hover_bg"]};
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 6, 9, 6)
        lay.setSpacing(6)

        esc_phrase = html.escape(phrase, quote=False).replace("&#x27;", "'")
        esc_pos = html.escape(pos_tag, quote=False).replace("&#x27;", "'") if pos_tag else ""
        esc_cn = html.escape(cn, quote=False).replace("&#x27;", "'") if cn else ""

        pos_html = (
            f" <i style='color:{LEXICON_THEME['text_muted']}; font-size:11px; font-family:Georgia, serif;'>{esc_pos}</i>"
            if esc_pos and not any(k in pos_tag for k in ("短语", "词汇", "释义"))
            else ""
        )
        cn_html = f"<span style='color:{LEXICON_THEME['text_sec']}; font-size:12px;'> · {esc_cn}</span>" if esc_cn else ""

        self.text_lbl = QLabel(
            f"<b style='color:{LEXICON_THEME['accent']}; font-family:Georgia, serif; font-size:12.5px;'>{esc_phrase}</b>"
            f"{pos_html}{cn_html}"
        )
        self.text_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.text_lbl.setStyleSheet(f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};")
        lay.addWidget(self.text_lbl, stretch=1)

        self.arrow_lbl = QLabel("›")
        self.arrow_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.arrow_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.arrow_lbl.setStyleSheet("color: #888888; font-size: 14px; font-weight: bold; border: none; background: transparent;")
        lay.addWidget(self.arrow_lbl)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self.layout().heightForWidth(w)

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()

class DictionaryCard(QWidget):
    """
    静奢暖白纸质风原生学术词典卡片 (Warm Cream Zen Paper & Editorial Luxury)
    纯粹极简版本：
    1. 彻底剔除底层虚假学术套话与繁复装饰，专注极致清晰的译文与语境；
    2. 若选取长句子，直接呈现高保真学术译文，绝不冗余复读原句；
    3. 若选取单生词，优雅展示双轨发音、精准释义与课件真实现场例句；
    4. 顶部极简拉手条支持触屏随心拖移，QScroller 触控动力顺滑滚屏；
    5. 右上角矢量双态圆球 (○ / ✦) 唯一定义收藏，双向可靠流转，绝无双重圈丑陋瑕疵。
    """
    collection_changed = Signal(str, bool)
    enrichment_ready = Signal(dict)
    vault_sync_needed = Signal()

    def __init__(self, parent=None, on_closed: Optional[Callable] = None):
        super().__init__(parent)
        self.on_closed = on_closed
        self.current_data: Optional[Dict[str, Any]] = None
        self._drag_pos = QPoint()
        self._is_dragging_window = False
        self.full_original_text = ""
        self.is_original_expanded = False
        self._current_gen_id = 0
        self._is_closed = False
        self._history_stack: List[Dict[str, Any]] = []

        # 窗口无边框、置顶、透明底色
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self._use_native_chrome = supports_win11_native_chrome()
        if self._use_native_chrome:
            self.setAutoFillBackground(True)
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor(LEXICON_THEME["bg"]))
            self.setPalette(pal)
            self.setStyleSheet(f"DictionaryCard {{ background-color: {LEXICON_THEME['bg']}; }}")
            self.resize(368, 228)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.resize(380, 240)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        _dfont = QFont()
        _dfont.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
        _dfont.setPointSize(10)
        self.setFont(_dfont)

        self._init_ui()
        self.enrichment_ready.connect(self._apply_enrichment)
        # 复用单一 QTimer 替代每次 show_word 新建 QTimer(self)，杜绝单例卡片对象树无界泄漏
        self._enrichment_fallback_timer = QTimer(self)
        self._enrichment_fallback_timer.setSingleShot(True)
        self._enrichment_fallback_timer.timeout.connect(lambda: self._handle_enrichment_timeout(self._current_gen_id))

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_use_native_chrome", False):
            apply_native_win11_chrome(self, corner_pref=1, border_color=LEXICON_THEME["border"])
        try:
            from .subtitle_window import apply_capture_exclusion
            apply_capture_exclusion(self, True)
        except Exception:
            pass
        enforce_window_topmost(self, activate=False)

    def paintEvent(self, event):
        """零阴影直角 Editorial 纸面容器"""
        if getattr(self, "_use_native_chrome", False):
            super().paintEvent(event)
            return
        clip = event.rect()
        painter = QPainter(self)
        painter.setClipRect(clip)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.eraseRect(clip)
        painter.end()
        super().paintEvent(event)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        if self._use_native_chrome:
            root_layout.setContentsMargins(0, 0, 0, 0)
            self.card = LexiconCardFrame(radius=0, parent=self)
            self.card.setObjectName("MainCard")
            self.card.setStyleSheet(f"""
                #MainCard, #MainCard QLabel, #MainCard QPushButton {{
                    font-family: {FONT_FAMILY_SANS};
                }}
                #MainCard {{
                    background: transparent;
                    border: none;
                }}
            """)
        else:
            root_layout.setContentsMargins(2, 2, 2, 2)

            # Editorial 直角暖米纸卡片容器：零圆角、零阴影、1px 墨黑细框
            self.card = LexiconCardFrame(radius=0, parent=self)
            self.card.setObjectName("MainCard")
            self.card.setStyleSheet(f"""
                #MainCard, #MainCard QLabel, #MainCard QPushButton {{
                    font-family: {FONT_FAMILY_SANS};
                }}
                #MainCard {{
                    background: transparent;
                    border: none;
                }}
            """)

        self.card_layout = card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(24, 18, 24, 20)
        card_layout.setSpacing(8)

        # 1. 顶部标题栏（衬线主词头 + 右上角陈玫书签与关闭图标）
        self.header_layout = header_layout = QHBoxLayout()
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header_layout.setSpacing(8)

        title_box = QHBoxLayout()
        title_box.setSpacing(8)
        title_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.back_btn = QPushButton("← 返回")
        self.back_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_btn.setFixedHeight(22)
        self.back_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {LEXICON_THEME['text_muted']}; "
            f"border: none; border-bottom: 1px solid rgba(28,28,28,76); border-radius: 0px; padding: 1px 2px; font-size: 11px; font-weight: 500; "
            f"font-family: {FONT_FAMILY_SANS}; }} "
            f"QPushButton:hover {{ color: {LEXICON_THEME['text']}; border-bottom: 1px solid #1C1C1C; }}"
        )
        self.back_btn.clicked.connect(self._navigate_back)
        self.back_btn.hide()
        title_box.addWidget(self.back_btn)

        self.word_label = QLabel("steps")
        self.word_label.setTextFormat(Qt.TextFormat.PlainText)
        self.word_label.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 23px; font-weight: 400; "
            f"font-family: 'Georgia', 'Noto Serif SC', 'STSong', serif;"
        )
        title_box.addWidget(self.word_label)
        title_box.addStretch()
        header_layout.addLayout(title_box, stretch=1)

        self.collect_btn = ZenCollectButton()
        self.collect_btn.clicked.connect(self._toggle_collect)
        header_layout.addWidget(self.collect_btn)

        self.close_btn = VectorIconButton(
            kind="close",
            color=LEXICON_THEME["text"],
            hover_color=LEXICON_THEME["mauve"],
            hover_bg=LEXICON_THEME["hover_neutral"],
            size=24,
            radius=0,
        )
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn)
        card_layout.addLayout(header_layout)

        # 2. 纯排印内联音标行（斜体 BrE / AmE + 1px 发声图标）
        self.phonetic_container = QWidget()
        self.phonetic_layout = QHBoxLayout(self.phonetic_container)
        self.phonetic_layout.setContentsMargins(0, 0, 0, 4)
        self.phonetic_layout.setSpacing(14)

        self.uk_pill = PhoneticButton("BrE")
        self.phonetic_label = self.uk_pill
        self.uk_pill.clicked.connect(lambda: self._speak("uk"))
        self.phonetic_layout.addWidget(self.uk_pill)

        self.us_pill = PhoneticButton("AmE")
        self.us_pill.clicked.connect(lambda: self._speak("us"))
        self.phonetic_layout.addWidget(self.us_pill)

        self.phonetic_layout.addStretch()
        self.phonetic_container.hide()
        card_layout.addWidget(self.phonetic_container)

        self.header_div = QFrame()
        self.header_div.setFixedHeight(1)
        self.header_div.setStyleSheet(f"background: {LEXICON_THEME['divider']}; border: none;")
        card_layout.addWidget(self.header_div)

        # 3. 滚动内容区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: {LEXICON_THEME["bg"]};
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                border: none;
                background: transparent;
                width: 0px;
                height: 0px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                border: none;
                background: transparent;
                width: 0px;
                height: 0px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
                border: none;
                background: none;
                width: 0px;
                height: 0px;
            }}
        """)
        scroll_content = QWidget()
        scroll_content.setObjectName("DictScrollContent")
        scroll_content.setStyleSheet(f"#DictScrollContent {{ background: {LEXICON_THEME['bg']}; border: none; }}")
        self.content_layout = QVBoxLayout(scroll_content)
        self.content_layout.setContentsMargins(0, 6, 0, 4)
        self.content_layout.setSpacing(12)

        # 拼写校订条（Editorial 直角陈玫线框 + 墨黑反色采纳按钮）
        self.spelling_banner = QFrame()
        self.spelling_banner.setObjectName("SpellingBanner")
        self.spelling_banner.setStyleSheet(f"""
            QFrame#SpellingBanner {{
                background-color: {LEXICON_THEME["warn_bg"]};
                border: 1px solid {LEXICON_THEME["warn_border"]};
                border-radius: 0px;
            }}
        """)
        sb_layout = QHBoxLayout(self.spelling_banner)
        sb_layout.setContentsMargins(10, 7, 10, 7)
        sb_layout.setSpacing(8)
        self.spelling_label = QLabel()
        self.spelling_label.setTextFormat(Qt.TextFormat.RichText)
        self.spelling_label.setWordWrap(True)
        self.spelling_label.setFont(QFont("Microsoft YaHei UI", 9))
        self.spelling_label.setStyleSheet(
            f"color: {LEXICON_THEME['text_sec']}; font-size: 12px; font-weight: 400; "
            f"font-family: {FONT_FAMILY_SANS}; border: none; background: transparent;"
        )
        sb_layout.addWidget(self.spelling_label, stretch=1)

        self.spelling_adopt_btn = QPushButton("采纳")
        self.spelling_adopt_btn.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Medium))
        self.spelling_adopt_btn.setFixedHeight(22)
        self.spelling_adopt_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.spelling_adopt_btn.setStyleSheet("""
            QPushButton {
                background: #1C1C1C;
                color: #F9F8F6;
                border-radius: 0px;
                padding: 1px 10px;
                font-size: 11px;
                font-weight: 500;
                border: 1px solid #1C1C1C;
            }
            QPushButton:hover {
                background: #F9F8F6;
                color: #1C1C1C;
            }
        """)
        self.spelling_adopt_btn.clicked.connect(self._adopt_spelling_suggestion)
        sb_layout.addWidget(self.spelling_adopt_btn)

        self.spelling_banner.hide()
        self.content_layout.addWidget(self.spelling_banner)

        # 离线/网络超时轻量状态提示条（独立于释义与译文区，支持一键重试）
        self.offline_banner = QFrame()
        self.offline_banner.setObjectName("OfflineBanner")
        self.offline_banner.setStyleSheet("""
            QFrame#OfflineBanner {
                background-color: #F3F1EC;
                border: 1px solid rgba(28, 28, 28, 0.16);
                border-radius: 0px;
            }
        """)
        ob_layout = QHBoxLayout(self.offline_banner)
        ob_layout.setContentsMargins(10, 7, 10, 7)
        ob_layout.setSpacing(8)
        self.offline_label = QLabel()
        self.offline_label.setTextFormat(Qt.TextFormat.PlainText)
        self.offline_label.setWordWrap(True)
        self.offline_label.setStyleSheet(
            f"color: #66625B; font-size: 11.5px; font-weight: 400; "
            f"font-family: {FONT_FAMILY_SANS}; border: none; background: transparent;"
        )
        ob_layout.addWidget(self.offline_label, stretch=1)

        self.retry_btn = QPushButton("重试")
        self.retry_btn.setFixedHeight(22)
        self.retry_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.retry_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #1C1C1C;
                border: 1px solid rgba(28, 28, 28, 0.35);
                border-radius: 0px;
                padding: 1px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #1C1C1C;
                color: #F9F8F6;
                border-color: #1C1C1C;
            }
        """)
        self.retry_btn.clicked.connect(self._retry_enrichment)
        ob_layout.addWidget(self.retry_btn)

        self.offline_banner.hide()
        self.content_layout.addWidget(self.offline_banner)

        # SECTION 1: 核心释义 / 译文（顶部仅保留极简词性 adj.）
        self.defs_container = QFrame()
        self.defs_container.setStyleSheet("background: transparent; border: none;")
        self.defs_vbox = QVBoxLayout(self.defs_container)
        self.defs_vbox.setContentsMargins(0, 0, 0, 0)
        self.defs_vbox.setSpacing(5)

        self.def_eyebrow = QLabel("DEFINITION · 释义")
        self.def_eyebrow.hide()
        self.defs_vbox.addWidget(self.def_eyebrow)

        self.pos_label = QLabel("n.")
        self.pos_label.setTextFormat(Qt.TextFormat.PlainText)
        self.pos_label.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11.5px; font-weight: 500; "
            f"letter-spacing: 1px; background: transparent; border: none; font-family: {FONT_FAMILY_SANS};"
        )
        self.defs_vbox.addWidget(self.pos_label)

        self.defs_layout = QVBoxLayout()
        self.defs_layout.setSpacing(5)
        self.defs_vbox.addLayout(self.defs_layout)
        self.content_layout.addWidget(self.defs_container)

        self.div1 = QFrame()
        self.div1.setFixedHeight(1)
        self.div1.setStyleSheet(f"background: {LEXICON_THEME['divider_soft']}; border: none;")
        self.content_layout.addWidget(self.div1)

        # 校订建议与规范表达
        self.grammar_container = QFrame()
        self.grammar_container.setStyleSheet("background: transparent; border: none;")
        self.grammar_vbox = QVBoxLayout(self.grammar_container)
        self.grammar_vbox.setContentsMargins(0, 0, 0, 0)
        self.grammar_vbox.setSpacing(4)

        self.grammar_eyebrow = QLabel("校订建议")
        self.grammar_eyebrow.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Medium))
        self.grammar_eyebrow.setStyleSheet(
            f"color: {LEXICON_THEME['mauve']}; font-size: 11px; font-weight: 500; "
            f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};"
        )
        self.grammar_vbox.addWidget(self.grammar_eyebrow)

        self.grammar_card = QFrame()
        self.grammar_card.setObjectName("GrammarCard")
        self.grammar_card.setStyleSheet("QFrame#GrammarCard { background: transparent; border: none; }")
        self.grammar_card_layout = QVBoxLayout(self.grammar_card)
        self.grammar_card_layout.setContentsMargins(0, 0, 0, 0)
        self.grammar_card_layout.setSpacing(4)

        self.corrections_list_layout = QVBoxLayout()
        self.corrections_list_layout.setSpacing(3)
        self.grammar_card_layout.addLayout(self.corrections_list_layout)

        self.polished_box = QFrame()
        self.polished_box.setObjectName("PolishedBox")
        self.polished_box.setStyleSheet("QFrame#PolishedBox { background: transparent; border: none; }")
        pb_layout = QVBoxLayout(self.polished_box)
        pb_layout.setContentsMargins(0, 2, 0, 0)
        pb_layout.setSpacing(2)

        self.polished_title = QLabel("规范表达：")
        self.polished_title.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Medium))
        self.polished_title.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11px; font-weight: 500; "
            f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};"
        )
        pb_layout.addWidget(self.polished_title)

        self.polished_text = QLabel()
        self.polished_text.setTextFormat(Qt.TextFormat.PlainText)
        self.polished_text.setWordWrap(True)
        self.polished_text.setFont(QFont("Georgia", 10))
        self.polished_text.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 12.5px; "
            f"font-family: 'Georgia', 'Noto Serif SC', serif; border: none; background: transparent;"
        )
        pb_layout.addWidget(self.polished_text)

        self.grammar_card_layout.addWidget(self.polished_box)
        self.grammar_vbox.addWidget(self.grammar_card)
        self.grammar_container.hide()
        self.content_layout.addWidget(self.grammar_container)

        self.div_grammar = QFrame()
        self.div_grammar.setFixedHeight(1)
        self.div_grammar.setStyleSheet(f"background: {LEXICON_THEME['divider_soft']}; border: none;")
        self.div_grammar.hide()
        self.content_layout.addWidget(self.div_grammar)

        # SECTION 2: Pull Quote 现场例句 / 原文
        self.sentence_container = QFrame()
        self.sentence_container.setStyleSheet("background: transparent; border: none;")
        self.sentence_vbox = QVBoxLayout(self.sentence_container)
        self.sentence_vbox.setContentsMargins(0, 0, 0, 0)
        self.sentence_vbox.setSpacing(3)

        self.immersion_eyebrow = QLabel("CONTEXT · 课件例句")
        self.immersion_eyebrow.hide()
        self.sentence_vbox.addWidget(self.immersion_eyebrow)

        self.quote_box = EditorialPullQuoteFrame(top_pad=2.0)
        self.quote_box.setObjectName("QuoteBox")
        self.quote_box.setStyleSheet("QFrame#QuoteBox { background: transparent; border: none; }")
        self.quote_layout = quote_layout = QVBoxLayout(self.quote_box)
        quote_layout.setContentsMargins(34, 4, 6, 4)
        quote_layout.setSpacing(6)

        self.sentence_text = InteractiveSentenceLabel()
        self.sentence_text.setWordWrap(True)
        self.sentence_text.setStyleSheet(
            "color: #1C1C1C; font-size: 13px; font-style: italic; font-family: 'Georgia', serif; "
            "border: none; background: transparent; padding: 0px;"
        )
        self.sentence_text.word_selected.connect(self._on_sentence_word_selected)
        quote_layout.addWidget(self.sentence_text)

        self.sentence_trans = QLabel()
        self.sentence_trans.setWordWrap(True)
        self.sentence_trans.setTextFormat(Qt.TextFormat.PlainText)
        self.sentence_trans.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 12px; border: none; background: transparent; "
            f"padding: 0px; font-family: {FONT_FAMILY_SANS};"
        )
        quote_layout.addWidget(self.sentence_trans)

        self.orig_toggle_btn = QPushButton("展开全部原文 ▾")
        self.orig_toggle_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.orig_toggle_btn.setStyleSheet(
            f"color: {LEXICON_THEME['text']}; font-size: 11px; font-weight: 500; "
            f"border: none; background: transparent; text-align: left; padding: 0; font-family: {FONT_FAMILY_SANS};"
        )
        self.orig_toggle_btn.clicked.connect(self._toggle_full_original)
        self.orig_toggle_btn.hide()
        quote_layout.addWidget(self.orig_toggle_btn)

        self.sentence_vbox.addWidget(self.quote_box)
        self.content_layout.addWidget(self.sentence_container)

        # SECTION 3: 重点短语
        self.div_phrases = QFrame()
        self.div_phrases.setFixedHeight(1)
        self.div_phrases.setStyleSheet(f"background: {LEXICON_THEME['divider_soft']}; border: none;")
        self.div_phrases.hide()
        self.content_layout.addWidget(self.div_phrases)

        self.phrases_container = QFrame()
        self.phrases_container.setStyleSheet("background: transparent; border: none;")
        self.phrases_vbox = QVBoxLayout(self.phrases_container)
        self.phrases_vbox.setContentsMargins(0, 0, 0, 0)
        self.phrases_vbox.setSpacing(5)

        self.phrases_eyebrow = QLabel("重点短语")
        self.phrases_eyebrow.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Medium))
        self.phrases_eyebrow.setStyleSheet(
            f"color: {LEXICON_THEME['text_muted']}; font-size: 11px; font-weight: 500; "
            f"border: none; background: transparent; font-family: {FONT_FAMILY_SANS};"
        )
        self.phrases_vbox.addWidget(self.phrases_eyebrow)

        self.phrases_list_layout = QVBoxLayout()
        self.phrases_list_layout.setSpacing(0)
        self.phrases_vbox.addLayout(self.phrases_list_layout)
        self.phrases_container.hide()
        self.content_layout.addWidget(self.phrases_container)

        self.content_layout.addStretch(1)

        self.scroll.setWidget(scroll_content)
        enhance_scroll_area(self.scroll, top_feather=18, bottom_feather=18, feather_color=LEXICON_THEME["bg"])
        card_layout.addWidget(self.scroll, stretch=1)

        root_layout.addWidget(self.card)

    def _toggle_full_original(self):
        """长句/段落模式下：平滑展开/收起完整识别原文，并自适应伸缩卡片高度"""
        if not self.full_original_text:
            return
        self.is_original_expanded = not self.is_original_expanded
        self.sentence_text.setTextFormat(Qt.TextFormat.PlainText)
        if self.is_original_expanded:
            self.sentence_text.setText(f"“{self.full_original_text}”")
            self.orig_toggle_btn.setText("收起 ▴")
        else:
            self.sentence_text.setText(f"“{self.full_original_text[:80].rstrip()}...”")
            self.orig_toggle_btn.setText("展开全部原文 ▾")
        if self.current_data:
            self._adjust_window_size(self.current_data)

    def _adopt_spelling_suggestion(self):
        """用户点击采纳拼写纠偏建议，平滑重载目标标准单词"""
        if not self.current_data:
            return
        spelling_corr = self.current_data.get("spelling_correction")
        if spelling_corr and spelling_corr.get("suggested"):
            sugg = spelling_corr["suggested"]
            from .dictionary_service import lookup_fast
            new_data = lookup_fast(sugg, self.current_data.get("context_sentence", ""))
            self.show_word(new_data, keep_history=True)

    def _get_collect_candidate_keys(self, data: Optional[Dict[str, Any]] = None) -> list[str]:
        d = data or self.current_data or {}
        raw_w = (d.get("word") or "").strip()
        keys: list[str] = []
        if raw_w:
            keys.append(raw_w)
        # 仅在不存在拼写纠偏建议（即屈折词/所有格词形还原场景）时关联 target_word，
        # 严防误拼词未点“采纳建议”时与标准词发生状态串扰
        if not d.get("spelling_correction"):
            target_w = (d.get("target_word") or "").strip()
            if target_w and target_w.lower() not in [k.lower() for k in keys]:
                keys.append(target_w)
        return keys

    def _is_data_collected(self, data: Optional[Dict[str, Any]] = None) -> bool:
        for k in self._get_collect_candidate_keys(data):
            row = get_word(make_word_id(k))
            if row and row.get("is_collected", False):
                return True
        return False

    def sync_word_uncollected(self, removed_word: str):
        """当生词本删除词条时，实时同步刷新当前打开卡片的收藏圆球状态。"""
        if not self.current_data or getattr(self, "_is_closed", False):
            return
        cand_low = {k.lower() for k in self._get_collect_candidate_keys(self.current_data)}
        if removed_word.strip().lower() in cand_low:
            self._update_collect_btn_style(self._is_data_collected(self.current_data))

    def _on_sentence_word_selected(self, selected_text: str):
        """用户在原文区通过点击、划词或圈选二次选中单词/短语时，钻取其详细词典卡。"""
        ctx = (
            self.full_original_text
            or (self.current_data.get("context_sentence", "") if self.current_data else "")
            or (self.current_data.get("word", "") if self.current_data else "")
        )
        self.drill_down_word(selected_text, ctx)

    def drill_down_word(self, target_text: str, context_sentence: str = ""):
        """从段落/重点短语钻取到特定单词或短语的完整词典详情页，并压入历史栈支持返回。"""
        target_text = (target_text or "").strip()
        if not target_text:
            return
        if self.current_data:
            self._history_stack.append(dict(self.current_data))
        from .dictionary_service import lookup_fast
        ctx = (
            context_sentence.strip()
            or self.full_original_text
            or (self.current_data.get("context_sentence", "") if self.current_data else "")
        )
        new_data = lookup_fast(target_text, ctx)
        if self.current_data and len(self.current_data.get("word", "").split()) > 3:
            cur_defs = self.current_data.get("definitions") or []
            cur_cn = cur_defs[0].get("cn", "") if cur_defs else ""
            if (
                cur_cn
                and cur_cn not in LOADING_PLACEHOLDERS
                and "网络超时" not in cur_cn
                and self.current_data.get("translation_status") != "offline"
            ):
                new_data["context_translation"] = f"↳ 译文：{cur_cn}"
                if new_data.get("definitions") and new_data["definitions"][0].get("cn") not in LOADING_PLACEHOLDERS:
                    new_data["needs_enrichment"] = False
        self.show_word(new_data, keep_history=True)

    def _navigate_back(self):
        """点击顶部返回按钮，恢复上一级段落翻译或词条视图。"""
        if not self._history_stack:
            return
        prev_data = self._history_stack.pop()
        self.show_word(prev_data, keep_history=True)

    def show_word(self, data: Dict[str, Any], global_pos: Optional[QPoint] = None, keep_history: bool = False):
        self._is_closed = False
        # 浅拷贝隔离，避免原地写入 _gen_id / needs_enrichment 污染调用方（如 vault_window.words）
        data = dict(data or {})
        if not keep_history:
            self._history_stack.clear()
        if self._history_stack:
            self.back_btn.show()
        else:
            self.back_btn.hide()

        self._current_gen_id += 1
        current_gen = self._current_gen_id
        data["_gen_id"] = current_gen
        self.current_data = data
        self.is_original_expanded = False
        self.full_original_text = ""
        word = (data.get("word") or "").strip()
        word_count = len(word.split())

        # 排版：长句/段落以译文为主体，单词以 Georgia 衬线词头为主体
        if word_count > 3:
            title_text = "句子翻译" if word_count < 8 else "段落翻译"
            self.word_label.setText(title_text)
            self.word_label.setStyleSheet(
                f"color: {LEXICON_THEME['text']}; font-size: 18px; font-weight: 400; "
                f"font-family: 'Georgia', 'Noto Serif SC', 'STSong', serif;"
            )

            self.pos_label.setText(f"共 {word_count} 词")
            self.pos_label.setStyleSheet(
                f"color: {LEXICON_THEME['text_muted']}; font-size: 11.5px; font-weight: 400; "
                f"background: transparent; padding: 0; border: none; font-style: normal; "
                f"font-family: {FONT_FAMILY_SANS};"
            )
            self.pos_label.show()

            self.def_eyebrow.setText("TRANSLATION · 译文")
        else:
            title_font_px = 21 if len(word) > 16 else 26
            self.word_label.setText(word)
            self.word_label.setStyleSheet(
                f"color: {LEXICON_THEME['text']}; font-size: {title_font_px}px; font-weight: 400; "
                f"font-family: 'Georgia', 'Noto Serif SC', 'STSong', serif;"
            )
            self.def_eyebrow.setText("DEFINITION · 释义")
            defs_list = data.get("definitions") or []
            first_pos = ((defs_list[0].get("pos") or "") if defs_list else "").strip()
            if (
                data.get("translation_status") != "offline_partial"
                and first_pos
                and not any(k in first_pos for k in ("短语", "释义", "长句", "译文"))
            ):
                self.pos_label.setText(first_pos)
                self.pos_label.setStyleSheet(
                    f"color: {LEXICON_THEME['text_muted']}; font-size: 11.5px; font-weight: 500; "
                    f"letter-spacing: 1px; background: transparent; padding: 0; border: none; font-family: {FONT_FAMILY_SANS};"
                )
                self.pos_label.show()
            else:
                self.pos_label.hide()

        # 检查是否已在数据库收藏（统一兼容原词与屈折基词，杜绝双键死锁）
        self._update_collect_btn_style(self._is_data_collected(data))

        # 纯排印内联音标与矢量朗读图标
        phonetic_uk = (data.get("phonetic_uk") or "").strip()
        phonetic_us = (data.get("phonetic_us") or "").strip()
        has_uk = bool(phonetic_uk and word_count <= 2)
        has_us = bool(phonetic_us and word_count <= 2)

        if has_uk:
            self.uk_pill.set_phonetic("BrE", phonetic_uk)
            self.uk_pill.show()
        else:
            self.uk_pill.hide()

        if has_us:
            self.us_pill.set_phonetic("AmE", phonetic_us)
            self.us_pill.show()
        else:
            self.us_pill.hide()

        if has_uk or has_us:
            self.phonetic_container.show()
        else:
            self.phonetic_container.hide()

        # 拼写校订提示展示（严格遵守 AGENTS.md 第 11.2 章，杜绝 &#x27; 乱码）
        spelling_corr = data.get("spelling_correction")
        if spelling_corr:
            orig = html.escape(spelling_corr.get("original", ""), quote=False).replace("&#x27;", "'")
            sugg = html.escape(spelling_corr.get("suggested", ""), quote=False).replace("&#x27;", "'")
            self.spelling_label.setText(
                f"<b style='color:{LEXICON_THEME['danger_title']};'>拼写校订</b>：疑似误拼，建议替换为 "
                f"<b style='color:{LEXICON_THEME['accent']}; text-decoration:underline;'>{sugg}</b>"
                f"（原词：<i>{orig}</i>）"
            )
            self.spelling_banner.show()
        else:
            self.spelling_banner.hide()

        # 语法与拼写纠错渲染
        self._render_grammar(data.get("grammar_result"))

        # 释义渲染与离线状态条解耦渲染
        self._render_definitions(data.get("definitions", []), word_count)
        self._render_offline_status(data, word_count)

        # 原文与例句渲染（长句简要展示原文，生词展示课件现场例句）
        self._render_sentences(data.get("context_sentence", ""), data.get("context_translation", ""), word, word_count)

        # 重点短语渲染（长句与段落模式下在最下方展示核心短语列表）
        self._render_phrases(data.get("key_phrases"), word_count)

        # 动态内容感知自适应尺寸系统：纯物理贴合，彻底消除滚动挤压，避开 Windows 任务栏
        self._adjust_window_size(data, global_pos)

        self.show()
        self.raise_()
        enforce_window_topmost(self, activate=False)
        self.card.update()
        self.update()

        # 针对残缺词条（如生词本点击打开无音标或仅单字粗糙机翻），主动触发后台自动补齐与回写；
        # 明确落入 offline/offline_partial 状态时不自动重试（交由用户点击“重试”按钮触发）
        if word_count <= 2 and data.get("translation_status") not in ("offline", "offline_partial"):
            cur_defs = data.get("definitions") or []
            has_no_phone = not data.get("phonetic_uk") and not data.get("phonetic_us")
            is_rough_def = len(cur_defs) <= 1 and all(d.get("pos") in ("释义", "短语", "") for d in cur_defs)
            if has_no_phone or is_rough_def:
                data["needs_enrichment"] = True

        # 异步子线程网络增强：附带 generation_id 严格杜绝脏更新与渐进式多阶段派发
        if data.get("needs_enrichment", False):
            def _async_worker():
                try:
                    payload = dict(data)
                    payload["_gen_id"] = current_gen
                    def _partial_cb(p_data):
                        if getattr(self, "_is_closed", False) or current_gen != self._current_gen_id:
                            return
                        p = dict(p_data)
                        p["_gen_id"] = current_gen
                        try:
                            self.enrichment_ready.emit(p)
                        except Exception:
                            pass
                    enriched = enrich_lookup_sync(payload, on_partial_update=_partial_cb)
                    enriched["_gen_id"] = current_gen
                    # 若用户在骨架加载窗口期点了收藏并提前关闭了卡片，仍在后台原子回写完整释义与音标，并通知生词本刷新
                    cand_keys = list(self._get_collect_candidate_keys(data))
                    for ek in self._get_collect_candidate_keys(enriched):
                        if ek.lower() not in [k.lower() for k in cand_keys]:
                            cand_keys.append(ek)
                    any_db_updated = False
                    for cand_k in cand_keys:
                        if update_enrichment_if_collected(
                            word=cand_k,
                            definitions=enriched.get("definitions", []),
                            context_sentence=enriched.get("context_sentence", ""),
                            context_translation=enriched.get("context_translation", ""),
                            ai_analysis=enriched.get("ai_analysis", ""),
                            phonetic_uk=enriched.get("phonetic_uk", ""),
                            phonetic_us=enriched.get("phonetic_us", ""),
                            tags=enriched.get("tags", []),
                        ):
                            any_db_updated = True
                    if any_db_updated:
                        try:
                            self.vault_sync_needed.emit()
                        except Exception:
                            pass
                    if not getattr(self, "_is_closed", False) and current_gen == self._current_gen_id:
                        try:
                            self.enrichment_ready.emit(enriched)
                        except Exception:
                            pass
                except Exception:
                    # 发生未知网络/解析异常时，仅当尚无真实释义时切换为干净的离线态（绝不覆盖已有离线词典释义或写入假译文）
                    if not getattr(self, "_is_closed", False) and current_gen == self._current_gen_id:
                        fallback = dict(data)
                        fallback["_gen_id"] = current_gen
                        clean_w = (fallback.get("target_word", fallback.get("word", "")) or "").strip()
                        cur_defs = fallback.get("definitions") or []
                        has_real = bool(
                            cur_defs
                            and any(
                                (d.get("cn") or "").strip() not in ("", *LOADING_PLACEHOLDERS)
                                and (d.get("cn") or "").strip().lower() != clean_w.lower()
                                for d in cur_defs
                            )
                        )
                        if not has_real:
                            fallback["definitions"] = []
                            fallback["translation_status"] = "offline"
                        else:
                            fallback["translation_status"] = ""
                        if (fallback.get("context_translation") or "").strip() in CTX_LOADING_PLACEHOLDERS:
                            fallback["context_translation"] = ""
                        fallback["needs_enrichment"] = False
                        try:
                            self.enrichment_ready.emit(fallback)
                        except Exception:
                            pass
            threading.Thread(target=_async_worker, daemon=True).start()

            # 启动 6.8 秒自愈安全阀（严格大于后台 5.2s 翻译竞速总预算，复用单一 QTimer 实例）
            self._enrichment_fallback_timer.start(6800)
        else:
            self._enrichment_fallback_timer.stop()

    def _adjust_window_size(self, data: Dict[str, Any], global_pos: Optional[QPoint] = None):
        """
        动态内容感知自适应尺寸系统 (Content-Aware Dynamic Resizing)
        采用真实布局物理测量替代字符粗估，精准计量内容高度与固定骨架边距，
        实现纯物理 0 滚动无挤压贴合，并结合 availableGeometry 彻底防范碰撞 Windows 底部任务栏。
        """
        word = data.get("word", "")
        word_count = len(word.split())
        grammar_res = data.get("grammar_result", {})
        has_grammar_corr = bool(grammar_res and grammar_res.get("corrections"))

        outer_pad = 0 if getattr(self, "_use_native_chrome", False) else 4
        target_w = (456 if (word_count > 3 or has_grammar_corr or bool(self._history_stack) or len(word) > 14) else 426) - outer_pad
        inner_w = target_w - outer_pad - 48 - 2

        # 确保动态添加的释义、纠错与重点短语条目被计入布局测量
        for layout in (self.defs_layout, self.corrections_list_layout, self.phrases_list_layout):
            for i in range(layout.count()):
                w = layout.itemAt(i).widget()
                if w and w.isHidden():
                    w.show()
        self.defs_vbox.activate()
        if not self.offline_banner.isHidden():
            self.offline_banner.layout().activate()
        if not self.grammar_container.isHidden():
            self.grammar_card_layout.activate()
            self.grammar_vbox.activate()
        if not self.sentence_container.isHidden():
            self.quote_box.layout().activate()
            self.sentence_vbox.activate()
        if not self.phrases_container.isHidden():
            self.phrases_vbox.activate()
        self.content_layout.activate()

        content_h = self.content_layout.totalHeightForWidth(inner_w)
        if content_h <= 0:
            content_h = self.content_layout.sizeHint().height()

        has_phonetic = not self.phonetic_container.isHidden()
        phonetic_h = self.phonetic_container.sizeHint().height() if has_phonetic else 0
        header_h = self.header_layout.sizeHint().height()
        visible_items = 4 if has_phonetic else 3
        spacing_h = self.card_layout.spacing() * (visible_items - 1)

        outer_h_pad = 0 if getattr(self, "_use_native_chrome", False) else 4
        fixed_h = outer_h_pad + 38 + 1 + header_h + phonetic_h + spacing_h + 10
        final_h = max(150 - outer_h_pad, min(fixed_h + content_h, 540 - outer_h_pad))

        was_visible = self.isVisible()
        self.resize(target_w, final_h)

        # 屏幕可用区域与任务栏碰撞自愈防线（多显示器优先跟随 global_pos 所在屏幕，统一 15px 安全边距防跳动）
        screen = (QApplication.screenAt(global_pos) if global_pos else None) or self.screen() or QApplication.primaryScreen()
        screen_geom = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

        if global_pos:
            target_x = min(global_pos.x() + 15, screen_geom.right() - target_w - 15)
            target_y = min(max(screen_geom.top() + 20, global_pos.y() - 100), screen_geom.bottom() - final_h - 15)
            self.move(max(screen_geom.left() + 15, target_x), max(screen_geom.top() + 20, target_y))
        elif not was_visible and self.x() <= 20 and self.y() <= 20:
            default_x = max(screen_geom.left() + 15, screen_geom.right() - target_w - 60)
            default_y = max(screen_geom.top() + 20, screen_geom.top() + (screen_geom.height() - final_h) // 3)
            self.move(default_x, min(default_y, screen_geom.bottom() - final_h - 15))
        else:
            curr_x = min(self.x(), screen_geom.right() - target_w - 15)
            curr_y = min(self.y(), screen_geom.bottom() - final_h - 15)
            self.move(max(screen_geom.left() + 15, curr_x), max(screen_geom.top() + 20, curr_y))

    def _handle_enrichment_timeout(self, gen_id: int):
        """网络挂起/超时安全自愈：主线程强制终止骨架加载态，切换为干净的离线提示条（不污染译文区）"""
        if getattr(self, "_is_closed", False) or gen_id != self._current_gen_id or not self.current_data:
            return
        cur_defs = self.current_data.get("definitions") or []
        need_fix = False
        if not cur_defs or (cur_defs[0].get("cn") or "") in LOADING_PLACEHOLDERS:
            self.current_data["definitions"] = []
            self.current_data["translation_status"] = "offline"
            need_fix = True
        if (self.current_data.get("context_translation") or "") in CTX_LOADING_PLACEHOLDERS or (self.current_data.get("context_translation") or "") == "正在同步译文...":
            self.current_data["context_translation"] = ""
            need_fix = True
        if need_fix:
            self.current_data["needs_enrichment"] = False
            self.current_data["_gen_id"] = gen_id
            self._apply_enrichment(self.current_data)

    def _render_offline_status(self, data: Dict[str, Any], word_count: int):
        """渲染独立离线/超时提示条，确保绝不占用释义/译文主体区。"""
        status = (data.get("translation_status") or "").strip()
        defs = data.get("definitions") or []
        if status == "offline":
            self.retry_btn.setEnabled(True)
            if word_count > 3:
                self.offline_label.setText("当前离线或网络超时，暂无法整句翻译 · 点下方原文单词可离线查词")
            else:
                self.offline_label.setText("当前离线或网络超时，离线词库暂未收录该词条")
            self.offline_banner.show()
            if not defs:
                self.defs_container.hide()
            else:
                self.defs_container.show()
        elif status == "offline_partial":
            self.retry_btn.setEnabled(True)
            self.offline_label.setText("当前离线 · 以下为离线词库逐词参考释义")
            self.offline_banner.show()
            self.defs_container.show()
        else:
            self.retry_btn.setEnabled(True)
            self.offline_banner.hide()
            self.defs_container.show()

    def _retry_enrichment(self):
        """用户点击离线提示条的重试按钮时，重新发起联网增强查询（防连点并发堆积）。"""
        if not self.current_data or not self.retry_btn.isEnabled():
            return
        word = (self.current_data.get("word") or "").strip()
        if not word:
            return
        self.retry_btn.setEnabled(False)
        ctx = self.full_original_text or (self.current_data.get("context_sentence") or "")
        from .dictionary_service import lookup_fast
        retry_data = lookup_fast(word, ctx)
        retry_data["translation_status"] = ""
        cur_defs = retry_data.get("definitions") or []
        is_long = len(word.split()) > 3
        if not cur_defs or self.current_data.get("translation_status") == "offline_partial":
            retry_data["definitions"] = [{
                "pos": "长句" if is_long else "释义",
                "cn": "正在智能翻译整句内容..." if is_long else "正在查询释义...",
                "en": "",
            }]
        retry_data["needs_enrichment"] = True
        self.show_word(retry_data, keep_history=bool(self._history_stack))

    def _render_grammar(self, grammar_data: Optional[Dict[str, Any]]):
        """渲染语法错误、拼写纠偏与推荐学术润色句"""
        clear_layout(self.corrections_list_layout)

        if not grammar_data:
            self.grammar_container.hide()
            self.div_grammar.hide()
            return

        corrections = grammar_data.get("corrections", [])
        # 没问题时（corrections 为空）完全不显示 correction 模块，保持纯净无干扰体验
        if not corrections:
            self.grammar_container.hide()
            self.div_grammar.hide()
            return

        polished = grammar_data.get("polished_sentence", "").strip()

        def _clean_chip_text(text: str) -> str:
            if not text:
                return ""
            raw = html.unescape(text)
            return html.escape(raw, quote=False).replace("&#x27;", "'").replace("&quot;", '"')

        for c in corrections:
            orig = _clean_chip_text(c.get("original", ""))
            sugg = _clean_chip_text(c.get("suggested", ""))
            exp = _clean_chip_text(c.get("explanation", ""))
            chip = QLabel(self.grammar_card)
            chip.setTextFormat(Qt.TextFormat.RichText)
            chip.setWordWrap(True)
            chip.setFont(QFont("Microsoft YaHei UI", 9))
            chip.setText(
                f"<span style='color:{LEXICON_THEME['mauve']}; text-decoration:line-through; font-family:Consolas, monospace; font-size:12px;'>{orig}</span> "
                f"<span style='color:{LEXICON_THEME['text_muted']};'>→</span> "
                f"<span style='color:{LEXICON_THEME['text']}; font-weight:600; text-decoration:underline; font-family:Consolas, monospace; font-size:12px;'>{sugg}</span> "
                f"<span style='color:{LEXICON_THEME['text_muted']}; font-size:11px; font-family:\"Microsoft YaHei UI\", sans-serif;'>({exp})</span>"
            )
            chip.setStyleSheet("border: none; background: transparent; padding: 1px 0px; font-family: \"Microsoft YaHei UI\", sans-serif;")
            self.corrections_list_layout.addWidget(chip)

        if polished:
            raw_polished = html.unescape(polished)
            self.polished_text.setText(raw_polished)
            self.polished_box.show()
        else:
            self.polished_box.hide()

        self.grammar_container.show()
        self.div_grammar.show()

    def _render_definitions(self, defs: List[Dict[str, str]], word_count: int):
        clear_layout(self.defs_layout)
        for idx, d in enumerate(defs):
            cn_text = d.get('cn', '')
            en_text = d.get('en', '').strip()
            pos = d.get('pos', '')
            cur_word = self.current_data.get("word", "") if self.current_data else ""
            is_loading = cn_text.strip() in LOADING_PLACEHOLDERS

            # 若为整句，直接纯净展示译文
            if word_count > 3:
                cn_lbl = QLabel(cn_text)
                cn_lbl.setTextFormat(Qt.TextFormat.PlainText)
                cn_lbl.setWordWrap(True)
                if is_loading:
                    cn_lbl.setStyleSheet(f"color: #888888; font-size: 13px; font-style: italic; font-weight: 400; font-family: {FONT_FAMILY_SANS};")
                else:
                    cn_lbl.setStyleSheet(f"color: {LEXICON_THEME['text']}; font-size: 14px; font-weight: 500; font-family: {FONT_FAMILY_SANS};")
                self.defs_layout.addWidget(cn_lbl)
            else:
                esc_pos = html.escape(pos, quote=False).replace("&#x27;", "'")
                esc_cn = html.escape(cn_text, quote=False).replace("&#x27;", "'")
                if is_loading:
                    cn_lbl = QLabel(f"<span style='color:#9CA3AF; font-size:13px; font-style:italic;'>{esc_cn}</span>")
                else:
                    is_partial = bool(self.current_data and self.current_data.get("translation_status") == "offline_partial")
                    num_prefix = f"<span style='color:{LEXICON_THEME['mauve']}; font-weight:500;'>{idx + 1}.</span> " if len(defs) > 1 else ""
                    pos_prefix = (
                        f"<span style='color:{LEXICON_THEME['text_muted']}; font-weight:400;'>{esc_pos}</span>&nbsp;&nbsp;"
                        if (is_partial or len(defs) > 1) and pos and not any(k in pos for k in ("短语", "释义", "长句", "译文"))
                        else ""
                    )
                    cn_lbl = QLabel(f"{num_prefix}{pos_prefix}<span style='color:{LEXICON_THEME['text']}; font-size:13.5px; font-weight:400;'>{esc_cn}</span>")
                cn_lbl.setTextFormat(Qt.TextFormat.RichText)
                cn_lbl.setWordWrap(True)
                cn_lbl.setStyleSheet(f"font-family: {FONT_FAMILY_SANS};")
                self.defs_layout.addWidget(cn_lbl)

                # 只有真正具备完整英文学术释义（且绝不是目标词自身或重复回显）时才呈现次级英文行
                is_redundant = en_text.lower() in (cur_word.lower(), clean_word_text(cur_word).lower(), cn_text.lower())
                if en_text and not is_redundant and len(en_text.split()) >= 3:
                    en_lbl = QLabel(en_text)
                    en_lbl.setTextFormat(Qt.TextFormat.PlainText)
                    en_lbl.setWordWrap(True)
                    en_lbl.setStyleSheet(f"color: {LEXICON_THEME['text_muted']}; font-size: 12.5px; margin-top: 2px; font-family: 'Georgia', serif;")
                    self.defs_layout.addWidget(en_lbl)

    def _render_sentences(self, sentence: str, context_trans: str, word: str, word_count: int):
        sentence = (sentence or "").strip()
        context_trans = (context_trans or "").strip()

        # 长句/段落模式下，“简要显示读取原文”（保留用户已手动展开状态，不被二阶段异步刷新打回折叠）
        if word_count > 3:
            self.immersion_eyebrow.setText("ORIGINAL · 选取原文")
            self.quote_box.set_quote_mark_visible(False)
            self.quote_layout.setContentsMargins(16, 4, 6, 4)
            self.sentence_trans.hide()
            self.div1.setVisible(not self.defs_container.isHidden())
            self.sentence_container.show()
            if word != self.full_original_text:
                self.is_original_expanded = False
            self.full_original_text = word
            self.sentence_text.setTextFormat(Qt.TextFormat.PlainText)

            if len(word) > 85:
                if self.is_original_expanded:
                    self.sentence_text.setText(f"“{word}”")
                    self.orig_toggle_btn.setText("收起 ▴")
                else:
                    self.sentence_text.setText(f"“{word[:80].rstrip()}...”")
                    self.orig_toggle_btn.setText("展开全部原文 ▾")
                self.orig_toggle_btn.show()
            else:
                self.sentence_text.setText(f"“{word}”")
                self.orig_toggle_btn.hide()
            self.sentence_text.setStyleSheet(
                "color: #4B4F56; font-size: 12.5px; font-style: normal; font-family: 'Georgia', 'Noto Serif SC', serif;"
            )
            return

        # 单个生词模式：Editorial Pull Quote 组件（左侧 2px 陈玫竖脊 + 陈玫色左引号）
        self.orig_toggle_btn.hide()
        self.immersion_eyebrow.setText("CONTEXT · 课件例句")
        self.quote_box.set_quote_mark_visible(True)
        self.quote_layout.setContentsMargins(34, 4, 6, 4)
        is_clean_sentence = len(sentence.split()) >= 3 and not any(bad in sentence for bad in ("__pycache__", ".pyc", "\\", "/"))
        if is_clean_sentence and sentence.lower() != word.lower():
            self.sentence_container.show()
            self.div1.show()
            self.sentence_text.setTextFormat(Qt.TextFormat.RichText)
            def _esc(s: str) -> str:
                return html.escape(s, quote=False).replace("&#x27;", "'")

            if word:
                pattern = re.compile(r'(\b' + re.escape(word) + r'\b)', flags=re.IGNORECASE)
                parts = pattern.split(sentence)
                rendered_parts = []
                for idx_p, part in enumerate(parts):
                    if idx_p % 2 == 1:
                        rendered_parts.append(
                            f'<span style="color:{LEXICON_THEME["mauve"]}; text-decoration:underline;">{_esc(part)}</span>'
                        )
                    else:
                        rendered_parts.append(_esc(part))
                highlighted = "".join(rendered_parts)
            else:
                highlighted = _esc(sentence)
            self.sentence_text.setText(highlighted)
            self.sentence_text.setStyleSheet("color: #1C1C1C; font-size: 13px; font-style: italic; font-family: 'Georgia', serif;")
            if context_trans and context_trans not in CTX_LOADING_PLACEHOLDERS:
                clean_trans = context_trans.replace("↳ 译文：", "").strip()
                self.sentence_trans.setText(clean_trans)
                self.sentence_trans.show()
            else:
                self.sentence_trans.setText("")
                self.sentence_trans.hide()
        else:
            self.sentence_container.hide()
            self.div1.hide()

    def _render_phrases(self, key_phrases: Optional[List[Dict[str, str]]], word_count: int):
        """渲染底部重点短语/核心表达列表，支持点击钻取查词。"""
        clear_layout(self.phrases_list_layout)
        if word_count <= 3 or not key_phrases:
            self.phrases_container.hide()
            self.div_phrases.hide()
            return

        added = 0
        for item in key_phrases:
            phrase = (item.get("phrase") or "").strip()
            cn = (item.get("cn") or "").strip()
            pos = (item.get("pos") or "").strip()
            if not phrase or not cn:
                continue
            btn = PhraseItemWidget(phrase, pos, cn)
            btn.clicked.connect(
                lambda _checked=False, p=phrase: self.drill_down_word(
                    p,
                    self.full_original_text or (self.current_data.get("word", "") if self.current_data else ""),
                )
            )
            self.phrases_list_layout.addWidget(btn)
            added += 1

        if added > 0:
            self.div_phrases.show()
            self.phrases_container.show()
        else:
            self.div_phrases.hide()
            self.phrases_container.hide()

    def _apply_enrichment(self, enriched_data: Dict[str, Any]):
        """子线程翻译完成后，在主线程丝滑无缝更新内容（校验 generation_id 杜绝脏写）"""
        if getattr(self, "_is_closed", False) or not self.current_data or enriched_data.get("_gen_id") != self._current_gen_id:
            return
        self.current_data = enriched_data
        corrected_word = enriched_data.get("word", "")
        word_count = len(corrected_word.split())

        # 智能纠偏同步更新大标题与词性（保持与 show_word 一致的字号 21/26px，消除二阶段刷新跳动）
        if word_count <= 3:
            if not enriched_data.get("spelling_correction"):
                title_font_px = 21 if len(corrected_word) > 16 else 26
                self.word_label.setText(corrected_word)
                self.word_label.setStyleSheet(
                    f"color: {LEXICON_THEME['text']}; font-size: {title_font_px}px; font-weight: 400; "
                    f"font-family: 'Georgia', 'Noto Serif SC', 'STSong', serif;"
                )
            defs_list = enriched_data.get("definitions") or []
            first_pos = ((defs_list[0].get("pos") or "") if defs_list else "").strip()
            if (
                enriched_data.get("translation_status") != "offline_partial"
                and first_pos
                and not any(k in first_pos for k in ("短语", "释义", "长句", "译文"))
            ):
                self.pos_label.setText(first_pos)
                self.pos_label.setStyleSheet(
                    f"color: {LEXICON_THEME['text_muted']}; font-size: 11.5px; font-weight: 500; "
                    f"letter-spacing: 1px; background: transparent; padding: 0; border: none; font-family: {FONT_FAMILY_SANS};"
                )
                self.pos_label.show()
            else:
                self.pos_label.hide()

        self._update_collect_btn_style(self._is_data_collected(enriched_data))

        # 纯排印音标与矢量朗读图标
        phonetic_uk = (enriched_data.get("phonetic_uk") or "").strip()
        phonetic_us = (enriched_data.get("phonetic_us") or "").strip()
        has_uk = bool(phonetic_uk and word_count <= 2)
        has_us = bool(phonetic_us and word_count <= 2)

        if has_uk:
            self.uk_pill.set_phonetic("BrE", phonetic_uk)
            self.uk_pill.show()
        else:
            self.uk_pill.hide()

        if has_us:
            self.us_pill.set_phonetic("AmE", phonetic_us)
            self.us_pill.show()
        else:
            self.us_pill.hide()

        if has_uk or has_us:
            self.phonetic_container.show()
        else:
            self.phonetic_container.hide()

        self._render_definitions(enriched_data.get("definitions", []), word_count)
        self._render_offline_status(enriched_data, word_count)
        self._render_sentences(
            enriched_data.get("context_sentence", ""),
            enriched_data.get("context_translation", ""),
            corrected_word,
            word_count
        )

        if "grammar_result" in enriched_data:
            self._render_grammar(enriched_data["grammar_result"])

        self._render_phrases(enriched_data.get("key_phrases"), word_count)

        # 动态内容感知自适应尺寸系统：纯物理贴合，彻底消除滚动挤压，避开 Windows 任务栏
        self._adjust_window_size(enriched_data)
        self.card.update()
        self.update()

        # 只要已有实质译文/释义、已明确落入离线状态，或全量增强完成，立即终止自愈定时器，严防误触超时覆写
        cur_d = enriched_data.get("definitions") or []
        has_real_defs = bool(cur_d and (cur_d[0].get("cn") or "") not in LOADING_PLACEHOLDERS)
        has_offline_state = bool(enriched_data.get("translation_status"))
        if (has_real_defs or has_offline_state or not enriched_data.get("needs_enrichment", False)) and hasattr(self, "_enrichment_fallback_timer"):
            self._enrichment_fallback_timer.stop()

    def _update_collect_btn_style(self, is_collected: bool):
        """双态收藏指示器 (○ / ✦) 纯净矢量渲染，绝无丑陋双重圈"""
        self.collect_btn.set_collected(is_collected)

    def _toggle_collect(self):
        """双向无死锁切换收藏状态：兼容原词与屈折基词双键解绑，防止重复分裂与骨架占位污染"""
        if not self.current_data:
            return
        cand_keys = self._get_collect_candidate_keys(self.current_data)
        if not cand_keys:
            return
        primary_word = cand_keys[0]

        collected_ids = []
        for k in cand_keys:
            wid = make_word_id(k)
            row = get_word(wid)
            if row and row.get("is_collected", False):
                collected_ids.append(wid)

        if collected_ids:
            # 只要任一关联键已收藏，点击一律执行彻底取消收藏，杜绝 steps/step 双键分裂死锁
            for wid in collected_ids:
                toggle_collection(wid)
            self._update_collect_btn_style(False)
            self.collection_changed.emit(primary_word, False)
        else:
            save_word(
                word=primary_word,
                definitions=self.current_data.get("definitions", []),
                context_sentence=self.current_data.get("context_sentence", ""),
                context_translation=self.current_data.get("context_translation", ""),
                ai_analysis=self.current_data.get("ai_analysis", ""),
                phonetic_uk=self.current_data.get("phonetic_uk", ""),
                phonetic_us=self.current_data.get("phonetic_us", ""),
                tags=self.current_data.get("tags", []),
                is_collected=True
            )
            self._update_collect_btn_style(True)
            self.collection_changed.emit(primary_word, True)

    def _speak(self, accent: str = "us"):
        if self.current_data:
            # 仅在存在拼写纠偏建议时朗读目标标准词；普通屈折词（如 nodes/orchestrators）朗读原展示词保持声画一致
            if self.current_data.get("spelling_correction"):
                word = (self.current_data.get("target_word") or self.current_data.get("word") or "").strip()
            else:
                word = (self.current_data.get("word") or self.current_data.get("target_word") or "").strip()
            speak_word(word, accent=accent)

    def _is_interactive_child(self, pt: QPoint) -> bool:
        w = self.childAt(pt)
        while w is not None and w is not self:
            if isinstance(w, (QAbstractButton, ZenCollectButton, VectorIconButton, PhoneticButton)):
                return True
            w = w.parentWidget()
        return False

    def mousePressEvent(self, event: QMouseEvent):
        pt = event.position().toPoint()
        drag_limit = 44 if getattr(self, "_use_native_chrome", False) else 50
        if event.button() == Qt.MouseButton.LeftButton and pt.y() < drag_limit and not self._is_interactive_child(pt):
            self._is_dragging_window = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging_window and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._is_dragging_window = False
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            pts = event.points()
            if pts:
                pos = pts[0].position()
                pt = pos.toPoint()
                drag_limit = 44 if getattr(self, "_use_native_chrome", False) else 50
                if event.type() == QEvent.Type.TouchBegin and pos.y() < drag_limit and not self._is_interactive_child(pt):
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

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._is_closed = True
        self._history_stack.clear()
        if hasattr(self, "_enrichment_fallback_timer"):
            self._enrichment_fallback_timer.stop()
        if self.on_closed:
            self.on_closed()
        super().closeEvent(event)
