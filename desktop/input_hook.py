import ctypes
from ctypes import wintypes
import time
import threading
from PySide6.QtCore import QObject, Signal

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_HOTKEY = 0x0312
VK_F8 = 0x77
WM_APP = 0x8000
WM_REHOOK = WM_APP + 1
WM_UNHOOK = WM_APP + 2
WM_RELOAD_HOTKEYS = WM_APP + 3

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

DEFAULT_HOTKEYS = {
    "enabled": True,
    "point_lookup": "F8",
    "freeze_capture": "Alt+Q",
    "toggle_subtitle": "Alt+B",
    "select_ocr_region": "Alt+R",
}

HOTKEY_LABELS = [
    ("point_lookup", "光标原位查词"),
    ("freeze_capture", "屏幕定格取词"),
    ("toggle_subtitle", "开关实时双语字幕"),
    ("select_ocr_region", "框选视频字幕区 (OCR)"),
]

HOTKEY_PRESETS = {
    "point_lookup": ["F8", "F9", "F7", "Alt+D", "Ctrl+Shift+D", ""],
    "freeze_capture": ["Alt+Q", "Alt+W", "Ctrl+Shift+Q", "F9", ""],
    "toggle_subtitle": ["Alt+B", "Alt+S", "Ctrl+Shift+B", "F10", ""],
    "select_ocr_region": ["Alt+R", "Alt+X", "Ctrl+Shift+R", "F11", ""],
}

_HOTKEY_ID_BASE = 1088
_HOTKEY_ACTION_ORDER = [k for k, _ in HOTKEY_LABELS]


def normalize_hotkey_spec(spec: str) -> str:
    """将用户/配置中的快捷键字符串规范化为标准格式，如 'Alt+Q'、'Ctrl+Shift+B'、'F8'。"""
    raw = (spec or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts:
        return ""
    mods = []
    key_part = ""
    for p in parts:
        up = p.upper()
        if up in ("CTRL", "CONTROL"):
            if "Ctrl" not in mods:
                mods.append("Ctrl")
        elif up == "ALT":
            if "Alt" not in mods:
                mods.append("Alt")
        elif up == "SHIFT":
            if "Shift" not in mods:
                mods.append("Shift")
        else:
            key_part = up
    if not key_part:
        return ""
    # 固定修饰键排序：Ctrl -> Alt -> Shift
    ordered_mods = [m for m in ("Ctrl", "Alt", "Shift") if m in mods]
    if ordered_mods:
        return "+".join(ordered_mods + [key_part])
    return key_part


def parse_hotkey_spec(spec: str):
    """解析标准快捷键字符串为 (fsModifiers, vk)；无效或禁用返回 None。"""
    norm = normalize_hotkey_spec(spec)
    if not norm:
        return None
    parts = norm.split("+")
    mods = 0
    for m in parts[:-1]:
        if m == "Alt":
            mods |= MOD_ALT
        elif m == "Ctrl":
            mods |= MOD_CONTROL
        elif m == "Shift":
            mods |= MOD_SHIFT
        else:
            return None
    key = parts[-1]
    vk = 0
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit():
        fnum = int(key[1:])
        if 1 <= fnum <= 12:
            vk = 0x70 + (fnum - 1)
    if not vk:
        return None
    # 非 F1~F12 的单字母/数字键禁止裸注册，必须带修饰键，避免劫持日常打字
    if mods == 0 and not (0x70 <= vk <= 0x7B):
        return None
    return (mods | MOD_NOREPEAT, vk)

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)
    ]

LRESULT = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# 显式声明 64 位 Win32 API 签名，彻底消除 HHOOK / LPARAM 32 位截断与 OverflowError
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

