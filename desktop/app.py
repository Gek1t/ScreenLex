import sys
import os
import ctypes

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
try:
    os.chdir(project_root)
except Exception:
    pass

# 优先添加 onnxruntime 的 CAPI 目录并预载，杜绝 Windows 跨 DLL 初始化例程符号冲突（兼容 PyInstaller 冻结态）
try:
    _ort_cands = []
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _ort_cands.append(os.path.join(_meipass, "onnxruntime", "capi"))
    _ort_cands.append(os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "onnxruntime", "capi"))
    try:
        import site
        for sp in site.getsitepackages():
            _ort_cands.append(os.path.join(sp, "onnxruntime", "capi"))
    except Exception:
        pass
    for dll_dir in _ort_cands:
        if os.path.exists(dll_dir) and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)
            break
    import onnxruntime
except Exception:
    pass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtCore import Qt, QPoint, QObject, Signal

from .floating_dock import FloatingZenPill
from .freeze_overlay import FreezeSelectOverlay
from .gesture_overlay import ScreenGestureOverlay
from .dictionary_window import DictionaryCard
from .vault_window import VocabularyVaultWindow
from .subtitle_window import BilingualSubtitleWindow
from .subtitle_engine import LiveSubtitleEngine
from .settings_window import SettingsWindow
from .database import init_db, save_word, update_enrichment_if_collected, get_setting, set_setting
from .input_hook import GlobalGestureEngine, DEFAULT_HOTKEYS
from .dictionary_service import lookup_fast, enrich_lookup_sync, prewarm_service, LOADING_PLACEHOLDERS
from .offline_dict import close_offline_dict
from .logger import setup_logging, get_logger
from .win_ocr import win_ocr_engine
from .ui_icons import (
    EditorialPopupMenu, LEXICON_THEME, normalize_win32_process_priority, enforce_window_topmost
)
import threading
import winreg

_log = get_logger("app")
HOTKEYS_SETTINGS_KEY = "hotkeys"


class _AppUiBridge(QObject):
    vault_refresh_requested = Signal()
    point_lookup_ready = Signal(int, bool, int, int, object)

REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "ScreenLex"

def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_REG_NAME)
            return bool(val)
    except Exception:
        return False

def set_autostart(enabled: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
            if enabled:
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                else:
                    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "run_desktop.py"))
                    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                    if not os.path.exists(pythonw):
                        pythonw = sys.executable
                    cmd = f'"{pythonw}" "{script_path}"'
                winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_REG_NAME)
                except FileNotFoundError:
                    pass
            return True
    except Exception as e:
        _log.warning("Failed to toggle autostart: %s", e)
        return False

