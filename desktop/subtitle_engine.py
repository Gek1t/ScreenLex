"""
ScreenLex 实时双语字幕捕获、滚动碎词缝合、翻译调度与 YouTube 字幕轨同步引擎。
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
from ctypes import wintypes
import difflib
import hashlib
import html
import http.cookiejar
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

# 优先确保 onnxruntime 在 winrt 前初始化（兼容独立测试导入与 PyInstaller 冻结态）
try:
    _dll_cands = []
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _dll_cands.append(os.path.join(_meipass, "onnxruntime", "capi"))
    _dll_cands.append(os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "onnxruntime", "capi"))
    try:
        import site
        for _sp in site.getsitepackages():
            _dll_cands.append(os.path.join(_sp, "onnxruntime", "capi"))
    except Exception:
        pass
    for _dll_dir in _dll_cands:
        if os.path.exists(_dll_dir) and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(_dll_dir)
            break
    import onnxruntime  # noqa: F401
except Exception:
    pass

import numpy as np
from PIL import Image, ImageGrab
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .database import get_setting, set_setting
from .dictionary_service import (
    translate_text,
    translate_batch,
    preprocess_for_translation,
    prewarm_translation_connections,
    _open_url_direct,
)
from .win_ocr import win_ocr_engine


# 独立 Win32 API 句柄与显式 64 位签名（杜绝污染全局 ctypes.windll.user32 缓存及 HWND 截断）
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_PROCESS_NAME_CACHE: Dict[int, str] = {}
_PROCESS_NAME_LOCK = threading.Lock()
_BLOCKED_PROCESS_EXES = frozenset({
    "antigravity.exe",
    "code.exe",
    "cursor.exe",
    "windsurf.exe",
    "devenv.exe",
    "pycharm64.exe",
    "idea64.exe",
    "onenote.exe",
    "onenotem.exe",
    "wechat.exe",
    "weixin.exe",
    "qq.exe",
    "sakuracat.exe",
    "gameviewer.exe",
})


def _get_process_exe_name(pid: int) -> str:
    if pid <= 0:
        return ""
    with _PROCESS_NAME_LOCK:
        cached = _PROCESS_NAME_CACHE.get(pid)
        if cached is not None:
            return cached
    name = ""
    h_proc = None
    try:
        h_proc = _kernel32.OpenProcess(0x1000, False, int(pid))
        if h_proc:
            buf = ctypes.create_unicode_buffer(512)
            sz = wintypes.DWORD(512)
            if _kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(sz)):
                name = os.path.basename(buf.value).lower()
    except Exception:
        name = ""
    finally:
        if h_proc:
            try:
                _kernel32.CloseHandle(h_proc)
            except Exception:
                pass
    with _PROCESS_NAME_LOCK:
        if len(_PROCESS_NAME_CACHE) >= 256:
            _PROCESS_NAME_CACHE.clear()
        _PROCESS_NAME_CACHE[pid] = name
    return name


# 常见播放器 UI 杂音特征正则（仅作整行/整条过滤或边界剥离）
_TIMESTAMP_RE = re.compile(r'^\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*/\s*[A-Za-z0-9:.\-]{1,10})?\s*$')
_INLINE_TIMESTAMP_RE = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?\s*/\s*[\d:oO.\-]{1,10}\b')
_NOISE_PHRASES = {
    "skip ad", "skip ads", "autoplay", "subscribe", "subscribed",
    "settings", "theater mode", "fullscreen", "miniplayer",
    "mute", "unmute", "subtitles/closed captions", "cc",
    "hd", "4k", "1080p", "720p", "youtube", "share", "clip",
    "watch later", "replay", "up next",
}
_WEAK_NOISE_UNPUNCTUATED = {"play", "pause", "save", "live", "next", "previous"}
_WEAK_SINGLE_OVERLAP_WORDS = {
    "a", "an", "the", "to", "of", "in", "on", "at", "by", "for", "with",
    "from", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "it", "its", "this", "that", "as", "so", "if", "we", "you", "they",
    "he", "she", "i", "my", "your", "our", "not", "do", "does", "did",
    "will", "can", "have", "has", "had", "up", "out", "all", "one", "no",
}
_ABBREV_ENDS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.",
    "e.g.", "i.e.", "u.s.", "u.s.a.", "u.k.", "st.", "fig.", "no.", "vol.",
    "inc.", "ltd.", "co.", "corp.", "dept.", "est.", "eq.", "ref.", "al.",
    "ph.d.", "m.s.", "b.s.", "b.a.", "m.a.", "a.m.", "p.m.", "approx.",
    "ch.", "sec.", "def.", "thm.", "prop.", "pp.", "nos.", "viz.", "cf.",
    "jan.", "feb.", "mar.", "apr.", "aug.", "sept.", "sep.", "oct.", "nov.", "dec.",
}
_CLAUSE_SPLIT_WORDS = {
    "because", "although", "though", "whereas", "while", "since", "unless",
    "however", "therefore", "moreover", "furthermore", "otherwise", "nevertheless",
    "meanwhile", "which", "where", "when", "whenever", "until", "before", "after",
    "if", "so", "but", "and", "rather", "instead",
}
_STRONG_CLAUSE_STARTERS = {
    "because", "although", "though", "whereas", "while", "since", "unless",
    "however", "therefore", "moreover", "furthermore", "otherwise", "nevertheless",
    "meanwhile", "which", "where", "when", "whenever", "until", "if",
}
_SO_CONJ_FOLLOWERS = {
    "that", "i", "we", "you", "they", "he", "she", "it", "there", "this",
    "these", "those", "the", "a", "an", "let", "lets", "let's", "now", "if",
    "when", "what", "one", "people", "students", "users", "we'll", "you'll",
    "they'll", "i'll", "he'll", "she'll", "it'll", "it's", "that's", "there's",
    "i'm", "we're", "you're", "they're", "he's", "she's", "we've", "you've",
    "i've", "they've", "we'd", "i'd", "you'd", "they'd",
}
_NON_TERMINAL_TAIL_WORDS = {
    "a", "an", "the", "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "into", "onto", "upon", "about", "above", "below", "under", "over", "through",
    "during", "between", "among", "against", "without", "within", "toward", "towards",
    "per", "via", "and", "or", "but", "nor", "yet", "so", "as", "than", "if",
    "whether", "that", "which", "who", "whom", "whose", "what", "where", "when",
    "why", "how", "is", "are", "was", "were", "am", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "my", "your", "his", "her", "its",
    "our", "their", "this", "these", "those", "some", "any", "each", "every",
    "no", "not", "very", "too", "quite", "rather", "almost", "just", "only",
}
_COMPOUND_CONJ_PAIRS = {
    ("even", "though"), ("even", "if"), ("so", "that"), ("as", "if"),
    ("as", "though"), ("only", "if"), ("in", "case"), ("now", "that"),
    ("rather", "than"), ("such", "that"), ("instead", "of"), ("in", "order"),
    ("as", "well"), ("as", "soon"), ("as", "long"),
}
_SENTENCE_END_CHARS = (".", "?", "!")
_CLAUSE_BREAK_PUNCT = (",", ";", ":", "—", "–")
_TRAIL_WRAPPER_CHARS = "\"'”’)]}"


def _strip_trail_wrappers(tok: str) -> str:
    return (tok or "").rstrip(_TRAIL_WRAPPER_CHARS)


def _is_sentence_end_token(tok: str) -> bool:
    core = _strip_trail_wrappers(tok)
    if not core or not core.endswith(_SENTENCE_END_CHARS):
        return False
    if core.endswith("..") or core.endswith("…"):
        return False
    low = core.lower()
    if low in _ABBREV_ENDS:
        return False
    if re.fullmatch(r"(?:[A-Za-z]{1,3}\.){2,}[.,?!]*", core):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)+[.,?!]*", core):
        return False
    if re.fullmatch(r"[A-Za-z]\.", core):
        return False
    return any(c.isascii() and c.isalpha() for c in core)


def _has_clause_break_punct(tok: str) -> bool:
    core = _strip_trail_wrappers(tok)
    return bool(core) and (core.endswith(_CLAUSE_BREAK_PUNCT) or core.endswith("--"))


def _split_item_by_sentences(text: str, start_t: float, end_t: float) -> List[Tuple[str, float, float]]:
    """若单条字幕内包含多句句末标点（'.?!'），按句子边界切分并按词数比例插值起止时间。"""
    toks = [t for t in (text or "").split() if t]
    if not toks:
        return []
    slices: List[List[str]] = []
    cur: List[str] = []
    for tok in toks:
        cur.append(tok)
        if _is_sentence_end_token(tok) and len(cur) >= 2:
            slices.append(cur)
            cur = []
    if cur:
        if (
            slices
            and len(cur) == 1
            and len(slices[-1]) >= 2
            and not _is_sentence_end_token(cur[0])
            and not any(c.isascii() and c.isalpha() for c in cur[0])
        ):
            slices[-1].extend(cur)
        else:
            slices.append(cur)
    if len(slices) <= 1:
        return [(" ".join(toks), start_t, max(start_t + 0.4, end_t))]

    total_w = max(1, sum(len(s) for s in slices))
    span = (end_t - start_t) if end_t > start_t else (0.6 * len(slices))
    total_dur = max(0.25, span)
    out: List[Tuple[str, float, float]] = []
    acc_w = 0
    for sl in slices:
        s_s = start_t + total_dur * (acc_w / total_w)
        acc_w += len(sl)
        s_e = start_t + total_dur * (acc_w / total_w)
        out.append((" ".join(sl), s_s, max(s_s + 0.15, min(start_t + total_dur, s_e))))
    return out


def _find_natural_clause_split(tokens: List[str], min_idx: int = 5, max_idx: Optional[int] = None, soft_target: int = 10) -> int:
    """
    在无句末标点的长英文片段中选取最自然的语义断句切分点（返回 cut_idx，表示前 cut_idx 个词独立成段）：
    1. 优先逗号/分号/冒号/破折号；
    2. 其次从句引导词/转折连词及复合引导结构（'even though' / 'rather than' / 'now that' / 'in order to' 等）；
    3. 严禁在冠词、介词、助动词、所有格等前置绑定词或副词 'so'（如 'so accurate'）处腰斩。
    """
    n = len(tokens)
    hi = (n - 2) if max_idx is None else min(n - 1, max_idx)
    lo = max(2, min_idx)
    if hi < lo:
        return -1

    norms = [_norm_token(t) for t in tokens]

    # 1. 标点子句边界（k 表示 tokens[k-1] 带逗号/分号，切在 k 之前）
    punct_cands: List[Tuple[float, int]] = []
    for k in range(lo, hi + 1):
        if _has_clause_break_punct(tokens[k - 1]) and norms[k - 1] not in _NON_TERMINAL_TAIL_WORDS:
            dist = abs(k - soft_target)
            punct_cands.append((dist, k))
    if punct_cands:
        punct_cands.sort(key=lambda x: (x[0], -x[1]))
        return punct_cands[0][1]

    # 2. 从句引导词 / 复合结构 / 连词边界
    conj_cands: List[Tuple[float, int]] = []
    for i in range(lo, hi + 1):
        w = norms[i]
        prev_w = norms[i - 1] if i - 1 >= 0 else ""
        next_w = norms[i + 1] if i + 1 < n else ""

        is_compound_head = (w, next_w) in _COMPOUND_CONJ_PAIRS
        if not is_compound_head and w not in _CLAUSE_SPLIT_WORDS:
            continue

        cut = i
        if is_compound_head:
            if (n - cut) < 3:
                continue
        elif (prev_w, w) in _COMPOUND_CONJ_PAIRS and i - 1 >= lo:
            cut = i - 1
            prev_w = norms[cut - 1] if cut - 1 >= 0 else ""
        elif w in {"rather", "instead"} and next_w not in {"than", "of"}:
            continue

        if prev_w in _NON_TERMINAL_TAIL_WORDS:
            # 兼容 'in which' / 'for which' / 'by which'：整组介词+关系代词保留到下一段开头
            if prev_w in {"in", "on", "at", "for", "by", "with", "from", "to", "of"} and w in {"which", "where", "when"} and cut - 1 >= lo:
                cut = cut - 1
                prev_w = norms[cut - 1] if cut - 1 >= 0 else ""
                if prev_w in _NON_TERMINAL_TAIL_WORDS:
                    continue
            else:
                continue
        if w == "so" and not is_compound_head:
            if not _has_clause_break_punct(tokens[i - 1]) and next_w not in _SO_CONJ_FOLLOWERS:
                continue
        if w in {"and", "but", "so"} and not is_compound_head:
            # 防止把 "time and space" / "black and white" 等并列短语腰斩
            if cut < max(lo, 5) or (n - cut) < (3 if w in {"but", "so"} else 4):
                continue
        prio = 0.0 if (w in _STRONG_CLAUSE_STARTERS or is_compound_head) else 2.5
        conj_cands.append((prio + abs(cut - soft_target), cut))
    if conj_cands:
        conj_cands.sort(key=lambda x: (x[0], -x[1]))
        return conj_cands[0][1]

    return -1


def _norm_token(tok: str) -> str:
    """将单词归一化为纯小写字母数字与撇号，用于前后缀重叠对比。"""
    return re.sub(r"[^a-z0-9']", "", tok.lower())


def _dedup_repeated_phrases(tokens: List[str], max_ngram: int = 6) -> List[str]:
    """消除序列中紧邻重复出现的 1~6 词片段（防止 OCR 抖动或叠加产生循环复读）。"""
    if len(tokens) < 2:
        return list(tokens)
    cur = list(tokens)
    changed = True
    while changed and len(cur) >= 2:
        changed = False
        norms = [_norm_token(t) for t in cur]
        n = len(cur)
        for k in range(min(max_ngram, n // 2), 0, -1):
            i = 0
            out_toks: List[str] = []
            local_hit = False
            while i < len(cur):
                if i + 2 * k <= len(cur):
                    left = norms[i : i + k]
                    right = norms[i + k : i + 2 * k]
                    if all(left) and left == right:
                        # 保留单次出现（优先保留末尾带标点/最新一次）
                        out_toks.extend(cur[i : i + k])
                        i += 2 * k
                        # 若还有第 3、4 次紧邻重复一并跳过
                        while i + k <= len(cur) and norms[i : i + k] == left:
                            i += k
                        local_hit = True
                        continue
                out_toks.append(cur[i])
                i += 1
            if local_hit:
                cur = out_toks
                changed = True
                break
    return cur


def _is_likely_video_subtitle_line(
    img: Image.Image,
    line_box: Tuple[int, int, int, int],
    scan_bbox: Tuple[int, int, int, int],
) -> bool:
    """
    像素对比度特征门控：区分真实视频字幕（含深色底条及斑驳彩底上的暗描边白字）与白底 PPT 课件/网页正文。
    若检测到浅色纸面背景（如白底黑字幻灯片或网页评论），返回 False 予以过滤。
    """
    try:
        lx1, ly1, lx2, ly2 = line_box
        sx1, sy1, _, _ = scan_bbox
        cx1 = max(0, int(lx1 - sx1) - 4)
        cy1 = max(0, int(ly1 - sy1) - 4)
        cx2 = min(img.width, int(lx2 - sx1) + 4)
        cy2 = min(img.height, int(ly2 - sy1) + 4)
        if cx2 - cx1 < 12 or cy2 - cy1 < 8:
            return False
        crop = img.crop((cx1, cy1, cx2, cy2)).convert("RGB")
        arr = np.asarray(crop, dtype=np.float32)
        luma = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        sat = arr.max(axis=2) - arr.min(axis=2)
        p18, p30, p55, p90 = np.percentile(luma, [18, 30, 55, 90])
        neutral_white_bg_ratio = float(np.mean((luma >= 205.0) & (sat <= 35.0)))
        if neutral_white_bg_ratio > 0.52 and p55 > 165.0:
            return False
        if p55 <= 135.0 and p90 >= 160.0 and p30 <= 115.0:
            return True
        has_bright_neutral_glyph = float(np.mean((luma >= 195.0) & (sat <= 55.0))) >= 0.04
        return bool(has_bright_neutral_glyph and p18 <= 85.0 and p90 >= 190.0 and neutral_white_bg_ratio <= 0.42)
    except Exception:
        return True


def _calc_frame_motion(prev_thumb: np.ndarray, cur_thumb: np.ndarray) -> Tuple[float, float]:
    """
    计算 6x8 局部网格块的帧差运动：同时返回全局平均绝对差与最大局部块平均差，
    防止静态幻灯片视频中底部新蹦出 1~2 个字幕单词时因全图占比低而被误判为静止。
    """
    diff_map = np.abs(cur_thumb - prev_thumb)
    mean_diff = float(np.mean(diff_map))
    h, w = diff_map.shape[:2]
    if h >= 6 and w >= 8 and h % 6 == 0 and w % 8 == 0:
        block_means = diff_map.reshape(6, h // 6, 8, w // 8).mean(axis=(1, 3))
        max_block_diff = float(np.max(block_means))
    else:
        max_block_diff = mean_diff
    return mean_diff, max_block_diff


def _has_significant_frame_change(prev_thumb: Optional[np.ndarray], cur_thumb: np.ndarray) -> bool:
    """判定屏幕图像较上一帧是否发生足以触发 OCR 的显著全局或局部变化。"""
    if prev_thumb is None or prev_thumb.shape != cur_thumb.shape:
        return True
    mean_diff, max_block_diff = _calc_frame_motion(prev_thumb, cur_thumb)
    return not (mean_diff < 0.85 and max_block_diff < 1.6)


def clean_subtitle_ocr_text(raw_text: str) -> str:
    """清洗屏幕字幕 OCR 文本中的播放器控制条时间戳与 UI 按钮杂音。"""
    if not raw_text:
        return ""
    text = _INLINE_TIMESTAMP_RE.sub(" ", raw_text)
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    kept_lines: List[str] = []
    for ln in lines:
        if _TIMESTAMP_RE.match(ln):
            continue
        low = re.sub(r"[^a-z0-9/\s]", "", ln.lower()).strip()
        if low in _NOISE_PHRASES:
            continue
        if low in _WEAK_NOISE_UNPUNCTUATED and not any(ln.endswith(ch) for ch in _SENTENCE_END_CHARS):
            continue
        # 至少包含 1 个 ASCII 字母（保留合法单字如 "I", "A", "O(1)"，过滤纯符号噪点）
        alpha_cnt = sum(1 for c in ln if c.isascii() and c.isalpha())
        if alpha_cnt < 1:
            continue
        kept_lines.append(ln)
    merged = " ".join(kept_lines)
    merged = re.sub(r"\s+", " ", merged).strip()
    merged = re.sub(r"\s+([,.!?;:])", r"\1", merged)
    merged = re.sub(
        r"\b([lL])n(?=\s+(?:the|a|an|this|that|these|those|our|my|his|her|its|their|order|fact|addition|contrast|practice|theory)\b)",
        lambda m: "In" if m.group(1) == "L" or m.start() == 0 else "in",
        merged,
    )
    return merged


def _format_clock_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _format_video_sec(sec: float) -> str:
    sec_i = max(0, int(round(sec)))
    m, s = divmod(sec_i, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class SubtitleEntry:
    entry_id: int
    timestamp_str: str
    en_text: str
    cn_text: str = ""
    start_sec: Optional[float] = None
    is_final: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_ASR_CONTRACTIONS = {
    ("i", "am"): "i'm", ("you", "are"): "you're", ("we", "are"): "we're",
    ("they", "are"): "they're", ("there", "are"): "there're",
    ("what", "are"): "what're", ("who", "are"): "who're",
    ("where", "are"): "where're", ("how", "are"): "how're",
    ("he", "is"): "he's", ("she", "is"): "she's", ("it", "is"): "it's",
    ("that", "is"): "that's", ("there", "is"): "there's", ("here", "is"): "here's",
    ("what", "is"): "what's", ("who", "is"): "who's", ("where", "is"): "where's",
    ("when", "is"): "when's", ("how", "is"): "how's", ("why", "is"): "why's",
    ("he", "has"): "he's", ("she", "has"): "she's", ("it", "has"): "it's",
    ("that", "has"): "that's", ("there", "has"): "there's", ("here", "has"): "here's",
    ("what", "has"): "what's", ("who", "has"): "who's", ("where", "has"): "where's",
    ("how", "has"): "how's",
    ("i", "will"): "i'll", ("you", "will"): "you'll", ("we", "will"): "we'll",
    ("they", "will"): "they'll", ("he", "will"): "he'll", ("she", "will"): "she'll",
    ("it", "will"): "it'll", ("that", "will"): "that'll", ("there", "will"): "there'll",
    ("who", "will"): "who'll", ("what", "will"): "what'll",
    ("where", "will"): "where'll", ("how", "will"): "how'll",
    ("i", "have"): "i've", ("you", "have"): "you've",
    ("we", "have"): "we've", ("they", "have"): "they've",
    ("could", "have"): "could've", ("would", "have"): "would've",
    ("should", "have"): "should've", ("might", "have"): "might've",
    ("must", "have"): "must've",
    ("i", "would"): "i'd", ("you", "would"): "you'd", ("we", "would"): "we'd",
    ("they", "would"): "they'd", ("he", "would"): "he'd", ("she", "would"): "she'd",
    ("it", "would"): "it'd", ("that", "would"): "that'd", ("there", "would"): "there'd",
    ("who", "would"): "who'd", ("what", "would"): "what'd",
    ("i", "had"): "i'd", ("you", "had"): "you'd", ("we", "had"): "we'd",
    ("they", "had"): "they'd", ("he", "had"): "he'd", ("she", "had"): "she'd",
    ("it", "had"): "it'd", ("that", "had"): "that'd",
    ("where", "did"): "where'd", ("how", "did"): "how'd",
    ("what", "did"): "what'd", ("who", "did"): "who'd",
    ("do", "not"): "don't", ("does", "not"): "doesn't", ("did", "not"): "didn't",
    ("is", "not"): "isn't", ("are", "not"): "aren't", ("was", "not"): "wasn't",
    ("were", "not"): "weren't", ("have", "not"): "haven't", ("has", "not"): "hasn't",
    ("had", "not"): "hadn't", ("will", "not"): "won't", ("can", "not"): "can't",
    ("cannot",): "can't", ("would", "not"): "wouldn't", ("could", "not"): "couldn't",
    ("should", "not"): "shouldn't", ("must", "not"): "mustn't",
    ("shall", "not"): "shan't", ("need", "not"): "needn't",
    ("let", "us"): "let's", ("you", "all"): "y'all",
    ("going", "to"): "gonna", ("want", "to"): "wanna", ("got", "to"): "gotta",
    ("kind", "of"): "kinda", ("sort", "of"): "sorta", ("out", "of"): "outta",
}


def _match_asr_contraction_at(
    seq_a: List[str], seq_b: List[str]
) -> Optional[Tuple[List[str], List[str], bool]]:
    """检查 seq_a 与 seq_b 起始位置是否互为 ASR 缩略/展开形式，返回 (tail_a, tail_b, exact_apostrophe)。"""
    if not seq_a or not seq_b:
        return None
    if len(seq_a) >= 2:
        c = _ASR_CONTRACTIONS.get((seq_a[0], seq_a[1]))
        if c is not None:
            if c == seq_b[0]:
                return seq_a[2:], seq_b[1:], True
            if c.replace("'", "") == seq_b[0].replace("'", ""):
                return seq_a[2:], seq_b[1:], False
        if seq_a[1] in ("is", "has") and len(seq_a[0]) >= 2 and seq_a[0].isascii() and seq_a[0].isalpha():
            if seq_b[0] == f"{seq_a[0]}'s":
                return seq_a[2:], seq_b[1:], True
    if len(seq_b) >= 2:
        c = _ASR_CONTRACTIONS.get((seq_b[0], seq_b[1]))
        if c is not None:
            if c == seq_a[0]:
                return seq_a[1:], seq_b[2:], True
            if c.replace("'", "") == seq_a[0].replace("'", ""):
                return seq_a[1:], seq_b[2:], False
        if seq_b[1] in ("is", "has") and len(seq_b[0]) >= 2 and seq_b[0].isascii() and seq_b[0].isalpha():
            if seq_a[0] == f"{seq_b[0]}'s":
                return seq_a[1:], seq_b[2:], True
    ca = _ASR_CONTRACTIONS.get((seq_a[0],))
    if ca is not None:
        if ca == seq_b[0]:
            return seq_a[1:], seq_b[1:], True
        if ca.replace("'", "") == seq_b[0].replace("'", ""):
            return seq_a[1:], seq_b[1:], False
    cb = _ASR_CONTRACTIONS.get((seq_b[0],))
    if cb is not None:
        if cb == seq_a[0]:
            return seq_a[1:], seq_b[1:], True
        if cb.replace("'", "") == seq_a[0].replace("'", ""):
            return seq_a[1:], seq_b[1:], False
    return None


def _asr_match_advance(seq_a: List[str], seq_b: List[str]) -> Tuple[int, int, int, bool]:
    """沿 seq_a / seq_b 同步前向对齐（支持连续精确词与多处 ASR 缩略/展开），返回 (ia, ib, contr_hits, all_exact_apos)。"""
    ia = 0
    ib = 0
    contr_hits = 0
    all_exact_apos = True
    na = len(seq_a)
    nb = len(seq_b)
    while ia < na and ib < nb:
        if seq_a[ia] == seq_b[ib]:
            ia += 1
            ib += 1
            continue
        cm = _match_asr_contraction_at(seq_a[ia : ia + 2], seq_b[ib : ib + 2])
        if cm is None:
            break
        ta, tb, exact_apos = cm
        da = min(2, na - ia) - len(ta)
        db = min(2, nb - ib) - len(tb)
        if da <= 0 or db <= 0:
            break
        ia += da
        ib += db
        contr_hits += 1
        if not exact_apos:
            all_exact_apos = False
    return ia, ib, contr_hits, all_exact_apos


def _is_similar_token(a: str, b: str) -> bool:
    """判定两个归一化词是否属于轻微 OCR/ASR 拼写修正（如 'welcom' <-> 'welcome'、'it\'s' <-> 'its'）。"""
    ac = a.replace("'", "")
    bc = b.replace("'", "")
    if not ac or not bc:
        return False
    if ac == bc:
        return True
    return difflib.SequenceMatcher(None, ac, bc).ratio() >= 0.72


class RollingCaptionStitcher:
    """
    YouTube 自动滚动碎词智能缝合状态机：
    1. 将逐词蹦出、两行交替滚动的残缺碎片通过词级前后缀重叠自动拼接为完整长句；
    2. 在句末标点跨句、长句自然结束或新语义切换时自动封存归档为历史条目；
    3. 防止滚动过程中出现重复词堆叠、切句后首行滞留回弹或实时 ASR 改写引发回弹。
    """

    def __init__(self, max_history: int = 300, max_sentence_words: int = 18):
        self._max_history = max_history
        self._max_sentence_words = max_sentence_words
        self._next_id = 1
        self._active_tokens: List[str] = []
        self._active_started_ts: float = 0.0
        self._last_nonempty_ts: float = 0.0
        self._last_raw_norm: str = ""
        self._committed_tail_tokens: List[str] = []
        self._committed_tail_ts: float = 0.0
        self._committed_segments: List[Tuple[List[str], float]] = []
        self._last_switched_prev_norms: List[str] = []
        self._last_switched_ts: float = 0.0
        self._history: List[SubtitleEntry] = []

    def reset(self):
        self._active_tokens.clear()
        self._active_started_ts = 0.0
        self._last_nonempty_ts = 0.0
        self._last_raw_norm = ""
        self._committed_tail_tokens.clear()
        self._committed_tail_ts = 0.0
        self._committed_segments.clear()
        self._last_switched_prev_norms.clear()
        self._last_switched_ts = 0.0
        self._history.clear()
        self._next_id = 1

    @property
    def active_en(self) -> str:
        return " ".join(self._active_tokens).strip()

    @property
    def hud_en(self) -> str:
        """供原位遮盖舱展示的紧凑实时子句（超长时优先在自然子句边界截取，不从短语中间腰斩）。"""
        n = len(self._active_tokens)
        if n <= 14:
            return " ".join(self._active_tokens).strip()
        win_lo = max(1, n - 14)
        win_hi = max(win_lo, n - 5)
        for i in range(win_lo, win_hi + 1):
            if _has_clause_break_punct(self._active_tokens[i - 1]):
                return " ".join(self._active_tokens[i:]).strip()
        for i in range(win_lo, win_hi + 1):
            if _norm_token(self._active_tokens[i]) in _STRONG_CLAUSE_STARTERS:
                prev_w = _norm_token(self._active_tokens[i - 1])
                start = i - 1 if (prev_w, _norm_token(self._active_tokens[i])) in _COMPOUND_CONJ_PAIRS else i
                return " ".join(self._active_tokens[start:]).strip()
        return " ".join(self._active_tokens[-14:]).strip()

    @property
    def history(self) -> List[SubtitleEntry]:
        return list(self._history)

    def get_history_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._history]

    def update_entry_translation(self, entry_id: int, cn_text: str) -> bool:
        for entry in reversed(self._history):
            if entry.entry_id == entry_id:
                if entry.cn_text != cn_text:
                    entry.cn_text = cn_text
                    return True
                return False
        return False

    def _commit_tokens(self, tokens: List[str], ts: float) -> Optional[SubtitleEntry]:
        text = " ".join(tokens).strip()
        if not text:
            return None
        alpha_cnt = sum(1 for c in text if c.isascii() and c.isalpha())
        if alpha_cnt < 1:
            return None
        # 避免连续或近期重复归档完全相同的句子
        if self._history:
            cur_norm = " ".join(_norm_token(t) for t in tokens if _norm_token(t))
            if cur_norm:
                lookback = 3 if len(tokens) >= 3 else 1
                for prev_e in self._history[-lookback:]:
                    prev_norm = " ".join(_norm_token(t) for t in prev_e.en_text.split() if _norm_token(t))
                    if cur_norm == prev_norm:
                        return None

        entry = SubtitleEntry(
            entry_id=self._next_id,
            timestamp_str=_format_clock_ts(ts),
            en_text=text,
            cn_text="",
            is_final=True,
        )
        self._next_id += 1
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        return entry

    def _record_committed_tail(self, tokens: List[str], ts: float):
        if not tokens:
            return
        self._committed_tail_tokens.extend(tokens)
        if len(self._committed_tail_tokens) > 40:
            self._committed_tail_tokens = self._committed_tail_tokens[-40:]
        self._committed_tail_ts = ts
        seg_norms = [_norm_token(t) for t in tokens if _norm_token(t)]
        if seg_norms:
            self._committed_segments.append((seg_norms, ts))
            if len(self._committed_segments) > 4:
                self._committed_segments = self._committed_segments[-4:]

    def _split_completed_sentences(self, ts: float) -> List[SubtitleEntry]:
        """当活跃序列内部已包含完整句末标点且后方接续了新句时，切分归档前一句。"""
        committed: List[SubtitleEntry] = []
        while len(self._active_tokens) >= 2:
            split_idx = -1
            for i in range(0, len(self._active_tokens) - 1):
                if _is_sentence_end_token(self._active_tokens[i]):
                    split_idx = i
                    break
            if split_idx != -1:
                done_toks = self._active_tokens[: split_idx + 1]
                rem_toks = self._active_tokens[split_idx + 1 :]
                entry = self._commit_tokens(done_toks, ts)
                if entry:
                    committed.append(entry)
                self._record_committed_tail(done_toks, ts)
                self._active_tokens = rem_toks
                self._active_started_ts = ts
            else:
                break

        # 若长口语无句末标点，优先在标点子句或从句引导词处自然切分，并避免把冠词/介词留在句尾
        while len(self._active_tokens) > 13:
            split_clause_idx = _find_natural_clause_split(
                self._active_tokens,
                min_idx=5,
                max_idx=len(self._active_tokens) - 3,
                soft_target=10,
            )
            if split_clause_idx != -1:
                done_toks = self._active_tokens[:split_clause_idx]
                rem_toks = self._active_tokens[split_clause_idx:]
                entry = self._commit_tokens(done_toks, ts)
                if entry:
                    committed.append(entry)
                self._record_committed_tail(done_toks, ts)
                self._active_tokens = rem_toks
                self._active_started_ts = ts
                continue
            if len(self._active_tokens) >= self._max_sentence_words:
                cut = -1
                for cand in range(len(self._active_tokens) - 4, 5, -1):
                    if _norm_token(self._active_tokens[cand - 1]) not in _NON_TERMINAL_TAIL_WORDS:
                        cut = cand
                        break
                if cut == -1:
                    cut = max(1, len(self._active_tokens) - 7)
                done_toks = self._active_tokens[:cut]
                rem_toks = self._active_tokens[cut:]
                entry = self._commit_tokens(done_toks, ts)
                if entry:
                    committed.append(entry)
                self._record_committed_tail(done_toks, ts)
                self._active_tokens = rem_toks
                self._active_started_ts = ts
                continue
            break

        return committed

    def _find_suffix_prefix_overlap(self, active_norms: List[str], new_norms: List[str]) -> int:
        """返回 active_norms 后缀与 new_norms 前缀的最大有效重叠词数。"""
        max_k = min(len(active_norms), len(new_norms))
        for k in range(max_k, 0, -1):
            if active_norms[-k:] != new_norms[:k]:
                continue
            if k >= 2:
                return k
            # 单词重叠时仅放行较长且非高频虚词，避免 'to'/'the' 误拼接完全不同的新句
            word = new_norms[0]
            if len(word) >= 5 and word not in _WEAK_SINGLE_OVERLAP_WORDS:
                return 1
        return 0

    def _is_tail_subsequence(self, active_norms: List[str], new_norms: List[str]) -> bool:
        """检查 new_norms 是否只是 _active_tokens 末尾（或中部连续）滚走后残留的子片段。"""
        n_len = len(new_norms)
        if n_len == 0 or n_len > len(active_norms):
            return False
        # 从靠后位置往前搜索，只要在活跃序列中连续出现且覆盖了已有片段，不倒退丢词
        for start in range(len(active_norms) - n_len, -1, -1):
            if active_norms[start : start + n_len] == new_norms:
                return True
        return False

    def _find_internal_anchor_merge(self, active_norms: List[str], new_norms: List[str]) -> Optional[Tuple[int, int]]:
        """
        应对双行同时轻微纠偏或仅截断最末 1 词后的滚动接续：
        查找 new_norms 前缀（长度 >= 2）在 active_norms 后半段中的锚点，
        不仅要求前 3 词锚点一致，还必须校验到旧句末尾的重合率（最多允许末 1 词不同），防止误吞句。
        """
        if len(new_norms) < 3 or len(active_norms) < 3:
            return None
        anchor_len = min(3, len(new_norms))
        anchor = new_norms[:anchor_len]
        start_search = max(0, len(active_norms) - len(new_norms) - 4)
        for idx in range(len(active_norms) - anchor_len, start_search - 1, -1):
            if active_norms[idx : idx + anchor_len] == anchor:
                rem_active = len(active_norms) - idx
                if len(new_norms) >= rem_active:
                    mismatches = sum(
                        1
                        for a, b in zip(active_norms[idx:], new_norms[:rem_active])
                        if a != b
                    )
                    if mismatches <= 1 and (rem_active - mismatches) >= 3:
                        return idx, rem_active
        return None

    def _match_committed_segment_prefix(
        self, new_tokens: List[str], new_norms: List[str], seg_norms: List[str], seg_ts: float, ts: float
    ) -> Optional[Tuple[bool, List[str]]]:
        if not seg_norms or (ts - seg_ts) > 10.0:
            return None
        ia, ib, contr_hits, exact_apos = _asr_match_advance(seg_norms, new_norms)
        if ia == len(seg_norms) and contr_hits >= 1 and exact_apos and ib >= 2:
            if ib == len(new_norms):
                return True, new_tokens
            return False, new_tokens[ib:]
        max_m = min(len(seg_norms), len(new_norms))
        for m in range(max_m, 1, -1):
            if m == 2:
                allow_two = _is_sentence_end_token(new_tokens[1]) or (len(seg_norms) == 2 and (ts - seg_ts) <= 3.5)
                if not allow_two:
                    continue
            if seg_norms[-m:] == new_norms[:m]:
                if m == len(new_norms):
                    return True, new_tokens
                return False, new_tokens[m:]
        # 允许已归档前缀内部存在 1 词 OCR 噪点（末词或倒数第 2 词需严格对齐）
        for m in range(max_m, 3, -1):
            sub = seg_norms[-m:]
            mismatches = sum(1 for a, b in zip(sub, new_norms[:m]) if a != b)
            if mismatches <= 1 and (sub[-1] == new_norms[m - 1] or (m >= 5 and sub[-2] == new_norms[m - 2])):
                if m == len(new_norms):
                    return True, new_tokens
                return False, new_tokens[m:]
        return None

    def _strip_committed_prefix(
        self, new_tokens: List[str], new_norms: List[str], ts: float
    ) -> Tuple[bool, List[str]]:
        """
        针对双行字幕切分后上一句仍滞留屏幕首行的场景：
        1. 若整帧只含已归档上一句的残留或回显，直接忽略防止画面回弹及历史重复；
        2. 若新帧前半段为滞留的已归档句、后半段延续当前活跃句（含 ASR 实时改写），精准剥离已归档前缀。
        """
        if self._last_switched_prev_norms and (ts - self._last_switched_ts) <= 2.0:
            prev_n = self._last_switched_prev_norms
            if new_norms == prev_n:
                return True, new_tokens
            if len(prev_n) >= 3 and len(new_norms) > len(prev_n) and new_norms[: len(prev_n)] == prev_n:
                return False, new_tokens[len(prev_n) :]
            if (
                len(new_norms) >= 3
                and (
                    self._is_tail_subsequence(prev_n, new_norms)
                    or (
                        len(new_norms) <= len(prev_n) + 1
                        and sum(1 for a, b in zip(prev_n, new_norms) if a == b) / max(1, len(new_norms)) >= 0.75
                    )
                )
            ):
                return True, new_tokens

        if not self._committed_tail_tokens or not self._active_tokens:
            return False, new_tokens
        if (ts - self._committed_tail_ts) > 10.0:
            self._committed_tail_tokens.clear()
            self._committed_segments.clear()
            return False, new_tokens

        tail_len = len(self._committed_tail_tokens)
        tail_norms = [_norm_token(t) for t in self._committed_tail_tokens]
        active_norms = [_norm_token(t) for t in self._active_tokens]
        combined_tokens = self._committed_tail_tokens + self._active_tokens
        combined_norms = tail_norms + active_norms

        if self._is_tail_subsequence(combined_norms, new_norms):
            if new_norms != active_norms:
                self._committed_tail_ts = ts
                return True, new_tokens
            return False, new_tokens

        ov = self._find_suffix_prefix_overlap(combined_norms, new_norms)
        if ov > 0:
            start_in_combined = len(combined_norms) - ov
            if start_in_combined < tail_len:
                merged_all = combined_tokens[:-ov] + new_tokens
                eff = merged_all[tail_len:]
                if eff:
                    self._committed_tail_ts = ts
                    return False, eff

        act_len = len(active_norms)
        if act_len >= 1:
            for p in range(1, len(new_norms) - act_len + 1):
                if new_norms[p : p + act_len] != active_norms:
                    continue
                prefix_norms = new_norms[:p]
                tail_set = {w for w in tail_norms if w}
                hit_cnt = sum(1 for w in prefix_norms if w in tail_set)
                need_hits = 1 if len(prefix_norms) <= 2 else max(2, int(len(prefix_norms) * 0.45))
                if hit_cnt >= need_hits and (act_len >= 2 or tail_norms[-1] == prefix_norms[-1]):
                    self._committed_tail_ts = ts
                    return False, new_tokens[p:]
            if act_len >= 2:
                for p in range(2, min(len(tail_norms), len(new_norms) - 1) + 1):
                    if tail_norms[-p:] == new_norms[:p] and self._is_asr_prefix_revision(active_norms, new_norms[p:]):
                        self._committed_tail_ts = ts
                        return False, new_tokens[p:]

        # 当活跃行同时发生 ASR 改写（如 "we will" -> "we'll"）或首行缺句号时，按最近已归档段后缀剥离首行残留
        for seg_norms, seg_ts in reversed(self._committed_segments):
            res = self._match_committed_segment_prefix(new_tokens, new_norms, seg_norms, seg_ts, ts)
            if res is not None:
                self._committed_tail_ts = ts
                return res
        res_tail = self._match_committed_segment_prefix(new_tokens, new_norms, tail_norms, self._committed_tail_ts, ts)
        if res_tail is not None:
            self._committed_tail_ts = ts
            return res_tail

        if len(new_norms) <= tail_len + 1 and len(new_norms) >= 3:
            tail_set = {w for w in tail_norms if w}
            hit_cnt = sum(1 for w in new_norms if w in tail_set)
            if hit_cnt / len(new_norms) >= 0.72:
                self._committed_tail_ts = ts
                return True, new_tokens

        return False, new_tokens

    def _find_asr_suffix_overlap(self, active_norms: List[str], new_norms: List[str]) -> Optional[int]:
        """当双行滚动伴随尾段 ASR 缩略改写时（如 'works and it is great' -> 'works and it\'s great for'），定位拼接切点。"""
        na = len(active_norms)
        if na < 3 or len(new_norms) < 2 or not self._active_tokens or _is_sentence_end_token(self._active_tokens[-1]):
            return None
        lo = max(1, na - len(new_norms) - 3)
        for idx in range(lo, na - 2):
            ia, ib, contr_hits, exact_apos = _asr_match_advance(active_norms[idx:], new_norms)
            if ia == (na - idx) and contr_hits >= 1 and exact_apos and ib >= 2:
                return idx
        return None

    def _is_asr_prefix_revision(self, active_norms: List[str], new_norms: List[str]) -> bool:
        """判定 new_norms 是否是当前未完结句的前缀锚定 ASR 实时改写（含句首/多处缩略与长句纠偏）。"""
        if not self._active_tokens or not active_norms or not new_norms:
            return False
        if _is_sentence_end_token(self._active_tokens[-1]):
            return False

        n_act = len(active_norms)
        n_new = len(new_norms)
        ia, ib, contr_hits, exact_apos = _asr_match_advance(active_norms, new_norms)
        rem_a = active_norms[ia:]
        rem_n = new_norms[ib:]

        # 1. 完全消耗 active_norms（含单处或多处 ASR 缩略/展开），且不因句首漏撇号误并无关新句
        if not rem_a and (ia > 0 or ib > 0):
            if exact_apos or ib >= 2 or not rem_n:
                return True

        # 2. 至少已锚定前缀（含句首缩略），校验后续尾部（支持后续再次出现缩略或轻微删改）
        if (ia > 0 or ib > 0) and rem_a and rem_n:
            for skip_a, skip_b in ((1, 1), (1, 0), (0, 1)):
                if len(rem_a) > skip_a and len(rem_n) > skip_b:
                    sa, _, _, ex2 = _asr_match_advance(rem_a[skip_a:], rem_n[skip_b:])
                    if sa > 0 and sa == len(rem_a) - skip_a and exact_apos and ex2:
                        if (ia + sa) >= max(2, n_act - 2):
                            return True

            if contr_hits >= 1:
                if len(rem_n) >= len(rem_a) and rem_n[: len(rem_a)] == rem_a:
                    return True
                rem_n_set = set(rem_n)
                hit_ratio = sum(1 for w in rem_a if w in rem_n_set) / len(rem_a)
                if len(rem_a) == 1:
                    if rem_a[0] == rem_n[0]:
                        return True
                elif (rem_a[0] == rem_n[0] and hit_ratio >= 0.5) or hit_ratio >= 0.6:
                    return True

        # 3. 句首首词轻微 OCR/ASR 纠偏后整句延续（如 "Welcom to" -> "Welcome to the class"）
        if ia == 0 and ib == 0 and n_act >= 2 and n_new >= n_act:
            if _is_similar_token(active_norms[0], new_norms[0]):
                sa, _, _, ex_tail = _asr_match_advance(active_norms[1:], new_norms[1:])
                if sa == n_act - 1 and ex_tail:
                    return True

        if ia == 0 or ib == 0:
            return False
        if contr_hits >= 1 and not rem_n and ib >= 2 and len(rem_a) == 1:
            return True
        if ib >= 2 and ia >= n_act - 2 and n_new >= max(ib, n_act - 1 - contr_hits):
            return True
        if n_act <= 6 and ib >= 1 and n_new >= max(2, n_act - 2 - contr_hits):
            if len(rem_a) >= 2 and len(rem_n) >= 2 and rem_a[1] == rem_n[1]:
                return True
        return False

    def feed_ocr_text(self, raw_text: str, timestamp: Optional[float] = None) -> Dict[str, Any]:
        ts = timestamp if timestamp is not None else time.time()
        cleaned = clean_subtitle_ocr_text(raw_text)
        committed: List[SubtitleEntry] = []

        if not cleaned:
            # 字幕消失超过 2.0s 时自动将活跃句归档为历史
            if self._active_tokens and self._last_nonempty_ts > 0 and (ts - self._last_nonempty_ts) >= 2.0:
                entry = self._commit_tokens(self._active_tokens, self._last_nonempty_ts)
                if entry:
                    committed.append(entry)
                self._active_tokens.clear()
                self._last_raw_norm = ""
                self._committed_tail_tokens.clear()
                self._committed_tail_ts = 0.0
                self._committed_segments.clear()
                self._last_switched_prev_norms.clear()
                self._last_switched_ts = 0.0
                return {"active_en": "", "committed": committed, "changed": True}
            return {"active_en": self.active_en, "committed": [], "changed": False}

        self._last_nonempty_ts = ts
        raw_tokens = [t for t in cleaned.split() if t]
        # 剔除完全无字母数字的孤立标点 token 并消除紧邻重复 n-gram 短语
        filtered_tokens = [t for t in raw_tokens if _norm_token(t)]
        if not filtered_tokens:
            return {"active_en": self.active_en, "committed": [], "changed": False}
        new_tokens = _dedup_repeated_phrases(filtered_tokens)
        new_norms = [_norm_token(t) for t in new_tokens]

        raw_norm_str = " ".join(new_norms)
        if raw_norm_str == self._last_raw_norm and self._active_tokens:
            return {"active_en": self.active_en, "committed": [], "changed": False}
        self._last_raw_norm = raw_norm_str

        if not self._active_tokens:
            self._active_tokens = new_tokens
            self._active_started_ts = ts
            committed.extend(self._split_completed_sentences(ts))
            return {"active_en": self.active_en, "committed": committed, "changed": True}

        should_ignore, eff_tokens = self._strip_committed_prefix(new_tokens, new_norms, ts)
        if should_ignore:
            return {"active_en": self.active_en, "committed": [], "changed": False}
        if eff_tokens is not new_tokens and eff_tokens:
            new_tokens = _dedup_repeated_phrases(eff_tokens)
            new_norms = [_norm_token(t) for t in new_tokens]

        active_norms = [_norm_token(t) for t in self._active_tokens]
        prev_en = self.active_en

        # 1. 完全相同（仅标点或大小写刷新）
        if active_norms == new_norms:
            self._active_tokens = new_tokens
            changed = self.active_en != prev_en
            return {"active_en": self.active_en, "committed": [], "changed": changed}

        # 2. 前缀扩展：新字幕完整包含当前句前缀（YouTube 逐词吐词）
        if len(new_norms) > len(active_norms) and new_norms[: len(active_norms)] == active_norms:
            self._active_tokens = _dedup_repeated_phrases(new_tokens)
            committed.extend(self._split_completed_sentences(ts))
            return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 3. 优先校验是否为当前活跃句已包含的连续子序列（防止滚动残留或部分回显触发错误重叠拼接）
        if self._is_tail_subsequence(active_norms, new_norms):
            return {"active_en": self.active_en, "committed": [], "changed": False}

        # 4. 后缀-前缀重叠拼接：YouTube 上行滚出、下行接续新词（且新增尾部不可已存在于当前句中）
        overlap = self._find_suffix_prefix_overlap(active_norms, new_norms)
        if overlap > 0:
            added_norms = new_norms[overlap:]
            if not (len(added_norms) >= 2 and self._is_tail_subsequence(active_norms, added_norms)):
                self._active_tokens = _dedup_repeated_phrases(self._active_tokens[:-overlap] + new_tokens)
                committed.extend(self._split_completed_sentences(ts))
                return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 5. 内部锚点续接（处理末尾 1 词 OCR 轻微识别变化或尾段缩略改写后的滚动延续）
        anchor_res = self._find_internal_anchor_merge(active_norms, new_norms)
        if anchor_res is not None:
            cut_idx, _ = anchor_res
            self._active_tokens = _dedup_repeated_phrases(self._active_tokens[:cut_idx] + new_tokens)
            committed.extend(self._split_completed_sentences(ts))
            return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        asr_suf_idx = self._find_asr_suffix_overlap(active_norms, new_norms)
        if asr_suf_idx is not None:
            self._active_tokens = _dedup_repeated_phrases(self._active_tokens[:asr_suf_idx] + new_tokens)
            committed.extend(self._split_completed_sentences(ts))
            return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 6. 同长度局部纠偏（如 OCR 首帧将 'welcome' 误识别为 'welcom'，下一帧修正）
        if abs(len(active_norms) - len(new_norms)) <= 1 and len(active_norms) >= 3:
            min_len = min(len(active_norms), len(new_norms))
            same_pos = sum(1 for a, b in zip(active_norms[:min_len], new_norms[:min_len]) if a == b)
            if same_pos / max(1, min_len) >= 0.6:
                if (
                    len(new_norms) == len(active_norms) - 1
                    and new_norms[-1] != active_norms[-1]
                    and not _is_sentence_end_token(new_tokens[-1])
                    and _is_similar_token(new_norms[-1], active_norms[-2])
                    and _match_asr_contraction_at(active_norms[-2:], new_norms[-1:]) is None
                ):
                    self._active_tokens = _dedup_repeated_phrases(new_tokens + [self._active_tokens[-1]])
                else:
                    self._active_tokens = _dedup_repeated_phrases(new_tokens)
                committed.extend(self._split_completed_sentences(ts))
                return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 6.5 前缀锚定的 ASR 实时改写（例如 'Today we will' -> 'Today we\'ll discuss'）
        if self._is_asr_prefix_revision(active_norms, new_norms):
            self._active_tokens = _dedup_repeated_phrases(new_tokens)
            committed.extend(self._split_completed_sentences(ts))
            return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 6.6 缩略改写伴随尾部临时裁切（如 'It is not easy to learn' -> 'It\'s not easy'）：保留原尾部不丢失也不误切
        if not _is_sentence_end_token(self._active_tokens[-1]):
            ia, ib, contr_hits, exact_apos = _asr_match_advance(active_norms, new_norms)
            if contr_hits >= 1 and exact_apos and ib == len(new_norms) and ib >= 2 and ia < len(active_norms):
                self._active_tokens = _dedup_repeated_phrases(new_tokens + self._active_tokens[ia:])
                committed.extend(self._split_completed_sentences(ts))
                return {"active_en": self.active_en, "committed": committed, "changed": self.active_en != prev_en or bool(committed)}

        # 7. 完全无重叠：旧句结束归档，开启全新句子（保留近期已归档前缀防后续双行帧回弹）
        self._last_switched_prev_norms = list(active_norms)
        self._last_switched_ts = ts
        entry = self._commit_tokens(self._active_tokens, ts)
        if entry:
            committed.append(entry)
        self._record_committed_tail(self._active_tokens, ts)
        self._active_tokens = new_tokens
        self._active_started_ts = ts
        committed.extend(self._split_completed_sentences(ts))
        return {"active_en": self.active_en, "committed": committed, "changed": True}


class YouTubeTranscriptLoader:
    """
    免插件 YouTube 完整字幕提取器与时间轴同步引擎：
    1. 支持 watch / youtu.be / shorts / live 链接直接提取，以及通过浏览器标题自动匹配视频 ID；
    2. 优先使用免令牌 InnerTube 移动端接口（IOS / ANDROID）拉取完整 timedtext 字幕轨，绕过网页 pot 空响应限制；
    3. 自动按自然句/从句分段并标注精确 [start, end] 时间戳，支持时间轴 0ms 驱动与视觉进度双校准。
    """

    _TITLE_VID_CACHE: Dict[str, str] = {}
    _IOS_CLIENT = {
        "clientName": "IOS",
        "clientVersion": "20.10.4",
        "hl": "en",
    }
    _IOS_UA = "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)"
    _ANDROID_CLIENT = {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "androidSdkVersion": 34,
        "hl": "en",
    }
    _ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip"
    _WEB_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    @staticmethod
    def extract_video_id(url_or_id: str) -> str:
        raw = (url_or_id or "").strip()
        if not raw:
            return ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
            return raw
        if "youtube.com" not in raw.lower() and "youtu.be" not in raw.lower():
            return ""
        patterns = [
            r"(?:[?&]v=|/v/|/embed/|/shorts/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])",
        ]
        for pat in patterns:
            m = re.search(pat, raw)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def extract_youtube_title_from_window(win_title: str) -> str:
        """从浏览器/客户端窗口标题中提取纯净 YouTube 视频标题（兼容 Edge 零宽字符及多标签后缀）。"""
        raw = (win_title or "").replace("\u200b", "").replace("\ufeff", "").strip()
        if not raw or "youtube" not in raw.lower():
            return ""
        # 剥离未读角标 "(3) " 等
        raw = re.sub(r"^\(\d+\)\s*", "", raw).strip()
        # 兼容 Edge/Chrome 多标签标题: "1.2 The network edge - YouTube and 17 more pages - Personal - Microsoft Edge"
        m = re.match(
            r"^(.+?)\s*[-—–]\s*YouTube(?:\s*Music)?(?:\s+and\s+\d+\s+more\s+pages?|\s+和另外\s*\d+\s*个页面|\s*[-—–].*|\s*$)",
            raw,
            flags=re.IGNORECASE,
        )
        if not m:
            return ""
        title = m.group(1).strip()
        if not title or title.lower() in {"youtube", "home", "subscriptions", "shorts", "library", "history", "explore", "music"}:
            return ""
        return title

    @staticmethod
    def _urlopen_prefer_system(req: urllib.request.Request, timeout: float = 4.5) -> bytes:
        # 若未配置系统代理且 urlopen 未被单测打桩，直接单次直连避免重复超时
        is_mocked = getattr(urllib.request.urlopen, "__module__", "") != "urllib.request"
        if not is_mocked and not urllib.request.getproxies():
            with _open_url_direct(req, timeout=timeout) as resp:
                return resp.read()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as he:
            # 明确的 HTTP 业务/鉴权错误（如 403/404/429）说明代理链路已通，绝不盲目直连重试放大超时
            try:
                he.close()
            except Exception:
                pass
            raise
        except Exception:
            with _open_url_direct(req, timeout=timeout) as resp:
                return resp.read()

    @classmethod
    def resolve_video_id_by_title(cls, video_title: str, timeout: float = 4.0) -> str:
        """通过 YouTube 搜索接口将浏览器的视频标题自动解析为 11 位 videoId。"""
        q = (video_title or "").strip()
        if not q or len(q) < 2:
            return ""
        cache_key = q.lower()
        cached = cls._TITLE_VID_CACHE.get(cache_key)
        if cached:
            return cached

        payload = json.dumps(
            {
                "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240726.00.00", "hl": "en"}},
                "query": q,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": cls._WEB_UA},
        )
        raw = cls._urlopen_prefer_system(req, timeout=timeout).decode("utf-8", errors="ignore")
        # 优先匹配带有相近标题的 videoRenderer，找不到再兜底首个 videoId
        q_norms = set(_norm_token(t) for t in q.split() if _norm_token(t))
        candidates: List[Tuple[int, str]] = []
        for m in re.finditer(r'"videoRenderer":\s*\{"videoId":"([A-Za-z0-9_-]{11})".*?"title":\{"runs":\[\{"text":"([^"]*)"', raw):
            vid, vtitle = m.group(1), m.group(2)
            t_norms = set(_norm_token(t) for t in vtitle.split() if _norm_token(t))
            score = len(q_norms & t_norms) if q_norms else 0
            candidates.append((score, vid))
        chosen = ""
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            chosen = candidates[0][1]
        else:
            m_any = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', raw)
            if m_any:
                chosen = m_any.group(1)
        if chosen:
            if len(cls._TITLE_VID_CACHE) > 80:
                cls._TITLE_VID_CACHE.clear()
            cls._TITLE_VID_CACHE[cache_key] = chosen
        return chosen

    @classmethod
    def _extract_player_response(cls, video_id: str, timeout: float = 4.5) -> Dict[str, Any]:
        api_url = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
        # 1. 优先尝试 InnerTube IOS / ANDROID 接口（timedtext 链接直连可用，不会被网页 exp=xpe 拦截返回空体）
        for client_cfg, ua in (
            (cls._IOS_CLIENT, cls._IOS_UA),
            (cls._ANDROID_CLIENT, cls._ANDROID_UA),
        ):
            try:
                payload = json.dumps({"context": {"client": client_cfg}, "videoId": video_id}).encode("utf-8")
                api_req = urllib.request.Request(
                    api_url,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": ua},
                )
                raw_json = cls._urlopen_prefer_system(api_req, timeout=timeout).decode("utf-8", errors="ignore").strip()
                if raw_json.startswith("{"):
                    parsed = json.loads(raw_json)
                    tracks = (
                        parsed.get("captions", {})
                        .get("playerCaptionsTracklistRenderer", {})
                        .get("captionTracks", [])
                    )
                    if tracks:
                        parsed["_screenlex_ua"] = ua
                        return parsed
            except Exception:
                continue

        # 2. 回退：解析 watch 页面中的 ytInitialPlayerResponse
        watch_url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
        req = urllib.request.Request(
            watch_url,
            headers={"User-Agent": cls._WEB_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
        raw_html = cls._urlopen_prefer_system(req, timeout=timeout).decode("utf-8", errors="ignore")
        m_assign = re.search(r"ytInitialPlayerResponse\s*=\s*\{", raw_html)
        if m_assign:
            brace_start = m_assign.end() - 1
            try:
                decoder = json.JSONDecoder()
                parsed, _ = decoder.raw_decode(raw_html[brace_start:])
                if isinstance(parsed, dict) and parsed.get("captions"):
                    parsed["_screenlex_ua"] = cls._WEB_UA
                    return parsed
            except Exception:
                pass
        raise RuntimeError("未能获取到该 YouTube 视频的字幕轨道数据")

    @classmethod
    def _pick_caption_track(cls, tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not tracks:
            return None
        # 1. 手动英文字幕
        for t in tracks:
            lang = (t.get("languageCode") or "").lower()
            if lang.startswith("en") and t.get("kind") != "asr":
                return t
        # 2. 自动生成英文字幕
        for t in tracks:
            lang = (t.get("languageCode") or "").lower()
            if lang.startswith("en"):
                return t
        # 3. 兜底返回第一条字幕轨
        return tracks[0]

    @classmethod
    def _parse_caption_payload(cls, raw_bytes: bytes) -> List[Dict[str, Any]]:
        text = raw_bytes.decode("utf-8", errors="ignore").strip()
        if not text:
            raise RuntimeError("字幕数据为空")
        if text.lstrip().lower().startswith("<!doctype html") or text.lstrip().lower().startswith("<html"):
            raise RuntimeError("字幕接口被风控或返回了网页内容")
        items: List[Dict[str, Any]] = []
        if text.startswith("{"):
            data = json.loads(text)
            for ev in data.get("events", []):
                segs = ev.get("segs") or []
                phrase = "".join(s.get("utf8", "") for s in segs)
                phrase = html.unescape(re.sub(r"\s+", " ", phrase)).strip()
                if not phrase or (phrase.startswith("[") and phrase.endswith("]")):
                    continue
                start_sec = float(ev.get("tStartMs", 0)) / 1000.0
                dur_sec = float(ev.get("dDurationMs", 2000)) / 1000.0
                items.append({"start": start_sec, "duration": dur_sec, "en": phrase})
        else:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            for node in root.iter():
                tag_name = node.tag.rsplit("}", 1)[-1]
                if tag_name in ("text", "p"):
                    raw_txt = "".join(node.itertext())
                    phrase = html.unescape(re.sub(r"\s+", " ", raw_txt)).strip()
                    if not phrase or (phrase.startswith("[") and phrase.endswith("]")):
                        continue
                    if "start" in node.attrib:
                        start_sec = float(node.attrib.get("start", "0"))
                        dur_sec = float(node.attrib.get("dur", "2.0"))
                    else:
                        start_sec = float(node.attrib.get("t", "0")) / 1000.0
                        dur_sec = float(node.attrib.get("d", "2000")) / 1000.0
                    items.append({"start": start_sec, "duration": dur_sec, "en": phrase})
        return items

    @staticmethod
    def merge_raw_segments(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 2~4 秒碎词按真实句末标点、说话停顿、子句边界智能断句并保留精确词级时间轴。"""
        merged: List[Dict[str, Any]] = []
        buf_toks: List[Tuple[str, float, float]] = []

        def _flush(count: Optional[int] = None):
            nonlocal buf_toks
            if not buf_toks:
                return
            take = len(buf_toks) if count is None else max(1, min(len(buf_toks), int(count)))
            chunk = buf_toks[:take]
            buf_toks = buf_toks[take:]
            sentence = re.sub(r"\s+", " ", " ".join(w for w, _, _ in chunk)).strip()
            if sentence:
                idx = len(merged) + 1
                start_v = chunk[0][1]
                end_v = max(start_v + 0.65, chunk[-1][2])
                merged.append(
                    {
                        "entry_id": idx,
                        "timestamp_str": _format_video_sec(start_v),
                        "start": start_v,
                        "end": end_v,
                        "duration": end_v - start_v,
                        "en_text": sentence,
                        "cn_text": "",
                    }
                )

        # 预处理：拆开单条字幕内的多句文本并校正前后重叠时间
        sub_items: List[Tuple[str, float, float]] = []
        clean_items = [it for it in raw_items if it.get("en", "").strip()]
        n_raw = len(clean_items)
        for i, item in enumerate(clean_items):
            seg_text = item["en"].strip()
            s_t = float(item.get("start", 0.0))
            d_t = float(item.get("duration", 2.0))
            raw_end = s_t + d_t
            if i + 1 < n_raw:
                next_s = float(clean_items[i + 1].get("start", raw_end))
                eff_end = min(raw_end, max(s_t + 0.6, next_s)) if next_s > s_t else raw_end
            else:
                eff_end = raw_end
            sub_items.extend(_split_item_by_sentences(seg_text, s_t, eff_end))

        n_sub = len(sub_items)
        for i, (sub_txt, s_t, e_t) in enumerate(sub_items):
            if buf_toks:
                prev_end = buf_toks[-1][2]
                gap = s_t - prev_end
                last_norm = _norm_token(buf_toks[-1][0])
                if gap >= 1.8 or (gap >= 1.05 and len(buf_toks) >= 4 and last_norm not in _NON_TERMINAL_TAIL_WORDS):
                    _flush()

            words = [w for w in sub_txt.split() if w]
            if not words:
                continue
            dur = max(0.4, e_t - s_t)
            nw = len(words)
            for j, w in enumerate(words):
                ws = s_t + dur * (j / nw)
                we = s_t + dur * ((j + 1) / nw)
                buf_toks.append((w, ws, we))

            while buf_toks:
                last_tok = buf_toks[-1][0]
                last_norm = _norm_token(last_tok)
                cur_dur = buf_toks[-1][2] - buf_toks[0][1]
                is_real_end = _is_sentence_end_token(last_tok)

                if is_real_end and (len(buf_toks) >= 2 or cur_dur >= 1.2):
                    _flush()
                    break

                # 若当前尾部停在冠词/介词/助动词等非完结词上且仍紧邻后续文本，延后一帧等待后续补全再寻自然断句点
                next_gap = (sub_items[i + 1][1] - buf_toks[-1][2]) if (i + 1 < n_sub) else 99.0
                if (
                    len(buf_toks) < 15
                    and i + 1 < n_sub
                    and next_gap <= 0.75
                    and last_norm in _NON_TERMINAL_TAIL_WORDS
                ):
                    break

                # 逗号/分号收尾且长度已够 8 词
                if _has_clause_break_punct(last_tok) and last_norm not in _NON_TERMINAL_TAIL_WORDS and len(buf_toks) >= 8:
                    _flush()
                    break

                need_clause_check = (len(buf_toks) >= 13) or (cur_dur >= 5.2 and len(buf_toks) >= 8)
                if not need_clause_check:
                    break

                next_words = sub_items[i + 1][0].split() if (i + 1 < n_sub) else []
                next_is_tiny_tail = (
                    i + 1 < n_sub
                    and next_gap <= 0.65
                    and len(buf_toks) <= 15
                    and (
                        len(next_words) <= 2
                        or (len(next_words) <= 4 and _is_sentence_end_token(next_words[-1]))
                    )
                )
                if next_is_tiny_tail:
                    break

                tok_strs = [w for w, _, _ in buf_toks]
                cut = _find_natural_clause_split(tok_strs, min_idx=5, max_idx=len(tok_strs) - 2, soft_target=10)
                if cut != -1:
                    _flush(cut)
                    continue

                # 无标点无连词但词数较长：寻找最近的非虚词结尾避免腰斩 "the / to / of"
                if last_norm not in _NON_TERMINAL_TAIL_WORDS and len(buf_toks) <= 15:
                    _flush()
                    break
                if len(buf_toks) >= 16:
                    safe_cut = -1
                    for cand in range(min(13, len(tok_strs) - 2), 5, -1):
                        if _norm_token(tok_strs[cand - 1]) not in _NON_TERMINAL_TAIL_WORDS:
                            safe_cut = cand
                            break
                    if safe_cut == -1:
                        safe_cut = 13
                    _flush(safe_cut)
                    continue
                break

        _flush()
        # 二次校准：确保 start 严格单调递增，且前一段 end 不跨越下一段 start（防止 ASR 时间重叠干扰定位）
        for i in range(1, len(merged)):
            if merged[i]["start"] < merged[i - 1]["start"] + 0.05:
                merged[i]["start"] = merged[i - 1]["start"] + 0.05
                merged[i]["timestamp_str"] = _format_video_sec(merged[i]["start"])
                merged[i]["end"] = max(merged[i]["start"] + 0.4, merged[i]["end"])
                merged[i]["duration"] = merged[i]["end"] - merged[i]["start"]
        for i in range(len(merged) - 1):
            nxt_s = merged[i + 1]["start"]
            if nxt_s > merged[i]["start"]:
                merged[i]["end"] = min(merged[i]["end"], nxt_s)
                merged[i]["duration"] = max(0.5, merged[i]["end"] - merged[i]["start"])
        return merged

    @classmethod
    def fetch_youtube_transcript(cls, url_or_id: str, timeout: float = 4.5) -> Dict[str, Any]:
        video_id = cls.extract_video_id(url_or_id)
        if not video_id:
            raise ValueError("未能从输入中识别出有效的 YouTube 视频链接或 ID")

        player_data = cls._extract_player_response(video_id, timeout=timeout)
        req_ua = player_data.get("_screenlex_ua", cls._IOS_UA)
        title = player_data.get("videoDetails", {}).get("title", f"YouTube ({video_id})")
        tracks = (
            player_data.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
        track = cls._pick_caption_track(tracks)
        if not track or not track.get("baseUrl"):
            raise RuntimeError("该视频未提供可用的字幕轨（CC）")

        base_url = track["baseUrl"]
        sep = "&" if "?" in base_url else "?"
        json3_url = base_url + (f"{sep}fmt=json3" if "fmt=" not in base_url else "")
        raw_items: List[Dict[str, Any]] = []
        try:
            raw_bytes = cls._urlopen_prefer_system(
                urllib.request.Request(base_url, headers={"User-Agent": req_ua}),
                timeout=timeout,
            )
            raw_items = cls._parse_caption_payload(raw_bytes)
        except Exception:
            raw_bytes = cls._urlopen_prefer_system(
                urllib.request.Request(json3_url, headers={"User-Agent": req_ua}),
                timeout=timeout,
            )
            raw_items = cls._parse_caption_payload(raw_bytes)

        segments = cls.merge_raw_segments(raw_items)
        return {
            "video_id": video_id,
            "title": title,
            "language": track.get("languageCode", "en"),
            "segments": segments,
        }

    @staticmethod
    def find_segment_index_at_time(transcript: List[Dict[str, Any]], current_sec: float) -> int:
        """根据当前播放时间（秒）定位应展示的字幕段索引；若处于长片头静默期返回 -1。"""
        if not transcript:
            return -1
        lead_sec = current_sec + 0.15
        first_start = float(transcript[0].get("start", 0.0))
        if first_start <= 1.5:
            if lead_sec < 0:
                return 0
        elif lead_sec < first_start:
            return -1

        chosen = 0
        for i, seg in enumerate(transcript):
            if float(seg.get("start", 0.0)) <= lead_sec:
                chosen = i
            else:
                break
        return chosen

    @staticmethod
    def match_timeline_index(
        ocr_snippet: str,
        transcript: List[Dict[str, Any]],
        current_idx: int = 0,
    ) -> int:
        """
        视觉锚定时间轴定位：用当前屏幕 OCR 识别出的残碎字幕在完整字幕轨中匹配定位。
        先查当前进度附近窗口，未命中再全局扫描以兼容用户拖拽进度条快进/快退。
        """
        if not transcript or not ocr_snippet:
            return -1
        ocr_tokens = [_norm_token(t) for t in ocr_snippet.split() if _norm_token(t)]
        if len(ocr_tokens) < 2 and sum(len(t) for t in ocr_tokens) < 6:
            return -1

        ocr_set = set(ocr_tokens)
        ocr_bigrams = set(zip(ocr_tokens, ocr_tokens[1:])) if len(ocr_tokens) >= 2 else set()

        def _score_seg(seg_en: str) -> float:
            seg_toks = [_norm_token(t) for t in seg_en.split() if _norm_token(t)]
            if not seg_toks:
                return 0.0
            seg_set = set(seg_toks)
            overlap_cnt = len(ocr_set & seg_set)
            if overlap_cnt == 0:
                return 0.0
            uni_score = overlap_cnt / max(1, len(ocr_set))
            if ocr_bigrams:
                seg_bigrams = set(zip(seg_toks, seg_toks[1:]))
                bi_score = len(ocr_bigrams & seg_bigrams) / max(1, len(ocr_bigrams))
                return 0.55 * uni_score + 0.45 * bi_score
            return uni_score

        n = len(transcript)
        lo = max(0, current_idx - 4)
        hi = min(n, current_idx + 18)
        best_idx = -1
        best_score = 0.0

        for idx in range(lo, hi):
            sc = _score_seg(transcript[idx].get("en_text", ""))
            # 略微偏向前进方向或保持稳定
            if idx >= current_idx:
                sc += 0.02
            if sc > best_score:
                best_score = sc
                best_idx = idx

        if best_score >= 0.50:
            return best_idx

        # 全局扫描应对大幅快进/快退（收紧阈值并要求至少 3 词匹配或极高分，防止高频短语误跳）
        global_idx = -1
        global_score = 0.0
        for idx, seg in enumerate(transcript):
            sc = _score_seg(seg.get("en_text", ""))
            if sc > global_score:
                global_score = sc
                global_idx = idx

        min_global_score = 0.75 if len(ocr_tokens) >= 3 else 0.88
        if global_score >= min_global_score:
            return global_idx
        return -1


BILI_AUTH_SETTINGS_KEY = "bilibili_auth"


class BilibiliAuthManager:
    """B站官方网页扫码登录凭证管理（持久化保存 SESSDATA / bili_jct / DedeUserID 至 app_settings）。"""

    _WEB_UA = YouTubeTranscriptLoader._WEB_UA

    @staticmethod
    def _build_direct_opener(cj: Optional[http.cookiejar.CookieJar] = None):
        handlers: List[Any] = [urllib.request.ProxyHandler({})]
        if cj is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(cj))
        return urllib.request.build_opener(*handlers)

    @staticmethod
    def _is_urlopen_mocked() -> bool:
        uopen = urllib.request.urlopen
        return hasattr(uopen, "_mock_name") or getattr(uopen, "__name__", "") != "urlopen"

    @classmethod
    def get_auth(cls) -> Dict[str, Any]:
        raw = get_setting(BILI_AUTH_SETTINGS_KEY, None)
        return dict(raw) if isinstance(raw, dict) else {}

    @classmethod
    def has_sessdata(cls) -> bool:
        return bool(cls.get_auth().get("sessdata", "").strip())

    @classmethod
    def get_display_name(cls) -> str:
        auth = cls.get_auth()
        if not auth.get("sessdata"):
            return ""
        return str(auth.get("uname") or auth.get("dede_uid") or "已登录").strip()

    @classmethod
    def get_cookie_header(cls, buvid3: str = "", buvid4: str = "") -> str:
        auth = cls.get_auth()
        parts: List[str] = []
        if buvid3:
            parts.append(f"buvid3={buvid3}")
        if buvid4:
            parts.append(f"buvid4={buvid4}")
        if auth.get("sessdata"):
            parts.append(f"SESSDATA={auth['sessdata']}")
        if auth.get("bili_jct"):
            parts.append(f"bili_jct={auth['bili_jct']}")
        if auth.get("dede_uid"):
            parts.append(f"DedeUserID={auth['dede_uid']}")
        return "; ".join(parts)

    @classmethod
    def save_auth(cls, sessdata: str, bili_jct: str = "", dede_uid: str = "", uname: str = "") -> bool:
        if not (sessdata or "").strip():
            return False
        return set_setting(
            BILI_AUTH_SETTINGS_KEY,
            {
                "sessdata": sessdata.strip(),
                "bili_jct": (bili_jct or "").strip(),
                "dede_uid": (dede_uid or "").strip(),
                "uname": (uname or "").strip(),
                "updated_at": int(time.time()),
            },
        )

    @classmethod
    def clear_auth(cls) -> bool:
        return set_setting(BILI_AUTH_SETTINGS_KEY, {})

    @classmethod
    def _direct_read(cls, req: urllib.request.Request, timeout: float = 5.0) -> bytes:
        """国内 B站接口严格遵循 AGENTS.md §19.2：旁路系统代理直连（兼容单测 urlopen 打桩）。"""
        if cls._is_urlopen_mocked():
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        with _open_url_direct(req, timeout=timeout) as resp:
            return resp.read()

    @classmethod
    def start_qr_login(cls, timeout: float = 6.0) -> Dict[str, str]:
        req = urllib.request.Request(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            headers={"User-Agent": cls._WEB_UA, "Referer": "https://www.bilibili.com/"},
        )
        raw = cls._direct_read(req, timeout=timeout).decode("utf-8", errors="ignore")
        parsed = json.loads(raw)
        if parsed.get("code") != 0 or not parsed.get("data", {}).get("url"):
            raise RuntimeError(parsed.get("message") or "无法生成 B站登录二维码")
        d = parsed["data"]
        return {"url": str(d["url"]), "qrcode_key": str(d.get("qrcode_key") or "")}

    @classmethod
    def poll_qr_login(cls, qrcode_key: str, timeout: float = 6.0) -> Dict[str, Any]:
        if not qrcode_key:
            return {"status": "error", "message": "缺少 qrcode_key"}
        poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={urllib.parse.quote(qrcode_key)}"
        headers = {"User-Agent": cls._WEB_UA, "Referer": "https://www.bilibili.com/"}

        cj = http.cookiejar.CookieJar()
        opener = cls._build_direct_opener(cj)

        def _fetch(url: str) -> bytes:
            req = urllib.request.Request(url, headers=headers)
            if cls._is_urlopen_mocked():
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()

        raw = _fetch(poll_url).decode("utf-8", errors="ignore")
        parsed = json.loads(raw)
        data = parsed.get("data") or {}
        code = int(data.get("code", -1))

        if code == 86101:
            return {"status": "waiting", "message": "请用手机哔哩哔哩 App 扫码"}
        if code == 86090:
            return {"status": "scanned", "message": "已扫码，请在手机端点击确认登录"}
        if code == 86038:
            return {"status": "expired", "message": "二维码已过期，请刷新后重试"}
        if code != 0:
            return {"status": "error", "message": str(data.get("message") or parsed.get("message") or f"状态码 {code}")}

        cross_url = str(data.get("url") or "").strip()
        if cross_url:
            try:
                _fetch(cross_url)
            except Exception:
                pass

        cookies_map: Dict[str, str] = {}
        for ck in cj:
            cookies_map[ck.name] = ck.value
        if cross_url and "SESSDATA" not in cookies_map:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(cross_url).query)
            for k in ("SESSDATA", "bili_jct", "DedeUserID"):
                if qs.get(k):
                    cookies_map[k] = qs[k][0]

        sessdata = cookies_map.get("SESSDATA", "")
        if not sessdata:
            return {"status": "error", "message": "未能从回调中读取到 SESSDATA"}

        bili_jct = cookies_map.get("bili_jct", "")
        dede_uid = cookies_map.get("DedeUserID", "")
        uname = ""
        try:
            nav_headers = dict(headers)
            nav_headers["Cookie"] = f"SESSDATA={sessdata}; bili_jct={bili_jct}; DedeUserID={dede_uid}"
            nav_req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=nav_headers)
            if cls._is_urlopen_mocked():
                with urllib.request.urlopen(nav_req, timeout=4.0) as nr:
                    nav_json = json.loads(nr.read().decode("utf-8", errors="ignore"))
            else:
                with opener.open(nav_req, timeout=4.0) as nr:
                    nav_json = json.loads(nr.read().decode("utf-8", errors="ignore"))
            uname = str(nav_json.get("data", {}).get("uname") or "")
        except Exception:
            uname = ""

        cls.save_auth(sessdata, bili_jct=bili_jct, dede_uid=dede_uid, uname=uname)
        return {"status": "success", "uname": uname, "message": f"已登录：{uname or dede_uid or 'B站账号'}"}


