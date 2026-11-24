import ctypes
import threading
import time
from ctypes import wintypes
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, Signal, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap, QImage, QMouseEvent, QTouchEvent
from PIL import ImageGrab
from .win_ocr import win_ocr_engine
from .ocr_helpers import extract_sentence_from_words
from .ui_icons import OVERLAY_COLORS, draw_hud_bar, draw_triage_split_hud, enforce_window_topmost
from typing import Optional, List, Dict, Any, Tuple

_u32_overlay = None
try:
    _u32_overlay = ctypes.WinDLL("user32", use_last_error=True)
    _u32_overlay.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    _u32_overlay.SetPropW.restype = wintypes.BOOL
except Exception:
    _u32_overlay = None

TABLET_DISABLE_PRESSANDHOLD = 0x00000001
TABLET_DISABLE_FLICKS = 0x00010000
TABLET_DISABLE_TOUCHSWITCH = 0x00008000

def point_in_polygon(
    x: float,
    y: float,
    poly: List[Tuple[float, float]],
    poly_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> bool:
    n = len(poly)
    if n < 3:
        return False

    if poly_bbox is not None:
        if x < poly_bbox[0] or x > poly_bbox[2] or y < poly_bbox[1] or y > poly_bbox[3]:
            return False

    # 1. 广义卷绕数算法 (Winding Number)：完美容忍手绘圈选的自相交、尾部交叉、顺逆时针及重叠环路
    wn = 0
    p1x, p1y = poly[0]
    for i in range(1, n + 1):
        p2x, p2y = poly[i % n]
        if p1y <= y:
            if p2y > y:
                if (p2x - p1x) * (y - p1y) - (x - p1x) * (p2y - p1y) > 0:
                    wn += 1
        else:
            if p2y <= y:
                if (p2x - p1x) * (y - p1y) - (x - p1x) * (p2y - p1y) < 0:
                    wn -= 1
        p1x, p1y = p2x, p2y
    if wn != 0:
        return True

    # 2. 射线奇偶检验兜底 (Ray-Casting Parity Fallback，兼容反向双环 8 字形轨迹)
    inside = False
    p1x, p1y = poly[0]
    for i in range(1, n + 1):
        p2x, p2y = poly[i % n]
        if (p1y < y <= p2y) or (p2y < y <= p1y):
            if x <= (p1x if p1x > p2x else p2x):
                x_inters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if x <= x_inters:
                    inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def is_vertical_bracket_stroke(sw: int, sh: int, stroke_points: Optional[List[QPoint]] = None) -> bool:
    """严格遵循 AGENTS.md 12.1 纵向大括号高宽比与端点拓扑约束，严密隔离对角拉框"""
    if not (sh >= 40 and sw <= 150 and sh >= 1.5 * max(1, sw)):
        return False
    if stroke_points and len(stroke_points) >= 2 and sw > 24:
        if abs(stroke_points[0].x() - stroke_points[-1].x()) > 0.68 * sw:
            return False
    return True


def select_word_indices_by_gesture(
    all_words: List[Dict[str, Any]],
    min_sx: int,
    min_sy: int,
    max_sx: int,
    max_sy: int,
    stroke_points: Optional[List[QPoint]] = None,
    path_len: Optional[float] = None,
    dpr: float = 1.0,
    pad_tol: float = 8.0,
    swipe_min_w: float = 28.0,
    swipe_flat_min_w: float = 32.0,
    swipe_v_pad: float = 16.0,
    swipe_max_h: float = 16.0,
    lasso_close_px: float = 120.0,
    lasso_head_px: float = 100.0,
    allow_vertical_bracket: bool = True,
) -> List[int]:
    """通用几何手势选词核（圈选 Lasso / 横向划词 / 纵向大括号 / 矩形拉框），供定格层与卡片原文区共同复用。"""
    if not all_words:
        return []

    inside_indices: List[int] = []

    # 优先使用圈选轨迹多边形 (Point-in-Polygon Lasso) 检测
    poly = None
    poly_bbox = None
    if stroke_points and len(stroke_points) >= 4:
        start_pt = stroke_points[0]
        end_pt = stroke_points[-1]
        closing_dist = ((end_pt.x() - start_pt.x()) ** 2 + (end_pt.y() - start_pt.y()) ** 2) ** 0.5
        sw = max_sx - min_sx
        sh = max_sy - min_sy
        diag = (sw * sw + sh * sh) ** 0.5
        if path_len is None or path_len <= 0:
            calc_path_len = sum(
                ((stroke_points[j + 1].x() - stroke_points[j].x()) ** 2 + (stroke_points[j + 1].y() - stroke_points[j].y()) ** 2) ** 0.5
                for j in range(len(stroke_points) - 1)
            )
        else:
            calc_path_len = path_len

        half_len = max(3, len(stroke_points) // 2)
        dists_to_head = [
            ((end_pt.x() - p.x()) ** 2 + (end_pt.y() - p.y()) ** 2) ** 0.5
            for p in stroke_points[:half_len]
        ]
        min_dist_to_head = min(dists_to_head)

        is_closed = (
            calc_path_len >= 1.2 * diag and (
                closing_dist <= lasso_close_px or closing_dist <= 0.35 * diag or
                min_dist_to_head <= lasso_head_px or min_dist_to_head <= 0.35 * diag
            )
        )

        if is_closed:
            head_idx = 0
            tail_idx = len(stroke_points) - 1
            if closing_dist > min(80.0, lasso_close_px * 0.7) and closing_dist > 0.25 * diag:
                min_d = closing_dist
                for i in range(half_len):
                    d = ((end_pt.x() - stroke_points[i].x()) ** 2 + (end_pt.y() - stroke_points[i].y()) ** 2) ** 0.5
                    if d < min_d:
                        min_d = d
                        head_idx = i
                for j in range(half_len, len(stroke_points)):
                    d = ((start_pt.x() - stroke_points[j].x()) ** 2 + (start_pt.y() - stroke_points[j].y()) ** 2) ** 0.5
                    if d < min_d:
                        min_d = d
                        head_idx = 0
                        tail_idx = j

            loop_points = stroke_points[head_idx:tail_idx + 1]
            if len(loop_points) > 128:
                decimated = [loop_points[0]]
                last_keep = loop_points[0]
                min_step = max(4, int(diag * 0.02))
                for pt in loop_points[1:-1]:
                    if abs(pt.x() - last_keep.x()) + abs(pt.y() - last_keep.y()) >= min_step:
                        decimated.append(pt)
                        last_keep = pt
                decimated.append(loop_points[-1])
                if len(decimated) > 192:
                    stride = max(2, len(decimated) // 128)
                    sampled = decimated[::stride]
                    if sampled[-1] != decimated[-1]:
                        sampled.append(decimated[-1])
                    decimated = sampled
                loop_points = decimated
            poly = [(float(p.x()), float(p.y())) for p in loop_points]
            if len(poly) >= 3:
                pxs = [pt[0] for pt in poly]
                pys = [pt[1] for pt in poly]
                poly_bbox = (min(pxs), min(pys), max(pxs), max(pys))

    if poly:
        for i, w_obj in enumerate(all_words):
            bx = w_obj["box"]["x"] / dpr
            by = w_obj["box"]["y"] / dpr
            bw = w_obj["box"]["w"] / dpr
            bh = w_obj["box"]["h"] / dpr
            cx = bx + bw / 2.0
            cy = by + bh / 2.0

            box_overlap = not (bx + bw < min_sx - pad_tol or bx > max_sx + pad_tol or by + bh < min_sy - pad_tol or by > max_sy + pad_tol)
            if box_overlap:
                if point_in_polygon(cx, cy, poly, poly_bbox):
                    inside_indices.append(i)
                else:
                    sample_pts = [
                        (bx + 2, cy), (bx + bw - 2, cy),
                        (bx + bw * 0.25, cy), (bx + bw * 0.75, cy),
                        (cx, by + 2), (cx, by + bh - 2),
                        (bx + bw * 0.1, by + bh * 0.1),
                        (bx + bw * 0.9, by + bh * 0.9),
                    ]
                    inside_cnt = sum(1 for px, py in sample_pts if point_in_polygon(px, py, poly, poly_bbox))
                    if inside_cnt >= 2:
                        inside_indices.append(i)

        if inside_indices:
            return sorted(list(set(inside_indices)), key=lambda idx: (all_words[idx].get("line_id", 0), all_words[idx]["box"]["x"]))

    sw = max_sx - min_sx
    sh = max_sy - min_sy

    is_horizontal_swipe = (sw >= swipe_min_w and (sw >= 2.2 * max(1, sh) or (sh <= swipe_max_h and sw >= swipe_flat_min_w)))
    is_vertical_bracket = allow_vertical_bracket and is_vertical_bracket_stroke(sw, sh, stroke_points)

    if is_horizontal_swipe:
        for i, w_obj in enumerate(all_words):
            bx1 = w_obj["box"]["x"] / dpr
            by1 = w_obj["box"]["y"] / dpr
            bx2 = (w_obj["box"]["x"] + w_obj["box"]["w"]) / dpr
            by2 = (w_obj["box"]["y"] + w_obj["box"]["h"]) / dpr
            cx = (bx1 + bx2) / 2
            cy = (by1 + by2) / 2

            v_hit = (not (max_sy + swipe_v_pad < by1 or min_sy - swipe_v_pad > by2)) or ((min_sy - swipe_v_pad) <= cy <= (max_sy + swipe_v_pad))
            h_overlap = max(0.0, min(bx2, max_sx) - max(bx1, min_sx))
            bw = max(1.0, bx2 - bx1)
            h_hit = (min_sx <= cx <= max_sx) or (h_overlap >= 0.3 * bw)

            if v_hit and h_hit:
                inside_indices.append(i)

        if inside_indices:
            selected_line_ids = {all_words[idx].get("line_id", 0) for idx in inside_indices}
            filled_indices = set(inside_indices)
            for i, w_obj in enumerate(all_words):
                if i in filled_indices:
                    continue
                lid = w_obj.get("line_id", 0)
                if lid in selected_line_ids:
                    cx = (w_obj["box"]["x"] + w_obj["box"]["w"] / 2) / dpr
                    if (min_sx - 6) <= cx <= (max_sx + 6):
                        filled_indices.add(i)
            inside_indices = list(filled_indices)

    elif is_vertical_bracket:
        candidate_indices = []
        for i, w_obj in enumerate(all_words):
            by1 = w_obj["box"]["y"] / dpr
            by2 = (w_obj["box"]["y"] + w_obj["box"]["h"]) / dpr
            cy = (by1 + by2) / 2
            v_hit = (not (max_sy + 20 < by1 or min_sy - 20 > by2)) or ((min_sy - 20) <= cy <= (max_sy + 20))
            if v_hit:
                candidate_indices.append(i)

        if candidate_indices:
            pt_start = stroke_points[0] if stroke_points else None
            pt_end = stroke_points[-1] if stroke_points else None
            ends_avg_x = ((pt_start.x() + pt_end.x()) / 2.0) if (pt_start and pt_end) else min_sx
            is_open_right = ends_avg_x > (min_sx + 0.25 * sw)

            def get_bracket_x_at_y(y_val):
                nearby = [p.x() for p in (stroke_points or ()) if abs(p.y() - y_val) <= 30]
                if nearby:
                    return max(nearby) if is_open_right else min(nearby)
                return max_sx if is_open_right else min_sx

            lines_dict: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
            for idx in candidate_indices:
                w = all_words[idx]
                lid = w.get("line_id", 0)
                lines_dict.setdefault(lid, []).append((idx, w))

            MAX_GAP = 90.0
            MAX_START_DIST = 160.0

            for lid, items in sorted(lines_dict.items()):
                if is_open_right:
                    row_items = []
                    for it in items:
                        cx = (it[1]["box"]["x"] + it[1]["box"]["w"] / 2) / dpr
                        cy = (it[1]["box"]["y"] + it[1]["box"]["h"] / 2) / dpr
                        bx_at_y = get_bracket_x_at_y(cy)
                        if cx >= bx_at_y - 15.0:
                            row_items.append(it)
                    row_items.sort(key=lambda it: it[1]["box"]["x"])
                    if not row_items:
                        continue
                    first_left = row_items[0][1]["box"]["x"] / dpr
                    first_cy = (row_items[0][1]["box"]["y"] + row_items[0][1]["box"]["h"] / 2) / dpr
                    first_bx = get_bracket_x_at_y(first_cy)
                    if (first_left - first_bx) > MAX_START_DIST:
                        continue
                    cur_right = (row_items[0][1]["box"]["x"] + row_items[0][1]["box"]["w"]) / dpr
                    inside_indices.append(row_items[0][0])
                    for it in row_items[1:]:
                        it_left = it[1]["box"]["x"] / dpr
                        if (it_left - cur_right) > MAX_GAP:
                            break
                        inside_indices.append(it[0])
                        cur_right = max(cur_right, (it[1]["box"]["x"] + it[1]["box"]["w"]) / dpr)
                else:
                    row_items = []
                    for it in items:
                        cx = (it[1]["box"]["x"] + it[1]["box"]["w"] / 2) / dpr
                        cy = (it[1]["box"]["y"] + it[1]["box"]["h"] / 2) / dpr
                        bx_at_y = get_bracket_x_at_y(cy)
                        if cx <= bx_at_y + 15.0:
                            row_items.append(it)
                    row_items.sort(key=lambda it: it[1]["box"]["x"], reverse=True)
                    if not row_items:
                        continue
                    first_right = (row_items[0][1]["box"]["x"] + row_items[0][1]["box"]["w"]) / dpr
                    first_cy = (row_items[0][1]["box"]["y"] + row_items[0][1]["box"]["h"] / 2) / dpr
                    first_bx = get_bracket_x_at_y(first_cy)
                    if (first_bx - first_right) > MAX_START_DIST:
                        continue
                    cur_left = row_items[0][1]["box"]["x"] / dpr
                    inside_indices.append(row_items[0][0])
                    for it in row_items[1:]:
                        it_right = (it[1]["box"]["x"] + it[1]["box"]["w"]) / dpr
                        if (cur_left - it_right) > MAX_GAP:
                            break
                        inside_indices.append(it[0])
                        cur_left = min(cur_left, it[1]["box"]["x"] / dpr)

    else:
        for i, w_obj in enumerate(all_words):
            bx1 = w_obj["box"]["x"] / dpr
            by1 = w_obj["box"]["y"] / dpr
            bx2 = (w_obj["box"]["x"] + w_obj["box"]["w"]) / dpr
            by2 = (w_obj["box"]["y"] + w_obj["box"]["h"]) / dpr
            cx = (bx1 + bx2) / 2
            cy = (by1 + by2) / 2
            inter_w = max(0.0, min(bx2, max_sx) - max(bx1, min_sx))
            inter_h = max(0.0, min(by2, max_sy) - max(by1, min_sy))
            inter_area = inter_w * inter_h
            w_area = max(1.0, (bx2 - bx1) * (by2 - by1))
            center_hit = (min_sx - pad_tol) <= cx <= (max_sx + pad_tol) and (min_sy - pad_tol) <= cy <= (max_sy + pad_tol)
            area_hit = (inter_area / w_area >= 0.35) and (min_sy - pad_tol) <= cy <= (max_sy + pad_tol)
            if center_hit or area_hit:
                inside_indices.append(i)

    if inside_indices:
        return sorted(list(set(inside_indices)), key=lambda idx: (all_words[idx].get("line_id", 0), all_words[idx]["box"]["x"]))
    return []


class FreezeSelectOverlay(QWidget):
    """
    定格即选模式交互层 (Freeze & Select 165Hz 原生触控多行扩选与 0.5s 智能锁定)
    1. 彻底支持跨行多行自由拖拽扩选 (Multi-line Selection)；
    2. 滑动/选词时持续扩选绝不锁定；唯有指尖停顿满 0.5 秒时正式微光吸附锁定；
    3. 锁定后顺势纵向分流：
       - 向下滑动 (dy > +18px) ➔ ✦ 翡翠绿秒存生词本；
       - 向上滑动 (dy < -18px) ➔ ✕ 珊瑚红划线取消；
       - 原位松手 ➔ 展开详细毛玻璃学术词典卡（含精准整句翻译）；
    4. 轻点单词 ➔ 毫秒级直出词典卡；轻触空白处 ➔ 0 延迟切回原课件。
    """
    triage_save = Signal(str, str)              # (selected_text, sentence)
    triage_open_dict = Signal(str, str, QPoint) # (selected_text, sentence, pos)
    triage_cancel = Signal()
    closed = Signal()
    sig_words_extracted = Signal(list, int)               # (new_words, session_id) 后台手写/印刷词提取完成
    sig_handwriting_done = Signal(str, str, QPoint, int, int)  # (text, sentence, pos, session_id, req_id) 异步手写识别完成
    _sig_pending_tap_resolved = Signal(str, str, QPoint, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_id = 0
        self._hw_req_id = 0
        self._img_lock = threading.Lock()
        self._bg_img_data: Optional[Tuple[int, int, int, bytes]] = None
        self.sig_words_extracted.connect(self._on_words_extracted)
        self.sig_handwriting_done.connect(self._on_handwriting_done)
        self._sig_pending_tap_resolved.connect(self._on_pending_tap_resolved)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setMouseTracking(False)

        self.background_pixmap: Optional[QPixmap] = None
        self._bg_qimage: Optional[QImage] = None
        self.stage = "IDLE"  # IDLE | DWELLING | SELECTING | LOCKED | TRIAGE_DOWN | TRIAGE_UP | NO_WORD

        self.touch_start = QPoint()
        self.touch_cur = QPoint()
        self.phys_start = QPoint()

        # 全局与多行选区数据
        self._all_words: List[Dict[str, Any]] = []
        self._cached_lines_map = None
        self._cached_line_bounds = {}
        self._cached_words_sig = None
        self.initial_idx: int = -1
        self.current_idx: int = -1
        self.min_idx: int = -1
        self.max_idx: int = -1
        self.is_locked: bool = False
        self.pulse_glow: bool = False

        self.locked_word: str = ""
        self.selected_boxes: List[QRect] = []
        self.context_sentence: str = ""
        self.lock_anchor_y: int = 0

        # 150ms 初态点触探针定时器
        self.dwell_timer = QTimer(self)
        self.dwell_timer.setSingleShot(True)
        self.dwell_timer.timeout.connect(self._on_dwell_triggered)

        # 🌟 核心：0.5 秒静止停顿自动锁定定时器
        self.lock_timer = QTimer(self)
        self.lock_timer.setSingleShot(True)
        self.lock_timer.timeout.connect(self._lock_selection)

        # 锁定微光脉冲定时器
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setSingleShot(True)
        self.pulse_timer.timeout.connect(self._end_pulse)

        # 无词提示淡出定时器
        self.fade_timer = QTimer(self)
        self.fade_timer.setSingleShot(True)
        self.fade_timer.timeout.connect(self._clear_no_word)
        self.stroke_points: List[QPoint] = []
        self.pristine_image = None
        self.min_sx: Optional[int] = None
        self.max_sx: Optional[int] = None
        self.min_sy: Optional[int] = None
        self.max_sy: Optional[int] = None
        self.path_len: float = 0.0
        self._last_select_time: float = 0.0

    @property
    def all_words(self) -> List[Dict[str, Any]]:
        return self._all_words

    @all_words.setter
    def all_words(self, value: List[Dict[str, Any]]):
        self._all_words = list(value) if value is not None else []
        self._cached_lines_map = None
        self._cached_line_bounds = {}
        self._cached_words_sig = None

    def _words_signature(self) -> Tuple[int, int, int, int]:
        words = self._all_words
        if not words:
            return (id(words), 0, 0, 0)
        return (id(words), len(words), id(words[0]), id(words[-1]))

    @staticmethod
    def _is_vertical_bracket(sw: int, sh: int, stroke_points: Optional[List[QPoint]] = None) -> bool:
        """严格遵循 AGENTS.md 12.1 纵向大括号高宽比与端点拓扑约束，严密隔离对角拉框"""
        return is_vertical_bracket_stroke(sw, sh, stroke_points)

    def _recompute_lines_map(self):
        if not self._all_words:
            self._cached_lines_map = {}
            self._cached_line_bounds = {}
            self._cached_words_sig = self._words_signature()
            return
        lines_map: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
        for idx, w in enumerate(self._all_words):
            lid = w.get("line_id", 0)
            if lid not in lines_map:
                lines_map[lid] = []
            lines_map[lid].append((idx, w))
        self._cached_lines_map = lines_map
        self._cached_line_bounds = {
            lid: (
                min(w["box"]["y"] for _, w in items),
                max(w["box"]["y"] + w["box"]["h"] for _, w in items),
                min(w["box"]["x"] for _, w in items),
                max(w["box"]["x"] + w["box"]["w"] for _, w in items),
            )
            for lid, items in lines_map.items()
        }
        self._cached_words_sig = self._words_signature()

    def _on_words_extracted(self, new_words: List[Dict[str, Any]], session_id: int = 0):
        """后台异步 OCR 提取结果合并：主线程接收新词块，无缝整合并自动触发选区重算（含 session 隔离）"""
        if session_id != self._session_id or not new_words:
            return

        # 智能去重与手写优先：若新词与已有词在物理空间有实质重叠（IoU 或距离过近），用高置信度新词替换
        def boxes_overlap(b1, b2) -> bool:
            x_left = max(b1["x"], b2["x"])
            y_top = max(b1["y"], b2["y"])
            x_right = min(b1["x"] + b1["w"], b2["x"] + b2["w"])
            y_bottom = min(b1["y"] + b1["h"], b2["y"] + b2["h"])
            if x_right <= x_left or y_bottom <= y_top:
                return False
            inter_area = (x_right - x_left) * (y_bottom - y_top)
            area1 = b1["w"] * b1["h"]
            area2 = b2["w"] * b2["h"]
            min_area = max(1.0, min(area1, area2))
            return (inter_area / min_area) >= 0.35

        def is_valid_standard_word(w_item) -> bool:
            t = w_item.get("clean", w_item.get("text", "")).strip()
            if t.lower() in ("a", "i"):
                return True
            alpha_cnt = sum(1 for c in t if c.isascii() and c.isalpha())
            return alpha_cnt >= 2 and (alpha_cnt / max(1, len(t))) >= 0.75

        def is_broken_sub_fragment(old_w, overlapping_nws) -> bool:
            if not overlapping_nws:
                return False
            if any(nw.get("ocr_source") == "outlined_sub" for nw in overlapping_nws):
                raw_t = (old_w.get("text") or "").strip()
                clean_low = (old_w.get("clean") or "").strip().lower()
                if "•" in raw_t:
                    return True
                try:
                    from .ocr_helpers import _ensure_glued_lexicon
                    core_w, long_w = _ensure_glued_lexicon()
                    old_known = (clean_low in core_w) or (clean_low in long_w)
                    new_known = any(
                        ((nw.get("clean") or "").lower() in core_w or (nw.get("clean") or "").lower() in long_w)
                        for nw in overlapping_nws
                    )
                    if (not old_known) and new_known:
                        return True
                except Exception:
                    pass
            return False

        # 过滤掉与新词高度重叠的原有残渣/碎片词（保护完整标准英文词汇，不让火星文反向清洗正规词）
        filtered_old = []
        clean_new_words = []
        for old_w in self.all_words:
            # 找到与之重叠的新词
            overlapped_nws = [nw for nw in new_words if boxes_overlap(old_w["box"], nw["box"])]
            if not overlapped_nws:
                filtered_old.append(old_w)
            else:
                if is_valid_standard_word(old_w) and not is_broken_sub_fragment(old_w, overlapped_nws):
                    filtered_old.append(old_w)
                else:
                    # 原有词为 WinRT 残渣碎片或描边碎裂片，同意被高召回新词替换
                    pass

        # 同样过滤掉与高质量保留旧词重叠的新碎片
        for nw in new_words:
            overlapped_with_valid_old = any(
                boxes_overlap(nw["box"], ow["box"]) and is_valid_standard_word(ow)
                for ow in filtered_old
            )
            if not overlapped_with_valid_old:
                clean_new_words.append(nw)

        self.all_words = filtered_old + clean_new_words

        from .ocr_helpers import sort_words_reading_order, process_ocr_tokens
        self.all_words = sort_words_reading_order(self.all_words)
        self.all_words = process_ocr_tokens(self.all_words)
        self._recompute_lines_map()

        # 词表重排后若有起笔锚点，必须按物理起笔点重新校准 initial_idx，防止插入中间漏词导致索引漂移
        if self.initial_idx != -1 and hasattr(self, "phys_start") and self.phys_start is not None:
            reanchored = self._find_word_at_phys(self.phys_start.x(), self.phys_start.y(), tolerance=22)
            if reanchored != -1:
                self.initial_idx = reanchored
            elif self.initial_idx >= len(self.all_words):
                self.initial_idx = len(self.all_words) - 1 if self.all_words else -1
            self.current_idx = -1

        # 若存在极速轻点挂起的点击：在词库就绪瞬间秒解算并打开词典（0ms 纯几何，无卡顿）
        if getattr(self, "_pending_tap_pos", None) is not None:
            px, py, t_pos = self._pending_tap_pos
            self._pending_tap_pos = None
            m_idx = self._find_word_at_phys(px, py, tolerance=22)
            if m_idx != -1:
                target_w = self.all_words[m_idx].get("text", self.all_words[m_idx].get("clean", ""))
                from .ocr_helpers import extract_sentence_from_words
                s_sent = extract_sentence_from_words(self.all_words, m_idx)
                self.close_freeze_mode()
                self.triage_open_dict.emit(target_w, s_sent, t_pos)
                return
            else:
                self.close_freeze_mode()
                return

        # 若用户正在按住单点或已划线：使用新到账词库瞬间唤醒或刷新高亮！
        if self.min_sx is not None and self.max_sx is not None:
            sw = self.max_sx - self.min_sx
            sh = (self.max_sy - self.min_sy) if (self.max_sy is not None and self.min_sy is not None) else 0
            if self.initial_idx == -1 and self.stage in ("DWELLING", "SELECTING") and sw < 20 and sh < 20 and hasattr(self, "phys_start"):
                m_idx = self._find_word_at_phys(self.phys_start.x(), self.phys_start.y(), tolerance=22)
                if m_idx != -1:
                    self.initial_idx = self.current_idx = self.min_idx = self.max_idx = m_idx
                    from .ocr_helpers import extract_sentence_from_words
                    self.context_sentence = extract_sentence_from_words(self.all_words, m_idx)
                    self._recalculate_multiline_selection()
                    self.update()
            elif sw >= 20 or self.stage in ("SELECTING", "LOCKED"):
                cur_pos = getattr(self, "touch_cur", None) or getattr(self, "touch_start", None)
                if cur_pos is not None:
                    if getattr(self, "is_locked", False) and hasattr(self, "lock_anchor_y"):
                        cur_pos = QPoint(cur_pos.x(), int(self.lock_anchor_y))
                    self._apply_stroke_selection_geometry(cur_pos)
                    self.update()

    def _on_handwriting_done(
        self,
        selected_text: str,
        sentence: str,
        pos: QPoint,
        session_id: int = 0,
        req_id: Optional[int] = None,
    ):
        """异步手写识别完成：自动打开词典抽屉展示解析与翻译（含 session / req 双世代隔离与防闪退兜底）"""
        if session_id != self._session_id:
            return
        if req_id is not None and req_id != self._hw_req_id:
            return
        if not selected_text:
            self.pulse_glow = False
            self.is_locked = False
            self.stage = "NO_WORD"
            self.fade_timer.start(750)
            self.update()
            return
        self.close_freeze_mode()
        self.triage_open_dict.emit(selected_text, sentence, pos)

    def _on_pending_tap_resolved(self, target_w: str, sent: str, t_pos: QPoint, session_id: int):
        if session_id != self._session_id:
            return
        self.close_freeze_mode()
        if target_w:
            self.triage_open_dict.emit(target_w, sent, t_pos)

    def _resolve_pending_tap_timeout(self, session_id: int):
        """挂起轻击超时兜底 (650ms)：严禁阻塞主线程，强制派发至后台线程执行局部切片极速识别"""
        if session_id != self._session_id or getattr(self, "_pending_tap_pos", None) is None:
            return
        px, py, t_pos = self._pending_tap_pos
        self._pending_tap_pos = None
        if self.all_words:
            m_idx = self._find_word_at_phys(px, py, tolerance=22)
            if m_idx != -1:
                target_w = self.all_words[m_idx].get("text", self.all_words[m_idx].get("clean", ""))
                from .ocr_helpers import extract_sentence_from_words
                s_sent = extract_sentence_from_words(self.all_words, m_idx)
                self.close_freeze_mode()
                self.triage_open_dict.emit(target_w, s_sent, t_pos)
                return
            self.close_freeze_mode()
            return

        def _tap_worker():
            target_w, sent = "", ""
            try:
                res = self._recognize_at_phys(px, py, radius_y=180)
                target_w = res.get("word", "") or ""
                sent = res.get("sentence", "") or ""
            except Exception:
                pass
            self._sig_pending_tap_resolved.emit(target_w, sent, t_pos, session_id)

        threading.Thread(target=_tap_worker, daemon=True).start()

    def _get_pristine_image(self, expected_session_id: Optional[int] = None):
        """线程安全地按需构造 PIL Image：绑定 session_id 校验与锁保护，绝不在已关闭/旧会话下回退抓屏"""
        with self._img_lock:
            if expected_session_id is not None and expected_session_id != self._session_id:
                return None
            if self.pristine_image is not None:
                if expected_session_id is None or getattr(self, "_pristine_session_id", -1) == expected_session_id:
                    return self.pristine_image
            raw_snap = self._bg_img_data
            raw_sid = getattr(self, "_bg_img_session_id", -1)
            if raw_snap is not None and (expected_session_id is None or raw_sid == expected_session_id):
                try:
                    rw, rh, rbpl, rbytes = raw_snap
                    from PIL import Image as PILImage
                    img_rgb = PILImage.frombytes(
                        "RGBA",
                        (rw, rh),
                        rbytes,
                        "raw",
                        "BGRA",
                        rbpl,
                        1,
                    ).convert("RGB")
                    if expected_session_id is not None and expected_session_id != self._session_id:
                        return None
                    self.pristine_image = img_rgb
                    self._pristine_session_id = raw_sid
                    # 转换完毕即释放原始 BGRA 字节快照，显著降低定格期内存峰值
                    self._bg_img_data = None
                    return self.pristine_image
                except Exception:
                    pass
            qimg = self._bg_qimage
            if qimg is not None and not qimg.isNull() and threading.current_thread() is threading.main_thread():
                try:
                    if qimg.format() not in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied):
                        qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
                    nbytes = qimg.sizeInBytes() if hasattr(qimg, "sizeInBytes") else (qimg.height() * qimg.bytesPerLine())
                    rbytes = bytes(qimg.constBits()[:nbytes])
                    from PIL import Image as PILImage
                    self.pristine_image = PILImage.frombytes(
                        "RGBA",
                        (qimg.width(), qimg.height()),
                        rbytes,
                        "raw",
                        "BGRA",
                        qimg.bytesPerLine(),
                        1,
                    ).convert("RGB")
                    self._pristine_session_id = self._session_id
                    return self.pristine_image
                except Exception:
                    pass
            if expected_session_id is not None:
                return None
            try:
                self.pristine_image = ImageGrab.grab()
                self._pristine_session_id = self._session_id
            except Exception:
                self.pristine_image = None
            return self.pristine_image

    def activate_freeze_mode(self):
        """定格当前全屏画面并铺开原生高刷交互画布"""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.closed.emit()
            return
        self._session_id += 1
        self._hw_req_id += 1
        cur_session = self._session_id

        dpr = screen.devicePixelRatio() if screen else 2.0

        # 单次截取全屏快照，并在 GUI 主线程拷贝不可变像素字节流供后台线程安全读取
        self.background_pixmap = screen.grabWindow(0)
        self.background_pixmap.setDevicePixelRatio(dpr)
        qimg = self.background_pixmap.toImage() if not self.background_pixmap.isNull() else None
        raw_img_data = None
        if qimg is not None and not qimg.isNull():
            try:
                if qimg.format() not in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied):
                    qimg = qimg.convertToFormat(QImage.Format.Format_ARGB32)
                nbytes = qimg.sizeInBytes() if hasattr(qimg, "sizeInBytes") else (qimg.height() * qimg.bytesPerLine())
                raw_img_data = (qimg.width(), qimg.height(), qimg.bytesPerLine(), bytes(qimg.constBits()[:nbytes]))
            except Exception:
                raw_img_data = None
        with self._img_lock:
            self._bg_qimage = None if raw_img_data is not None else qimg
            self._bg_img_data = raw_img_data
            self._bg_img_session_id = cur_session
            self.pristine_image = None
            self._pristine_session_id = -1

        # 后台异步启动当前屏幕整屏 OCR 预提取（先 WinRT 秒出印刷词，再仅对缺词手写/描边区域增量补全）
        def _pre_extract_all(session_id: int):
            try:
                pimg = self._get_pristine_image(expected_session_id=session_id)
                if pimg:
                    pw, ph = pimg.size
                    # 1. 先提取全屏 WinRT 印刷体，第一时间解锁点词与常规拖选 (<100ms)
                    try:
                        import asyncio
                        winrt_res = asyncio.run(win_ocr_engine._recognize_winrt_async(pimg, 0, 0, pw // 2, ph // 2))
                        raw_extracted = winrt_res.get("all_words", []) if winrt_res else []
                    except Exception:
                        raw_extracted = []

                    if session_id != self._session_id:
                        return

                    # 2. 毫秒级探测手写笔迹区与亮色描边视频字幕区
                    hw_crops = win_ocr_engine.detect_handwriting_regions(pimg)
                    sub_crops = win_ocr_engine.detect_outlined_subtitle_regions(pimg)

                    if hw_crops:
                        def in_hw_region(w_item):
                            # 标准正规英文印刷体词汇绝对保护 (Anti-Overkill Protection)
                            raw_t = w_item.get("clean", w_item.get("text", "")).strip()
                            alpha_chars = sum(1 for c in raw_t if c.isascii() and c.isalpha())
                            if alpha_chars >= 2 and (alpha_chars / max(1, len(raw_t))) >= 0.75:
                                return False

                            w_box = w_item["box"]
                            pad = 25
                            bx1 = w_box["x"]
                            by1 = w_box["y"]
                            bx2 = w_box["x"] + w_box["w"]
                            by2 = w_box["y"] + w_box["h"]
                            for item in hw_crops:
                                rx, ry, rw, rh = item["box"]
                                gx1 = max(0, rx - pad)
                                gy1 = max(0, ry - pad)
                                gx2 = min(pw, rx + rw + pad)
                                gy2 = min(ph, ry + rh + pad)
                                if not (bx2 < gx1 or bx1 > gx2 or by2 < gy1 or by1 > gy2):
                                    return True
                            return False
                        clean_extracted = [w for w in raw_extracted if not in_hw_region(w)]
                    else:
                        clean_extracted = raw_extracted

                    if clean_extracted:
                        self.sig_words_extracted.emit(clean_extracted, session_id)

                    def _region_winrt_coverage(rx: int, ry: int, rw: int, rh: int):
                        inside = []
                        rx2, ry2 = rx + rw, ry + rh
                        for w_it in clean_extracted:
                            wb = w_it.get("box", {})
                            cx = wb.get("x", 0) + wb.get("w", 0) * 0.5
                            cy = wb.get("y", 0) + wb.get("h", 0) * 0.5
                            if rx <= cx <= rx2 and ry <= cy <= ry2:
                                t = (w_it.get("clean") or w_it.get("text") or "").strip()
                                ac = sum(1 for c in t if c.isascii() and c.isalpha())
                                if ac >= 2 and (ac / max(1, len(t))) >= 0.72 and "•" not in (w_it.get("text") or ""):
                                    inside.append(w_it)
                        return inside

                    # 3. 仅对 WinRT 存在碎裂或中段漏词的亮色描边视频字幕区启动专精补全（最多 2 处，免扫整张 PPT）
                    sub_candidates = sorted(
                        sub_crops,
                        key=lambda s: (s["box"][2] * (1.2 if s["box"][1] >= ph * 0.45 else 0.7)),
                        reverse=True,
                    )[:2]
                    for s_item in sub_candidates:
                        if session_id != self._session_id:
                            return
                        rx, ry, rw, rh = s_item["box"]
                        cov = _region_winrt_coverage(rx, ry, rw, rh)
                        has_mid_gap = False
                        if len(cov) >= 2:
                            cov_sorted = sorted(cov, key=lambda w: (w.get("line_id", 0), w["box"]["x"]))
                            for ci in range(len(cov_sorted) - 1):
                                w1, w2 = cov_sorted[ci], cov_sorted[ci + 1]
                                if w1.get("line_id") == w2.get("line_id"):
                                    gap_px = w2["box"]["x"] - (w1["box"]["x"] + w1["box"]["w"])
                                    lh = max(14, w1["box"]["h"], w2["box"]["h"])
                                    if gap_px > lh * 2.8:
                                        has_mid_gap = True
                                        break
                        if len(cov) >= 6 and not has_mid_gap:
                            continue
                        sub_words = win_ocr_engine.recognize_outlined_subtitle_words(
                            s_item["crop"], s_item["origin_x"], s_item["origin_y"]
                        )
                        if sub_words and session_id == self._session_id:
                            for sw_it in sub_words:
                                sw_it["ocr_source"] = "outlined_sub"
                            self.sig_words_extracted.emit(sub_words, session_id)

                    # 4. 手写区域专精识别：仅处理 WinRT 未能稳定识别的真手写笔记区（绕过 PPT 横幅/图标误触发）
                    for item in hw_crops[:3]:
                        if session_id != self._session_id:
                            return
                        rx, ry, rw, rh = item["box"]
                        if len(_region_winrt_coverage(rx, ry, rw, rh)) >= 3:
                            continue
                        hw_words = win_ocr_engine.recognize_handwriting_words(
                            item["crop"], item["origin_x"], item["origin_y"]
                        )
                        if hw_words and session_id == self._session_id:
                            self.sig_words_extracted.emit(hw_words, session_id)
            except Exception:
                pass
        threading.Thread(target=_pre_extract_all, args=(cur_session,), daemon=True).start()

        self.stage = "IDLE"
        self.all_words = []
        self._pending_tap_pos = None
        self._cached_lines_map = None
        self._cached_line_bounds = {}
        self.selected_boxes = []
        self.locked_word = ""
        self.stroke_points = []
        self.is_locked = False
        self.pulse_glow = False
        self.initial_idx = -1
        self.current_idx = -1
        self.min_sx = None
        self.max_sx = None
        self.min_sy = None
        self.max_sy = None
        self.path_len = 0.0
        self._last_select_time = 0.0

        self.setGeometry(screen.geometry() if screen else QRect(0, 0, 1280, 800))
        self.show()
        self.raise_()
        enforce_window_topmost(self, activate=True)

        # 屏蔽 Windows 系统的白圈长按打断（64位安全句柄签名）
        if _u32_overlay is not None:
            try:
                hwnd = int(self.winId())
                if hwnd:
                    flags = TABLET_DISABLE_PRESSANDHOLD | TABLET_DISABLE_FLICKS | TABLET_DISABLE_TOUCHSWITCH
                    _u32_overlay.SetPropW(wintypes.HWND(hwnd), "MicrosoftTabletPenServiceProperty", wintypes.HANDLE(flags))
            except Exception:
                pass

        self.update()

    def nativeEvent(self, eventType, message):
        msg = wintypes.MSG.from_address(message.__int__())
        if msg.message == 0x02CC: # WM_TABLET_QUERYSYSTEMGESTURESTATUS
            return True, TABLET_DISABLE_PRESSANDHOLD | TABLET_DISABLE_FLICKS
        return False, 0

    def event(self, event: QEvent):
        if isinstance(event, QTouchEvent):
            self._last_touch_ts = time.monotonic()
            pts = event.points()
            if pts:
                pos = pts[0].position().toPoint()
                evt_type = event.type()
                if evt_type == QEvent.Type.TouchBegin:
                    self._handle_pointer_down(pos)
                elif evt_type == QEvent.Type.TouchUpdate:
                    self._handle_pointer_move(pos)
                elif evt_type == QEvent.Type.TouchEnd:
                    self._handle_pointer_up(pos)
                elif evt_type == QEvent.Type.TouchCancel:
                    # 手掌抑制或系统取消触摸时干净复原，杜绝手指已离屏却卡死在 LOCKED 态
                    self.dwell_timer.stop()
                    self.lock_timer.stop()
                    self.pulse_timer.stop()
                    self.stage = "IDLE"
                    self.is_locked = False
                    self.stroke_points = []
                    self.update()
                event.accept()
                return True
        return super().event(event)

    def _is_touch_synthesized_mouse(self, event: QMouseEvent) -> bool:
        return (
            event.source() != Qt.MouseEventSource.MouseEventNotSynthesized
            and (time.monotonic() - getattr(self, "_last_touch_ts", 0.0)) < 0.45
        )

    def mousePressEvent(self, event: QMouseEvent):
        if self._is_touch_synthesized_mouse(event):
            return
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._handle_pointer_down(event.position().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_touch_synthesized_mouse(event):
            return
        self._handle_pointer_move(event.position().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._is_touch_synthesized_mouse(event):
            return
        self._handle_pointer_up(event.position().toPoint())

    def _find_word_at_phys(self, phys_x: int, phys_y: int, tolerance: int = 22) -> int:
        """纯内存 0ms 几何碰撞匹配：以最高精度从已提取词库中检索目标物理坐标落入的单词"""
        if not self.all_words:
            return -1
        best_idx = -1
        min_dist_sq = float('inf')
        for idx, w in enumerate(self.all_words):
            box = w.get("box", {})
            bx, by, bw, bh = box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)
            if (bx - tolerance) <= phys_x <= (bx + bw + tolerance) and (by - tolerance) <= phys_y <= (by + bh + tolerance):
                cx = bx + bw / 2.0
                cy = by + bh / 2.0
                dist_sq = (phys_x - cx) ** 2 + (phys_y - cy) ** 2
                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    best_idx = idx
        return best_idx

    def _handle_pointer_down(self, pos: QPoint):
        self._hw_req_id += 1
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        self.touch_start = pos
        self.touch_cur = pos
        self.phys_start = QPoint(int(pos.x() * dpr), int(pos.y() * dpr))
        self.stroke_points = [pos]
        self.min_sx = pos.x()
        self.max_sx = pos.x()
        self.min_sy = pos.y()
        self.max_sy = pos.y()
        self.path_len = 0.0
        self._last_select_time = 0.0

        self.fade_timer.stop()
        self.dwell_timer.stop()
        self.lock_timer.stop()
        self.pulse_timer.stop()

        self.stage = "DWELLING"
        self.is_locked = False
        self.pulse_glow = False
        self.locked_word = ""
        self.selected_boxes = []
        self.initial_idx = -1
        self.current_idx = -1
        self.min_idx = -1
        self.max_idx = -1

        # 🌟 纯内存 0ms 预判定：若词库已就绪，按下瞬间即刻完成几何命中断言，消灭 150ms 怠速与抬手卡顿
        if self.all_words:
            matched_idx = self._find_word_at_phys(self.phys_start.x(), self.phys_start.y(), tolerance=12)
            if matched_idx != -1:
                self.initial_idx = matched_idx
                self.current_idx = matched_idx
                self.min_idx = matched_idx
                self.max_idx = matched_idx
                from .ocr_helpers import extract_sentence_from_words
                self.context_sentence = extract_sentence_from_words(self.all_words, matched_idx)
                self._recalculate_multiline_selection()

        # 启动 150ms 探针兜底，若未预中或手指发生拖拽则平滑接管
        self.dwell_timer.start(150)
        self.update()

    def _recognize_at_phys(self, phys_x: int, phys_y: int, radius_y: int = 600) -> Dict[str, Any]:
        """
        基于定格瞬间捕获的 100% 原始纯净截图进行 OCR 识别
        完全避免屏幕已显示的半透明遮罩与水波纹对字母笔画（如 'pass' 的 'p'、'number' 的 'n'）产生侵蚀溶解
        """
        if not self.pristine_image and not self.background_pixmap:
            return win_ocr_engine.recognize_at_point(phys_x, phys_y, radius_y=radius_y)

        pimg = self._get_pristine_image()
        if not pimg:
            return win_ocr_engine.recognize_at_point(phys_x, phys_y, radius_y=radius_y)

        pw, ph = pimg.size
        x1 = 0
        x2 = pw
        y1 = max(0, phys_y - radius_y)
        y2 = min(ph, phys_y + radius_y)

        crop_img = pimg.crop((x1, y1, x2, y2))
        return win_ocr_engine.recognize_image(crop_img, x1, y1, phys_x, phys_y)

    def _select_words_in_range(self, min_sx: int, min_sy: int, max_sx: int, max_sy: int, stroke_points: Optional[List[QPoint]] = None, force_crop_ocr: bool = False) -> bool:
        """识别并提取落在屏幕几何选区 [min_sx, min_sy, max_sx, max_sy] 内的所有单词"""
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        # 仅在抬手释放且显式请求 force_crop_ocr 时执行动态裁切（绝不在 pointer_move 中调用截屏或同步 OCR 阻断主线程）
        if force_crop_ocr:
            pimg = self._get_pristine_image()
            if pimg:
                pw, ph = pimg.size
                pad = int(30 * dpr)
                x1 = max(0, int(min_sx * dpr) - pad)
                y1 = max(0, int(min_sy * dpr) - pad)
                x2 = min(pw, int(max_sx * dpr) + pad)
                y2 = min(ph, int(max_sy * dpr) + pad)

                if x2 > x1 and y2 > y1:
                    crop_img = pimg.crop((x1, y1, x2, y2))
                    target_x = int(((min_sx + max_sx) / 2) * dpr)
                    target_y = int(((min_sy + max_sy) / 2) * dpr)
                    ocr_res = win_ocr_engine.recognize_image(crop_img, x1, y1, target_x, target_y)
                    new_words = ocr_res.get("all_words", [])
                    if new_words:
                        if not self.all_words:
                            self.all_words = new_words
                        else:
                            existing_boxes = {(w["box"]["x"], w["box"]["y"]) for w in self.all_words}
                            for nw in new_words:
                                if (nw["box"]["x"], nw["box"]["y"]) not in existing_boxes:
                                    self.all_words.append(nw)
                            from .ocr_helpers import sort_words_reading_order
                            self.all_words = sort_words_reading_order(self.all_words)
                        self._recompute_lines_map()

        if not self.all_words:
            return False

        inside_indices = select_word_indices_by_gesture(
            self.all_words,
            min_sx,
            min_sy,
            max_sx,
            max_sy,
            stroke_points=stroke_points,
            path_len=self.path_len if stroke_points is self.stroke_points else None,
            dpr=dpr,
        )
        if inside_indices:
            self._recalculate_multiline_selection(inside_indices)
            self.context_sentence = extract_sentence_from_words(self.all_words, inside_indices[0])
            return True

        return False

    def _start_selection_from_motion(self):
        """运动触发进入选词态：纯几何状态转换，严禁在主线程发起同步 OCR 或网络请求，保障 165Hz 流畅跟手"""
        self.stage = "SELECTING"
        self.is_locked = False
        self.lock_timer.start(500)
        self.update()

    def _on_dwell_triggered(self):
        """满 150ms 或开始拖拽：优先使用已提取词库秒级定位，若词库尚未就绪则静默等待，杜绝主线程阻塞"""
        if self.stage not in ("DWELLING", "SELECTING"):
            return

        target_phys_x = self.phys_start.x()
        target_phys_y = self.phys_start.y()

        matched_idx = self.initial_idx if (0 <= self.initial_idx < len(self.all_words)) else -1
        if matched_idx == -1 and self.all_words:
            matched_idx = self._find_word_at_phys(target_phys_x, target_phys_y, tolerance=12)

        if matched_idx != -1:
            self.initial_idx = matched_idx
            self.current_idx = self.initial_idx
            self.min_idx = self.initial_idx
            self.max_idx = self.initial_idx
            self.context_sentence = extract_sentence_from_words(self.all_words, self.initial_idx)
            self._recalculate_multiline_selection()
            self.stage = "SELECTING"
            self.is_locked = False
            self.lock_timer.start(500)
            self.update()
        else:
            self.stage = "SELECTING"
            self.initial_idx = -1
            self.current_idx = -1
            self.min_idx = -1
            self.max_idx = -1
            self.is_locked = False
            self.update()

    def _lock_selection(self):
        """停顿满 0.5 秒：触发微光脉冲，正式锁定当前所选多行内容，进入手势分流态"""
        if self.stage in ("IDLE", "NO_WORD") or self.is_locked or not self.locked_word:
            return
        self.is_locked = True
        self.stage = "LOCKED"
        self.pulse_glow = True
        self.lock_anchor_y = self.touch_cur.y()
        self.pulse_timer.start(250)
        self.update()

    def _handle_pointer_move(self, pos: QPoint):
        if self.stage not in ("DWELLING", "SELECTING", "LOCKED", "TRIAGE_DOWN", "TRIAGE_UP", "NO_WORD"):
            return

        if self.min_sx is None or not self.stroke_points:
            self.min_sx = pos.x()
            self.max_sx = pos.x()
            self.min_sy = pos.y()
            self.max_sy = pos.y()
            self.stroke_points = [pos]
            self.touch_cur = pos
            self.touch_start = pos
            self.path_len = 0.0
            return

        # 165Hz 高刷极速过滤微抖 (< 2px 移动直接忽略，杜绝主线程计算拥塞)
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

        dx = pos.x() - self.touch_start.x()
        dy = pos.y() - self.touch_start.y()
        dist = (dx * dx + dy * dy) ** 0.5

        # 1. 在 150ms 初探前或处于无词提示时：若发生移动 (> 12px)，立即提速进入滑选或圈选识别并立即计算
        if self.stage == "DWELLING":
            if dist > 12:
                self.dwell_timer.stop()
                self._start_selection_from_motion()
            else:
                return
        elif self.stage == "NO_WORD":
            if dist > 12:
                self.fade_timer.stop()
                self._start_selection_from_motion()
            else:
                return

        # 2. 🌟 多行自由滑选模式或二维圈选模式 (未锁定状态)：手指在动，坚决不锁！
        if self.stage == "SELECTING" and not self.is_locked:
            self.lock_timer.stop()
            self._apply_stroke_selection_geometry(pos)

            # 从手指停下的这一瞬间起，重新倒数 500ms
            self.lock_timer.start(500)
            self.update()
            return

        # 3. 🌟 已锁定状态 (停顿满 0.5s 锁定后)：由锁定点向外进行纵向手势分流判定
        if self.is_locked:
            triage_dy = pos.y() - self.lock_anchor_y
            VERTICAL_THRESHOLD = 18

            # 🟢 向下滑动 ➔ 翡翠绿秒存
            if triage_dy > VERTICAL_THRESHOLD:
                self.stage = "TRIAGE_DOWN"

            # 🔴 向上滑动 ➔ 珊瑚红划线防误触
            elif triage_dy < -VERTICAL_THRESHOLD:
                self.stage = "TRIAGE_UP"

            # ⚪ 原位待命
            else:
                self.stage = "LOCKED"

            self.update()

    def _apply_stroke_selection_geometry(self, pos: QPoint) -> None:
        """统一执行圈选、拉框、横划、大括号与行感知连片选词计算，消除异步补词与滑选路径分歧。"""
        min_sx = self.min_sx
        max_sx = self.max_sx
        min_sy = self.min_sy
        max_sy = self.max_sy
        if min_sx is None or max_sx is None or min_sy is None or max_sy is None:
            return
        sw = max_sx - min_sx
        sh = max_sy - min_sy
        path_len = self.path_len

        lasso_selected = False
        if len(self.stroke_points) >= 2:
            start_pt = self.stroke_points[0]
            end_pt = self.stroke_points[-1]
            closing_dist = ((end_pt.x() - start_pt.x()) ** 2 + (end_pt.y() - start_pt.y()) ** 2) ** 0.5
            half_len = max(1, len(self.stroke_points) // 2)
            min_dist_to_head = min(
                ((end_pt.x() - p.x()) ** 2 + (end_pt.y() - p.y()) ** 2) ** 0.5
                for p in self.stroke_points[:half_len]
            )
            diag = (sw * sw + sh * sh) ** 0.5

            is_circle_loop = (
                len(self.stroke_points) >= 4
                and sw >= 18
                and sh >= 12
                and path_len >= 40
                and path_len >= 1.2 * diag
                and (
                    closing_dist <= 120
                    or closing_dist <= 0.35 * diag
                    or min_dist_to_head <= 100
                    or min_dist_to_head <= 0.35 * diag
                )
            )
            is_range_drag = sw >= 16 and sh >= 12 and self.initial_idx == -1
            is_straight_swipe = (
                sw >= 28
                and path_len >= 28
                and (self.initial_idx == -1 or sh <= 16)
                and (sw >= 2.0 * max(1, sh) or sh <= 16)
            )
            is_vertical_bracket = self._is_vertical_bracket(sw, sh, self.stroke_points)

            if is_circle_loop or is_range_drag or is_straight_swipe or is_vertical_bracket:
                lasso_selected = self._select_words_in_range(
                    min_sx,
                    min_sy,
                    max_sx,
                    max_sy,
                    stroke_points=self.stroke_points,
                )

        if not lasso_selected and self.initial_idx != -1 and self.all_words:
            self._update_reading_selection_to_point(pos)

    def _update_reading_selection_to_point(self, pos: QPoint):
        if self.initial_idx == -1 or not self.all_words:
            return
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        if (
            self._cached_lines_map is None
            or not self._cached_line_bounds
            or self._cached_words_sig != self._words_signature()
        ):
            self._recompute_lines_map()
        lines_map = self._cached_lines_map
        line_bounds = self._cached_line_bounds

        best_lid = None
        min_rank = (float("inf"), float("inf"), float("inf"))

        for lid, (min_y, max_y, _, _) in line_bounds.items():
            top = min_y / dpr
            bottom = max_y / dpr
            # 紧邻多行（如 2x DPI 视频字幕）容差带重叠时，必须按核心行框与垂直中心距离决出真实所在行
            pad_dy = (
                0.0
                if (top - 6) <= pos.y() <= (bottom + 6)
                else ((top - 6) - pos.y() if pos.y() < (top - 6) else pos.y() - (bottom + 6))
            )
            raw_dy = (
                0.0
                if top <= pos.y() <= bottom
                else (top - pos.y() if pos.y() < top else pos.y() - bottom)
            )
            center_dy = abs(pos.y() - (top + bottom) / 2.0)
            rank = (pad_dy, raw_dy, center_dy)

            if rank < min_rank:
                min_rank = rank
                best_lid = lid

        if best_lid is not None:
            chosen_items = lines_map[best_lid]
            _, _, min_x, max_x = line_bounds[best_lid]
            line_left = min_x / dpr
            line_right = max_x / dpr

            if pos.x() >= line_right - 6:
                closest_idx = chosen_items[-1][0]
            elif pos.x() <= line_left + 6:
                closest_idx = chosen_items[0][0]
            else:
                best_idx = chosen_items[0][0]
                min_dx = float("inf")
                for idx, w in chosen_items:
                    cx = (w["box"]["x"] + w["box"]["w"] / 2) / dpr
                    dx = abs(pos.x() - cx)
                    if dx < min_dx:
                        min_dx = dx
                        best_idx = idx
                closest_idx = best_idx
        else:
            closest_idx = self.initial_idx

        if closest_idx != self.current_idx:
            self.current_idx = closest_idx
            self.min_idx = min(self.initial_idx, closest_idx)
            self.max_idx = max(self.initial_idx, closest_idx)
            self._recalculate_multiline_selection()

    def _handle_pointer_up(self, pos: QPoint):
        self.dwell_timer.stop()
        self.lock_timer.stop()
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

        # A. 🌟 轻点单词查词 (Quick Tap 0ms 纯几何秒开，未超时未滑动的轻击)
        sw_tap = (self.max_sx - self.min_sx) if (self.max_sx is not None and self.min_sx is not None) else 0
        sh_tap = (self.max_sy - self.min_sy) if (self.max_sy is not None and self.min_sy is not None) else 0
        is_tap = (self.stage == "DWELLING") or (self.stage == "SELECTING" and not self.is_locked and sw_tap < 16 and sh_tap < 16 and self.path_len < 24)
        if is_tap:
            if not self.locked_word:
                if self.all_words:
                    matched_idx = self._find_word_at_phys(self.phys_start.x(), self.phys_start.y(), tolerance=22)
                    if matched_idx != -1:
                        self.initial_idx = matched_idx
                        self.current_idx = matched_idx
                        self.min_idx = matched_idx
                        self.max_idx = matched_idx
                        from .ocr_helpers import extract_sentence_from_words
                        self.context_sentence = extract_sentence_from_words(self.all_words, matched_idx)
                        self._recalculate_multiline_selection()
                elif not self.all_words:
                    # 极端竞态：后台预提取尚未回传，挂起由 _on_words_extracted 0ms 异步唤醒，并施加 650ms 超时兜底
                    self._pending_tap_pos = (self.phys_start.x(), self.phys_start.y(), self.touch_cur)
                    QTimer.singleShot(650, lambda s=self._session_id: self._resolve_pending_tap_timeout(s))
                    return

            if self.locked_word:
                pos = self.touch_cur
                selected_text = self.locked_word
                sentence = self.context_sentence
                self.close_freeze_mode()
                self.triage_open_dict.emit(selected_text, sentence, pos)
                return
            else:
                self.close_freeze_mode()
                return

        # B. 抬手兜底检测：存在明显的划线、圈选或框选轨迹，强制最终决算（使用已有词库 0ms 纯几何决算，杜绝同步阻塞卡顿）
        if len(self.stroke_points) >= 2:
            min_sx = min(p.x() for p in self.stroke_points)
            max_sx = max(p.x() for p in self.stroke_points)
            min_sy = min(p.y() for p in self.stroke_points)
            max_sy = max(p.y() for p in self.stroke_points)
            sw = max_sx - min_sx
            sh = max_sy - min_sy
            if sw >= 16 or sh >= 10:
                # 抬手几何决算：只要属于大括号手势、圈选或尚未选词，必须执行决算（杜绝词块起笔大括号被扼杀）
                is_bracket_up = self._is_vertical_bracket(sw, sh, self.stroke_points)
                if not self.locked_word or self.initial_idx == -1 or is_bracket_up:
                    self._select_words_in_range(min_sx, min_sy, max_sx, max_sy, stroke_points=self.stroke_points, force_crop_ocr=False)

        selected_text = self.locked_word
        sentence = self.context_sentence
        cur_stage = self.stage

        # C. 🌟 手写笔记按需识别兜底：
        # 若当前未命中任何已有词，但用户明确划过了长横线 (sw >= 28) 或画了闭合圈/大括号，
        # 绝不直接当作空白处点击而闪退！异步启动手写局部切片解析，识别成功后自动弹出词典抽屉！
        if not selected_text and len(self.stroke_points) >= 2:
            min_sx = min(p.x() for p in self.stroke_points)
            max_sx = max(p.x() for p in self.stroke_points)
            min_sy = min(p.y() for p in self.stroke_points)
            max_sy = max(p.y() for p in self.stroke_points)
            sw = max_sx - min_sx
            sh = max_sy - min_sy
            is_bracket = self._is_vertical_bracket(sw, sh, self.stroke_points)

            if sw >= 28 or self.path_len >= 32 or is_bracket:
                screen = QApplication.primaryScreen()
                dpr = screen.devicePixelRatio() if screen else 1.0
                pimg = self._get_pristine_image()
                if pimg:
                    pw, ph = pimg.size
                    pad_x = max(30, int(25 * dpr))
                    pad_y = max(30, int(25 * dpr))
                    if is_bracket:
                        pt_s = self.stroke_points[0]
                        pt_e = self.stroke_points[-1]
                        ends_avg_x = (pt_s.x() + pt_e.x()) / 2.0
                        is_open_right = ends_avg_x > (min_sx + 0.25 * sw)
                        if is_open_right:
                            crop_x1 = max(0, int(min_sx * dpr) - pad_x)
                            crop_x2 = min(pw, int(max_sx * dpr) + int(800 * dpr))
                        else:
                            crop_x1 = max(0, int(min_sx * dpr) - int(800 * dpr))
                            crop_x2 = min(pw, int(max_sx * dpr) + pad_x)
                    else:
                        crop_x1 = max(0, int(min_sx * dpr) - pad_x)
                        crop_x2 = min(pw, int(max_sx * dpr) + pad_x)
                    crop_y1 = max(0, int(min_sy * dpr) - pad_y)
                    crop_y2 = min(ph, int(max_sy * dpr) + pad_y)

                    if crop_x2 > crop_x1 and crop_y2 > crop_y1:
                        crop_slice = pimg.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                        target_pos = self.touch_cur

                        # 进入微光等待态，保留笔迹，绝不阻塞主 UI 线程
                        self.stage = "LOCKED"
                        self.pulse_glow = True
                        self.update()

                        cur_session = self._session_id
                        cur_req = self._hw_req_id
                        def _async_hw_recognize(session_id: int, req_id: int):
                            try:
                                # 优先检测暗描边亮色视频字幕（应对预提取竞态未就绪时的按需划选）
                                words = []
                                if win_ocr_engine.detect_outlined_subtitle_regions(crop_slice):
                                    words = win_ocr_engine.recognize_outlined_subtitle_words(crop_slice, crop_x1, crop_y1)
                                if not words:
                                    # 严格手写专精离线双探针管线，坚决剥离 WinRT 杜绝火星文假词
                                    words = win_ocr_engine.recognize_handwriting_words(crop_slice, crop_x1, crop_y1)
                                if words:
                                    from .ocr_helpers import sort_words_reading_order, process_ocr_tokens, extract_sentence_from_words
                                    words = sort_words_reading_order(words)
                                    words = process_ocr_tokens(words)
                                    # 必须包含真实 ASCII 英文单词，彻底杜绝 Unicode 汉字或符号乱码穿透 isalpha
                                    valid_words = [w for w in words if any(c.isascii() and c.isalpha() for c in w.get("clean", ""))]
                                    if valid_words:
                                        text_res = " ".join([w.get("clean", "") for w in valid_words])
                                        sent_res = extract_sentence_from_words(valid_words, 0)
                                        self.sig_handwriting_done.emit(text_res, sent_res, target_pos, session_id, req_id)
                                        return
                            except Exception:
                                pass
                            self.sig_handwriting_done.emit("", "", target_pos, session_id, req_id)

                        threading.Thread(target=_async_hw_recognize, args=(cur_session, cur_req), daemon=True).start()
                        return

        # D. 空白处释放或无有效选词：退出
        if cur_stage in ("IDLE", "NO_WORD") or not selected_text:
            self.close_freeze_mode()
            return

        # D. 🟢 向下滑动释放：0.1 秒秒存入生词本
        if cur_stage == "TRIAGE_DOWN":
            self.close_freeze_mode()
            self.triage_save.emit(selected_text, sentence)

        # E. 🔴 向上滑动释放：安全取消退出
        elif cur_stage == "TRIAGE_UP":
            self.close_freeze_mode()
            self.triage_cancel.emit()

        # F. ⚪ 原位抬手 / 圈选或多行选完直接松手：展开全功能词典抽屉
        else:
            pos = self.touch_cur
            self.close_freeze_mode()
            self.triage_open_dict.emit(selected_text, sentence, pos)

    def _recalculate_combined_box(self):
        self._recalculate_multiline_selection()

    def _recalculate_multiline_selection(self, selected_indices: Optional[List[int]] = None):
        """核心突破：支持跨行所有词块的多行聚类与高亮外框生成"""
        if not self.all_words:
            return

        if selected_indices is not None:
            if not selected_indices:
                return
            selected = [self.all_words[i] for i in selected_indices if 0 <= i < len(self.all_words)]
            self.min_idx = min(selected_indices)
            self.max_idx = max(selected_indices)
        else:
            if self.min_idx < 0 or self.max_idx >= len(self.all_words):
                return
            selected = self.all_words[self.min_idx : self.max_idx + 1]

        if not selected:
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        self.locked_word = " ".join([w.get("text", w.get("clean", "")) for w in selected])

        # 按行号 line_id 聚类生成每行的独立高亮圆角框
        lines_dict: Dict[int, List[Dict[str, Any]]] = {}
        for w in selected:
            lid = w.get("line_id", 0)
            if lid not in lines_dict:
                lines_dict[lid] = []
            lines_dict[lid].append(w)

        boxes = []
        for lid in sorted(lines_dict.keys()):
            lw_list = lines_dict[lid]
            min_bx = min(w["box"]["x"] for w in lw_list)
            min_by = min(w["box"]["y"] for w in lw_list)
            max_bx2 = max(w["box"]["x"] + w["box"]["w"] for w in lw_list)
            max_by2 = max(w["box"]["y"] + w["box"]["h"] for w in lw_list)

            lx = int(min_bx / dpr)
            ly = int(min_by / dpr)
            lw = max(12, int((max_bx2 - min_bx) / dpr))
            lh = max(12, int((max_by2 - min_by) / dpr))
            boxes.append(QRect(lx, ly, lw, lh))

        self.selected_boxes = boxes

    def _end_pulse(self):
        self.pulse_glow = False
        self.update()

    def _clear_no_word(self):
        self.stage = "IDLE"
        self.update()

    def close_freeze_mode(self):
        # 严格遵循 AGENTS.md §5.3：退出定格层时立即自增 _session_id，阻断任何在途异步线程回写或弹幽灵卡片
        self._session_id += 1
        self._hw_req_id += 1
        self.dwell_timer.stop()
        self.lock_timer.stop()
        self.pulse_timer.stop()
        self.fade_timer.stop()
        self.stage = "IDLE"
        self.background_pixmap = None
        with self._img_lock:
            self._bg_qimage = None
            self._bg_img_data = None
            self._bg_img_session_id = -1
            self.pristine_image = None
            self._pristine_session_id = -1
        self.locked_word = ""
        self.selected_boxes = []
        self.all_words = []
        self._pending_tap_pos = None
        self.stroke_points = []
        self.is_locked = False
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_freeze_mode()

    def _clamp_tip_rect(self, r: QRect, margin: int = 8) -> QRect:
        w = max(320, self.width())
        h = max(240, self.height())
        nx = max(margin, min(r.x(), w - r.width() - margin))
        ny = max(margin, min(r.y(), h - r.height() - margin))
        return QRect(nx, ny, r.width(), r.height())

    def _place_tip_rect(
        self,
        anchor_rect: QRect,
        tip_w: int,
        tip_h: int,
        prefer_below: bool = True,
        gap: int = 10,
        margin: int = 8,
    ) -> QRect:
        """自适应翻转提示条坐标：屏幕底边不足时自动上翻至选区上方，绝不遮挡所选单词或手写笔迹"""
        h = max(240, self.height())
        tx = anchor_rect.center().x() - tip_w // 2
        if prefer_below:
            ty_below = anchor_rect.bottom() + gap
            if ty_below + tip_h <= h - margin:
                ty = ty_below
            else:
                ty = anchor_rect.top() - gap - tip_h
        else:
            ty_above = anchor_rect.top() - gap - tip_h
            if ty_above >= margin:
                ty = ty_above
            else:
                ty = anchor_rect.bottom() + gap
        return self._clamp_tip_rect(QRect(tx, ty, tip_w, tip_h), margin=margin)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. 绘制高清 1:1 定格背景快照 + 极轻微高雅暗化
        if self.background_pixmap:
            painter.drawPixmap(0, 0, self.background_pixmap)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 18))

        # 顶部极简指示条（锐利 3px 微圆角 HUD）
        if self.stage in ("IDLE", "DWELLING"):
            bar_w = min(400, max(260, self.width() - 24))
            top_bar = self._clamp_tip_rect(QRect(self.width() // 2 - bar_w // 2, 16, bar_w, 28))
            draw_hud_bar(painter, top_bar, "滑选或圈选  ·  停顿 0.5s 锁定  ·  下滑秒存  ·  轻触空白退出")

        # 2. 长按初探反馈 (DWELLING 阶段)
        if self.stage == "DWELLING":
            center = self.touch_start
            painter.setPen(QPen(OVERLAY_COLORS["halo_light"], 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 22, 22)
            painter.setPen(QPen(OVERLAY_COLORS["navy_border"], 2))
            painter.setBrush(QBrush(QColor(26, 54, 93, 36)))
            painter.drawEllipse(center, 20, 20)

        # 3. 未检测到单词提示
        if self.stage == "NO_WORD":
            center = self.touch_cur if not self.touch_cur.isNull() else self.touch_start
            anchor = QRect(center.x() - 12, center.y() - 12, 24, 24)
            tip_rect = self._place_tip_rect(anchor, 136, 26, prefer_below=True, gap=14)
            draw_hud_bar(painter, tip_rect, "未检测到英文内容", fg=OVERLAY_COLORS["hud_text_muted"])

        # 3.5 触控/手写笔圈选轨迹渲染与按需手写识别等待态
        if len(self.stroke_points) >= 2 and (
            (self.stage == "SELECTING" and not self.is_locked)
            or (self.stage == "LOCKED" and not self.selected_boxes)
        ):
            is_hw_waiting = (self.stage == "LOCKED" and not self.selected_boxes)
            stroke_color = OVERLAY_COLORS["save_border"] if is_hw_waiting else OVERLAY_COLORS["navy_stroke"]
            stroke_pen = QPen(stroke_color, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(stroke_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            from PySide6.QtGui import QPolygon
            painter.drawPolyline(QPolygon(self.stroke_points))

            min_sx = self.min_sx if self.min_sx is not None else min(p.x() for p in self.stroke_points)
            max_sx = self.max_sx if self.max_sx is not None else max(p.x() for p in self.stroke_points)
            min_sy = self.min_sy if self.min_sy is not None else min(p.y() for p in self.stroke_points)
            max_sy = self.max_sy if self.max_sy is not None else max(p.y() for p in self.stroke_points)

            if is_hw_waiting:
                stroke_anchor = QRect(min_sx, min_sy, max(1, max_sx - min_sx), max(1, max_sy - min_sy))
                hw_tip = self._place_tip_rect(stroke_anchor, 168, 28, prefer_below=True, gap=12)
                draw_hud_bar(
                    painter,
                    hw_tip,
                    "正在识别手写笔记...",
                    border=OVERLAY_COLORS["save_hud_border"],
                    fg=OVERLAY_COLORS["hud_text_save"],
                    bold=True,
                )
            elif self.initial_idx == -1 and not self.selected_boxes:
                if (max_sx - min_sx) >= 16 and (max_sy - min_sy) >= 12:
                    drag_rect = QRect(min_sx, min_sy, max_sx - min_sx, max_sy - min_sy)
                    painter.setPen(QPen(QColor(26, 54, 93, 150), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(QBrush(QColor(26, 54, 93, 24)))
                    painter.drawRoundedRect(drag_rect, 2, 2)

        # 4. 多行选区高光框渲染（2px 锐利微圆角）
        if self.selected_boxes and self.stage not in ("IDLE", "DWELLING", "NO_WORD"):
            pad_x = 6
            pad_y = 3
            first_box = self.selected_boxes[0]
            last_box = self.selected_boxes[-1]
            span_anchor = QRect(
                last_box.center().x() - 12,
                first_box.top() - pad_y,
                24,
                max(12, (last_box.bottom() + pad_y) - (first_box.top() - pad_y)),
            )

            # A. 选词扩选中 (SELECTING - 停顿满0.5秒前)
            if self.stage == "SELECTING":
                halo_pen = QPen(OVERLAY_COLORS["halo_light"], 1)
                painter.setPen(halo_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for b in self.selected_boxes:
                    painter.drawRoundedRect(b.adjusted(-pad_x - 1, -pad_y - 1, pad_x + 1, pad_y + 1), 2, 2)

                painter.setPen(QPen(OVERLAY_COLORS["navy_border"], 2))
                painter.setBrush(QBrush(OVERLAY_COLORS["navy_fill"]))
                for b in self.selected_boxes:
                    painter.drawRoundedRect(b.adjusted(-pad_x, -pad_y, pad_x, pad_y), 2, 2)

                tip_rect = self._place_tip_rect(span_anchor, 196, 26, prefer_below=True, gap=10)
                draw_hud_bar(painter, tip_rect, "滑选多行或圈选  ·  停顿 0.5s 锁定")

            # B. 向下滑动 -> 沉稳墨绿秒存 (TRIAGE_DOWN)
            elif self.stage == "TRIAGE_DOWN":
                painter.setPen(QPen(OVERLAY_COLORS["save_border"], 2))
                painter.setBrush(QBrush(OVERLAY_COLORS["save_fill"]))
                for b in self.selected_boxes:
                    painter.drawRoundedRect(b.adjusted(-pad_x, -pad_y, pad_x, pad_y), 2, 2)

                cur_anchor = QRect(self.touch_cur.x() - 12, min(first_box.top() - pad_y, self.touch_cur.y() - 12), 24, max(24, max(last_box.bottom() + pad_y, self.touch_cur.y() + 12) - min(first_box.top() - pad_y, self.touch_cur.y() - 12)))
                tip_rect = self._place_tip_rect(cur_anchor, 148, 30, prefer_below=True, gap=16)
                draw_hud_bar(
                    painter,
                    tip_rect,
                    "松手加入生词本",
                    bg=OVERLAY_COLORS["save_hud_bg"],
                    border=OVERLAY_COLORS["save_hud_border"],
                    fg=QColor(255, 255, 255),
                    font_size=10,
                    bold=True,
                )

            # C. 向上滑动 -> 学院深红划线取消 (TRIAGE_UP)
            elif self.stage == "TRIAGE_UP":
                pen = QPen(OVERLAY_COLORS["cancel_border"], 2)
                painter.setPen(pen)
                painter.setBrush(QBrush(OVERLAY_COLORS["cancel_fill"]))
                for b in self.selected_boxes:
                    adj = b.adjusted(-pad_x, -pad_y, pad_x, pad_y)
                    painter.drawRoundedRect(adj, 2, 2)
                    painter.setPen(QPen(OVERLAY_COLORS["cancel_strike"], 2))
                    painter.drawLine(adj.left(), adj.center().y(), adj.right(), adj.center().y())
                    painter.setPen(pen)

                cur_anchor = QRect(self.touch_cur.x() - 12, min(first_box.top() - pad_y, self.touch_cur.y() - 12), 24, max(24, max(last_box.bottom() + pad_y, self.touch_cur.y() + 12) - min(first_box.top() - pad_y, self.touch_cur.y() - 12)))
                tip_rect = self._place_tip_rect(cur_anchor, 112, 30, prefer_below=False, gap=16)
                draw_hud_bar(
                    painter,
                    tip_rect,
                    "松手取消",
                    bg=OVERLAY_COLORS["cancel_hud_bg"],
                    border=OVERLAY_COLORS["cancel_hud_border"],
                    fg=QColor(255, 255, 255),
                    font_size=10,
                    bold=True,
                )

            # D. 已锁定状态 (LOCKED - 停顿满0.5秒后)
            else:
                border_color = QColor(255, 255, 255, 245) if self.pulse_glow else OVERLAY_COLORS["navy_border"]
                if not self.pulse_glow:
                    painter.setPen(QPen(OVERLAY_COLORS["halo_light"], 1))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    for b in self.selected_boxes:
                        painter.drawRoundedRect(b.adjusted(-pad_x - 1, -pad_y - 1, pad_x + 1, pad_y + 1), 2, 2)
                painter.setPen(QPen(border_color, 2))
                painter.setBrush(QBrush(OVERLAY_COLORS["navy_fill_locked"]))
                for b in self.selected_boxes:
                    painter.drawRoundedRect(b.adjusted(-pad_x, -pad_y, pad_x, pad_y), 2, 2)

                lock_anchor = QRect(
                    self.touch_cur.x() - 12,
                    min(first_box.top() - pad_y, self.touch_cur.y() - 12),
                    24,
                    max(24, max(last_box.bottom() + pad_y, self.touch_cur.y() + 12) - min(first_box.top() - pad_y, self.touch_cur.y() - 12)),
                )
                tip_rect = self._place_tip_rect(lock_anchor, 228, 28, prefer_below=True, gap=14)
                draw_triage_split_hud(painter, tip_rect)