def create_tray_icon():
    ico_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenlex.ico")
    if getattr(sys, 'frozen', False):
        frozen_ico = os.path.join(os.path.dirname(sys.executable), "screenlex.ico")
        if os.path.exists(frozen_ico):
            ico_path = frozen_ico

    if os.path.exists(ico_path):
        return QIcon(ico_path)

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setBrush(QColor(LEXICON_THEME["text"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRect(2, 2, 28, 28)

    painter.setPen(QColor("#F9F8F6"))
    painter.setFont(QFont("Georgia", 11, QFont.Weight.Normal))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "SL")
    painter.end()
    return QIcon(pixmap)

class ScreenLexApplication:
    """
    ScreenLex 屏幕实时取词主应用
    针对 Windows 11 触控平板与全英文学术课堂双模驱动：
    1. 触控平板模式：屏幕右侧边缘驻留【灵动微胶囊 (Zen Edge Pill)】，大拇指轻碰即定格，轻点单词释义，长按下滑秒存；
    2. 全局长按模式：鼠标左键 / 手写笔在任意界面长按 220ms 即时光学分流；
    3. 全局辅助热键：随时按 F8 或点击托盘快速提取单词。
    """
    def __init__(self):
        normalize_win32_process_priority()
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 统一全局字体族：英文优先使用 Windows 11 Segoe UI Variable Text，中文平滑回退 Microsoft YaHei UI
        global_font = QFont()
        global_font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
        global_font.setPointSize(10)
        global_font.setStyleHint(QFont.StyleHint.SansSerif)
        global_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.app.setFont(global_font)
        self.app.setStyleSheet(
            'QToolTip { background-color: #1C1C1C; color: #F9F8F6; border: 1px solid rgba(249, 248, 246, 48); '
            'border-radius: 0px; padding: 5px 9px; font-family: "Segoe UI Variable Text", "Microsoft YaHei UI"; font-size: 12px; }'
        )

        setup_logging()
        _log.info("ScreenLex starting up")
        init_db()

        # 核心交互与视窗组件
        self.freeze_overlay = FreezeSelectOverlay()
        self.gesture_overlay = ScreenGestureOverlay()
        self.overlay = self.gesture_overlay
        self.dict_card = DictionaryCard()
        self.vault_window = VocabularyVaultWindow(on_word_clicked=self._on_vault_word_clicked)
        self.subtitle_window = BilingualSubtitleWindow()
        self.subtitle_engine = LiveSubtitleEngine(self.app)
        self.settings_window = SettingsWindow()
        self._dict_opened_from_subtitle = False
        self._subtitle_was_visible_before_freeze = False
        self.dict_card.on_closed = self._on_dict_card_closed

        # 屏幕边缘极简小胶囊（为平板量身打造，支持左右贴边记忆）
        self._quitting = False
        self._quit_done = False
        self._activating_freeze = False
        self.floating_pill = FloatingZenPill()
        self.floating_pill.triggered.connect(self._handle_pill_triggered)
        self.floating_pill.open_vault.connect(self._open_vault)
        self.floating_pill.toggle_subtitle.connect(self._toggle_subtitle_mode)
        self.floating_pill.open_settings.connect(self._open_settings)
        self.floating_pill.position_changed.connect(self._on_dock_position_changed)
        self.floating_pill.show()

        # 联动生词本同步与后台线程安全 UI 信号桥
        self._ui_bridge = _AppUiBridge(self.app)
        self._ui_bridge.vault_refresh_requested.connect(self.vault_window.refresh_list)
        self._point_lookup_seq = 0
        self._ui_bridge.point_lookup_ready.connect(self._on_point_lookup_ready)
        self.dict_card.collection_changed.connect(lambda w, s: self.vault_window.refresh_list())
        self.dict_card.vault_sync_needed.connect(self.vault_window.refresh_list)
        self.vault_window.word_removed.connect(self.dict_card.sync_word_uncollected)
        self.vault_window.word_restored.connect(self.dict_card.sync_word_uncollected)

        # 联动设置窗口（热键配置 / 停靠微柄 / 开机自启）
        self.settings_window.hotkeys_changed.connect(self._on_hotkeys_changed)
        self.settings_window.dock_side_changed.connect(self._on_settings_dock_side_changed)
        self.settings_window.dock_reset_requested.connect(self._on_settings_dock_reset)
        self.settings_window.autostart_toggled.connect(self._on_settings_autostart_toggled)

        # 联动实时双语字幕引擎与双语悬浮字幕舱
        self.subtitle_engine.subtitle_updated.connect(self.subtitle_window.update_subtitle_data)
        self.subtitle_engine.transcript_loaded.connect(self.subtitle_window.set_transcript_loaded)
        self.subtitle_engine.transcript_error.connect(self.subtitle_window.set_transcript_error)
        self.subtitle_engine.bilibili_login_needed.connect(self.subtitle_window.set_bilibili_login_hint)
        self.subtitle_engine.status_notice.connect(self.subtitle_window.set_status_notice)
        self.subtitle_window.link_submitted.connect(self.subtitle_engine.load_youtube_url)
        self.subtitle_window.bilibili_auth_changed.connect(self.subtitle_engine.retry_bilibili_after_login)
        self.subtitle_window.switch_to_ocr_requested.connect(self.subtitle_engine.switch_to_ocr_mode)
        self.subtitle_window.custom_region_selected.connect(self._on_custom_subtitle_region_selected)
        self.subtitle_window.custom_region_cleared.connect(self.subtitle_engine.clear_custom_region)
        self.subtitle_window.clear_requested.connect(self.subtitle_engine.clear_history)
        self.subtitle_window.transcript_item_activated.connect(self.subtitle_engine.set_transcript_index)
        self.subtitle_window.seek_relative_requested.connect(self.subtitle_engine.seek_relative)
        self.subtitle_window.word_clicked.connect(self._on_subtitle_word_clicked)
        self.subtitle_window.closed.connect(self._on_subtitle_closed)

        # 绑定定格交互层分流意图与生命周期（定格层活跃时静默全局钩子，关闭后恢复胶囊与钩子）
        self.freeze_overlay.triage_save.connect(self._on_triage_save)
        self.freeze_overlay.triage_open_dict.connect(self._on_triage_open_dict)
        self.freeze_overlay.triage_cancel.connect(self._on_triage_cancel)
        self.freeze_overlay.closed.connect(self._on_freeze_closed)

        # 绑定透明交互层分流意图
        self.gesture_overlay.triage_save.connect(self._on_triage_save)
        self.gesture_overlay.triage_open_dict.connect(self._on_triage_open_dict)
        self.gesture_overlay.triage_cancel.connect(self._on_triage_cancel)

        # 初始化全局热键引擎 (禁用非截图状态下的全局鼠标左键长按识别，杜绝误触与系统级鼠标拦截；支持 4 组自定义全局热键)
        saved_hotkeys = get_setting(HOTKEYS_SETTINGS_KEY, None)
        merged_hotkeys = dict(DEFAULT_HOTKEYS)
        if isinstance(saved_hotkeys, dict):
            merged_hotkeys.update(saved_hotkeys)
        self.engine = GlobalGestureEngine(
            dwell_ms=220,
            jitter_radius=80,
            enable_mouse_hook=False,
            hotkeys_config=merged_hotkeys,
        )
        self.engine.action_hotkey_triggered.connect(self._on_action_hotkey_triggered)
        self.engine.hotkeys_registration_updated.connect(self.settings_window.set_hotkey_feedback)
        self.engine.start()

        # 托盘系统
        self.tray = QSystemTrayIcon(create_tray_icon(), self.app)
        self._init_tray_menu()
        self.tray.show()

        # 后台静默预热 OCR 引擎与网络链路（延后 1000ms 启动，确保应用窗口与托盘完全就绪后再在后台低优先级预热，避免启动竞争）
        def _prewarm_all():
            try:
                from PIL import Image
                dummy_img = Image.new('RGB', (100, 100), (255, 255, 255))
                import asyncio
                asyncio.run(win_ocr_engine._recognize_winrt_async(dummy_img, 0, 0, 50, 50))
            except Exception:
                pass
            try:
                prewarm_service()
            except Exception:
                pass

        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, lambda: threading.Thread(target=_prewarm_all, daemon=True).start())

        # Windows 开机自启早期阶段：Explorer / 任务栏 / 通知横幅尚未全部就绪，分阶段补强停靠微柄置顶与贴边
        for _delay_ms in (1500, 4500, 10000):
            QTimer.singleShot(_delay_ms, self._reassert_pill_topmost_after_boot)

        self.tray.showMessage(
            "ScreenLex 已就绪",
            "轻触屏幕边缘停靠微柄即可定格屏幕取词。",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )

    def _reassert_pill_topmost_after_boot(self):
        if not self._quitting and self.floating_pill and self.floating_pill.isVisible():
            self.floating_pill.ensure_topmost_and_positioned()

    def _init_tray_menu(self):
        menu = EditorialPopupMenu()
        self.tray_menu = menu

        freeze_action = menu.addAction("屏幕取词")
        freeze_action.triggered.connect(self._handle_pill_triggered)

        self.subtitle_action = menu.addAction("实时双语字幕")
        self.subtitle_action.setCheckable(True)
        self.subtitle_action.setChecked(False)
        self.subtitle_action.triggered.connect(self._toggle_subtitle_mode)

        ocr_region_action = menu.addAction("框选视频字幕区 (OCR)")
        ocr_region_action.triggered.connect(self._trigger_custom_subtitle_region)

        vault_action = menu.addAction("课堂生词本")
        vault_action.triggered.connect(self._open_vault)

        settings_action = menu.addAction("设置")
        settings_action.triggered.connect(self._open_settings)

        menu.addSeparator()

        self.autostart_action = menu.addAction("开机自启动")
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(is_autostart_enabled())
        self.autostart_action.triggered.connect(self._on_toggle_autostart)

        menu.addSeparator()
        quit_action = menu.addAction("退出程序")
        quit_action.triggered.connect(self._quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)

    def _on_toggle_autostart(self, checked: bool):
        success = set_autostart(checked)
        if not success:
            self.autostart_action.setChecked(not checked)
        else:
            if hasattr(self, "settings_window") and self.settings_window is not None:
                self.settings_window.autostart_cb.blockSignals(True)
                self.settings_window.autostart_cb.setChecked(checked)
                self.settings_window.autostart_cb.blockSignals(False)
            msg = "已开启开机自启动" if checked else "已关闭开机自启动"
            self.tray.showMessage("ScreenLex", msg, QSystemTrayIcon.MessageIcon.Information, 2500)

    def _on_settings_autostart_toggled(self, checked: bool):
        success = set_autostart(checked)
        if not success:
            self.settings_window.autostart_cb.blockSignals(True)
            self.settings_window.autostart_cb.setChecked(not checked)
            self.settings_window.autostart_cb.blockSignals(False)
        elif hasattr(self, "autostart_action"):
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(checked)
            self.autostart_action.blockSignals(False)

    def _on_hotkeys_changed(self, cfg: dict):
        set_setting(HOTKEYS_SETTINGS_KEY, cfg)
        self.engine.update_hotkeys(cfg)
        _log.info("Hotkeys updated: %s", cfg)

    def _on_settings_dock_side_changed(self, side: str):
        if self.floating_pill:
            self.floating_pill.set_dock_side(side)

    def _on_settings_dock_reset(self):
        if self.floating_pill:
            self.floating_pill.reset_position()
            self.settings_window.sync_state(
                hotkeys=self.engine.get_hotkeys_config(),
                dock_side=self.floating_pill.dock_side,
                autostart=is_autostart_enabled(),
                failed_actions=self.engine.get_failed_hotkey_actions(),
            )

    def _on_dock_position_changed(self, side: str, _y_ratio: float):
        if hasattr(self, "settings_window") and self.settings_window.isVisible():
            self.settings_window.sync_state(
                hotkeys=self.engine.get_hotkeys_config(),
                dock_side=side,
                autostart=is_autostart_enabled(),
                failed_actions=self.engine.get_failed_hotkey_actions(),
            )

    def _open_settings(self):
        self.settings_window.sync_state(
            hotkeys=self.engine.get_hotkeys_config(),
            dock_side=self.floating_pill.dock_side if self.floating_pill else "right",
            autostart=is_autostart_enabled(),
            failed_actions=self.engine.get_failed_hotkey_actions(),
        )
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            self.settings_window.adjustSize()
            sw = self.settings_window.width()
            sh = max(380, self.settings_window.sizeHint().height())
            self.settings_window.move(avail.center().x() - sw // 2, avail.center().y() - sh // 2)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_action_hotkey_triggered(self, action: str, cur_x: int, cur_y: int):
        """分发全部 4 组全局快捷键至对应核心功能。"""
        if self._quitting:
            return
        if action == "point_lookup":
            self._on_engine_hotkey_triggered(cur_x, cur_y)
        elif action == "freeze_capture":
            self._handle_pill_triggered()
        elif action == "toggle_subtitle":
            self._toggle_subtitle_mode()
        elif action == "select_ocr_region":
            self._trigger_custom_subtitle_region()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._handle_pill_triggered()

    def _on_freeze_closed(self):
        self.engine.set_active(True)
        if self._subtitle_was_visible_before_freeze and not self._quitting:
            self._subtitle_was_visible_before_freeze = False
            self.subtitle_window.show()
            self.subtitle_window.raise_()
            enforce_window_topmost(self.subtitle_window, activate=False)
        if self.subtitle_engine.is_running:
            self.subtitle_engine.set_paused(False)
        if not self._quitting and self.floating_pill:
            if not self.floating_pill.isVisible():
                self.floating_pill.show()
            self.floating_pill.ensure_topmost_and_positioned()

    def _handle_pill_triggered(self, *_args):
        """轻点边缘胶囊：前置清理旧卡片与胶囊高亮残影（AGENTS.md 第 9.3 章），瞬间定格屏幕"""
        if self._quitting or self._activating_freeze or self.freeze_overlay.isVisible():
            return
        self._activating_freeze = True
        try:
            if self.subtitle_engine.is_running:
                self.subtitle_engine.set_paused(True)
            self._subtitle_was_visible_before_freeze = self.subtitle_window.isVisible()
            if self._subtitle_was_visible_before_freeze:
                self.subtitle_window.hide()
            if self.dict_card.isVisible():
                self._dict_opened_from_subtitle = False
                self.dict_card.hide()
                self.dict_card.close()
            if self.floating_pill.isVisible():
                self.floating_pill.reset_interaction_state()
                self.floating_pill.hide()
            QApplication.processEvents()
            if self._quitting:
                return
            self.engine.set_active(False)
            self.freeze_overlay.activate_freeze_mode()
        finally:
            self._activating_freeze = False

    def _trigger_current_pos_lookup(self):
        """对当前鼠标光标所在单词执行快速查词"""
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        self._on_engine_hotkey_triggered(point.x, point.y)

    def _dispatch_async_point_ocr(self, phys_x: int, phys_y: int, is_dwell: bool):
        self._point_lookup_seq += 1
        seq = self._point_lookup_seq

        def _worker():
            try:
                res = win_ocr_engine.recognize_at_point(phys_x, phys_y)
            except Exception:
                res = {}
            self._ui_bridge.point_lookup_ready.emit(seq, is_dwell, phys_x, phys_y, res)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_point_lookup_ready(self, seq: int, is_dwell: bool, cur_x: int, cur_y: int, res: object):
        if seq != self._point_lookup_seq or self._quitting or self.freeze_overlay.isVisible():
            return
        if not isinstance(res, dict):
            res = {}
        if is_dwell:
            if res.get("word"):
                self.gesture_overlay.show_locked(
                    cur_x,
                    cur_y,
                    res["line_words"],
                    res["initial_index"],
                    res["sentence"],
                )
            else:
                self.gesture_overlay.show_no_word_hint(cur_x, cur_y)
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        pos = QPoint(int(cur_x / dpr), int(cur_y / dpr))
        if res.get("word"):
            self._dict_opened_from_subtitle = False
            word_info = lookup_fast(res["word"], res.get("sentence", ""))
            self.dict_card.show_word(word_info, pos)
        else:
            self.gesture_overlay.show_no_word_hint(cur_x, cur_y)

    def _on_engine_dwell_locked(self, phys_x: int, phys_y: int):
        """全局长按触发：后台异步 OCR 后在屏幕呈现光学高刷锁定框，绝不阻塞 GUI 主线程"""
        if self.freeze_overlay.isVisible() or self.gesture_overlay.isVisible():
            return

        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        lx = int(phys_x / dpr)
        ly = int(phys_y / dpr)

        if self.dict_card.isVisible():
            if self.dict_card.geometry().contains(QPoint(lx, ly)):
                return
            else:
                self._dict_opened_from_subtitle = False
                self.dict_card.close()

        self._dispatch_async_point_ocr(phys_x, phys_y, is_dwell=True)

    def _on_engine_touch_moved(self, cur_x: int, cur_y: int, dx: int, dy: int):
        """指尖在屏幕滑动：派发给交互层进行意图分流与连词计算"""
        if self.gesture_overlay.isVisible():
            self.gesture_overlay.handle_touch_move(cur_x, cur_y, dx, dy)

    def _on_engine_touch_released(self, cur_x: int, cur_y: int, dx: int, dy: int):
        """手指离屏：触发意图分流动作"""
        if self.gesture_overlay.isVisible():
            self.gesture_overlay.handle_touch_release(cur_x, cur_y, dx, dy)

    def _on_engine_hotkey_triggered(self, cur_x: int, cur_y: int):
        """F8 触发：后台异步定位单词后秒开词典卡片，杜绝主线程卡顿白屏"""
        self._dispatch_async_point_ocr(cur_x, cur_y, is_dwell=False)

    def _on_triage_save(self, selected_text: str, sentence: str):
        """🟢 向下滑动释放：主线程 <1ms 极速落盘入生词库，后台静默异步补全权威释义与音标"""
        word_info = lookup_fast(selected_text, sentence)
        fast_defs = word_info.get("definitions", [])
        if fast_defs and fast_defs[0].get("cn", "").startswith("正在"):
            fallback_cn = word_info.get("target_word", word_info["word"])
            fast_defs = [{"pos": fast_defs[0].get("pos", "释义"), "cn": fallback_cn, "en": ""}]

        saved_word_key = word_info["word"]
        save_word(
            word=saved_word_key,
            definitions=fast_defs,
            context_sentence=word_info["context_sentence"],
            context_translation=word_info["context_translation"],
            ai_analysis=word_info["ai_analysis"],
            phonetic_uk=word_info["phonetic_uk"],
            phonetic_us=word_info.get("phonetic_us", ""),
            tags=word_info["tags"],
            is_collected=True
        )
        self.tray.showMessage(
            "已加入生词本",
            f"“{selected_text}” 已保存至生词本。",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )
        self.vault_window.refresh_list()

        if word_info.get("needs_enrichment", False):
            def _bg_enrich_saved(seed_info):
                try:
                    enriched = enrich_lookup_sync(dict(seed_info))
                    updated = update_enrichment_if_collected(
                        word=saved_word_key,
                        definitions=enriched.get("definitions", []),
                        context_sentence=enriched.get("context_sentence", ""),
                        context_translation=enriched.get("context_translation", ""),
                        ai_analysis=enriched.get("ai_analysis", ""),
                        phonetic_uk=enriched.get("phonetic_uk", ""),
                        phonetic_us=enriched.get("phonetic_us", ""),
                        tags=enriched.get("tags", []),
                    )
                    if updated:
                        self._ui_bridge.vault_refresh_requested.emit()
                except Exception:
                    pass

            threading.Thread(target=_bg_enrich_saved, args=(dict(word_info),), daemon=True).start()

    def _on_triage_open_dict(self, selected_text: str, sentence: str, pos: QPoint):
        """⚪ 原位抬手 / 轻点单词：0 毫秒秒开全功能词典抽屉（异步静默补全译文）"""
        self._dict_opened_from_subtitle = False
        word_info = lookup_fast(selected_text, sentence)
        self.dict_card.show_word(word_info, pos)

    def _on_triage_cancel(self):
        """🔴 向上滑动释放：安全退出，不做任何修改"""
        pass

    def _on_vault_word_clicked(self, word_data):
        """在生词本中点击词汇时展开完整词典卡片（锚定于生词本侧边或中心偏右，避免拍在屏幕左上角）"""
        self._dict_opened_from_subtitle = False
        anchor = None
        if self.vault_window.isVisible():
            vg = self.vault_window.geometry()
            anchor = QPoint(vg.right() + 12, vg.top() + 60)
        self.dict_card.show_word(word_data, anchor)

    def _on_dict_card_closed(self):
        """仅当本次词典卡片由字幕点按打开且正常关闭时，通知字幕窗按需恢复视频播放。"""
        if self._dict_opened_from_subtitle and not self._quitting:
            self._dict_opened_from_subtitle = False
            self.subtitle_window.on_dictionary_card_closed()
        else:
            self._dict_opened_from_subtitle = False

    def _toggle_subtitle_mode(self, checked=None):
        """开启或关闭实时双语字幕舱与后台字幕追踪引擎。"""
        target_open = (not self.subtitle_window.isVisible()) if not isinstance(checked, bool) else checked
        if target_open:
            self.subtitle_window.show()
            self.subtitle_window.raise_()
            self.subtitle_engine.start()
            if hasattr(self, "subtitle_action"):
                self.subtitle_action.setChecked(True)
            if self.floating_pill:
                self.floating_pill.set_subtitle_active(True)
        else:
            self.subtitle_engine.stop()
            if self.subtitle_window.isVisible():
                self.subtitle_window.close()
            if hasattr(self, "subtitle_action"):
                self.subtitle_action.setChecked(False)
            if self.floating_pill:
                self.floating_pill.set_subtitle_active(False)

    def _on_custom_subtitle_region_selected(self, x1: int, y1: int, x2: int, y2: int):
        if not self.subtitle_window.isVisible():
            self._toggle_subtitle_mode(True)
        self.subtitle_engine.set_custom_region(x1, y1, x2, y2)

    def _trigger_custom_subtitle_region(self):
        if not self.subtitle_window.isVisible():
            self._toggle_subtitle_mode(True)
        self.subtitle_window._open_custom_region_selector()

    def _on_subtitle_closed(self):
        self.subtitle_engine.stop()
        if hasattr(self, "subtitle_action"):
            self.subtitle_action.setChecked(False)
        if self.floating_pill:
            self.floating_pill.set_subtitle_active(False)

    def _on_subtitle_word_clicked(self, word: str, en_sentence: str, cn_translation: str, pos: QPoint):
        """字幕单词 0ms 点按即查，并自动把完整双语字幕作为语境例句注入词典卡片。"""
        self._dict_opened_from_subtitle = True
        word_info = lookup_fast(word, en_sentence)
        if cn_translation:
            word_info["context_translation"] = f"↳ 译文：{cn_translation}"
            defs = word_info.get("definitions") or []
            has_real_def = defs and defs[0].get("cn") not in LOADING_PLACEHOLDERS
            if has_real_def and (word_info.get("phonetic_uk") or word_info.get("phonetic_us")):
                word_info["needs_enrichment"] = False
        self.dict_card.show_word(word_info, pos)

    def _open_vault(self):
        self.vault_window.refresh_list()
        self.vault_window.show()
        self.vault_window.raise_()

    def _quit(self):
        if getattr(self, "_quit_done", False):
            return
        self._quit_done = True
        self._quitting = True
        self._dict_opened_from_subtitle = False
        try:
            self.dict_card.on_closed = None
        except Exception:
            pass
        try:
            self.tray.hide()
        except Exception:
            pass
        try:
            self.subtitle_engine.shutdown()
        except Exception:
            pass
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self.freeze_overlay.close_freeze_mode()
        except Exception:
            pass
        try:
            if hasattr(self.gesture_overlay, "hide_overlay"):
                self.gesture_overlay.hide_overlay()
        except Exception:
            pass
        for w in (
            self.floating_pill,
            self.freeze_overlay,
            self.gesture_overlay,
            self.subtitle_window,
            self.dict_card,
            self.vault_window,
            self.settings_window,
        ):
            try:
                w.hide()
                w.close()
            except Exception:
                pass
        try:
            close_offline_dict()
        except Exception:
            pass
        try:
            from .database import close_all_connections
            close_all_connections()
        except Exception:
            pass
        _log.info("ScreenLex shut down cleanly")
        self.app.quit()

    def run(self):
        return self.app.exec()

def main():
    app = ScreenLexApplication()
    ret = app.run()
    try:
        app._quit()
    except Exception:
        pass
    os._exit(ret if isinstance(ret, int) else 0)

if __name__ == "__main__":
    main()