class BilibiliTranscriptLoader:
    """
    Bilibili (B站) 视频标识解析、WBI 签名字幕轨拉取及时间轴同步加载器：
    - 支持 bilibili.com/video/BV...（含分P ?p=N）、av号与 b23.tv 短链；
    - 仅选择英文字幕轨（en / ai-en）送入英中双语流水线；无英文 CC 或需授权时提示并平滑交由 OCR 兜底。
    """

    _MIXIN_KEY_ENC_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
        33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
        61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
        36, 20, 34, 44, 52,
    ]
    _WEB_UA = YouTubeTranscriptLoader._WEB_UA
    _buvid_lock = threading.Lock()
    _buvid_cache: Tuple[str, str] = ("", "")
    _wbi_cache: Tuple[str, float] = ("", 0.0)

    @staticmethod
    def is_bilibili_input(url_or_id: str) -> bool:
        raw = (url_or_id or "").strip().lower()
        if not raw:
            return False
        if "bilibili.com" in raw or "b23.tv" in raw:
            return True
        if re.fullmatch(r"(?:bv[0-9a-z]{10}|av\d{4,12})(?:[/?#].*)?", raw, flags=re.IGNORECASE):
            return True
        return False

    @staticmethod
    def extract_bvid_and_page(url_or_id: str) -> Tuple[str, int]:
        raw = (url_or_id or "").strip()
        if not raw:
            return "", 1
        page = 1
        m_p = re.search(r"[?&]p=(\d+)", raw)
        if m_p:
            try:
                page = max(1, int(m_p.group(1)))
            except Exception:
                page = 1

        m_bv = re.search(r"(BV[0-9A-Za-z]{10})", raw)
        if m_bv:
            return m_bv.group(1), page

        low = raw.lower()
        if "bilibili.com" in low or re.match(r"^av\d{4,12}(?:[/?#]|$)", low):
            m_av = re.search(r"(?:^|/)(av\d{4,12})(?:[/?#]|$)", raw, flags=re.IGNORECASE)
            if m_av:
                return m_av.group(1).lower(), page
        return "", 1

    @staticmethod
    def canonical_id(vid: str, page: int = 1) -> str:
        if not vid:
            return ""
        return f"bili:{vid}:p{max(1, int(page))}"

    @staticmethod
    def extract_bilibili_title_from_window(win_title: str) -> str:
        raw = (win_title or "").replace("\u200b", "").replace("\ufeff", "").strip()
        if not raw or ("bilibili" not in raw.lower() and "哔哩哔哩" not in raw):
            return ""
        raw = re.sub(r"^\(\d+\)\s*", "", raw).strip()
        m = re.match(
            r"^(.+?)(?:_哔哩哔哩(?:_bilibili)?|\s*[-—–]\s*哔哩哔哩)(?:\s+and\s+\d+\s+more\s+pages?|\s+和另外\s*\d+\s*个页面|\s*[-—–].*|\s*$)",
            raw,
            flags=re.IGNORECASE,
        )
        if not m:
            return ""
        return m.group(1).strip()

    @classmethod
    def _bili_headers(cls, buvid3: str = "", buvid4: str = "") -> Dict[str, str]:
        h = {
            "User-Agent": cls._WEB_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        ck = BilibiliAuthManager.get_cookie_header(buvid3=buvid3, buvid4=buvid4)
        if ck:
            h["Cookie"] = ck
        return h

    @classmethod
    def _ensure_buvid(cls, timeout: float = 4.5) -> Tuple[str, str]:
        with cls._buvid_lock:
            if cls._buvid_cache[0]:
                return cls._buvid_cache
        try:
            req = urllib.request.Request(
                "https://api.bilibili.com/x/frontend/finger/spi",
                headers=cls._bili_headers(),
            )
            raw = BilibiliAuthManager._direct_read(req, timeout=timeout).decode("utf-8", errors="ignore")
            data = json.loads(raw).get("data") or {}
            b3 = str(data.get("b_3") or "")
            b4 = str(data.get("b_4") or "")
            if b3:
                with cls._buvid_lock:
                    cls._buvid_cache = (b3, b4)
                return b3, b4
        except Exception:
            pass
        return "", ""

    @classmethod
    def _get_wbi_mixin_key(cls, buvid3: str = "", buvid4: str = "", timeout: float = 4.5) -> str:
        now = time.time()
        with cls._buvid_lock:
            if cls._wbi_cache[0] and (now - cls._wbi_cache[1]) < 600.0:
                return cls._wbi_cache[0]
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=cls._bili_headers(buvid3, buvid4),
        )
        raw = BilibiliAuthManager._direct_read(req, timeout=timeout).decode("utf-8", errors="ignore")
        wbi = json.loads(raw).get("data", {}).get("wbi_img", {})
        img_k = str(wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub_k = str(wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        raw_k = img_k + sub_k
        if len(raw_k) < 32:
            return ""
        mixin = "".join(raw_k[i] for i in cls._MIXIN_KEY_ENC_TAB if i < len(raw_k))[:32]
        with cls._buvid_lock:
            cls._wbi_cache = (mixin, now)
        return mixin

    @classmethod
    def sign_wbi_params(cls, params: Dict[str, Any], mixin_key: str) -> str:
        signed = {k: str(v) for k, v in params.items()}
        signed["wts"] = str(int(time.time()))
        clean_items: List[Tuple[str, str]] = []
        for k, v in sorted(signed.items()):
            cv = re.sub(r"[!'()*]", "", v)
            clean_items.append((k, cv))
        query = urllib.parse.urlencode(clean_items)
        w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return f"{query}&w_rid={w_rid}"

    @staticmethod
    def _pick_subtitle_track(tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not tracks:
            return None
        # 仅挑选英文轨道（先人工英文字幕 en，再 AI 英文字幕 ai-en），绝不将纯中文字幕喂给英文翻译管道
        for t in tracks:
            lan = str(t.get("lan") or "").lower()
            if lan == "en" or lan.startswith("en-") or lan.startswith("en_"):
                return t
        for t in tracks:
            lan = str(t.get("lan") or "").lower()
            if "ai-en" in lan or "en" in lan:
                return t
        for t in tracks:
            doc = str(t.get("lan_doc") or "").lower()
            lan = str(t.get("lan") or "").lower()
            if ("english" in doc or "英文" in doc or "英语" in doc) and not lan.startswith(("zh", "ai-zh")):
                return t
        return None

    @staticmethod
    def _extract_bilingual_en_items(body: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """当 B站外挂字幕被 UP主标为 zh-CN 但内含中英双语分行时，智能抽取纯英文对白行。"""
        items: List[Dict[str, Any]] = []
        total_nonempty = 0
        for item in body or []:
            raw = html.unescape(str(item.get("content") or "")).strip()
            if not raw:
                continue
            total_nonempty += 1
            parts = [re.sub(r"\s+", " ", ln).strip() for ln in re.split(r"[\r\n]+", raw) if ln.strip()]
            en_parts: List[str] = []
            for p in parts:
                en_only = re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", p)
                en_only = re.sub(r"\s+", " ", en_only).strip(" -/|·—")
                en_words = re.findall(r"[A-Za-z]{2,}", en_only)
                cjk_cnt = sum(1 for c in p if "\u4e00" <= c <= "\u9fff")
                ascii_cnt = sum(1 for c in p if c.isascii() and c.isalpha())
                if len(en_words) >= 2 and ascii_cnt >= cjk_cnt:
                    en_parts.append(en_only if cjk_cnt > 0 else p)
            if en_parts:
                s_t = float(item.get("from", 0.0))
                e_t = float(item.get("to", s_t + 2.0))
                items.append({"start": s_t, "duration": max(0.4, e_t - s_t), "en": " ".join(en_parts)})
        if total_nonempty == 0 or len(items) < max(3, int(total_nonempty * 0.25)):
            return []
        return items

    @classmethod
    def _resolve_short_url(cls, url: str, timeout: float = 4.5) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": cls._WEB_UA})
        with _open_url_direct(req, timeout=timeout) as resp:
            return str(resp.geturl() or url)

    @classmethod
    def fetch_bilibili_transcript(cls, url_or_id: str, timeout: float = 5.0) -> Dict[str, Any]:
        target_in = (url_or_id or "").strip()
        if "b23.tv" in target_in.lower():
            try:
                target_in = cls._resolve_short_url(target_in, timeout=timeout)
            except Exception:
                pass

        vid, page = cls.extract_bvid_and_page(target_in)
        if not vid:
            raise ValueError("未能识别出有效的 B站视频链接或 BV 号")

        b3, b4 = cls._ensure_buvid(timeout=timeout)
        headers = cls._bili_headers(b3, b4)

        if vid.startswith("av"):
            view_url = f"https://api.bilibili.com/x/web-interface/view?aid={vid[2:]}"
        else:
            view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={vid}"

        view_raw = BilibiliAuthManager._direct_read(
            urllib.request.Request(view_url, headers=headers), timeout=timeout
        ).decode("utf-8", errors="ignore")
        view_json = json.loads(view_raw)
        if view_json.get("code") != 0 or not view_json.get("data"):
            raise RuntimeError(view_json.get("message") or "无法读取 B站视频信息")

        vdata = view_json["data"]
        bvid = str(vdata.get("bvid") or vid)
        title = str(vdata.get("title") or f"Bilibili ({bvid})").strip()
        pages = vdata.get("pages") or []
        cid = vdata.get("cid")
        if pages and 1 <= page <= len(pages):
            p_item = pages[page - 1]
            cid = p_item.get("cid") or cid
            part_name = str(p_item.get("part") or "").strip()
            if part_name and len(pages) > 1:
                title = f"P{page} {part_name} - {title}"
        if not cid:
            raise RuntimeError("未能获取该 B站视频的 CID")

        view_subs = (vdata.get("subtitle") or {}).get("list") or []
        mixin = cls._get_wbi_mixin_key(b3, b4, timeout=timeout)
        if not mixin:
            raise RuntimeError("无法获取 B站 WBI 签名密钥")

        wbi_qs = cls.sign_wbi_params({"bvid": bvid, "cid": cid}, mixin)
        pv2_url = f"https://api.bilibili.com/x/player/wbi/v2?{wbi_qs}"
        pv2_raw = BilibiliAuthManager._direct_read(
            urllib.request.Request(pv2_url, headers=headers), timeout=timeout
        ).decode("utf-8", errors="ignore")
        pv2_json = json.loads(pv2_raw)
        pv2_data = pv2_json.get("data") or {}
        sub_block = pv2_data.get("subtitle") or {}
        subtitles = sub_block.get("subtitles") or []
        need_login = bool(pv2_data.get("need_login_subtitle"))

        raw_items: List[Dict[str, Any]] = []
        track = cls._pick_subtitle_track(subtitles)
        if track:
            sub_url = str(track.get("subtitle_url") or "").strip()
            if not sub_url:
                if not BilibiliAuthManager.has_sessdata():
                    raise RuntimeError("B站外挂字幕需登录账号，请点击「样式 -> B站扫码」授权")
                raise RuntimeError("B站外挂字幕链接为空")
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            sub_raw = BilibiliAuthManager._direct_read(
                urllib.request.Request(sub_url, headers={"User-Agent": cls._WEB_UA, "Referer": "https://www.bilibili.com/"}),
                timeout=timeout,
            ).decode("utf-8", errors="ignore")
            sub_json = json.loads(sub_raw)
            body = sub_json.get("body") or []
            for item in body:
                txt = html.unescape(re.sub(r"\s+", " ", str(item.get("content") or ""))).strip()
                if not txt:
                    continue
                s_t = float(item.get("from", 0.0))
                e_t = float(item.get("to", s_t + 2.0))
                raw_items.append({"start": s_t, "duration": max(0.4, e_t - s_t), "en": txt})
            lang = str(track.get("lan") or "en")
        else:
            # 若仅有 zh* 轨道，检查是否属于“中英双语混排在 zh-CN 中”的 B站搬运课程
            zh_tracks = [t for t in subtitles if str(t.get("subtitle_url") or "").strip()]
            for zt in zh_tracks:
                z_url = str(zt.get("subtitle_url") or "").strip()
                if z_url.startswith("//"):
                    z_url = "https:" + z_url
                try:
                    z_raw = BilibiliAuthManager._direct_read(
                        urllib.request.Request(z_url, headers={"User-Agent": cls._WEB_UA, "Referer": "https://www.bilibili.com/"}),
                        timeout=timeout,
                    ).decode("utf-8", errors="ignore")
                    z_body = json.loads(z_raw).get("body") or []
                    bilingual_items = cls._extract_bilingual_en_items(z_body)
                    if bilingual_items:
                        raw_items = bilingual_items
                        track = zt
                        break
                except Exception:
                    continue
            if not raw_items:
                if (need_login or view_subs) and not BilibiliAuthManager.has_sessdata():
                    raise RuntimeError("B站外挂字幕需登录账号，请点击「样式 -> B站扫码」授权")
                raise RuntimeError("该 B站视频未提供可用的外挂英文字幕轨")
            lang = f"{zt.get('lan') or 'zh'}(en)"

        segments = YouTubeTranscriptLoader.merge_raw_segments(raw_items)
        if not segments:
            raise RuntimeError("B站字幕内容为空")
        return {
            "video_id": cls.canonical_id(bvid, page),
            "platform": "bilibili",
            "title": title,
            "language": lang,
            "segments": segments,
        }


# ---------------- Win32 活动视频窗口探测辅助 ----------------

_ALLOWED_VIDEO_CLASSES = {
    "MozillaWindowClass",
    "PotPlayer64",
    "PotPlayer",
    "mpv",
    "PPTFrameClass",
    "screenClass",
}
_CHROMIUM_VIDEO_CLASSES = {
    "Chrome_WidgetWin_1",
    "Chrome_WidgetWin_0",
}
_BLOCKED_APP_TITLE_KEYWORDS = (
    "visual studio code",
    "antigravity",
    "cursor",
    "pycharm",
    "wechat",
    "微信",
    "qq",
    "powershell",
    "cmd.exe",
    "terminal",
    "notepad",
    "pcb defect",
    "settings",
)
_MEDIA_TITLE_KEYWORDS = (
    "youtube",
    "edge",
    "chrome",
    "firefox",
    "brave",
    "vivaldi",
    "opera",
    "bilibili",
    "coursera",
    "udemy",
    "edx",
    "vlc",
    "potplayer",
    "mpv",
    "player",
    "video",
    "lecture",
    "media",
    "powerpoint",
    "wps",
    ".ppt",
    ".mp4",
    ".mkv",
    "电影",
    "播放",
)


def _get_window_title(hwnd: int) -> str:
    try:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


def _get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        rect = wintypes.RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def _get_window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        cls_buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, cls_buf, 128)
        return cls_buf.value
    except Exception:
        return ""


def _is_non_video_browser_page(hwnd: int, uia_res: Optional[Dict[str, Any]], win_title: str) -> Optional[bool]:
    """
    判定浏览器当前活跃标签页是否属于非视频页面（如 PDF 阅读器、普通文档网页、空白页、网站首页）。
    返回:
    - True: 明确为非视频页面（必须跳过画面 OCR，防止粘附网页/PDF英文正文）
    - False: 明确为视频播放器页面（允许字幕识别）
    - None: 暂无法从本轮确定（沿用上次浏览器状态）
    """
    if not hwnd:
        return False
    cls_name = _get_window_class(hwnd)
    if cls_name not in _CHROMIUM_VIDEO_CLASSES and cls_name != "MozillaWindowClass":
        return False

    t_low = (win_title or "").replace("\u200b", "").replace("\ufeff", "").lower()
    if re.search(r"\.(?:pdf|docx?|pptx?|xlsx?|txt|md)\b", t_low):
        return True

    if uia_res is not None:
        if uia_res.get("player_rect") is not None or uia_res.get("sec") is not None or uia_res.get("video_id"):
            return False
        url = str(uia_res.get("url") or "").strip().lower()
        if url and not url.startswith("data:"):
            if any(ext in url for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".m3u8")):
                return False
            if url.startswith(("file://", "edge://", "chrome://", "about:", "devtools://")) or ".pdf" in url:
                return True
            if any(
                vp in url
                for vp in (
                    "youtube.com/watch",
                    "youtu.be/",
                    "youtube.com/shorts/",
                    "youtube.com/live/",
                    "bilibili.com/video/",
                    "bilibili.com/bangumi/",
                    "bilibili.com/cheese/",
                    "bilibili.com/list/",
                    "coursera.org/learn",
                    "udemy.com/course",
                    "edx.org/learn",
                    "twitch.tv/",
                    "vimeo.com/",
                    "netflix.com/watch",
                    "iqiyi.com/v_",
                    "v.qq.com/x/",
                )
            ):
                return False
            # 普通浏览网页既无视频 URL 也无 UIA 播放器控件：判定为非视频页面
            return True

    if any(kw in t_low for kw in ("youtube", "bilibili", "哔哩哔哩", "coursera", "udemy", "edx")):
        # 浏览器首页（无视频标题）与普通浏览区别：待 UIA 下一帧确认
        return None
    return None


def _is_valid_video_window(hwnd: int, own_pid: int) -> bool:
    if not hwnd:
        return False
    try:
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return False
        if _get_process_exe_name(int(pid.value)) in _BLOCKED_PROCESS_EXES:
            return False
        cls_buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, cls_buf, 128)
        cls_name = cls_buf.value
        if cls_name in ("Shell_TrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow"):
            return False
        rect = _get_window_rect(hwnd)
        if not rect:
            return False
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w < 360 or h < 220:
            return False

        title_low = _get_window_title(hwnd).replace("\u200b", "").replace("\ufeff", "").lower()
        is_browser_win = any(
            bw in title_low
            for bw in ("microsoft edge", "google chrome", "mozilla firefox", "brave", "vivaldi", "opera", "youtube", "bilibili", "哔哩哔哩")
        )
        if not is_browser_win and any(bad in title_low for bad in _BLOCKED_APP_TITLE_KEYWORDS):
            return False
        if cls_name in _ALLOWED_VIDEO_CLASSES or any(kw in cls_name.lower() for kw in ("vlc", "potplayer", "mpv", "media")):
            return True
        if cls_name == "ApplicationFrameWindow":
            return any(kw in title_low for kw in ("media player", "movies", "films", "电影和电视", "媒体播放器", "video", "player"))
        if cls_name in _CHROMIUM_VIDEO_CLASSES:
            # 拦截与视频无关的 Electron 桌面软件，仅放行浏览器与已知视频站点窗口
            return any(kw in title_low for kw in _MEDIA_TITLE_KEYWORDS)
        if any(kw in title_low for kw in ("youtube", "bilibili", "哔哩哔哩", "coursera", "udemy", "edx", "vlc", "potplayer", "mpv", "powerpoint", "wps")):
            return True
        return False
    except Exception:
        return False


def _parse_clock_str_to_sec(clock_str: str) -> Optional[float]:
    parts = [p.strip() for p in clock_str.split(":")]
    if len(parts) == 2:
        m, s = int(parts[0]), int(parts[1])
        if 0 <= m < 600 and 0 <= s < 60:
            return float(m * 60 + s)
    elif len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if 0 <= h < 48 and 0 <= m < 60 and 0 <= s < 60:
            return float(h * 3600 + m * 60 + s)
    return None


def extract_player_timestamp_sec(raw_text: str) -> Optional[float]:
    """从 OCR 原文中提取视频播放器控制条时间码（如 '01:24 / 15:02'）并返回当前播放秒数。"""
    if not raw_text:
        return None
    for m in re.finditer(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\s*/\s*(\d{1,2}:\d{2}(?::\d{2})?)\b", raw_text):
        cur_sec = _parse_clock_str_to_sec(m.group(1))
        total_sec = _parse_clock_str_to_sec(m.group(2))
        if cur_sec is not None and total_sec is not None and total_sec >= 5.0 and cur_sec <= total_sec + 5.0:
            return cur_sec
    return None


_UIA_PROBE_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$T_DESC = [System.Windows.Automation.TreeScope]::Descendants
$P_TYPE = [System.Windows.Automation.AutomationElement]::ControlTypeProperty
$P_NAME = [System.Windows.Automation.AutomationElement]::NameProperty
$C_DOC  = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Document)
$C_SLD  = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Slider)
$C_GRP  = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Group)
$C_EDIT = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Edit)
$C_TXT  = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Text)
$C_BTN  = New-Object System.Windows.Automation.PropertyCondition($P_TYPE, [System.Windows.Automation.ControlType]::Button)
$C_BILI_PLAYER = New-Object System.Windows.Automation.PropertyCondition($P_NAME, '哔哩哔哩播放器')
$C_YT_PLAYER   = New-Object System.Windows.Automation.PropertyCondition($P_NAME, 'YouTube Video Player')