class GlobalGestureEngine(QObject):
    """
    Windows 11 专为二合一触控平板研发的三轨高敏识别引擎
    - 轨 1：Windows 原生触屏长按截获（针对 Edge/Acrobat 等 PDF/课件直接触发的触屏右键事件，0 毫秒秒级截获并阻止上下文菜单）；
    - 轨 2：WH_MOUSE_LL 鼠标与手写笔 220ms 驻留判定，自适应 80px 物理指尖防抖容差；
    - 轨 3：硬件级异步电容接触轮询守护（避免应用层 DirectManipulation 手势锁死）；
    - 轨 4：全局物理热键 F8 备用极速呼出。
    """
    dwell_locked = Signal(int, int)          # (phys_x, phys_y)
    touch_moved = Signal(int, int, int, int) # (cur_x, cur_y, dx, dy)
    touch_released = Signal(int, int, int, int) # (cur_x, cur_y, dx, dy)
    hotkey_triggered = Signal(int, int)      # (cur_x, cur_y) 向后兼容 point_lookup
    action_hotkey_triggered = Signal(str, int, int)  # (action_name, cur_x, cur_y)
    hotkeys_registration_updated = Signal(list)      # failed action names

    def __init__(self, dwell_ms: int = 220, jitter_radius: int = 80, enable_mouse_hook: bool = False, hotkeys_config=None, parent=None):
        super().__init__(parent)
        self.dwell_ms = dwell_ms
        self.jitter_radius = jitter_radius
        self.enable_mouse_hook = enable_mouse_hook

        self.is_down = False
        self.dwell_fired = False
        self.start_pos = (0, 0)
        self.last_pos = (0, 0)
        self.start_time = 0

        self._dwell_timer: threading.Timer = None
        self._hook_id = None
        self._hook_proc_ref = None
        self._thread = None
        self._poll_thread = None
        self._thread_id = None
        self._thread_ready = threading.Event()
        self._running = False
        self._enabled = True
        self._lock = threading.RLock()
        self._hotkeys_config = dict(DEFAULT_HOTKEYS)
        if isinstance(hotkeys_config, dict):
            self._hotkeys_config.update(hotkeys_config)
        self._registered_hotkeys = {}  # hotkey_id -> action_name
        self._failed_hotkey_actions = []

    def get_hotkeys_config(self) -> dict:
        with self._lock:
            return dict(self._hotkeys_config)

    def get_failed_hotkey_actions(self) -> list:
        with self._lock:
            return list(self._failed_hotkey_actions)

    def update_hotkeys(self, config: dict):
        """线程安全热更新全部全局快捷键（由持有消息循环的钩子线程执行 Unregister/RegisterHotKey）。"""
        merged = dict(DEFAULT_HOTKEYS)
        if isinstance(config, dict):
            merged.update(config)
        with self._lock:
            self._hotkeys_config = merged
            tid = self._thread_id
        if tid and self._running:
            try:
                user32.PostThreadMessageW(tid, WM_RELOAD_HOTKEYS, 0, 0)
            except Exception:
                pass

    def _unregister_all_hotkeys_on_thread(self):
        for hk_id in list(self._registered_hotkeys.keys()):
            try:
                user32.UnregisterHotKey(None, hk_id)
            except Exception:
                pass
        self._registered_hotkeys.clear()

    def _apply_hotkeys_on_thread(self):
        self._unregister_all_hotkeys_on_thread()
        with self._lock:
            cfg = dict(self._hotkeys_config)
        failed = []
        if cfg.get("enabled", True):
            for idx, action in enumerate(_HOTKEY_ACTION_ORDER):
                spec = str(cfg.get(action, "") or "").strip()
                if not spec:
                    continue
                parsed = parse_hotkey_spec(spec)
                if not parsed:
                    continue
                mods, vk = parsed
                hk_id = _HOTKEY_ID_BASE + idx
                try:
                    ok = bool(user32.RegisterHotKey(None, hk_id, mods, vk))
                except Exception:
                    ok = False
                if ok:
                    self._registered_hotkeys[hk_id] = action
                else:
                    failed.append(action)
        with self._lock:
            self._failed_hotkey_actions = list(failed)
        try:
            self.hotkeys_registration_updated.emit(list(failed))
        except Exception:
            pass

    def set_active(self, active: bool):
        with self._lock:
            self._enabled = active
            if not active:
                self.is_down = False
                self.dwell_fired = False
                if self._dwell_timer:
                    self._dwell_timer.cancel()
                    self._dwell_timer = None

        if self.enable_mouse_hook and self._thread_id:
            msg_type = WM_REHOOK if active else WM_UNHOOK
            try:
                user32.PostThreadMessageW(self._thread_id, msg_type, 0, 0)
            except Exception:
                pass

    def start(self):
        self._running = True
        self._thread_ready.clear()
        self._thread = threading.Thread(target=self._run_hook_loop, daemon=True)
        self._thread.start()

        if self.enable_mouse_hook:
            self._poll_thread = threading.Thread(target=self._run_polling_watchdog, daemon=True)
            self._poll_thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            if self._dwell_timer:
                try:
                    self._dwell_timer.cancel()
                except Exception:
                    pass
                self._dwell_timer = None

        # 等待钩子线程就绪后投递 WM_QUIT，由该线程自身在 finally 中执行 UnregisterHotKey / UnhookWindowsHookEx
        if self._thread and self._thread.is_alive():
            self._thread_ready.wait(timeout=0.5)
            tid = self._thread_id
            if tid:
                try:
                    user32.PostThreadMessageW(tid, 0x0012, 0, 0)
                except Exception:
                    pass
            try:
                self._thread.join(timeout=1.0)
            except Exception:
                pass
        if self._poll_thread and self._poll_thread.is_alive():
            try:
                self._poll_thread.join(timeout=0.5)
            except Exception:
                pass

    def _trigger_lock(self, x, y):
        with self._lock:
            if not self.dwell_fired:
                self.dwell_fired = True
                self.is_down = True
                self.start_pos = (x, y)
                self.last_pos = (x, y)
                self.dwell_locked.emit(x, y)

    def _trigger_release(self, x, y):
        with self._lock:
            if self.dwell_fired:
                self.dwell_fired = False
                self.is_down = False
                dx = x - self.start_pos[0]
                dy = y - self.start_pos[1]
                self.touch_released.emit(x, y, dx, dy)
            else:
                self.is_down = False

    def _run_polling_watchdog(self):
        """硬件级异步轮询守护：确保在任何被底层手势接管的窗口中均能 100% 触发"""
        pt = POINT()
        while self._running:
            if not self._enabled:
                time.sleep(0.1)
                continue
            down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            user32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
            now = time.time()

            if down:
                should_lock = False
                lock_x, lock_y = x, y
                with self._lock:
                    if not self.is_down:
                        self.is_down = True
                        self.start_pos = (x, y)
                        self.last_pos = (x, y)
                        self.start_time = now
                    else:
                        dx = x - self.start_pos[0]
                        dy = y - self.start_pos[1]
                        dist = (dx * dx + dy * dy) ** 0.5

                        if not self.dwell_fired:
                            if dist > self.jitter_radius:
                                # 发生滑动翻页：动态平移锚点并取消未触发的驻留计时器
                                self.start_pos = (x, y)
                                self.start_time = now
                                if self._dwell_timer:
                                    try:
                                        self._dwell_timer.cancel()
                                    except Exception:
                                        pass
                                    self._dwell_timer = None
                            else:
                                elapsed_ms = (now - self.start_time) * 1000
                                if elapsed_ms >= self.dwell_ms:
                                    should_lock = True
                                    lock_x, lock_y = self.start_pos[0], self.start_pos[1]
                        else:
                            # 已经锁词：持续监测指尖纵向微滑
                            self.last_pos = (x, y)
                            self.touch_moved.emit(x, y, dx, dy)
                    if should_lock:
                        self._trigger_lock(lock_x, lock_y)
            else:
                should_release = False
                with self._lock:
                    if self._dwell_timer:
                        try:
                            self._dwell_timer.cancel()
                        except Exception:
                            pass
                        self._dwell_timer = None
                    if self.is_down or self.dwell_fired:
                        should_release = True
                if should_release:
                    self._trigger_release(x, y)

            time.sleep(0.015)

    def _run_hook_loop(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        # 强制创建线程消息队列后再发出 ready，避免 PostThreadMessageW 丢失
        _peek_msg = wintypes.MSG()
        try:
            user32.PeekMessageW(ctypes.byref(_peek_msg), None, 0, 0, 0)
        except Exception:
            pass
        self._apply_hotkeys_on_thread()
        self._thread_ready.set()

        if self.enable_mouse_hook:
            def hook_proc(nCode, wParam, lParam):
                if nCode < 0:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                if not self._enabled:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                # 极速快通路径：未按下状态下的普通鼠标移动直接透传，零 ctypes 构造与零计算开销
                # 彻底杜绝高刷屏 (165Hz) 与高回报率 (1000Hz) 鼠标在软件启动与日常移动时的卡顿及 GIL 争用
                if wParam == WM_MOUSEMOVE and not self.is_down:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = data.pt.x, data.pt.y

                # 1. 针对触控长按（Windows 原生触屏长按会向应用下发带有 touch 标记的 WM_RBUTTONDOWN）
                if wParam in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                    extra = data.dwExtraInfo
                    is_touch = (extra & 0xFFFFFF00) == 0xFF515700
                    if wParam == WM_RBUTTONDOWN and is_touch:
                        self._trigger_lock(x, y)
                        return 1 # 坚决消费，防止 PDF 软件弹出系统右键菜单
                    elif wParam == WM_RBUTTONUP and is_touch:
                        if self.dwell_fired:
                            self._trigger_release(x, y)
                            return 1

                # 2. 鼠标/触控左键按下
                elif wParam == WM_LBUTTONDOWN:
                    with self._lock:
                        self.is_down = True
                        self.dwell_fired = False
                        self.start_pos = (x, y)
                        self.last_pos = (x, y)
                        self.start_time = time.time()

                        if self._dwell_timer:
                            self._dwell_timer.cancel()
                        self._dwell_timer = threading.Timer(
                            self.dwell_ms / 1000.0,
                            self._trigger_lock,
                            args=(x, y)
                        )
                        self._dwell_timer.daemon = True
                        self._dwell_timer.start()

                elif wParam == WM_MOUSEMOVE:
                    if self.is_down:
                        self.last_pos = (x, y)
                        dx = x - self.start_pos[0]
                        dy = y - self.start_pos[1]
                        dist = (dx * dx + dy * dy) ** 0.5

                        if not self.dwell_fired:
                            if dist > self.jitter_radius:
                                with self._lock:
                                    self.is_down = False
                                    if self._dwell_timer:
                                        self._dwell_timer.cancel()
                                        self._dwell_timer = None
                        else:
                            self.touch_moved.emit(x, y, dx, dy)

                elif wParam == WM_LBUTTONUP:
                    with self._lock:
                        was_fired = self.dwell_fired
                        self.is_down = False
                        if self._dwell_timer:
                            self._dwell_timer.cancel()
                            self._dwell_timer = None

                    if was_fired:
                        self._trigger_release(x, y)
                        return 1

                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            self._hook_proc_ref = HOOKPROC(hook_proc)
            self._hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc_ref, None, 0)
        else:
            self._hook_proc_ref = None
            self._hook_id = None

        msg = _peek_msg
        try:
            while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_UNHOOK:
                    if self._hook_id:
                        user32.UnhookWindowsHookEx(self._hook_id)
                        self._hook_id = None
                elif msg.message == WM_REHOOK:
                    if not self._hook_id and self._hook_proc_ref is not None and self._enabled and self._running:
                        self._hook_id = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc_ref, None, 0)
                elif msg.message == WM_RELOAD_HOTKEYS:
                    self._apply_hotkeys_on_thread()
                elif msg.message == WM_HOTKEY:
                    action = self._registered_hotkeys.get(int(msg.wParam))
                    if action and self._enabled:
                        pt = POINT()
                        user32.GetCursorPos(ctypes.byref(pt))
                        if action == "point_lookup":
                            self.hotkey_triggered.emit(pt.x, pt.y)
                        self.action_hotkey_triggered.emit(action, pt.x, pt.y)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_id:
                try:
                    user32.UnhookWindowsHookEx(self._hook_id)
                except Exception:
                    pass
                self._hook_id = None
            self._unregister_all_hotkeys_on_thread()
