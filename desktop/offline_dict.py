"""
ScreenLex 真·离线全词库只读查询引擎（52,000+ 常见/四六级/考研/雅思/托福/GRE 词汇）
- 数据源：MIT 协议 ECDICT 核心词频表 + 内置 IELTS 学术语料词典编译而成的只读 SQLite (offline_dict.db)
- 性能：PRIMARY KEY WITHOUT ROWID，单次查询 < 0.1ms，零网络依赖
- 线程安全：只读模式打开 + RLock 串行化复用连接，避免跨线程关闭 SQLite 句柄异常
"""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_DB_PATH_CACHE: Optional[str] = None
_COUNT_CACHE: Optional[int] = None

_POS_LINE_RE = re.compile(r"^([a-zA-Z]{1,6}\.(?:\s*/\s*[a-zA-Z]{1,6}\.)?)\s+(.+)$")
_DOMAIN_LINE_RE = re.compile(r"^(\[[^\]]{1,8}\])\s*(.+)$")


def resolve_offline_dict_path() -> Optional[str]:
    """定位只读离线词库 offline_dict.db（兼容源码开发态与 PyInstaller _MEIPASS 冻结态）。"""
    global _DB_PATH_CACHE
    if _DB_PATH_CACHE and os.path.exists(_DB_PATH_CACHE):
        return _DB_PATH_CACHE

    candidates: List[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "desktop", "offline_dict.db"))
        candidates.append(os.path.join(meipass, "offline_dict.db"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "offline_dict.db"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "_internal", "desktop", "offline_dict.db"))
        candidates.append(os.path.join(exe_dir, "desktop", "offline_dict.db"))

    for path in candidates:
        if path and os.path.isfile(path) and os.path.getsize(path) > 4096:
            _DB_PATH_CACHE = path
            return path
    return None


def _get_conn() -> Optional[sqlite3.Connection]:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = resolve_offline_dict_path()
    if not path:
        return None
    try:
        # 使用 Path.as_uri() 正确编码含空格/#/？的路径，确保只读目录下也 100% 打开成功
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=2.0)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA cache_size = -4000")
        _CONN = conn
        return _CONN
    except Exception:
        try:
            conn = sqlite3.connect(path, check_same_thread=False, timeout=2.0)
            conn.execute("PRAGMA query_only = ON")
            _CONN = conn
            return _CONN
        except Exception:
            return None


def close_offline_dict():
    global _CONN
    with _LOCK:
        conn = _CONN
        _CONN = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


atexit.register(close_offline_dict)


def normalize_ipa_text(raw: str) -> str:
    """将 ECDICT 音标字符规整为标准 Unicode IPA，并包裹斜杠 /.../。"""
    s = (raw or "").strip()
    if ";" in s:
        # 仅保留首个主读音，去除末尾孤立分号
        parts = [p.strip() for p in s.split(";") if p.strip()]
        s = parts[0] if parts else ""
    s = s.strip("/").strip("[]").strip()
    if not s:
        return ""
    s = (
        s.replace("\u04d9", "ə")  # Cyrillic schwa -> IPA schwa
        .replace("'", "ˈ")
        .replace(",", "ˌ")        # ECDICT secondary stress -> IPA ˌ
        .replace(":", "ː")
    )
    return f"/{s}/"


def parse_offline_translation(raw_trans: str) -> List[Dict[str, str]]:
    """将 ECDICT 多行中文释义解析为标准 [{pos, cn, en}] 结构。"""
    if not raw_trans:
        return []
    out: List[Dict[str, str]] = []
    cleaned_raw = raw_trans.replace("\\r\\n", "\n").replace("\\r", "").replace("\\n", "\n").replace("\r", "")
    for raw_line in cleaned_raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m_pos = _POS_LINE_RE.match(line)
        if m_pos:
            pos = m_pos.group(1).strip()
            cn = m_pos.group(2).strip()
            if pos == "a.":
                pos = "adj."
            elif pos == "ad.":
                pos = "adv."
            out.append({"pos": pos, "cn": cn, "en": ""})
        else:
            m_dom = _DOMAIN_LINE_RE.match(line)
            if m_dom:
                dom = m_dom.group(1).strip()
                cn = m_dom.group(2).strip()
                out.append({"pos": dom, "cn": cn, "en": ""})
            else:
                out.append({"pos": "释义", "cn": line, "en": ""})
        if len(out) >= 4:
            break
    return out


def format_exam_tags(raw_tag: str) -> List[str]:
    if not raw_tag:
        return []
    mapping = {
        "cet4": "CET-4",
        "cet6": "CET-6",
        "ky": "考研",
        "toefl": "TOEFL",
        "ielts": "IELTS",
        "gre": "GRE",
        "gk": "高考",
        "zk": "中考",
    }
    tags: List[str] = []
    for tok in raw_tag.lower().split():
        label = mapping.get(tok)
        if label and label not in tags:
            tags.append(label)
        if len(tags) >= 3:
            break
    return tags


def lookup_offline_dict(word: str) -> Optional[Dict[str, Any]]:
    """
    0ms 级只读查询离线全词库。
    命中返回：{"word": str, "phonetic": "/.../", "defs": [...], "tags": [...]}
    未命中或库不可用返回 None。
    """
    key = (word or "").strip().lower()
    if not key or len(key) > 48:
        return None
    with _LOCK:
        conn = _get_conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT word, phonetic, translation, tag FROM dict WHERE word = ?",
                (key,),
            ).fetchone()
        except Exception:
            return None
    if not row:
        return None
    defs = parse_offline_translation(row[2] or "")
    if not defs:
        return None
    phon = normalize_ipa_text(row[1] or "")
    tags = format_exam_tags(row[3] or "")
    return {
        "word": row[0],
        "phonetic": phon,
        "defs": defs,
        "tags": tags,
    }


def get_offline_dict_count() -> int:
    global _COUNT_CACHE
    if _COUNT_CACHE is not None:
        return _COUNT_CACHE
    with _LOCK:
        conn = _get_conn()
        if conn is None:
            return 0
        try:
            row = conn.execute("SELECT COUNT(*) FROM dict").fetchone()
            _COUNT_CACHE = int(row[0]) if row else 0
            return _COUNT_CACHE
        except Exception:
            return 0