$cache = @{}

function Find-BrowserNodes([long]$h) {
    $win = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$h)
    if (-not $win) { return $null }
    $addr = $null
    $edits = $win.FindAll($T_DESC, $C_EDIT)
    foreach ($ed in $edits) {
        $nm = $ed.Current.Name
        if ($nm -match 'Address|search bar|地址|搜索栏|Omnibox') { $addr = $ed; break }
    }
    $doc = $null
    $fallbackDoc = $null
    $docs = $win.FindAll($T_DESC, $C_DOC)
    foreach ($d in $docs) {
        try {
            if ($d.Current.BoundingRectangle.Width -lt 300) { continue }
            $du = ''
            try { $du = [string]$d.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
            # 跳过 Edge 内置空白暗色衬底文档 (data:text/html) 与扩展内部文档，避免误锁假文档导致切 B站无反应
            if ($du -match '^(?:data:|about:|edge://|chrome://|devtools://|chrome-extension://)') { continue }
            if ($du -match 'youtube\.com|youtu\.be|bilibili\.com|b23\.tv') { $doc = $d; break }
            if (-not $doc -and $du -match '^https?://') { $doc = $d }
            elseif (-not $fallbackDoc) { $fallbackDoc = $d }
        } catch {}
    }
    if (-not $doc) { $doc = $fallbackDoc }
    if (-not $doc -and $docs -and $docs.Count -gt 0) { $doc = $docs[0] }

    $root = if ($doc) { $doc } else { $win }
    $player = $root.FindFirst($T_DESC, $C_YT_PLAYER)
    if (-not $player) { $player = $root.FindFirst($T_DESC, $C_BILI_PLAYER) }
    if (-not $player -and $docs -and $docs.Count -gt 1) {
        foreach ($d in $docs) {
            if ($d -eq $doc) { continue }
            $pTry = $d.FindFirst($T_DESC, $C_YT_PLAYER)
            if (-not $pTry) { $pTry = $d.FindFirst($T_DESC, $C_BILI_PLAYER) }
            if ($pTry) { $doc = $d; $root = $d; $player = $pTry; break }
        }
    }
    if (-not $player) {
        $grps = $root.FindAll($T_DESC, $C_GRP)
        foreach ($g in $grps) {
            $gn = $g.Current.Name
            if ($gn -match 'YouTube Video Player|Video Player|哔哩哔哩播放器|bpx-player') { $player = $g; break }
        }
    }

    $isBiliNode = $false
    if ($player -and ($player.Current.Name -match '哔哩哔哩|bpx-player')) { $isBiliNode = $true }
    if (-not $isBiliNode -and $addr) {
        try {
            $au = [string]$addr.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
            if ($au -match 'bilibili\.com|b23\.tv') { $isBiliNode = $true }
        } catch {}
    }

    $sliderRoot = if ($player) { $player } else { $root }
    $slider = $null
    $sliders = $sliderRoot.FindAll($T_DESC, $C_SLD)
    foreach ($s in $sliders) {
        $sn = $s.Current.Name
        if ($sn -match 'Seek slider|Seek|进度滑块|进度|时间滑块|时间|progress') { $slider = $s; break }
    }
    if (-not $slider -and (-not $isBiliNode)) {
        foreach ($s in $sliders) {
            try {
                if ($s.Current.BoundingRectangle.Width -ge 150) {
                    $rvP = $s.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern)
                    if ($rvP -and $rvP.Current.Maximum -ge 15 -and $rvP.Current.Maximum -ne 100) {
                        $slider = $s; break
                    }
                }
            } catch {}
        }
    }
    return @{ Win=$win; Addr=$addr; Doc=$doc; Slider=$slider; Player=$player; Tick=0; LastUrl='' }
}

