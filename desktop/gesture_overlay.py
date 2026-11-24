import ctypes
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QPoint, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from typing import Optional, List, Dict, Any
from .ui_icons import OVERLAY_COLORS, draw_hud_bar, draw_triage_split_hud, draw_vector_icon

class ScreenGestureOverlay(QWidget):
    """
    全屏高刷透明交互层 (120Hz/165Hz 原生跟手视觉反馈)
    严格实现 PROJECT_SPEC.md 规范的 6 态指尖即时分流：
    - DWELL_LOCKED: 抓词锁定，光学胶囊吸附
    - HORIZ_SELECT: 左右滑选手势连词成句
    - TRIAGE_DOWN: 向下滑动 (dy > +28px) 翡翠绿高亮与 ✦ 收藏角标
    - TRIAGE_UP: 向上滑动 (dy < -28px) 珊瑚红删除划线
    - RELEASE: 原位抬手展开全功能词典抽屉
    """
    triage_save = Signal(str, str)              # (selected_text, sentence)
    triage_open_dict = Signal(str, str, QPoint) # (selected_text, sentence, pos)
    triage_cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

        self.stage = "IDLE"  # IDLE | LOCKED | HORIZ_SELECT | TRIAGE_DOWN | TRIAGE_UP | NO_WORD
        self.touch_start = QPoint()
        self.touch_cur = QPoint()
        self.phys_start_x = 0
        self.phys_start_y = 0

        # 多词选区与连词成句状态
        self.line_words: List[Dict[str, Any]] = []
        self.initial_idx: int = 0
        self.current_idx: int = 0
        self.min_idx: int = 0
        self.max_idx: int = 0
        self.pulse_glow: bool = False

        self.locked_word: str = ""
        self.locked_box: Optional[QRect] = None
        self.context_sentence: str = ""

        # 锁定微光脉冲定时器
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setSingleShot(True)
        self.pulse_timer.timeout.connect(self._end_pulse)

        # 无词提示自动淡出定时器
        self.fade_timer = QTimer(self)
        self.fade_timer.setSingleShot(True)
        self.fade_timer.timeout.connect(self.hide_overlay)

    def show_locked(self, phys_x: int, phys_y: int, line_words: List[Dict[str, Any]], initial_idx: int, sentence: str):
        """长按静止满 250ms 时被唤起并定位锁词"""
        self.fade_timer.stop()
        self.pulse_timer.stop()

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        lx = int(phys_x / dpr)
        ly = int(phys_y / dpr)

        self.phys_start_x = phys_x
        self.phys_start_y = phys_y
        self.touch_start = QPoint(lx, ly)
        self.touch_cur = QPoint(lx, ly)

        self.line_words = line_words
        self.initial_idx = max(0, min(initial_idx, len(line_words) - 1)) if line_words else 0
        self.current_idx = self.initial_idx
        self.min_idx = self.initial_idx
        self.max_idx = self.initial_idx
        self.context_sentence = sentence

        self._recalculate_combined_box()
        self.stage = "LOCKED"
        self.pulse_glow = True
        self.pulse_timer.start(200)

        geom = screen.geometry() if screen else QRect(0, 0, 1280, 800)
        self.setGeometry(geom)
        self.show()
        self.raise_()
        self.update()

    def show_no_word_hint(self, phys_x: int, phys_y: int):
        """未命中单词时的轻柔视觉提示"""
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        lx = int(phys_x / dpr)
        ly = int(phys_y / dpr)

        self.touch_start = QPoint(lx, ly)
        self.touch_cur = QPoint(lx, ly)
        self.stage = "NO_WORD"
        self.locked_word = ""
        self.locked_box = None

        geom = screen.geometry() if screen else QRect(0, 0, 1280, 800)
        self.setGeometry(geom)
        self.show()
        self.raise_()
        self.update()
        self.fade_timer.start(750)

    def handle_touch_move(self, cur_phys_x: int, cur_phys_y: int, dx: int, dy: int):
        """实时接收指尖移动，进行 120Hz 跟手分流计算"""
        if self.stage in ("IDLE", "NO_WORD"):
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        lx = int(cur_phys_x / dpr)
        ly = int(cur_phys_y / dpr)
        self.touch_cur = QPoint(lx, ly)

        logical_dx = dx / dpr
        logical_dy = dy / dpr

        VERTICAL_THRESHOLD = 18

        # 1. 优先判定大角度纵向意图分流
        if logical_dy > VERTICAL_THRESHOLD:
            self.stage = "TRIAGE_DOWN"
        elif logical_dy < -VERTICAL_THRESHOLD:
            self.stage = "TRIAGE_UP"
        else:
            # 2. 纵向处于水平平静区 (|dy| <= 18px)：判定是否进行横向扩选连词
            if abs(logical_dx) > 14 and self.line_words:
                self.stage = "HORIZ_SELECT"
                # 寻找距离当前触点横向 X 最近的词块
                closest_idx = self.initial_idx
                min_dist = float("inf")
                for i, w in enumerate(self.line_words):
                    cx = (w["box"]["x"] + w["box"]["w"] / 2) / dpr
                    dist = abs(lx - cx)
                    if dist < min_dist:
                        min_dist = dist
                        closest_idx = i

                if closest_idx != self.current_idx:
                    self.current_idx = closest_idx
                    self.min_idx = min(self.initial_idx, closest_idx)
                    self.max_idx = max(self.initial_idx, closest_idx)
                    self._recalculate_combined_box()
            else:
                self.stage = "LOCKED"

        self.update()

    def handle_touch_release(self, cur_phys_x: int, cur_phys_y: int, dx: int, dy: int):
        """指尖离开屏幕时，坚决执行分流意图"""
        if self.stage == "IDLE":
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        logical_dy = dy / dpr
        selected_text = self.locked_word
        sentence = self.context_sentence
        cur_stage = self.stage
        pos = self.touch_cur

        self.hide_overlay()

        if not selected_text:
            self.triage_cancel.emit()
            return

        if cur_stage == "TRIAGE_DOWN" or logical_dy > 18:
            # 🟢 向下滑动释放：秒存入库
            self.triage_save.emit(selected_text, sentence)
        elif cur_stage == "TRIAGE_UP" or logical_dy < -18:
            # 🔴 向上滑动释放：安全取消
            self.triage_cancel.emit()
        else:
            # ⚪ 原位自然抬手：展开全功能词典抽屉
            self.triage_open_dict.emit(selected_text, sentence, pos)

    def _recalculate_combined_box(self):
        """计算 min_idx ~ max_idx 所覆盖的复合高亮包围盒"""
        if not self.line_words or self.min_idx < 0 or self.max_idx >= len(self.line_words):
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        selected = self.line_words[self.min_idx : self.max_idx + 1]
        self.locked_word = " ".join([w["clean"] for w in selected])

        min_bx = min(w["box"]["x"] for w in selected)
        min_by = min(w["box"]["y"] for w in selected)
        max_bx2 = max(w["box"]["x"] + w["box"]["w"] for w in selected)
        max_by2 = max(w["box"]["y"] + w["box"]["h"] for w in selected)

        lx = int(min_bx / dpr)
        ly = int(min_by / dpr)
        lw = max(12, int((max_bx2 - min_bx) / dpr))
        lh = max(12, int((max_by2 - min_by) / dpr))

        self.locked_box = QRect(lx, ly, lw, lh)

    def _end_pulse(self):
        self.pulse_glow = False
        self.update()

    def hide_overlay(self):
        self.pulse_timer.stop()
        self.fade_timer.stop()
        self.stage = "IDLE"
        self.locked_word = ""
        self.locked_box = None
        self.line_words = []
        self.hide()

    def _clamp_tip_rect(self, r: QRect, margin: int = 8) -> QRect:
        w = self.width() if self.width() > 0 else 320
        h = self.height() if self.height() > 0 else 240
        nx = max(margin, min(r.x(), max(margin, w - r.width() - margin)))
        ny = max(margin, min(r.y(), max(margin, h - r.height() - margin)))
        return QRect(nx, ny, r.width(), r.height())

    def _place_tip_rect(self, anchor: QRect, tip_w: int, tip_h: int, gap: int = 10, prefer_below: bool = True, margin: int = 8) -> QRect:
        """智能上下翻转避让：底部空间不足时自动翻转至目标选区上方，严防提示胶囊盖死目标单词。"""
        h = self.height() if self.height() > 0 else 240
        tx = anchor.center().x() - tip_w // 2
        below_y = anchor.bottom() + gap
        above_y = anchor.top() - tip_h - gap
        if prefer_below:
            ty = above_y if (below_y + tip_h > h - margin and above_y >= margin) else below_y
        else:
            ty = below_y if (above_y < margin and below_y + tip_h <= h - margin) else above_y
        return self._clamp_tip_rect(QRect(tx, ty, tip_w, tip_h), margin=margin)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. 未命中单词提示
        if self.stage == "NO_WORD":
            center = self.touch_start
            painter.setPen(QPen(QColor(255, 255, 255, 80), 1.5))
            painter.setBrush(QBrush(QColor(24, 24, 27, 175)))
            painter.drawEllipse(center, 18, 18)

            anchor = QRect(center.x() - 18, center.y() - 18, 36, 36)
            tip_rect = self._place_tip_rect(anchor, 136, 26, gap=6, prefer_below=True)
            draw_hud_bar(painter, tip_rect, "未检测到英文单词", fg=OVERLAY_COLORS["hud_text_muted"])
            return

        if not self.locked_box or self.stage == "IDLE":
            return

        pad = 4
        box = self.locked_box.adjusted(-pad, -pad, pad, pad)

        # 2. 左右滑选连词态 (HORIZ_SELECT)
        if self.stage == "HORIZ_SELECT":
            painter.setPen(QPen(OVERLAY_COLORS["halo_light"], 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 2, 2)

            painter.setPen(QPen(OVERLAY_COLORS["navy_border"], 2))
            painter.setBrush(QBrush(OVERLAY_COLORS["navy_fill"]))
            painter.drawRoundedRect(box, 2, 2)

            tip_rect = self._place_tip_rect(box, 168, 26, gap=10, prefer_below=True)
            draw_hud_bar(painter, tip_rect, "左右滑选连词成句")

        # 3. 向下滑动 -> 沉稳墨绿秒存 (TRIAGE_DOWN)
        elif self.stage == "TRIAGE_DOWN":
            painter.setPen(QPen(OVERLAY_COLORS["save_border"], 2))
            painter.setBrush(QBrush(OVERLAY_COLORS["save_fill"]))
            painter.drawRoundedRect(box, 2, 2)

            draw_vector_icon(
                painter,
                "bookmark_on",
                QRectF(box.right() - 10, box.top() - 16, 14, 14),
                OVERLAY_COLORS["save_border"],
                stroke_width=1.4,
            )

            touch_anchor = QRect(self.touch_cur.x() - 12, min(box.top(), self.touch_cur.y() - 12), 24, max(24, max(box.bottom(), self.touch_cur.y() + 12) - min(box.top(), self.touch_cur.y() - 12)))
            tip_rect = self._place_tip_rect(touch_anchor, 148, 30, gap=14, prefer_below=True)
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

        # 4. 向上滑动 -> 学院深红划线取消 (TRIAGE_UP)
        elif self.stage == "TRIAGE_UP":
            painter.setPen(QPen(OVERLAY_COLORS["cancel_border"], 2))
            painter.setBrush(QBrush(OVERLAY_COLORS["cancel_fill"]))
            painter.drawRoundedRect(box, 2, 2)

            painter.setPen(QPen(OVERLAY_COLORS["cancel_strike"], 2))
            painter.drawLine(box.left(), box.center().y(), box.right(), box.center().y())

            touch_anchor = QRect(self.touch_cur.x() - 12, min(box.top(), self.touch_cur.y() - 12), 24, max(24, max(box.bottom(), self.touch_cur.y() + 12) - min(box.top(), self.touch_cur.y() - 12)))
            tip_rect = self._place_tip_rect(touch_anchor, 112, 30, gap=14, prefer_below=False)
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

        # 5. 抓词锁定态 (LOCKED)
        else:
            border_color = QColor(255, 255, 255, 245) if self.pulse_glow else OVERLAY_COLORS["navy_border"]
            if not self.pulse_glow:
                painter.setPen(QPen(OVERLAY_COLORS["halo_light"], 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 2, 2)

            painter.setPen(QPen(border_color, 2))
            painter.setBrush(QBrush(OVERLAY_COLORS["navy_fill_locked"]))
            painter.drawRoundedRect(box, 2, 2)

            tip_rect = self._place_tip_rect(box, 228, 28, gap=12, prefer_below=True)
            draw_triage_split_hud(painter, tip_rect)