function Parse-ClockToken([string]$tok) {
    $parts = $tok.Trim().Split(':')
    if ($parts.Count -eq 2) {
        $m = [int]$parts[0]; $s = [int]$parts[1]
        if ($m -ge 0 -and $m -lt 600 -and $s -ge 0 -and $s -lt 60) { return [double]($m * 60 + $s) }
    } elseif ($parts.Count -eq 3) {
        $h = [int]$parts[0]; $m = [int]$parts[1]; $s = [int]$parts[2]
        if ($h -ge 0 -and $h -lt 48 -and $m -ge 0 -and $m -lt 60 -and $s -ge 0 -and $s -lt 60) { return [double]($h * 3600 + $m * 60 + $s) }
    }
    return $null
}

function Get-PlayerClockSec($playerEl) {
    if (-not $playerEl) { return $null }
    try {
        $txts = $playerEl.FindAll($T_DESC, $C_TXT)
        $tokens = New-Object System.Collections.Generic.List[string]
        foreach ($t in $txts) {
            $nm = [string]$t.Current.Name
            if (-not $nm) { continue }
            $nm = $nm.Trim()
            if ($nm -match '^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*/\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$') {
                $c = Parse-ClockToken $matches[1]
                $tot = Parse-ClockToken $matches[2]
                if ($null -ne $c -and $null -ne $tot -and $tot -ge 5 -and $c -le ($tot + 5)) { return $c }
            }
            if ($nm -match '^\d{1,2}:\d{2}(?::\d{2})?$' -or $nm -eq '/') {
                $tokens.Add($nm)
            }
            if ($tokens.Count -ge 12) { break }
        }
        for ($i = 0; $i -lt ($tokens.Count - 2); $i++) {
            if ($tokens[$i+1] -eq '/' -and $tokens[$i] -match '^\d{1,2}:\d{2}' -and $tokens[$i+2] -match '^\d{1,2}:\d{2}') {
                $c = Parse-ClockToken $tokens[$i]
                $tot = Parse-ClockToken $tokens[$i+2]
                if ($null -ne $c -and $null -ne $tot -and $tot -ge 5 -and $c -le ($tot + 5)) { return $c }
            }
        }
    } catch {}
    return $null
}

function Safe-Rect($el) {
    if (-not $el) { return $null }
    try {
        $r = $el.Current.BoundingRectangle
        if (-not $r.IsEmpty -and -not [double]::IsInfinity($r.X) -and -not [double]::IsNaN($r.X) -and $r.Width -gt 80 -and $r.Height -gt 40) {
            return @([int][Math]::Round($r.X), [int][Math]::Round($r.Y), [int][Math]::Round($r.Width), [int][Math]::Round($r.Height))
        }
    } catch {}
    return $null
}

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if (-not $line) { continue }
    try {
        $parts = $line.Split('|')
        $h = [long]$parts[0]
        $force = ($parts.Count -gt 1 -and $parts[1] -eq '1')
        $ent = $cache[$h]
        if ($force -or (-not $ent) -or ($ent.Tick -ge 180)) {
            $ent = Find-BrowserNodes $h
            if ($ent) { $cache[$h] = $ent }
        }
        if (-not $ent) {
            [Console]::Out.WriteLine('{}')
            [Console]::Out.Flush()
            continue
        }
        $ent.Tick = $ent.Tick + 1

        $docUrl = ''
        if ($ent.Doc) {
            try { $docUrl = [string]$ent.Doc.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { $ent.Doc = $null }
        }
        $addrUrl = ''
        if ($ent.Addr) {
            try { $addrUrl = [string]$ent.Addr.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { $ent.Addr = $null }
        }
        $normAddr = ''
        if ($addrUrl) {
            if ($addrUrl -match '^(?:https?|file)://') { $normAddr = $addrUrl } elseif ($addrUrl -match '^[\w.-]+\.[a-z]{2,}(?:/|$)') { $normAddr = 'https://' + $addrUrl }
        }
        if ($addrUrl -and $ent.LastUrl -and ($addrUrl -ne $ent.LastUrl)) {
            $ent = Find-BrowserNodes $h
            if ($ent) {
                $cache[$h] = $ent
                if ($ent.Doc) { try { $docUrl = [string]$ent.Doc.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {} }
            }
        }
        if ($addrUrl) { $ent.LastUrl = $addrUrl }
        if ($docUrl -match '^data:') { $docUrl = '' }
        # 地址栏 URL 永远代表当前聚焦标签页，避免网页内嵌 iframe 覆盖主 URL
        $url = if ($normAddr) { $normAddr } elseif ($docUrl) { $docUrl } elseif ($addrUrl) { 'https://' + $addrUrl } else { '' }

        $sec = $null
        if ($ent.Player -and ($url -match 'bilibili\.com|b23\.tv')) {
            $sec = Get-PlayerClockSec $ent.Player
        }
        if ($null -eq $sec -and $ent.Slider) {
            $sv = ''
            try {
                $sv = [string]$ent.Slider.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
                if ($sv) {
                    $curPart = ($sv -split '(?:\bof\b|共|/|（|\()')[0]
                    $hVal = 0; $mVal = 0; $sVal = 0; $matchedUnit = $false
                    if ($curPart -match '(\d+)\s*(?:Hours?|小时|时)') { $hVal = [int]$matches[1]; $matchedUnit = $true }
                    if ($curPart -match '(\d+)\s*(?:Minutes?|分钟|分)') { $mVal = [int]$matches[1]; $matchedUnit = $true }
                    if ($curPart -match '(\d+)\s*(?:Seconds?|秒)') { $sVal = [int]$matches[1]; $matchedUnit = $true }
                    if ($matchedUnit) {
                        $sec = [double]($hVal * 3600 + $mVal * 60 + $sVal)
                    } elseif ($curPart -match '(\d{1,2}:\d{2}(?::\d{2})?)') {
                        $sec = Parse-ClockToken $matches[1]
                    }
                }
            } catch {}
            if ($null -eq $sec -and ($sv -notmatch '%')) {
                try {
                    $curRv = $ent.Slider.GetCurrentPattern([System.Windows.Automation.RangeValuePattern]::Pattern).Current
                    $rv = [double]$curRv.Value
                    $rmax = [double]$curRv.Maximum
                    $isBiliUrl = ($url -match 'bilibili\.com|b23\.tv')
                    if (-not [double]::IsNaN($rv) -and -not [double]::IsInfinity($rv) -and $rmax -ge 15 -and -not ($isBiliUrl -and $rmax -eq 100)) {
                        $sec = $rv
                    }
                } catch { $ent.Slider = $null }
            }
        }
        if ($null -eq $sec -and $ent.Player) {
            $sec = Get-PlayerClockSec $ent.Player
        }
        if ($null -eq $sec -and ($url -match 'youtube\.com/watch|youtube\.com/shorts|bilibili\.com|b23\.tv')) {
            $ent.Tick = 180
        }

        $isPaused = $false
        if ($ent.Player) {
            try {
                $btns = $ent.Player.FindAll($T_DESC, $C_BTN)
                foreach ($b in $btns) {
                    $bn = [string]$b.Current.Name
                    if ($bn -match '^(?:Play(?:\s*\(k\))?|播放|继续播放)$') {
                        $isPaused = $true
                        break
                    }
                }
            } catch {}
        }

        $pRect = Safe-Rect $ent.Player
        $dRect = Safe-Rect $ent.Doc
        $wRect = Safe-Rect $ent.Win
        $obj = [ordered]@{ url=$url; sec=$sec; paused=$isPaused; player_rect=$pRect; doc_rect=$dRect; win_rect=$wRect }
        [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress))
        [Console]::Out.Flush()
    } catch {
        [Console]::Out.WriteLine('{}')
        [Console]::Out.Flush()
    }
}
"""


class _BrowserUIAProbe:
    """
    常驻无窗 Windows UIA 浏览器状态探针：
    在不安装外部 UIA 库的前提下，从 Edge/Chrome 直接读取当前标签页 URL、Seek slider 秒数与视频播放器像素框。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._out_queue: queue.Queue = queue.Queue()
        self._closed: bool = False
        self._ps_exe = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        if not os.path.exists(self._ps_exe):
            self._ps_exe = "powershell.exe"

    def reopen(self):
        with self._proc_lock:
            self._closed = False

    @staticmethod
    def _terminate_proc(proc: Optional[subprocess.Popen]):
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=0.3)
            except Exception:
                pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    def _ensure_proc(self) -> Tuple[Optional[subprocess.Popen], Optional[queue.Queue], bool]:
        with self._proc_lock:
            if self._closed:
                return None, None, False
            if self._proc is not None and self._proc.poll() is None:
                return self._proc, self._out_queue, False
            try:
                b64 = base64.b64encode(_UIA_PROBE_PS.encode("utf-16-le")).decode("ascii")
                proc = subprocess.Popen(
                    [self._ps_exe, "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", b64],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
                out_q: queue.Queue = queue.Queue()

                def _reader(p=proc, q=out_q):
                    try:
                        if p.stdout is not None:
                            for ln in p.stdout:
                                q.put(ln)
                    except Exception:
                        pass
                    finally:
                        q.put(None)

                threading.Thread(target=_reader, daemon=True, name="uia_probe_reader").start()
                self._proc = proc
                self._out_queue = out_q
                return proc, out_q, True
            except Exception:
                self._proc = None
                return None, None, False

    def query(self, hwnd: int, force_refresh: bool = False, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not hwnd:
            return None
        with self._lock:
            proc, out_q, spawned = self._ensure_proc()
            if proc is None or out_q is None or proc.stdin is None or proc.stdout is None:
                return None
            eff_timeout = float(timeout) if timeout is not None else (3.5 if (spawned or force_refresh) else 2.0)
            try:
                # 清空旧积压行，确保返回本次查询的对应结果
                while True:
                    try:
                        out_q.get_nowait()
                    except queue.Empty:
                        break
                proc.stdin.write(f"{int(hwnd)}|{1 if force_refresh else 0}\n")
                proc.stdin.flush()
                try:
                    line = out_q.get(timeout=eff_timeout)
                except queue.Empty:
                    self._close_current(proc)
                    return None
                if not line:
                    self._close_current(proc)
                    return None
                data = json.loads(line.strip())
                if not isinstance(data, dict):
                    return None
                url = str(data.get("url") or "")
                yt_vid = YouTubeTranscriptLoader.extract_video_id(url)
                if yt_vid:
                    data["platform"] = "youtube"
                    data["video_id"] = yt_vid
                    data["raw_id"] = yt_vid
                    data["page"] = 1
                elif "bilibili.com" in url.lower() or "b23.tv" in url.lower():
                    bvid, page = BilibiliTranscriptLoader.extract_bvid_and_page(url)
                    data["platform"] = "bilibili"
                    data["raw_id"] = bvid
                    data["page"] = page
                    data["video_id"] = BilibiliTranscriptLoader.canonical_id(bvid, page) if bvid else ""
                else:
                    data["platform"] = ""
                    data["video_id"] = ""
                    data["raw_id"] = ""
                    data["page"] = 1
                return data
            except Exception:
                self._close_current(proc)
                return None

    def _close_current(self, target_proc: Optional[subprocess.Popen] = None):
        with self._proc_lock:
            if target_proc is not None and self._proc is not target_proc:
                proc = target_proc
            else:
                proc = self._proc
                self._proc = None
        self._terminate_proc(proc)

    def close(self):
        # 绝不等待 query() 持有的 self._lock：先设关闭标志并立刻强杀子进程，EOF 秒级唤醒阻塞读线程
        with self._proc_lock:
            self._closed = True
            proc = self._proc
            self._proc = None
        self._terminate_proc(proc)


class LiveSubtitleEngine(QObject):
    """
    实时双语字幕引擎：
    - 零常驻取景框，自动锁定目标浏览器/视频窗口并在多显示器/异 DPI 下动态适配；
    - 220ms 像素帧差门控，画面静止时 0 OCR 消耗，字幕消失静止后 2s 自动归档；
    - 全线程安全 RLock 保护、会话隔离、防抖直连翻译与 YouTube 链接同步。
    """
    subtitle_updated = Signal(dict)
    transcript_loaded = Signal(dict)
    transcript_error = Signal(str)
    bilibili_login_needed = Signal(str)
    status_notice = Signal(str)

    def __init__(self, parent=None, poll_interval_ms: int = 110):
        super().__init__(parent)
        self._own_pid = os.getpid()
        self._state_lock = threading.RLock()
        self._running = False
        self._paused = False
        self._worker_busy = False
        self._target_hwnd: int = 0
        self._custom_region_phys: Optional[Tuple[int, int, int, int]] = None

        self._prev_thumb: Optional[np.ndarray] = None
        self._thumb_cache: Dict[str, Tuple[Tuple[int, int, int, int], np.ndarray]] = {}
        self._last_native_phys_box: Optional[Tuple[int, int, int, int]] = None
        self._last_subtitle_seen_ts: float = 0.0
        self._poll_tick_seq: int = 0
        self._force_full_next: bool = False

        self._last_ocr_had_text: bool = False
        self._empty_since_ts: float = 0.0
        self._stitcher = RollingCaptionStitcher()
        self._trans_cache: OrderedDict[str, str] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._pending_trans: set[str] = set()
        self._active_trans_seq: int = 0
        self._done_active_trans_seq: int = 0
        self._latest_wanted_en: str = ""
        self._latest_wanted_session: int = 0
        self._active_worker_running: bool = False
        self._last_active_en_translated: str = ""
        self._last_active_cn: str = ""
        self._last_native_box: Optional[Tuple[int, int, int, int]] = None

        # 补充模式：YouTube 自动识别 / 链接完整字幕轨 + 高精度本地时间轴 + UIA 锁相探针
        self._mode: str = "ocr"  # "ocr" | "transcript"
        self._ocr_forced: bool = False
        self._transcript_segments: List[Dict[str, Any]] = []
        self._transcript_title: str = ""
        self._transcript_index: int = 0
        self._current_video_id: str = ""
        self._suppressed_video_id: str = ""
        self._no_caption_vids: set[str] = set()
        self._playback_sec: float = 0.0
        self._playback_rate: float = 1.0
        self._manual_time_offset: float = 0.0
        self._playback_playing: bool = True
        self._last_clock_tick_ts: float = 0.0
        self._last_auto_yt_title: str = ""
        self._auto_yt_suppressed_title: str = ""
        self._auto_resolving_title: bool = False
        self._loading_video_id: str = ""
        self._last_player_box: Optional[Tuple[int, int, int, int]] = None
        self._last_player_phys_rect: Optional[Tuple[int, int, int, int]] = None
        self._last_seen_win_title: str = ""
        self._last_uia_query_ts: float = 0.0
        self._last_uia_raw_sec: Optional[float] = None
        self._last_uia_change_ts: float = 0.0
        self._last_uia_sec_seen_ts: float = 0.0
        self._uia_prev_poll_ts: float = 0.0
        self._uia_rate_anchor_sec: Optional[float] = None
        self._uia_rate_anchor_ts: float = 0.0
        self._uia_last_step_ts: float = 0.0
        self._uia_step_dts: List[float] = []
        self._uia_back_cand: Tuple[float, int] = (-1.0, 0)
        self._latest_uia_sec: Optional[float] = None
        self._ocr_back_cand: Tuple[int, int] = (-1, 0)
        self._last_bilibili_login_hint_vid: str = ""
        self._last_status_notice_vid: str = ""
        self._force_uia_refresh: bool = False
        self._last_browser_non_video: bool = False
        self._uia_probe = _BrowserUIAProbe()
        self._session_id: int = 0
        self._snapshot_seq: int = 0

        self._active_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sub_act")
        self._history_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sub_hist")
        self._ocr_queue: queue.Queue = queue.Queue(maxsize=1)
        self._ocr_worker_stop = threading.Event()
        self._ocr_worker_lock = threading.Lock()
        self._ocr_worker_thread: Optional[threading.Thread] = None
        self._ocr_loop_reuse_count: int = 0

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_interval_ms)
        self._poll_timer.timeout.connect(self._on_poll_tick)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(80)
        self._clock_timer.timeout.connect(self._on_clock_tick)

    def _ensure_ocr_worker(self):
        """确保单常驻 OCR 工作线程运行并复用持久化 asyncio 事件循环，避免每 110ms 新建线程与事件循环。"""
        with self._ocr_worker_lock:
            if self._ocr_worker_thread is not None and self._ocr_worker_thread.is_alive():
                return
            self._ocr_worker_stop.clear()
            t = threading.Thread(target=self._ocr_worker_main, daemon=True, name="sub_ocr_worker")
            self._ocr_worker_thread = t
            t.start()

    def _ocr_worker_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._ocr_worker_stop.is_set():
                try:
                    job = self._ocr_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if job is None:
                    break
                try:
                    self._ocr_loop_reuse_count += 1
                    self._capture_and_process_worker(*job, ocr_loop=loop)
                except Exception:
                    self._worker_busy = False
        finally:
            try:
                loop.close()
            except Exception:
                pass
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def target_hwnd(self) -> int:
        return self._target_hwnd

    def start(self):
        with self._state_lock:
            if self._running:
                return
            self._session_id += 1
            self._running = True
            self._paused = False
            self._prev_thumb = None
            self._thumb_cache.clear()
            self._last_native_phys_box = None
            self._last_subtitle_seen_ts = 0.0
            self._poll_tick_seq = 0
            self._force_full_next = False
            self._last_ocr_had_text = False
            self._empty_since_ts = 0.0
            self._last_clock_tick_ts = time.perf_counter()
        self._uia_probe.reopen()
        self._refresh_screen_cache()
        self._refresh_target_window()
        self._poll_timer.start()
        self._clock_timer.start()
        try:
            prewarm_translation_connections()
        except Exception:
            pass
        self._emit_snapshot()

    def stop(self):
        with self._state_lock:
            self._running = False
            self._session_id += 1
            self._loading_video_id = ""
            self._auto_resolving_title = False
        self._poll_timer.stop()
        self._clock_timer.stop()
        self._uia_probe.close()

    def shutdown(self):
        """安全终止轮询、常驻 OCR 工作线程、UIA 探针子进程并释放后台翻译线程池，杜绝退出阶段线程泄漏。"""
        self.stop()
        self._ocr_worker_stop.set()
        try:
            while True:
                self._ocr_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._ocr_queue.put_nowait(None)
        except queue.Full:
            pass
        with self._ocr_worker_lock:
            worker = self._ocr_worker_thread
            self._ocr_worker_thread = None
        if worker is not None and worker.is_alive():
            try:
                worker.join(timeout=0.8)
            except Exception:
                pass
        for ex in (self._active_executor, self._history_executor):
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def set_paused(self, paused: bool):
        with self._state_lock:
            self._paused = paused
            self._last_clock_tick_ts = time.perf_counter()

    def clear_history(self):
        with self._state_lock:
            self._session_id += 1
            self._stitcher.reset()
            self._mode = "ocr"
            self._transcript_segments = []
            self._transcript_title = ""
            self._transcript_index = 0
            self._current_video_id = ""
            self._suppressed_video_id = ""
            self._playback_sec = 0.0
            self._playback_rate = 1.0
            self._manual_time_offset = 0.0
            self._last_clock_tick_ts = time.perf_counter()
            self._last_auto_yt_title = ""
            self._auto_yt_suppressed_title = ""
            self._auto_resolving_title = False
            self._ocr_forced = self._custom_region_phys is not None
            self._loading_video_id = ""
            self._last_uia_raw_sec = None
            self._last_uia_change_ts = 0.0
            self._last_uia_sec_seen_ts = 0.0
            self._uia_prev_poll_ts = 0.0
            self._uia_rate_anchor_sec = None
            self._uia_rate_anchor_ts = 0.0
            self._uia_last_step_ts = 0.0
            self._uia_step_dts.clear()
            self._uia_back_cand = (-1.0, 0)
            self._latest_uia_sec = None
            self._ocr_back_cand = (-1, 0)
            self._last_native_box = None
            self._last_native_phys_box = None
            self._last_player_phys_rect = None
            self._last_subtitle_seen_ts = 0.0
            self._force_full_next = False
            self._prev_thumb = None
            self._thumb_cache.clear()
            self._last_ocr_had_text = False
            self._empty_since_ts = 0.0
            self._last_active_en_translated = ""
            self._last_active_cn = ""
            self._done_active_trans_seq = 0
        with self._cache_lock:
            self._latest_wanted_en = ""
            self._active_trans_seq += 1
        self._emit_snapshot()

    def switch_to_ocr_mode(self):
        with self._state_lock:
            self._session_id += 1
            self._mode = "ocr"
            self._ocr_forced = True
            self._transcript_segments = []
            self._transcript_title = ""
            self._auto_yt_suppressed_title = self._last_auto_yt_title
            self._suppressed_video_id = self._current_video_id
            self._loading_video_id = ""
            self._auto_resolving_title = False
            self._force_full_next = True
            self._prev_thumb = None
            self._thumb_cache.clear()
        self.status_notice.emit("OCR 实时识别模式")
        self._emit_snapshot()

    def set_custom_region(self, phys_bbox: Any, y1: Optional[int] = None, x2: Optional[int] = None, y2: Optional[int] = None):
        if y1 is not None and x2 is not None and y2 is not None:
            coords = (int(phys_bbox), int(y1), int(x2), int(y2))
        else:
            coords = tuple(int(v) for v in phys_bbox)
        nx1 = min(coords[0], coords[2])
        ny1 = min(coords[1], coords[3])
        nx2 = max(coords[0], coords[2])
        ny2 = max(coords[1], coords[3])
        if (nx2 - nx1) < 40 or (ny2 - ny1) < 18:
            return
        norm_box = (nx1, ny1, nx2, ny2)
        _, _, _, _, dpr = self._get_screen_metrics(norm_box)
        lw = max(40, int(round((nx2 - nx1) / dpr)))
        lh = max(20, int(round((ny2 - ny1) / dpr)))
        logic_box = (int(round(nx1 / dpr)), int(round(ny1 / dpr)), lw, lh)
        with self._state_lock:
            self._session_id += 1
            self._custom_region_phys = norm_box
            self._ocr_forced = True
            self._mode = "ocr"
            self._transcript_segments = []
            self._transcript_title = ""
            self._auto_yt_suppressed_title = self._last_auto_yt_title
            self._suppressed_video_id = self._current_video_id
            self._loading_video_id = ""
            self._auto_resolving_title = False
            self._last_native_phys_box = (nx1, ny1, nx2 - nx1, ny2 - ny1)
            self._last_native_box = logic_box
            self._last_subtitle_seen_ts = time.time()
            self._prev_thumb = None
            self._thumb_cache.clear()
        self.status_notice.emit("已锁定字幕区 · OCR 实时识别")
        self._emit_snapshot()

    def clear_custom_region(self):
        with self._state_lock:
            self._session_id += 1
            self._custom_region_phys = None
            self._ocr_forced = False
            self._suppressed_video_id = ""
            self._auto_yt_suppressed_title = ""
            self._force_full_next = True
            self._prev_thumb = None
            self._thumb_cache.clear()
        self.status_notice.emit("")
        self._emit_snapshot()

    def _find_top_video_window(self) -> int:
        candidates: List[Tuple[int, int]] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _):
            h = int(hwnd or 0)
            if _is_valid_video_window(h, self._own_pid):
                title_low = _get_window_title(h).lower()
                is_preferred = ("youtube" in title_low) or ("bilibili" in title_low) or ("哔哩哔哩" in title_low)
                prio = 2 if is_preferred else 1
                candidates.append((prio, h))
                if prio == 2:
                    return False
            return True

        try:
            _user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        return 0

    def _compute_player_bottom_box(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        win_rect = _get_window_rect(hwnd) if hwnd else None
        sx1, sy1, sx2, sy2, dpr = self._get_screen_metrics(win_rect)
        if not win_rect:
            return None
        wx1 = max(sx1, win_rect[0])
        wy1 = max(sy1, win_rect[1])
        wx2 = min(sx2, win_rect[2])
        wy2 = min(sy2, win_rect[3])
        ww = wx2 - wx1
        wh = wy2 - wy1
        if ww < 360 or wh < 240:
            return None
        is_fullscreen = (ww >= (sx2 - sx1) - 60) and (wh >= (sy2 - sy1) - 60)
        box_w_phys = min(int(ww * 0.64), int(760 * dpr))
        box_h_phys = int(62 * dpr)
        cx_phys = (wx1 + wx2) // 2
        anchor_y_phys = wy1 + int(wh * (0.86 if is_fullscreen else 0.70))
        bx_phys = cx_phys - box_w_phys // 2
        by_phys = anchor_y_phys - box_h_phys
        return (
            int(round(bx_phys / dpr)),
            int(round(by_phys / dpr)),
            max(360, int(round(box_w_phys / dpr))),
            max(42, int(round(box_h_phys / dpr))),
        )

    def retry_bilibili_after_login(self):
        """B站扫码登录/退出后清理缓存黑名单并强制刷新 UIA 探测，立刻重新同步当前 B站视频。"""
        with self._state_lock:
            self._no_caption_vids = {v for v in self._no_caption_vids if not v.startswith("bili:")}
            if self._suppressed_video_id.startswith("bili:"):
                self._suppressed_video_id = ""
            self._last_bilibili_login_hint_vid = ""
            self._last_status_notice_vid = ""
            self._force_uia_refresh = True
        self._emit_snapshot()

    def _reset_transcript_to_ocr_locked(self) -> bool:
        """在持有 _state_lock 时从旧视频外挂字幕轨干净切回画面 OCR（例如从 YouTube 切到无 CC 的 B站视频）。"""
        changed = (self._mode == "transcript") or bool(self._transcript_segments) or bool(self._current_video_id)
        self._mode = "ocr"
        self._transcript_segments = []
        self._transcript_title = ""
        self._transcript_index = 0
        self._current_video_id = ""
        self._playback_sec = 0.0
        self._playback_rate = 1.0
        self._playback_playing = False
        self._last_native_box = None
        self._last_native_phys_box = None
        self._last_ocr_had_text = False
        self._empty_since_ts = 0.0
        self._thumb_cache.clear()
        self._prev_thumb = None
        self._stitcher.reset()
        return changed

    def _start_load_youtube_vid(self, vid: str, fallback_title: str = ""):
        self._start_load_video_vid("youtube", vid, vid, 1, fallback_title=fallback_title)

    def _start_load_video_vid(
        self,
        platform: str,
        canon_vid: str,
        raw_id: str,
        page: int = 1,
        fallback_title: str = "",
    ):
        """根据精确平台与视频 ID 异步拉取完整字幕轨并在 UIA 进度点处预翻译对齐。"""
        with self._state_lock:
            if (
                not self._running
                or not canon_vid
                or canon_vid in self._no_caption_vids
                or canon_vid == self._loading_video_id
                or canon_vid == self._suppressed_video_id
                or (canon_vid == self._current_video_id and self._mode == "transcript" and self._transcript_segments)
            ):
                return
            self._loading_video_id = canon_vid
            self._latest_uia_sec = None
            self._last_uia_raw_sec = None
            self._last_uia_change_ts = 0.0
            self._last_uia_sec_seen_ts = 0.0
            self._uia_prev_poll_ts = 0.0
            self._uia_rate_anchor_sec = None
            self._uia_rate_anchor_ts = 0.0
            self._uia_last_step_ts = 0.0
            self._uia_step_dts.clear()
            self._uia_back_cand = (-1.0, 0)
            self._ocr_back_cand = (-1, 0)
            self._playback_rate = 1.0
            sid = self._session_id
            if fallback_title:
                self._last_auto_yt_title = fallback_title
        self._emit_snapshot()

        def _vid_worker():
            try:
                if platform == "bilibili":
                    target_spec = f"{raw_id}?p={page}" if page > 1 else raw_id
                    res = BilibiliTranscriptLoader.fetch_bilibili_transcript(target_spec)
                else:
                    res = YouTubeTranscriptLoader.fetch_youtube_transcript(raw_id or canon_vid)
                segments = res.get("segments", [])
                if not segments:
                    reset_emit = False
                    with self._state_lock:
                        self._no_caption_vids.add(canon_vid)
                        if self._running and sid == self._session_id and self._mode == "transcript" and self._current_video_id != canon_vid:
                            reset_emit = self._reset_transcript_to_ocr_locked()
                    if reset_emit:
                        self._emit_snapshot()
                    return
                for seg in segments:
                    cached = self._get_cached_translation(seg.get("en_text", ""))
                    if cached:
                        seg["cn_text"] = cached
                with self._state_lock:
                    if not self._running or sid != self._session_id:
                        return
                    init_sec = float(self._latest_uia_sec) if self._latest_uia_sec is not None else 0.0
                    self._current_video_id = canon_vid
                    self._transcript_segments = segments
                    self._transcript_title = res.get("title", "") or fallback_title or canon_vid
                    self._last_auto_yt_title = fallback_title or self._transcript_title
                    self._manual_time_offset = 0.0
                    self._playback_sec = init_sec
                    self._playback_rate = 1.0
                    self._last_uia_raw_sec = None
                    self._last_uia_change_ts = 0.0
                    self._last_uia_sec_seen_ts = 0.0
                    self._uia_prev_poll_ts = 0.0
                    self._uia_rate_anchor_sec = None
                    self._uia_rate_anchor_ts = 0.0
                    self._uia_last_step_ts = 0.0
                    self._uia_step_dts.clear()
                    self._uia_back_cand = (-1.0, 0)
                    self._ocr_back_cand = (-1, 0)
                    self._playback_playing = True
                    self._last_clock_tick_ts = time.perf_counter()
                    self._last_native_box = None
                    self._transcript_index = YouTubeTranscriptLoader.find_segment_index_at_time(segments, init_sec)
                    self._mode = "transcript"
                    loaded_meta = {
                        "title": self._transcript_title,
                        "count": len(segments),
                        "language": res.get("language", "en"),
                    }
                self._start_full_transcript_batch_translation(sid)
                self.transcript_loaded.emit(loaded_meta)
                self.status_notice.emit("已同步 B站外挂字幕" if platform == "bilibili" else "已同步 YouTube 外挂字幕")
                self._emit_snapshot()
            except Exception as exc:
                msg = str(exc)
                if platform == "bilibili" and "需登录账号" in msg:
                    emit_hint = False
                    reset_emit = False
                    with self._state_lock:
                        self._no_caption_vids.add(canon_vid)
                        if self._running and sid == self._session_id:
                            if self._mode == "transcript" and self._current_video_id != canon_vid:
                                reset_emit = self._reset_transcript_to_ocr_locked()
                            if self._last_bilibili_login_hint_vid != canon_vid:
                                self._last_bilibili_login_hint_vid = canon_vid
                                emit_hint = True
                    if reset_emit:
                        self._emit_snapshot()
                    if emit_hint:
                        self.bilibili_login_needed.emit(fallback_title or raw_id or canon_vid)
                elif "未提供可用的字幕轨" in msg or "字幕数据为空" in msg or "未提供英文" in msg or "未提供可用" in msg:
                    emit_notice = False
                    reset_emit = False
                    with self._state_lock:
                        self._no_caption_vids.add(canon_vid)
                        if self._running and sid == self._session_id:
                            if self._mode == "transcript" and self._current_video_id != canon_vid:
                                reset_emit = self._reset_transcript_to_ocr_locked()
                            if platform == "bilibili" and self._last_status_notice_vid != canon_vid:
                                self._last_status_notice_vid = canon_vid
                                emit_notice = True
                    if reset_emit:
                        self._emit_snapshot()
                    if emit_notice:
                        self.status_notice.emit("已识别 B站视频 · 无英文外挂CC，已切画面OCR")
            finally:
                with self._state_lock:
                    if self._loading_video_id == canon_vid:
                        self._loading_video_id = ""

        threading.Thread(target=_vid_worker, daemon=True).start()

    def _maybe_auto_detect_youtube_window(self, hwnd: int):
        if not hwnd or not self._running:
            return
        yt_title = YouTubeTranscriptLoader.extract_youtube_title_from_window(_get_window_title(hwnd))
        if not yt_title:
            return
        with self._state_lock:
            if (
                not self._running
                or self._auto_resolving_title
                or self._loading_video_id
                or yt_title == self._last_auto_yt_title
                or yt_title == self._auto_yt_suppressed_title
            ):
                return
            self._last_auto_yt_title = yt_title
            self._auto_resolving_title = True
            self._latest_uia_sec = None
            sid = self._session_id
        self._emit_snapshot()

        def _auto_worker():
            try:
                vid = YouTubeTranscriptLoader.resolve_video_id_by_title(yt_title)
                if not vid:
                    return
                with self._state_lock:
                    if (
                        not self._running
                        or sid != self._session_id
                        or vid in self._no_caption_vids
                        or (self._current_video_id == vid and self._mode == "transcript" and self._transcript_segments)
                    ):
                        return
                res = YouTubeTranscriptLoader.fetch_youtube_transcript(vid)
                segments = res.get("segments", [])
                if not segments:
                    with self._state_lock:
                        self._no_caption_vids.add(vid)
                    return
                for seg in segments:
                    cached = self._get_cached_translation(seg.get("en_text", ""))
                    if cached:
                        seg["cn_text"] = cached
                with self._state_lock:
                    if (
                        not self._running
                        or sid != self._session_id
                        or (self._current_video_id == vid and self._mode == "transcript" and self._transcript_segments)
                    ):
                        return
                    init_sec = float(self._latest_uia_sec) if self._latest_uia_sec is not None else 0.0
                    self._current_video_id = vid
                    self._transcript_segments = segments
                    self._transcript_title = res.get("title", "") or yt_title
                    self._manual_time_offset = 0.0
                    self._playback_sec = init_sec
                    self._playback_rate = 1.0
                    self._last_uia_raw_sec = None
                    self._last_uia_change_ts = 0.0
                    self._last_uia_sec_seen_ts = 0.0
                    self._uia_prev_poll_ts = 0.0
                    self._uia_rate_anchor_sec = None
                    self._uia_rate_anchor_ts = 0.0
                    self._uia_last_step_ts = 0.0
                    self._uia_step_dts.clear()
                    self._uia_back_cand = (-1.0, 0)
                    self._ocr_back_cand = (-1, 0)
                    self._playback_playing = True
                    self._last_clock_tick_ts = time.perf_counter()
                    self._last_native_box = None
                    self._transcript_index = YouTubeTranscriptLoader.find_segment_index_at_time(segments, init_sec)
                    self._mode = "transcript"
                    loaded_meta = {
                        "title": self._transcript_title,
                        "count": len(segments),
                        "language": res.get("language", "en"),
                    }
                self._start_full_transcript_batch_translation(sid)
                self.transcript_loaded.emit(loaded_meta)
                self.status_notice.emit("已同步 YouTube 外挂字幕")
                self._emit_snapshot()
            except Exception as exc:
                msg = str(exc)
                if "未提供可用的字幕轨" in msg or "字幕数据为空" in msg:
                    pass
            finally:
                with self._state_lock:
                    if sid == self._session_id:
                        self._auto_resolving_title = False

        threading.Thread(target=_auto_worker, daemon=True).start()

    def _min_keep_sec_for_cur_idx(self) -> Optional[float]:
        """返回维持当前已进入句不被 find_segment_index_at_time(+0.15s) 误判回退的最小安全播放秒数。"""
        cur_idx = self._transcript_index
        if 0 <= cur_idx < len(self._transcript_segments):
            cur_start = float(self._transcript_segments[cur_idx].get("start", 0.0))
            return max(0.0, cur_start - 0.14)
        return None

    def _sync_clock_from_uia(self, uia_sec: float, session_id: int, uia_paused: bool = False) -> bool:
        """
        根据 UIA Seek slider / 播放器时钟读取的播放秒数进行亚秒级锁相与倍速自适应：
        - 区分整数秒跳变、单帧 UIA 抖动与真实拖拽/暂停，禁止在正常播放、暂停或恢复瞬间引发句子回弹；
        - 仅在播放器控件显式处于暂停态 (uia_paused=True) 时锁停，防止鼠标停在字幕区使视频控制栏自动隐藏后误判暂停；
        - 拖拽快进/快退与长期暂停锁定时准确校准，小幅相位超前仅温和减速收敛不倒退跨句。
        """
        now = time.monotonic()
        prev_poll_ts = self._uia_prev_poll_ts
        self._uia_prev_poll_ts = now
        changed = False

        if self._last_uia_raw_sec is None or abs(uia_sec - self._last_uia_raw_sec) >= 0.35:
            self._last_uia_sec_seen_ts = now
            prev_raw = self._last_uia_raw_sec
            duia = (uia_sec - prev_raw) if prev_raw is not None else 0.0
            is_seek_back = False
            if prev_raw is not None and duia < -0.5:
                if duia <= -2.5:
                    is_seek_back = True
                    self._uia_back_cand = (-1.0, 0)
                else:
                    cand_sec, cand_hits = self._uia_back_cand
                    cand_hits = (cand_hits + 1) if (cand_sec >= 0.0 and abs(cand_sec - uia_sec) <= 0.65) else 1
                    self._uia_back_cand = (uia_sec, cand_hits)
                    if cand_hits < 2:
                        return False
                    is_seek_back = True
                    self._uia_back_cand = (-1.0, 0)
            else:
                self._uia_back_cand = (-1.0, 0)

            is_seek_fwd = (prev_raw is not None and duia > 3.5)
            is_natural_step = (prev_raw is not None and 0.35 <= duia <= 3.5)

            if not is_natural_step:
                self._uia_rate_anchor_sec = uia_sec if prev_raw is None else None
                self._uia_rate_anchor_ts = now if prev_raw is None else 0.0
                self._uia_last_step_ts = now if prev_raw is None else 0.0
                self._uia_step_dts.clear()
            else:
                step_dt = (now - self._uia_last_step_ts) if self._uia_last_step_ts > 0.0 else 0.0
                self._uia_last_step_ts = now
                if self._uia_rate_anchor_sec is None or step_dt > 2.45 or step_dt <= 0.0:
                    self._uia_rate_anchor_sec = uia_sec
                    self._uia_rate_anchor_ts = now
                    self._uia_step_dts.clear()
                else:
                    self._uia_step_dts.append(step_dt / max(0.5, duia))
                    if len(self._uia_step_dts) > 12:
                        self._uia_step_dts = self._uia_step_dts[-12:]
                    dt_anchor = now - self._uia_rate_anchor_ts
                    d_anchor = uia_sec - self._uia_rate_anchor_sec
                    if dt_anchor >= 5.5 and 2.5 <= d_anchor <= 24.0 and len(self._uia_step_dts) >= 3:
                        min_sdt = min(self._uia_step_dts)
                        max_sdt = max(self._uia_step_dts)
                        if max_sdt > min_sdt * 1.85 + 0.30:
                            # 窗口内存在明显暂停/卡顿抖动，重置锚点避免把 1.0x 误算为慢速
                            self._uia_rate_anchor_sec = uia_sec
                            self._uia_rate_anchor_ts = now
                            self._uia_step_dts.clear()
                        else:
                            meas_rate = d_anchor / dt_anchor
                            if 0.4 <= meas_rate <= 3.2:
                                snapped = None
                                if abs(meas_rate - 1.0) <= 0.10:
                                    snapped = 1.0
                                else:
                                    for std in (0.5, 0.75, 1.25, 1.5, 1.75, 2.0):
                                        if abs(meas_rate - std) <= 0.06:
                                            snapped = std
                                            break
                                blended = snapped if snapped is not None else (0.50 * self._playback_rate + 0.50 * meas_rate)
                                self._playback_rate = max(0.45, min(3.0, blended))
                                self._uia_rate_anchor_sec = uia_sec
                                self._uia_rate_anchor_ts = now
                                self._uia_step_dts.clear()

            half_lag = 0.0
            if is_natural_step and prev_poll_ts > 0.0:
                poll_dt = max(0.0, now - prev_poll_ts)
                if abs(uia_sec - round(uia_sec)) < 0.05:
                    half_lag = min(0.22, poll_dt * 0.42 + 0.02)
                else:
                    half_lag = min(0.12, poll_dt * 0.25)

            est_sec = uia_sec + min(0.24, half_lag * max(0.75, self._playback_rate * 0.85))
            target_sec = max(0.0, est_sec + self._manual_time_offset)
            err = target_sec - self._playback_sec
            min_keep = self._min_keep_sec_for_cur_idx()

            if prev_raw is None or is_seek_back or is_seek_fwd:
                if (
                    not is_seek_back
                    and target_sec <= self._playback_sec <= target_sec + (1.10 if prev_raw is None else 1.05)
                ):
                    pass
                elif (
                    prev_raw is None
                    and not is_seek_back
                    and min_keep is not None
                    and target_sec <= self._playback_sec < target_sec + 2.5
                    and self._playback_sec >= min_keep - 0.02
                ):
                    pass
                else:
                    self._playback_sec = target_sec
            elif not self._playback_playing:
                if is_natural_step and min_keep is not None and (target_sec - min_keep) > -2.5:
                    self._playback_sec = max(target_sec, min_keep)
                else:
                    self._playback_sec = target_sec
            elif err >= 1.1:
                self._playback_sec = target_sec
            elif err <= -1.25:
                if is_natural_step and err > -3.5 and min_keep is not None:
                    self._playback_sec = max(target_sec, min_keep)
                else:
                    self._playback_sec = target_sec
            elif err >= 0.12:
                self._playback_sec += err * 0.60
            elif err <= -0.14:
                # 正常前向播放时若本地时钟略超前整数秒，温和减速收敛，严禁倒退越过已进入的当前句起点
                next_sec = self._playback_sec + err * 0.30
                if min_keep is not None and (self._playback_sec >= min_keep - 0.02 or is_natural_step):
                    next_sec = max(next_sec, min_keep)
                self._playback_sec = next_sec

            self._last_uia_raw_sec = uia_sec
            self._last_uia_change_ts = now
            self._playback_playing = True
            self._ocr_back_cand = (-1, 0)
            self._last_clock_tick_ts = time.perf_counter()

            new_idx = YouTubeTranscriptLoader.find_segment_index_at_time(self._transcript_segments, self._playback_sec)
            if (
                not is_seek_back
                and (is_natural_step or is_seek_fwd)
                and new_idx >= 0
                and self._transcript_index >= 0
                and new_idx < self._transcript_index
                and err > -3.5
            ):
                new_idx = self._transcript_index

            if new_idx != self._transcript_index:
                self._transcript_index = new_idx
                if new_idx >= 0:
                    self._ensure_transcript_window_translated(new_idx, session_id, radius_before=2, radius_after=15)
                changed = True
        else:
            self._uia_back_cand = (-1.0, 0)
            pause_wait = max(1.65, min(3.2, 1.65 / max(0.45, self._playback_rate)))
            stalled_dur = (now - self._last_uia_change_ts) if self._last_uia_change_ts > 0.0 else 0.0
            if stalled_dur >= 2.65:
                self._uia_rate_anchor_sec = None
                self._uia_rate_anchor_ts = 0.0
                self._uia_step_dts.clear()
            if uia_paused and self._last_uia_change_ts > 0.0 and stalled_dur >= min(pause_wait, 0.35):
                floor_sec = max(0.0, uia_sec + self._manual_time_offset)
                # 严禁倒退回弹至低于当前 _playback_sec；仅将播放态置为暂停
                target_sec = max(self._playback_sec, floor_sec)
                new_idx = YouTubeTranscriptLoader.find_segment_index_at_time(self._transcript_segments, target_sec)
                if new_idx >= 0 and self._transcript_index >= 0 and new_idx < self._transcript_index:
                    new_idx = self._transcript_index
                if self._playback_playing or abs(self._playback_sec - target_sec) >= 0.35 or new_idx != self._transcript_index:
                    self._playback_playing = False
                    self._playback_sec = target_sec
                    self._last_clock_tick_ts = time.perf_counter()
                    if new_idx != self._transcript_index:
                        self._transcript_index = new_idx
                        if new_idx >= 0:
                            self._ensure_transcript_window_translated(new_idx, session_id, radius_before=2, radius_after=15)
                    changed = True
        return changed

    def _sync_clock_from_ocr_timestamp(self, detected_player_sec: float, session_id: int) -> bool:
        """
        UIA 不可用且画面字幕未能匹配时，使用 OCR 读取的播放器整数秒时间码做兜底校准：
        - 尊重整数秒区间 [floor_sec, floor_sec + 1.0] 与手动偏移 _manual_time_offset；
        - 仅在播放时钟明显偏离整数秒区间 (>= 1.5s) 时调整，禁止在刚切入下一句时因整数秒向下取整而回弹。
        """
        floor_sec = max(0.0, float(detected_player_sec) + self._manual_time_offset)
        ceil_sec = floor_sec + 1.0
        min_keep = self._min_keep_sec_for_cur_idx()

        if self._playback_sec < floor_sec:
            if (floor_sec - self._playback_sec) < 1.5:
                return False
            target_sec = floor_sec + 0.5
        elif self._playback_sec > ceil_sec:
            overshoot = self._playback_sec - ceil_sec
            if overshoot < 1.5:
                return False
            target_sec = floor_sec + (0.95 if overshoot < 2.5 else 0.5)
            if (
                overshoot < 2.5
                and min_keep is not None
                and self._playback_sec >= min_keep - 0.02
            ):
                target_sec = max(target_sec, min_keep)
        else:
            return False

        time_idx = YouTubeTranscriptLoader.find_segment_index_at_time(
            self._transcript_segments, target_sec
        )
        if (
            min_keep is not None
            and time_idx >= 0
            and self._transcript_index >= 0
            and time_idx < self._transcript_index
            and target_sec >= min_keep - 0.02
        ):
            time_idx = self._transcript_index
        if abs(target_sec - self._playback_sec) < 0.15 and time_idx == self._transcript_index:
            return False
        self._playback_sec = target_sec
        self._last_clock_tick_ts = time.perf_counter()
        if time_idx != self._transcript_index:
            self._transcript_index = time_idx
            self._ocr_back_cand = (-1, 0)
            if time_idx >= 0:
                self._ensure_transcript_window_translated(
                    time_idx, session_id, radius_before=2, radius_after=15
                )
        return True

    def _apply_uia_player_rect(
        self,
        hwnd: int,
        player_rect_uia: Optional[List[int]],
        win_rect_uia: Optional[List[int]],
        doc_rect_uia: Optional[List[int]] = None,
        platform: str = "youtube",
    ) -> bool:
        """将 UIA 播放器或 B站网页文档坐标自校准为物理与逻辑框，底部内缩 42px 避开进度条。"""
        win_rect_phys = _get_window_rect(hwnd)
        _, _, _, _, dpr = self._get_screen_metrics(win_rect_phys)
        scale = 1.0
        if win_rect_phys and win_rect_uia and len(win_rect_uia) == 4 and win_rect_uia[2] > 100:
            phys_w = win_rect_phys[2] - win_rect_phys[0]
            uia_w = float(win_rect_uia[2])
            if abs(uia_w - phys_w) > 30 and abs(uia_w * dpr - phys_w) <= 35:
                scale = dpr

        if player_rect_uia and len(player_rect_uia) == 4:
            px = int(round(player_rect_uia[0] * scale))
            py = int(round(player_rect_uia[1] * scale))
            pw = int(round(player_rect_uia[2] * scale))
            ph = int(round(player_rect_uia[3] * scale))
        elif platform == "bilibili" and doc_rect_uia and len(doc_rect_uia) == 4:
            dx = int(round(doc_rect_uia[0] * scale))
            dy = int(round(doc_rect_uia[1] * scale))
            dw = int(round(doc_rect_uia[2] * scale))
            dh = int(round(doc_rect_uia[3] * scale))
            if dw < 300 or dh < 200:
                return False
            logical_dw = dw / max(1.0, dpr)
            if logical_dw >= 960:
                # 桌面端标准 B 站双栏布局：主播放器位于左侧 ~3%~67%（避开右侧选集/弹幕/推荐栏），顶部跳过 ~64px 导航栏
                px = dx + int(dw * 0.03)
                pw = int(dw * 0.64)
            else:
                px = dx + int(dw * 0.02)
                pw = int(dw * 0.96)
            py = dy + int(64 * dpr)
            ph = min(int(round(pw * 9 / 16)), max(200, dh - int(80 * dpr)))
        else:
            return False

        if pw < 200 or ph < 120:
            return False
        self._last_player_phys_rect = (px, py, px + pw, py + ph)
        lx = int(round(px / dpr))
        ly = int(round(py / dpr))
        lw = max(320, int(round(pw / dpr)))
        lh = max(60, int(round(ph / dpr)) - 42)
        new_box = (lx, ly, lw, lh)
        if self._last_player_box is not None and all(abs(a - b) < 12 for a, b in zip(new_box, self._last_player_box)):
            return False
        self._last_player_box = new_box
        return True

    def _refresh_target_window(self) -> int:
        try:
            prev_hwnd = self._target_hwnd
            fg = int(_user32.GetForegroundWindow() or 0)
            if _is_valid_video_window(fg, self._own_pid):
                self._target_hwnd = fg
            elif not _is_valid_video_window(self._target_hwnd, self._own_pid):
                found = self._find_top_video_window()
                if found:
                    self._target_hwnd = found
            if self._target_hwnd != prev_hwnd:
                with self._state_lock:
                    self._last_player_phys_rect = None
                    self._last_player_box = None
            if self._target_hwnd:
                with self._state_lock:
                    need_fallback_box = (self._last_player_phys_rect is None and self._last_player_box is None)
                if need_fallback_box:
                    pbox = self._compute_player_bottom_box(self._target_hwnd)
                    if pbox is not None:
                        with self._state_lock:
                            if self._last_player_phys_rect is None and self._last_player_box is None:
                                self._last_player_box = pbox
        except Exception:
            pass
        return self._target_hwnd

    def _refresh_screen_cache(self):
        """在 Qt 主线程快照所有屏幕物理边界与 DPR，供后台线程安全读取而不直接触碰 QScreen。"""
        metrics: List[Tuple[int, int, int, int, float]] = []
        try:
            screens = QGuiApplication.screens()
            for scr in screens:
                sdpr = float(scr.devicePixelRatio() or 1.0)
                sg = scr.geometry()
                metrics.append((
                    int(sg.left() * sdpr),
                    int(sg.top() * sdpr),
                    int((sg.left() + sg.width()) * sdpr),
                    int((sg.top() + sg.height()) * sdpr),
                    sdpr,
                ))
        except Exception:
            pass
        if metrics:
            self._cached_screens = metrics

    def _get_screen_metrics(self, ref_rect_phys: Optional[Tuple[int, int, int, int]] = None) -> Tuple[int, int, int, int, float]:
        """支持多显示器与异 DPI：优先读取主线程快照的屏幕物理边界，避免后台线程访问 QScreen。"""
        cached = getattr(self, "_cached_screens", None)
        if not cached:
            # 仅在主线程允许兜底初始化快照
            try:
                app = QApplication.instance()
                if app is not None and threading.current_thread() is threading.main_thread():
                    self._refresh_screen_cache()
                    cached = getattr(self, "_cached_screens", None)
            except Exception:
                pass
        if cached:
            if ref_rect_phys:
                cx_phys = (ref_rect_phys[0] + ref_rect_phys[2]) / 2.0
                cy_phys = (ref_rect_phys[1] + ref_rect_phys[3]) / 2.0
                for item in cached:
                    if item[0] <= cx_phys <= item[2] and item[1] <= cy_phys <= item[3]:
                        return item
            return cached[0]
        return (0, 0, 1920, 1080, 1.0)

    def _compute_capture_bbox(self, target_hwnd: Optional[int] = None) -> Tuple[int, int, int, int, float]:
        if self._custom_region_phys is not None:
            cx1, cy1, cx2, cy2 = self._custom_region_phys
            sx1, sy1, sx2, sy2, dpr = self._get_screen_metrics(self._custom_region_phys)
            return (
                max(sx1, cx1),
                max(sy1, cy1),
                min(sx2, cx2),
                min(sy2, cy2),
                dpr,
            )

        hwnd = target_hwnd if target_hwnd is not None else self._refresh_target_window()
        win_rect = _get_window_rect(hwnd) if hwnd else None
        sx1, sy1, sx2, sy2, dpr = self._get_screen_metrics(win_rect)

        # 若 UIA 已锁定 YouTube 播放器精确矩形，兜底 OCR 也严格限定在播放器底部 60%~98%，绝不抓取右侧播放列表
        if self._last_player_phys_rect is not None and hwnd:
            px1, py1, px2, py2 = self._last_player_phys_rect
            pw = px2 - px1
            ph = py2 - py1
            if pw >= 240 and ph >= 140:
                return (
                    max(sx1, px1),
                    max(sy1, py1 + int(ph * 0.52)),
                    min(sx2, px2),
                    min(sy2, py2),
                    dpr,
                )

        if win_rect:
            wx1, wy1, wx2, wy2 = win_rect
            wx1 = max(sx1, wx1)
            wy1 = max(sy1, wy1)
            wx2 = min(sx2, wx2)
            wy2 = min(sy2, wy2)
            ww = wx2 - wx1
            wh = wy2 - wy1
            if ww >= 320 and wh >= 200:
                is_fullscreen = (ww >= (sx2 - sx1) - 60) and (wh >= (sy2 - sy1) - 60)
                rx1 = wx1 + int(ww * 0.05)
                rx2 = wx2 - int(ww * 0.05)
                if is_fullscreen:
                    ry1 = wy1 + int(wh * 0.38)
                    ry2 = wy1 + int(wh * 0.97)
                else:
                    # 非全屏浏览器网页，视频播放器主体通常落在顶部 22%~82%，兼容播放器进度时间码捕获
                    ry1 = wy1 + int(wh * 0.22)
                    ry2 = wy1 + int(wh * 0.82)
                return (rx1, ry1, rx2, ry2, dpr)

        sw = sx2 - sx1
        sh = sy2 - sy1
        return (
            sx1 + int(sw * 0.05),
            sy1 + int(sh * 0.40),
            sx2 - int(sw * 0.05),
            sy1 + int(sh * 0.94),
            dpr,
        )

    def _compute_capture_request(self, target_hwnd: Optional[int] = None) -> Tuple[Tuple[int, int, int, int, float], str]:
        """计算捕获区域并返回 (bbox_with_dpr, 'roi'|'full')；字幕位置稳定时优先窄带极速追踪。"""
        full_bbox = self._compute_capture_bbox(target_hwnd=target_hwnd)
        fx1, fy1, fx2, fy2, dpr = full_bbox
        now = time.time()

        with self._state_lock:
            self._poll_tick_seq += 1
            seq = self._poll_tick_seq
            force_full = self._force_full_next
            if force_full:
                self._force_full_next = False
            custom_region = self._custom_region_phys
            last_phys = self._last_native_phys_box
            last_seen = self._last_subtitle_seen_ts
            prev_roi = self._thumb_cache.get("roi")

        if (
            custom_region is None
            and not force_full
            and last_phys is not None
            and (now - last_seen) < 2.2
            and (seq % 6 != 0)
        ):
            _, ny, _, nh = last_phys
            if ny >= fy1 - int(60 * dpr) and (ny + nh) <= fy2 + int(60 * dpr):
                pad_y = int(round(95 * dpr))
                ry1 = max(fy1, ny - pad_y)
                ry2 = min(fy2, ny + nh + pad_y)
                min_h = int(round(200 * dpr))
                if (ry2 - ry1) < min_h:
                    mid_y = (ry1 + ry2) // 2
                    ry1 = max(fy1, mid_y - min_h // 2)
                    ry2 = min(fy2, ry1 + min_h)
                    if (ry2 - ry1) < min_h:
                        ry1 = max(fy1, ry2 - min_h)
                if prev_roi is not None:
                    pb, _ = prev_roi
                    tol = int(24 * dpr)
                    if pb[0] == fx1 and pb[2] == fx2 and abs(pb[1] - ry1) <= tol and abs(pb[3] - ry2) <= tol:
                        return (pb[0], pb[1], pb[2], pb[3], dpr), "roi"
                return (fx1, ry1, fx2, ry2, dpr), "roi"

        return full_bbox, "full"

    def _on_poll_tick(self):
        if not self._running or self._paused or self._worker_busy:
            return

        # 若上一帧字幕已经消失并停留在无字幕静态画面超过 2.0 秒，主动触发归档清屏
        now = time.time()
        should_commit_empty = False
        with self._state_lock:
            if (
                self._mode == "ocr"
                and not self._last_ocr_had_text
                and self._empty_since_ts > 0.0
                and self._stitcher.active_en
                and (now - self._empty_since_ts) >= 2.0
            ):
                res = self._stitcher.feed_ocr_text("", timestamp=now)
                if res["committed"] or res["changed"]:
                    sid = self._session_id
                    for entry in res["committed"]:
                        self._ensure_entry_translated(entry, sid)
                    should_commit_empty = True
                    self._empty_since_ts = 0.0
        if should_commit_empty:
            self._emit_snapshot()

        if (self._poll_tick_seq & 31) == 0:
            self._refresh_screen_cache()
        target_hwnd = self._refresh_target_window()
        win_title = _get_window_title(target_hwnd) if target_hwnd else ""
        title_changed = bool(win_title and win_title != self._last_seen_win_title)
        if win_title:
            self._last_seen_win_title = win_title

        bbox_with_dpr, capture_kind = self._compute_capture_request(target_hwnd=target_hwnd)
        x1, y1, x2, y2, _ = bbox_with_dpr
        if x2 - x1 < 120 or y2 - y1 < 60:
            return
        self._worker_busy = True
        with self._state_lock:
            sid = self._session_id
        self._ensure_ocr_worker()
        try:
            self._ocr_queue.put_nowait((bbox_with_dpr, sid, capture_kind, target_hwnd, win_title, title_changed))
        except queue.Full:
            self._worker_busy = False

    def _capture_and_process_worker(
        self,
        bbox_with_dpr: Tuple[int, int, int, int, float],
        session_id: int,
        capture_kind: str = "full",
        target_hwnd: int = 0,
        win_title: str = "",
        title_changed: bool = False,
        ocr_loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        try:
            with self._state_lock:
                if not self._running or self._paused or session_id != self._session_id:
                    return

            now = time.time()
            # 1. 后台读取浏览器 Windows UIA 状态（精确 URL / 播放器时间秒数 / 播放器矩形，零主线程阻塞）
            uia_res = None
            with self._state_lock:
                if not self._running or self._paused or session_id != self._session_id:
                    return
                force_uia = self._force_uia_refresh
                if force_uia:
                    self._force_uia_refresh = False
                has_custom_box = (self._custom_region_phys is not None)
            target_cls = _get_window_class(target_hwnd) if target_hwnd else ""
            if (
                target_hwnd
                and (not has_custom_box)
                and (target_cls in _CHROMIUM_VIDEO_CLASSES)
                and (now - self._last_uia_query_ts >= 0.24 or title_changed or force_uia)
            ):
                self._last_uia_query_ts = now
                uia_res = self._uia_probe.query(target_hwnd, force_refresh=(title_changed or force_uia))

            yt_title_from_win = YouTubeTranscriptLoader.extract_youtube_title_from_window(win_title)
            bili_title_from_win = BilibiliTranscriptLoader.extract_bilibili_title_from_window(win_title)
            title_low = (win_title or "").lower()
            is_yt_title = bool(yt_title_from_win) or ("youtube" in title_low)
            is_bili_title = bool(bili_title_from_win) or ("bilibili" in title_low) or ("哔哩哔哩" in title_low)

            if uia_res:
                uia_platform = uia_res.get("platform", "") or ""
                uia_vid = uia_res.get("video_id", "") or ""
                uia_raw_id = uia_res.get("raw_id", "") or uia_vid
                uia_page = int(uia_res.get("page", 1) or 1)
                uia_sec = uia_res.get("sec")
                box_updated = False
                with self._state_lock:
                    if not self._running or session_id != self._session_id:
                        return
                    eff_platform = uia_platform or ("bilibili" if is_bili_title else "youtube")
                    box_updated = self._apply_uia_player_rect(
                        target_hwnd,
                        uia_res.get("player_rect"),
                        uia_res.get("win_rect"),
                        doc_rect_uia=uia_res.get("doc_rect"),
                        platform=eff_platform,
                    )
                    if uia_sec is not None:
                        try:
                            self._latest_uia_sec = float(uia_sec)
                        except Exception:
                            uia_sec = None

                if uia_vid:
                    with self._state_lock:
                        ocr_locked = bool(self._ocr_forced or self._custom_region_phys is not None)
                        suppressed = (uia_vid == self._suppressed_video_id)
                        no_cap = (uia_vid in self._no_caption_vids)
                        cur_loaded = (
                            uia_vid == self._current_video_id
                            and self._mode == "transcript"
                            and bool(self._transcript_segments)
                        )
                        is_loading = (uia_vid == self._loading_video_id)
                    if not ocr_locked and not suppressed and not no_cap:
                        if is_loading:
                            return
                        if not cur_loaded:
                            fallback_title = bili_title_from_win if uia_platform == "bilibili" else yt_title_from_win
                            self._start_load_video_vid(
                                uia_platform or "youtube",
                                uia_vid,
                                uia_raw_id,
                                page=uia_page,
                                fallback_title=fallback_title,
                            )
                            return
                        # 已锁定当前外挂字幕轨：仅用 UIA 进度锁相控制播放/暂停/跳转，严禁扫网页 OCR 杂音
                        need_emit_uia = box_updated
                        uia_paused = bool(uia_res.get("paused"))
                        with self._state_lock:
                            if not self._running or session_id != self._session_id:
                                return
                            self._last_native_box = None
                            if uia_sec is not None:
                                if self._sync_clock_from_uia(float(uia_sec), session_id, uia_paused=uia_paused):
                                    need_emit_uia = True
                        if need_emit_uia:
                            self._emit_snapshot()
                        return
                    else:
                        # 已切到新视频但新视频无外挂 CC / 已禁用外挂：立刻清理旧视频字幕轨切回 OCR，杜绝停留在旧 YouTube 字幕无反应
                        reset_on_switch = False
                        with self._state_lock:
                            if (
                                self._running
                                and session_id == self._session_id
                                and (no_cap or suppressed)
                                and self._mode == "transcript"
                                and self._current_video_id
                                and uia_vid != self._current_video_id
                            ):
                                reset_on_switch = self._reset_transcript_to_ocr_locked()
                        if reset_on_switch or box_updated:
                            if reset_on_switch:
                                self.status_notice.emit("无英文 CC · 已切画面 OCR")
                            self._emit_snapshot()

            # 若正在自动识别或加载字幕轨，或已锁定外挂字幕轨：严禁把网页推荐/播放列表抓成字幕
            with self._state_lock:
                if not self._running or session_id != self._session_id:
                    return
                ocr_locked = bool(self._ocr_forced or self._custom_region_phys is not None)
                yt_loading_now = bool(self._loading_video_id or self._auto_resolving_title)
                in_transcript_yt = (self._mode == "transcript" and bool(self._transcript_segments))
                cur_vid_now = self._current_video_id or ""
                uia_fresh = (self._last_uia_sec_seen_ts > 0.0 and (time.monotonic() - self._last_uia_sec_seen_ts) < 3.0)
                vid_suppressed_or_no_cap = bool(
                    cur_vid_now
                    and (cur_vid_now == self._suppressed_video_id or cur_vid_now in self._no_caption_vids)
                )
            if yt_loading_now:
                return

            # 跨平台切换守卫（例如从 YouTube 切换到 B站，或从 B站切回 YouTube）：
            # 绝不让旧平台残留的 transcript 状态拦截新平台的画面 OCR 识别
            cur_is_bili = cur_vid_now.startswith("bili:")
            platform_mismatch = in_transcript_yt and (
                (is_bili_title and not is_yt_title and not cur_is_bili)
                or (is_yt_title and not is_bili_title and cur_is_bili)
            )
            uia_vid_now = str((uia_res or {}).get("video_id") or "")
            vid_mismatch = bool(uia_vid_now and cur_vid_now and uia_vid_now != cur_vid_now)
            if platform_mismatch:
                reset_cross = False
                with self._state_lock:
                    if self._running and session_id == self._session_id and self._mode == "transcript":
                        reset_cross = self._reset_transcript_to_ocr_locked()
                if reset_cross:
                    self.status_notice.emit("已切换平台 · 画面 OCR 识别")
                    self._emit_snapshot()
                in_transcript_yt = False

            uia_navigated_away = bool(uia_res and (uia_res.get("url") or "").strip() and not uia_vid_now)
            if uia_navigated_away and in_transcript_yt:
                with self._state_lock:
                    if self._running and session_id == self._session_id:
                        self._playback_playing = False
            if (not ocr_locked) and (not uia_navigated_away) and (not vid_mismatch) and in_transcript_yt and ((is_yt_title or is_bili_title) and (not title_changed or uia_fresh)):
                return
            if (not ocr_locked) and yt_title_from_win and not vid_suppressed_or_no_cap:
                if uia_res is not None and not uia_vid_now:
                    self._maybe_auto_detect_youtube_window(target_hwnd)
                return
            if (not ocr_locked) and bili_title_from_win and in_transcript_yt and cur_is_bili and (not vid_mismatch):
                return

            if self._custom_region_phys is None:
                non_vid_state = _is_non_video_browser_page(target_hwnd, uia_res, win_title)
                if non_vid_state is not None:
                    with self._state_lock:
                        self._last_browser_non_video = non_vid_state
                elif title_changed:
                    with self._state_lock:
                        self._last_browser_non_video = False
                with self._state_lock:
                    skip_non_video = self._last_browser_non_video
                if skip_non_video:
                    changed_reset = False
                    with self._state_lock:
                        if not self._running or session_id != self._session_id:
                            return
                        if self._last_native_box is not None or self._last_player_box is not None or self._stitcher.active_en:
                            self._last_native_box = None
                            self._last_native_phys_box = None
                            self._last_player_box = None
                            self._last_player_phys_rect = None
                            self._last_ocr_had_text = False
                            self._empty_since_ts = 0.0
                            if self._stitcher.active_en:
                                flush_res = self._stitcher.feed_ocr_text("", timestamp=time.time() + 10.0)
                                for entry in flush_res.get("committed") or []:
                                    self._ensure_entry_translated(entry, session_id)
                            changed_reset = True
                    if changed_reset:
                        self._emit_snapshot()
                    return

            x1, y1, x2, y2, dpr = bbox_with_dpr
            img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
            if img is None or img.width < 40 or img.height < 20:
                return

            # 6x8 局部块帧差门控：防止静态课件上底部新增 1~2 词因全图占比低被误过滤
            thumb = np.asarray(img.resize((160, 60), Image.Resampling.BILINEAR).convert("L"), dtype=np.int16)
            cur_box = (x1, y1, x2, y2)
            with self._state_lock:
                if not self._running or session_id != self._session_id:
                    return
                prev_entry = self._thumb_cache.get(capture_kind)
                prev_thumb = prev_entry[1] if (prev_entry is not None and prev_entry[0] == cur_box) else None
                if not _has_significant_frame_change(prev_thumb, thumb):
                    return
                self._thumb_cache[capture_kind] = (cur_box, thumb)
                self._prev_thumb = thumb

            # 运行 WinRT 印刷体快速 OCR（复用常驻事件循环，raw_only=True 绕过手写替换表污染）
            ocr_coro = win_ocr_engine._recognize_winrt_async(
                img, x1, y1, (x1 + x2) // 2, (y1 + y2) // 2, raw_only=True
            )
            if ocr_loop is not None and not ocr_loop.is_closed():
                winrt_res = ocr_loop.run_until_complete(ocr_coro)
            else:
                winrt_res = asyncio.run(ocr_coro)
            all_words = winrt_res.get("all_words", []) if winrt_res else []
            with self._state_lock:
                is_ocr_mode = (self._mode == "ocr")
                has_custom_region = (self._custom_region_phys is not None)
            if is_ocr_mode or has_custom_region:
                # 亮色描边字幕强力召回：当 WinRT 因视频花哨底纹碎裂/漏词（含中段大空洞，如 IMG2 仅剩首尾 4 词）时切换描边双引擎识别
                short_frag_cnt = sum(
                    1
                    for w in all_words
                    if ("•" in w.get("text", ""))
                    or (len(w.get("clean", "")) <= 2 and w.get("clean", "").lower() not in ("a", "i", "in", "on", "at", "of", "to", "is", "it", "as", "be", "we", "so", "no", "or", "by", "if", "do", "up", "my"))
                )
                has_line_gap_hole = False
                if len(all_words) >= 2:
                    ordered_tmp = sorted(all_words, key=lambda w: (int(w.get("line_id", 0)), int(w.get("box", {}).get("x", 0))))
                    line_spans: Dict[int, List[int]] = {}
                    for wi in range(len(ordered_tmp)):
                        w_cur = ordered_tmp[wi]
                        b_cur = w_cur.get("box", {})
                        lid_c = int(w_cur.get("line_id", 0))
                        bx1_c, bx2_c = b_cur.get("x", 0), b_cur.get("x", 0) + b_cur.get("w", 0)
                        if lid_c not in line_spans:
                            line_spans[lid_c] = [bx1_c, bx2_c]
                        else:
                            line_spans[lid_c][0] = min(line_spans[lid_c][0], bx1_c)
                            line_spans[lid_c][1] = max(line_spans[lid_c][1], bx2_c)
                        if wi < len(ordered_tmp) - 1:
                            w2 = ordered_tmp[wi + 1]
                            if w_cur.get("line_id") == w2.get("line_id"):
                                b2 = w2.get("box", {})
                                gap_px = b2.get("x", 0) - bx2_c
                                lh = max(14, b_cur.get("h", 0), b2.get("h", 0))
                                if gap_px > max(int(90 * dpr), int(lh * 3.8)):
                                    has_line_gap_hole = True
                                    break
                    if (not has_line_gap_hole) and len(all_words) <= 6 and len(line_spans) >= 2:
                        spans = list(line_spans.values())
                        min_lx = min(s[0] for s in spans)
                        max_rx = max(s[1] for s in spans)
                        if (max_rx - min_lx) >= int(img.width * 0.45):
                            # 若双行跨屏却总词数极少，或首尾两行左右严重错位缺段（如 IMG2 只剩左上与右下 4 词）
                            if any((s[0] - x1) > int(img.width * 0.42) or (s[1] - x1) < int(img.width * 0.30) for s in spans):
                                has_line_gap_hole = True
                has_weak_winrt = (
                    len(all_words) < 4
                    or short_frag_cnt >= 2
                    or has_line_gap_hole
                    or any("•" in w.get("text", "") for w in all_words)
                )
                if has_weak_winrt:
                    if has_custom_region:
                        # 空白/纯色过渡帧轻量对比度门控，避免静音无字幕画面每帧触发 RapidOCR ONNX 尖峰
                        need_rapid_custom = bool(all_words)
                        if not need_rapid_custom:
                            try:
                                g_arr = np.asarray(img.convert("L"), dtype=np.int16)
                                if g_arr.size > 0 and (int(g_arr.max()) - int(g_arr.min())) >= 36:
                                    grad_h = np.abs(g_arr[:, 1:] - g_arr[:, :-1])
                                    if float(np.mean(grad_h >= 24)) >= 0.0025:
                                        need_rapid_custom = True
                            except Exception:
                                need_rapid_custom = True
                        if need_rapid_custom:
                            sub_words = win_ocr_engine.recognize_outlined_subtitle_words(img, x1, y1)
                            if len(sub_words) > len(all_words) or ((short_frag_cnt >= 2 or has_line_gap_hole) and sub_words):
                                all_words = sub_words
                    else:
                        sub_regs = win_ocr_engine.detect_outlined_subtitle_regions(img)
                        if sub_regs:
                            rec_sub: List[Dict[str, Any]] = []
                            for sr in sub_regs[:2]:
                                sw = win_ocr_engine.recognize_outlined_subtitle_words(
                                    sr["crop"], x1 + sr["origin_x"], y1 + sr["origin_y"]
                                )
                                rec_sub.extend(sw)
                            if len(rec_sub) > len(all_words) or ((short_frag_cnt >= 2 or has_line_gap_hole) and rec_sub):
                                all_words = rec_sub
            raw_ocr_joined = " ".join(w.get("text", "").strip() for w in all_words if w.get("text", "").strip())
            detected_player_sec = extract_player_timestamp_sec(raw_ocr_joined)
            subtitle_text, native_box_phys = self._extract_subtitle_lines_and_box(
                all_words, (x1, y1, x2, y2), dpr=dpr, img=img, trust_custom_region=has_custom_region
            )

            native_box_logic: Optional[Tuple[int, int, int, int]] = None
            if native_box_phys is not None:
                bx, by, bw, bh = native_box_phys
                native_box_logic = (
                    int(round(bx / dpr)),
                    int(round(by / dpr)),
                    max(40, int(round(bw / dpr))),
                    max(20, int(round(bh / dpr))),
                )

            now = time.time()
            need_emit = False
            with self._state_lock:
                if not self._running or session_id != self._session_id:
                    return

                if native_box_phys is not None:
                    self._last_native_phys_box = native_box_phys
                    self._last_subtitle_seen_ts = now
                    self._force_full_next = False
                elif capture_kind == "roi":
                    # 窄带未命中时下一帧立刻全量重定位
                    self._force_full_next = True

                if native_box_logic is not None:
                    self._last_native_box = native_box_logic

                if self._mode == "transcript" and self._transcript_segments:
                    uia_active = (
                        self._last_uia_sec_seen_ts > 0.0
                        and (time.monotonic() - self._last_uia_sec_seen_ts) < 3.0
                    )
                    if uia_active:
                        if native_box_logic is not None:
                            need_emit = True
                    else:
                        matched_idx = -1
                        if subtitle_text:
                            safe_cur = max(0, self._transcript_index)
                            matched_idx = YouTubeTranscriptLoader.match_timeline_index(
                                subtitle_text, self._transcript_segments, safe_cur
                            )
                            if matched_idx != -1:
                                allow_ocr_jump = True
                                if matched_idx < self._transcript_index:
                                    # 若画面仍包含当前已进入句的词语（双行原生字幕首行滞留），严禁回跳任何更早句子
                                    still_shows_cur = False
                                    if 0 <= self._transcript_index < len(self._transcript_segments):
                                        cur_en = self._transcript_segments[self._transcript_index].get("en_text", "")
                                        cur_toks = {
                                            _norm_token(t)
                                            for t in cur_en.split()
                                            if len(_norm_token(t)) >= 3
                                        }
                                        ocr_toks = {
                                            _norm_token(t)
                                            for t in subtitle_text.split()
                                            if len(_norm_token(t)) >= 3
                                        }
                                        if len(cur_toks & ocr_toks) >= 2:
                                            still_shows_cur = True
                                    if still_shows_cur:
                                        allow_ocr_jump = False
                                        self._ocr_back_cand = (-1, 0)
                                    elif (self._transcript_index - matched_idx) <= 4:
                                        prev_c, hit_c = self._ocr_back_cand
                                        hit_c = (hit_c + 1) if prev_c == matched_idx else 1
                                        self._ocr_back_cand = (matched_idx, hit_c)
                                        if hit_c < 6:
                                            allow_ocr_jump = False
                                    else:
                                        self._ocr_back_cand = (-1, 0)
                                else:
                                    self._ocr_back_cand = (-1, 0)

                                if allow_ocr_jump:
                                    seg_s = float(self._transcript_segments[matched_idx].get("start", 0.0))
                                    seg_e = float(self._transcript_segments[matched_idx].get("end", seg_s + 3.0))
                                    if matched_idx != self._transcript_index or not (seg_s - 0.5 <= self._playback_sec <= seg_e + 1.0):
                                        self._transcript_index = matched_idx
                                        self._playback_sec = seg_s + 0.1
                                        self._last_clock_tick_ts = time.perf_counter()
                                        self._ensure_transcript_window_translated(matched_idx, session_id, radius_before=2, radius_after=15)
                                        need_emit = True
                                    elif native_box_logic is not None:
                                        need_emit = True
                                elif native_box_logic is not None:
                                    need_emit = True
                        if matched_idx == -1 and detected_player_sec is not None:
                            if self._sync_clock_from_ocr_timestamp(detected_player_sec, session_id):
                                need_emit = True
                else:
                    if subtitle_text:
                        self._last_ocr_had_text = True
                        self._empty_since_ts = 0.0
                    elif capture_kind == "full":
                        if self._last_ocr_had_text:
                            self._empty_since_ts = now
                        self._last_ocr_had_text = False
                    else:
                        # ROI 盲帧先触发下一帧 full 确认，若无字幕则不喂空
                        return

                    res = self._stitcher.feed_ocr_text(subtitle_text, timestamp=now)
                    for entry in res["committed"]:
                        self._ensure_entry_translated(entry, session_id)
                    active_en = res["active_en"]
                    if active_en:
                        self._schedule_active_translation(active_en, session_id)
                    need_emit = bool(res["changed"] or native_box_logic is not None)

            if need_emit:
                self._emit_snapshot()
        except Exception:
            pass
        finally:
            self._worker_busy = False

    def _extract_subtitle_lines_and_box(
        self,
        all_words: List[Dict[str, Any]],
        scan_bbox: Tuple[int, int, int, int],
        dpr: float = 1.0,
        img: Optional[Image.Image] = None,
        trust_custom_region: bool = False,
    ) -> Tuple[str, Optional[Tuple[int, int, int, int]]]:
        """从扫描区全部 OCR 词块中筛选字幕文本行并返回合成文本与联合包围盒。"""
        if not all_words:
            return "", None

        sx1, sy1, sx2, sy2 = scan_bbox
        scan_w = max(1, sx2 - sx1)
        max_lh = int(160 * max(1.0, dpr))
        min_lh = int(9 * max(1.0, dpr))
        lines_map: Dict[int, List[Dict[str, Any]]] = {}
        for w in all_words:
            lid = int(w.get("line_id", 0))
            lines_map.setdefault(lid, []).append(w)

        valid_lines: List[Tuple[int, str, int, int, int, int]] = []
        for lid, words in sorted(lines_map.items()):
            words.sort(key=lambda item: item["box"]["x"])
            raw_line = " ".join(w.get("text", "").strip() for w in words if w.get("text", "").strip())
            cleaned_line = clean_subtitle_ocr_text(raw_line)
            if not cleaned_line:
                continue
            alpha_cnt = sum(1 for c in cleaned_line if c.isascii() and c.isalpha())
            if alpha_cnt < 3:
                continue
            lx1 = min(w["box"]["x"] for w in words)
            ly1 = min(w["box"]["y"] for w in words)
            lx2 = max(w["box"]["x"] + w["box"]["w"] for w in words)
            ly2 = max(w["box"]["y"] + w["box"]["h"] for w in words)
            lw = lx2 - lx1
            lh = ly2 - ly1
            if lw < int(24 * max(1.0, dpr)) or lh < min_lh or lh > max_lh:
                continue
            if (not trust_custom_region) and len(cleaned_line) <= 4 and (lx2 < sx1 + scan_w * 0.14 or lx1 > sx2 - scan_w * 0.14):
                continue
            if (not trust_custom_region) and img is not None and not _is_likely_video_subtitle_line(img, (lx1, ly1, lx2, ly2), scan_bbox):
                continue
            valid_lines.append((lid, cleaned_line, lx1, ly1, lx2, ly2))

        if not valid_lines:
            return "", None

        clusters: List[List[Tuple[int, str, int, int, int, int]]] = []
        for item in valid_lines:
            if not clusters:
                clusters.append([item])
                continue
            prev = clusters[-1][-1]
            line_h = max(16, prev[5] - prev[3], item[5] - item[3])
            v_gap = item[3] - prev[5]
            if v_gap <= line_h * 2.2:
                clusters[-1].append(item)
            else:
                clusters.append([item])

        scan_cx = (sx1 + sx2) / 2.0
        scan_h = max(1, sy2 - sy1)
        prev_box = self._last_native_phys_box
        has_recent_box = prev_box is not None and (time.time() - self._last_subtitle_seen_ts) < 2.5
        best_cluster = clusters[-1]
        best_score = -1.0
        for cl in clusters:
            # 超过 4 行的密集文本块通常是网页简介/评论区而非字幕
            if len(cl) > 4:
                continue
            cl_eval = cl[-3:] if len(cl) > 3 else cl
            joined = " ".join(x[1] for x in cl_eval)
            alpha_cnt = sum(1 for c in joined if c.isascii() and c.isalpha())
            ux1 = min(x[2] for x in cl_eval)
            uy1 = min(x[3] for x in cl_eval)
            ux2 = max(x[4] for x in cl_eval)
            uy2 = max(x[5] for x in cl_eval)
            cl_cx = (ux1 + ux2) / 2.0
            cl_cy = (uy1 + uy2) / 2.0
            center_factor = max(0.45, 1.35 - abs(cl_cx - scan_cx) / max(1.0, scan_w * 0.6))
            cy_norm = max(0.0, min(1.0, (cl_cy - sy1) / scan_h))
            v_factor = 0.70 + 0.65 * cy_norm
            cont_factor = 1.0
            if has_recent_box and prev_box is not None:
                p_cy = prev_box[1] + prev_box[3] / 2.0
                if abs(cl_cy - p_cy) <= max(65.0, prev_box[3] * 1.4):
                    cont_factor = 1.25
            score = alpha_cnt * center_factor * v_factor * cont_factor
            if score > best_score:
                best_score = score
                best_cluster = cl_eval

        if best_score < 0:
            cl_fallback = clusters[-1]
            best_cluster = cl_fallback[-3:] if len(cl_fallback) > 3 else cl_fallback

        valid_lines = best_cluster
        text = " ".join(item[1] for item in valid_lines).strip()
        ux1 = min(item[2] for item in valid_lines)
        uy1 = min(item[3] for item in valid_lines)
        ux2 = max(item[4] for item in valid_lines)
        uy2 = max(item[5] for item in valid_lines)
        return text, (ux1, uy1, ux2 - ux1, uy2 - uy1)

    def _get_cached_translation(self, en_text: str) -> Optional[str]:
        key = en_text.strip()
        if not key:
            return ""
        with self._cache_lock:
            val = self._trans_cache.get(key)
            if val is not None:
                self._trans_cache.move_to_end(key)
            return val

    def _put_cached_translation(self, en_text: str, cn_text: str):
        key = en_text.strip()
        if not key or not cn_text:
            return
        with self._cache_lock:
            self._trans_cache[key] = cn_text
            self._trans_cache.move_to_end(key)
            while len(self._trans_cache) > 400:
                self._trans_cache.popitem(last=False)

    def _translate_sync_cached(self, en_text: str) -> str:
        cached = self._get_cached_translation(en_text)
        if cached is not None:
            return cached
        prepared = preprocess_for_translation(en_text)
        cn = translate_text(prepared or en_text)
        if cn:
            self._put_cached_translation(en_text, cn)
        return cn

    def _schedule_active_translation(self, active_en: str, session_id: int):
        cached = self._get_cached_translation(active_en)
        if cached is not None:
            with self._cache_lock:
                self._active_trans_seq += 1
                seq = self._active_trans_seq
                self._latest_wanted_en = active_en
                self._latest_wanted_session = session_id
            with self._state_lock:
                if session_id == self._session_id and seq >= self._done_active_trans_seq:
                    self._done_active_trans_seq = seq
                    self._last_active_en_translated = active_en
                    self._last_active_cn = cached
            return

        with self._cache_lock:
            if active_en != self._latest_wanted_en or session_id != self._latest_wanted_session:
                self._active_trans_seq += 1
                self._latest_wanted_en = active_en
                self._latest_wanted_session = session_id
            if self._active_worker_running:
                return
            self._active_worker_running = True

        my_token = getattr(self, "_active_worker_token", 0) + 1
        self._active_worker_token = my_token

        def _relay_worker(worker_token: int = my_token):
            try:
                while True:
                    with self._cache_lock:
                        target_en = self._latest_wanted_en
                        target_seq = self._active_trans_seq
                        target_sid = self._latest_wanted_session
                    with self._state_lock:
                        running_now = self._running
                        cur_sid = self._session_id
                    if not running_now or not target_en:
                        return
                    if target_sid != cur_sid:
                        with self._cache_lock:
                            if self._latest_wanted_session == cur_sid and self._latest_wanted_en:
                                continue
                            return

                    cn = self._translate_sync_cached(target_en)
                    emitted = False
                    if cn:
                        with self._state_lock:
                            if self._running and target_sid == self._session_id and target_seq >= self._done_active_trans_seq:
                                self._done_active_trans_seq = target_seq
                                self._last_active_en_translated = target_en
                                self._last_active_cn = cn
                                emitted = True
                    if emitted:
                        self._emit_snapshot()

                    with self._cache_lock:
                        if not self._running or (
                            self._active_trans_seq == target_seq
                            and self._latest_wanted_session == target_sid
                        ):
                            return
            finally:
                with self._cache_lock:
                    if getattr(self, "_active_worker_token", 0) == worker_token:
                        self._active_worker_running = False

        try:
            self._active_executor.submit(_relay_worker)
        except RuntimeError:
            with self._cache_lock:
                self._active_worker_running = False

    def _ensure_entry_translated(self, entry: SubtitleEntry, session_id: int):
        cached = self._get_cached_translation(entry.en_text)
        if cached:
            entry.cn_text = cached
            return
        # 若该归档句已拥有极接近的活跃翻译，先暂继承中文译文并同时提交完整译文校验
        if (
            self._last_active_cn
            and self._last_active_en_translated
            and entry.en_text.lower().startswith(self._last_active_en_translated[:16].lower())
        ):
            entry.cn_text = self._last_active_cn
        eid = entry.entry_id
        en_text = entry.en_text

        def _task():
            with self._state_lock:
                if not self._running or session_id != self._session_id:
                    return
            cn = self._translate_sync_cached(en_text)
            if not cn:
                return
            updated = False
            with self._state_lock:
                if self._running and session_id == self._session_id:
                    updated = self._stitcher.update_entry_translation(eid, cn)
            if updated:
                self._emit_snapshot()

        try:
            self._history_executor.submit(_task)
        except RuntimeError:
            pass

    def _lookup_active_cn_best_effort(self, active_en: str) -> str:
        if not active_en:
            return ""
        cached = self._get_cached_translation(active_en)
        if cached:
            return cached
        if self._last_active_en_translated and self._last_active_cn:
            cur_norms = [_norm_token(t) for t in active_en.split() if _norm_token(t)]
            prev_norms = [_norm_token(t) for t in self._last_active_en_translated.split() if _norm_token(t)]
            if len(cur_norms) >= 2 and len(prev_norms) >= 2:
                if cur_norms[:2] == prev_norms[:2]:
                    return self._last_active_cn
                prev_joined = " ".join(prev_norms)
                cur_joined = " ".join(cur_norms)
                if prev_joined in cur_joined or cur_joined in prev_joined:
                    return self._last_active_cn
        return ""

    # ---------------- 补充模式：YouTube / Bilibili 链接加载 ----------------

    def load_youtube_url(self, url_or_id: str):
        """在后台加载指定 YouTube 或 Bilibili 链接/BV号的完整字幕并自动预翻译双语轨。"""
        is_bili = BilibiliTranscriptLoader.is_bilibili_input(url_or_id)
        if is_bili:
            bili_id, page = BilibiliTranscriptLoader.extract_bvid_and_page(url_or_id)
            vid_hint = BilibiliTranscriptLoader.canonical_id(bili_id, page) if bili_id else ""
        else:
            vid_hint = YouTubeTranscriptLoader.extract_video_id(url_or_id)

        with self._state_lock:
            self._session_id += 1
            sid = self._session_id
            self._ocr_forced = False
            self._custom_region_phys = None
            self._suppressed_video_id = ""
            if vid_hint != self._current_video_id:
                self._latest_uia_sec = None
            if vid_hint and vid_hint in self._no_caption_vids:
                self._no_caption_vids.discard(vid_hint)
            self._loading_video_id = vid_hint or "__manual__"

        def _worker():
            try:
                if is_bili:
                    res = BilibiliTranscriptLoader.fetch_bilibili_transcript(url_or_id)
                else:
                    res = YouTubeTranscriptLoader.fetch_youtube_transcript(url_or_id)
                segments = res.get("segments", [])
                if not segments:
                    with self._state_lock:
                        if self._running and sid == self._session_id:
                            self.transcript_error.emit("该视频字幕轨为空")
                    return
                for seg in segments:
                    cached = self._get_cached_translation(seg.get("en_text", ""))
                    if cached:
                        seg["cn_text"] = cached
                with self._state_lock:
                    if not self._running or sid != self._session_id:
                        return
                    self._current_video_id = res.get("video_id", vid_hint)
                    self._transcript_segments = segments
                    self._transcript_title = res.get("title", "")
                    if self._transcript_title:
                        self._last_auto_yt_title = self._transcript_title
                    init_sec = float(self._latest_uia_sec) if self._latest_uia_sec is not None else 0.0
                    self._manual_time_offset = 0.0
                    self._playback_sec = init_sec
                    self._playback_rate = 1.0
                    self._last_uia_raw_sec = None
                    self._last_uia_change_ts = 0.0
                    self._last_uia_sec_seen_ts = 0.0
                    self._uia_prev_poll_ts = 0.0
                    self._uia_rate_anchor_sec = None
                    self._uia_rate_anchor_ts = 0.0
                    self._uia_last_step_ts = 0.0
                    self._uia_step_dts.clear()
                    self._uia_back_cand = (-1.0, 0)
                    self._ocr_back_cand = (-1, 0)
                    self._playback_playing = True
                    self._last_clock_tick_ts = time.perf_counter()
                    self._last_native_box = None
                    self._transcript_index = YouTubeTranscriptLoader.find_segment_index_at_time(segments, init_sec)
                    self._mode = "transcript"
                    loaded_meta = {
                        "title": self._transcript_title,
                        "count": len(segments),
                        "language": res.get("language", "en"),
                    }
                self._start_full_transcript_batch_translation(sid)
                self.transcript_loaded.emit(loaded_meta)
                self.status_notice.emit("已载入 B站外挂字幕" if is_bili else "已载入 YouTube 外挂字幕")
                self._emit_snapshot()
            except Exception as exc:
                msg = str(exc)
                with self._state_lock:
                    if self._running and sid == self._session_id:
                        if is_bili and "需登录账号" in msg:
                            self.bilibili_login_needed.emit(vid_hint or url_or_id)
                        self.transcript_error.emit(msg)
            finally:
                with self._state_lock:
                    if sid == self._session_id and self._loading_video_id in (vid_hint, "__manual__"):
                        self._loading_video_id = ""

        threading.Thread(target=_worker, daemon=True).start()

    load_video_url = load_youtube_url

    def _on_clock_tick(self):
        """80ms 高精度播放时钟推进器：结合预翻译窗口实现 0ms 延迟切换字幕。"""
        need_emit = False
        now_perf = time.perf_counter()
        with self._state_lock:
            if (
                not self._running
                or self._paused
                or not self._playback_playing
                or self._mode != "transcript"
                or not self._transcript_segments
            ):
                self._last_clock_tick_ts = now_perf
                return
            dt = max(0.0, min(0.5, now_perf - self._last_clock_tick_ts)) if self._last_clock_tick_ts > 0 else 0.08
            self._last_clock_tick_ts = now_perf
            self._playback_sec += dt * self._playback_rate
            new_idx = YouTubeTranscriptLoader.find_segment_index_at_time(
                self._transcript_segments, self._playback_sec
            )
            if new_idx != self._transcript_index:
                self._transcript_index = new_idx
                self._ocr_back_cand = (-1, 0)
                if new_idx >= 0:
                    self._ensure_transcript_window_translated(new_idx, self._session_id, radius_before=2, radius_after=15)
                need_emit = True
        if need_emit:
            self._emit_snapshot()

    def seek_relative(self, delta_sec: float):
        """微调当前时间轴偏移（如 -2s / +2s）并立即切换与预翻译。"""
        changed = False
        with self._state_lock:
            if not self._transcript_segments:
                return
            d_val = float(delta_sec)
            self._manual_time_offset += d_val
            self._playback_sec = max(0.0, self._playback_sec + d_val)
            self._last_clock_tick_ts = time.perf_counter()
            self._ocr_back_cand = (-1, 0)
            new_idx = YouTubeTranscriptLoader.find_segment_index_at_time(
                self._transcript_segments, self._playback_sec
            )
            self._transcript_index = new_idx
            if new_idx >= 0:
                self._ensure_transcript_window_translated(new_idx, self._session_id, radius_before=2, radius_after=15)
            changed = True
        if changed:
            self._emit_snapshot()

    def set_transcript_index(self, index: int):
        with self._state_lock:
            if not self._transcript_segments:
                return
            idx = max(0, min(len(self._transcript_segments) - 1, int(index)))
            self._transcript_index = idx
            self._playback_sec = float(self._transcript_segments[idx].get("start", 0.0)) + 0.05
            self._last_clock_tick_ts = time.perf_counter()
            self._ocr_back_cand = (-1, 0)
            sid = self._session_id
            self._ensure_transcript_window_translated(idx, sid, radius_before=2, radius_after=15)
        self._emit_snapshot()

    def _start_full_transcript_batch_translation(self, sid: int):
        """后台全片分批极速预翻译：首批立刻翻译当前进度附近 20 句，其余按每批 24 句全量覆盖至 100%。"""

        def _batch_worker():
            with self._state_lock:
                if not self._running or sid != self._session_id or not self._transcript_segments:
                    return
                n = len(self._transcript_segments)
                cur_idx = max(0, min(n - 1, int(self._transcript_index)))

            win_lo = max(0, cur_idx - 2)
            win_hi = min(n, cur_idx + 18)
            ordered_ranges: List[Tuple[int, int]] = [(win_lo, win_hi)]
            pos = win_hi
            while pos < n:
                nxt = min(n, pos + 24)
                ordered_ranges.append((pos, nxt))
                pos = nxt
            pos = 0
            while pos < win_lo:
                nxt = min(win_lo, pos + 24)
                ordered_ranges.append((pos, nxt))
                pos = nxt

            for r_lo, r_hi in ordered_ranges:
                batch_by_text: Dict[str, List[int]] = {}
                with self._state_lock:
                    if not self._running or sid != self._session_id or len(self._transcript_segments) != n:
                        return
                    for i in range(r_lo, r_hi):
                        seg = self._transcript_segments[i]
                        if seg.get("cn_text"):
                            continue
                        en_t = (seg.get("en_text") or "").strip()
                        if not en_t:
                            continue
                        cached = self._get_cached_translation(en_t)
                        if cached:
                            seg["cn_text"] = cached
                            continue
                        if en_t in batch_by_text:
                            batch_by_text[en_t].append(i)
                            continue
                        with self._cache_lock:
                            if en_t in self._pending_trans:
                                continue
                            self._pending_trans.add(en_t)
                        batch_by_text[en_t] = [i]

                if not batch_by_text:
                    continue
                unique_texts = list(batch_by_text.keys())
                try:
                    cns = translate_batch(unique_texts)
                finally:
                    with self._cache_lock:
                        for en_t in unique_texts:
                            self._pending_trans.discard(en_t)
                changed = False
                with self._state_lock:
                    if not self._running or sid != self._session_id or len(self._transcript_segments) != n:
                        return
                    for en_t, cn in zip(unique_texts, cns):
                        if not cn:
                            continue
                        self._put_cached_translation(en_t, cn)
                        for i in batch_by_text[en_t]:
                            if (self._transcript_segments[i].get("en_text") or "").strip() == en_t:
                                if not self._transcript_segments[i].get("cn_text"):
                                    self._transcript_segments[i]["cn_text"] = cn
                                    changed = True
                if changed:
                    self._emit_snapshot()

        threading.Thread(target=_batch_worker, daemon=True, name="sub_batch_tr").start()

    def _ensure_transcript_window_translated(self, center_idx: int, session_id: int, radius_before: int = 2, radius_after: int = 12):
        with self._state_lock:
            lo = max(0, center_idx - radius_before)
            hi = min(len(self._transcript_segments), center_idx + radius_after)
            pending_jobs: List[Tuple[int, str]] = []
            for idx in range(lo, hi):
                seg = self._transcript_segments[idx]
                if seg.get("cn_text"):
                    continue
                en_txt = (seg.get("en_text") or "").strip()
                if not en_txt:
                    continue
                cached = self._get_cached_translation(en_txt)
                if cached:
                    seg["cn_text"] = cached
                    continue
                with self._cache_lock:
                    if en_txt in self._pending_trans:
                        continue
                    self._pending_trans.add(en_txt)
                pending_jobs.append((idx, en_txt))

        if not pending_jobs:
            return

        def _task():
            try:
                with self._state_lock:
                    if not self._running or session_id != self._session_id:
                        return
                cns = translate_batch([txt for _, txt in pending_jobs])
                changed = False
                with self._state_lock:
                    if self._running and session_id == self._session_id:
                        n = len(self._transcript_segments)
                        for (i, txt), cn in zip(pending_jobs, cns):
                            if cn and i < n and (self._transcript_segments[i].get("en_text") or "").strip() == txt:
                                self._put_cached_translation(txt, cn)
                                if not self._transcript_segments[i].get("cn_text"):
                                    self._transcript_segments[i]["cn_text"] = cn
                                    changed = True
                if changed:
                    self._emit_snapshot()
            finally:
                with self._cache_lock:
                    for _, txt in pending_jobs:
                        self._pending_trans.discard(txt)

        try:
            self._history_executor.submit(_task)
        except RuntimeError:
            with self._cache_lock:
                for _, txt in pending_jobs:
                    self._pending_trans.discard(txt)

    def _emit_snapshot(self):
        with self._state_lock:
            if not self._running:
                return
            self._snapshot_seq += 1
            snap_seq = self._snapshot_seq
            eng_id = id(self)
            if self._mode == "transcript" and self._transcript_segments:
                idx = self._transcript_index
                if 0 <= idx < len(self._transcript_segments):
                    cur_seg = self._transcript_segments[idx]
                    act_en = cur_seg.get("en_text", "")
                    act_cn = cur_seg.get("cn_text", "")
                else:
                    act_en = ""
                    act_cn = ""
                payload = {
                    "engine_id": eng_id,
                    "snapshot_seq": snap_seq,
                    "mode": "transcript",
                    "active_en": act_en,
                    "active_cn": act_cn,
                    "history": [dict(s) for s in self._transcript_segments],
                    "native_box": self._last_native_box,
                    "player_box": self._last_player_box,
                    "playback_sec": self._playback_sec,
                    "transcript_index": idx,
                    "transcript_title": self._transcript_title,
                    "target_hwnd": self._target_hwnd,
                }
            else:
                if self._loading_video_id or self._auto_resolving_title:
                    active_en = ""
                    hud_en = ""
                    active_cn = ""
                else:
                    active_en = self._stitcher.active_en
                    hud_en = self._stitcher.hud_en
                    active_cn = self._lookup_active_cn_best_effort(active_en)
                payload = {
                    "engine_id": eng_id,
                    "snapshot_seq": snap_seq,
                    "mode": "ocr",
                    "active_en": active_en,
                    "hud_en": hud_en,
                    "active_cn": active_cn,
                    "history": self._stitcher.get_history_dicts(),
                    "native_box": self._last_native_box,
                    "player_box": self._last_player_box,
                    "playback_sec": 0.0,
                    "transcript_index": -1,
                    "transcript_title": "",
                    "target_hwnd": self._target_hwnd,
                }
        self.subtitle_updated.emit(payload)
