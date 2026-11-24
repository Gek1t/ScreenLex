import sqlite3
import json
import threading
import time
import os
from contextlib import contextmanager
from typing import List, Dict, Optional, Any

import sys
import shutil

def _resolve_db_path() -> str:
    app_data = os.environ.get("APPDATA")
    if app_data:
        data_dir = os.path.join(app_data, "ScreenLex")
    else:
        data_dir = os.path.join(os.path.expanduser("~"), ".screenlex")
    os.makedirs(data_dir, exist_ok=True)
    target_path = os.path.join(data_dir, "screenlex.db")

    # 防御性迁移：如果持久化目录尚无数据库，但旧目录存在，自动复制迁移
    # 冻结发行态绝不从 _MEIPASS/desktop 迁移，避免任何构建期历史库污染新用户
    if not os.path.exists(target_path) or os.path.getsize(target_path) == 0:
        legacy_candidates = []
        if getattr(sys, 'frozen', False):
            legacy_candidates.append(os.path.join(os.path.dirname(sys.executable), "screenlex.db"))
        else:
            legacy_candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenlex.db"))
        for cand in legacy_candidates:
            if cand and os.path.exists(cand) and os.path.getsize(cand) > 0:
                try:
                    shutil.copy2(cand, target_path)
                    break
                except Exception:
                    pass
    return target_path

DB_PATH = _resolve_db_path()
_MAIN_CONN_LOCK = threading.Lock()
_MAIN_CONN: Optional[sqlite3.Connection] = None
_MAIN_CONN_PATH: Optional[str] = None


def _open_configured_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def close_all_connections():
    """显式关闭主线程复用的 SQLite 连接（用于退出清理与测试隔离）。"""
    global _MAIN_CONN, _MAIN_CONN_PATH
    with _MAIN_CONN_LOCK:
        conn = _MAIN_CONN
        _MAIN_CONN = None
        _MAIN_CONN_PATH = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _get_main_conn() -> sqlite3.Connection:
    global _MAIN_CONN, _MAIN_CONN_PATH
    with _MAIN_CONN_LOCK:
        if _MAIN_CONN is not None and _MAIN_CONN_PATH == DB_PATH:
            try:
                _MAIN_CONN.execute("SELECT 1")
                return _MAIN_CONN
            except Exception:
                try:
                    _MAIN_CONN.close()
                except Exception:
                    pass
                _MAIN_CONN = None
        elif _MAIN_CONN is not None:
            try:
                _MAIN_CONN.close()
            except Exception:
                pass
            _MAIN_CONN = None
        _MAIN_CONN = _open_configured_conn()
        _MAIN_CONN_PATH = DB_PATH
        return _MAIN_CONN


@contextmanager
def db_session(immediate: bool = False):
    is_main = threading.current_thread() is threading.main_thread()
    conn = _get_main_conn() if is_main else _open_configured_conn()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if immediate and conn.in_transaction:
            conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        if not is_main:
            try:
                conn.close()
            except Exception:
                pass

_PLACEHOLDER_CN = frozenset({
    "正在获取权威精准释义...",
    "正在智能翻译整句内容...",
    "正在查询释义...",
    "正在翻译整句...",
    "网络超时，已切换至离线原文",
})
_CTX_PLACEHOLDERS = frozenset({
    "正在同步现场语境译文...",
    "↳ 正在翻译语境例句...",
})
_DEFAULT_TAGS = ["Academic Lecture", "Screen Capture"]

def _sanitize_definitions(word_clean: str, definitions: List[Dict[str, str]]) -> tuple[List[Dict[str, str]], bool]:
    """剥离骨架占位符，并判定是否包含非原词回退的真实释义。"""
    cleaned: List[Dict[str, str]] = []
    has_rich_def = False
    w_low = word_clean.lower()
    for d in (definitions or []):
        cn = (d.get("cn") or "").strip()
        if not cn or cn in _PLACEHOLDER_CN:
            continue
        cleaned.append(d)
        if cn.lower() != w_low:
            has_rich_def = True
    return cleaned, has_rich_def

def init_db():
    with db_session() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS words (
                id TEXT PRIMARY KEY,
                word TEXT NOT NULL,
                phonetic_uk TEXT,
                phonetic_us TEXT,
                definitions_json TEXT,
                tags_json TEXT,
                context_sentence TEXT,
                context_translation TEXT,
                ai_analysis TEXT,
                source_app TEXT,
                is_collected INTEGER DEFAULT 1,
                created_at INTEGER,
                collected_at INTEGER
            )
        """)
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(words)").fetchall()}
        _migrations = [
            ("phonetic_uk", "TEXT"),
            ("phonetic_us", "TEXT"),
            ("definitions_json", "TEXT"),
            ("tags_json", "TEXT"),
            ("context_sentence", "TEXT"),
            ("context_translation", "TEXT"),
            ("ai_analysis", "TEXT"),
            ("source_app", "TEXT"),
            ("is_collected", "INTEGER DEFAULT 1"),
            ("created_at", "INTEGER"),
            ("collected_at", "INTEGER"),
        ]
        for col_name, col_def in _migrations:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE words ADD COLUMN {col_name} {col_def}")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_words_collected
            ON words(is_collected, collected_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at INTEGER
            )
        """)

init_db()

def save_word(
    word: str,
    definitions: List[Dict[str, str]],
    context_sentence: str = "",
    context_translation: str = "",
    ai_analysis: str = "",
    phonetic_uk: str = "",
    phonetic_us: str = "",
    tags: Optional[List[str]] = None,
    source_app: str = "Windows Screen",
    is_collected: bool = True
) -> Dict[str, Any]:
    word_clean = (word or "").strip()
    if not word_clean:
        return {}
    word_id = f"word_{word_clean.lower()}"
    now = int(time.time() * 1000)
    tags_data = tags or list(_DEFAULT_TAGS)
    tags_json_update = json.dumps(tags, ensure_ascii=False) if tags else ""
    ctx_trans_safe = (context_translation or "")
    clean_ctx_trans = "" if ctx_trans_safe.strip() in _CTX_PLACEHOLDERS else ctx_trans_safe
    context_sentence = context_sentence or ""
    ai_analysis = ai_analysis or ""
    phonetic_uk = phonetic_uk or ""
    phonetic_us = phonetic_us or ""

    clean_defs, has_rich_def = _sanitize_definitions(word_clean, definitions)
    insert_defs = clean_defs if clean_defs else [{"pos": "释义", "cn": word_clean, "en": ""}]

    with db_session(immediate=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, definitions_json, is_collected, collected_at FROM words WHERE id = ?", (word_id,))
        existing = cursor.fetchone()

        if existing:
            existing_defs_raw = existing["definitions_json"] or ""
            existing_has_rich = False
            if existing_defs_raw:
                try:
                    _, existing_has_rich = _sanitize_definitions(word_clean, json.loads(existing_defs_raw))
                except Exception:
                    existing_has_rich = False

            # 若旧记录已有真实中文释义，严禁用离线英文原词回退或空占位符反向覆盖
            if has_rich_def or (clean_defs and not existing_has_rich):
                defs_json_update = json.dumps(clean_defs, ensure_ascii=False)
            else:
                defs_json_update = ""

            # 保留已收藏词条原有的 collected_at 时间序，仅在新收藏（0 -> 1）时赋当前时间
            if is_collected:
                next_collected_at = existing["collected_at"] if (existing["is_collected"] == 1 and existing["collected_at"]) else now
            else:
                next_collected_at = None

            cursor.execute("""
                UPDATE words SET
                    phonetic_uk = COALESCE(NULLIF(?, ''), phonetic_uk),
                    phonetic_us = COALESCE(NULLIF(?, ''), phonetic_us),
                    definitions_json = COALESCE(NULLIF(?, ''), definitions_json),
                    tags_json = COALESCE(NULLIF(?, ''), tags_json),
                    context_sentence = COALESCE(NULLIF(?, ''), context_sentence),
                    context_translation = COALESCE(NULLIF(?, ''), context_translation),
                    ai_analysis = COALESCE(NULLIF(?, ''), ai_analysis),
                    is_collected = ?,
                    collected_at = ?
                WHERE id = ?
            """, (
                phonetic_uk,
                phonetic_us,
                defs_json_update,
                tags_json_update,
                context_sentence,
                clean_ctx_trans,
                ai_analysis,
                1 if is_collected else 0,
                next_collected_at,
                word_id,
            ))
        else:
            cursor.execute("""
                INSERT INTO words (
                    id, word, phonetic_uk, phonetic_us, definitions_json, tags_json,
                    context_sentence, context_translation, ai_analysis, source_app,
                    is_collected, created_at, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                word_id, word_clean, phonetic_uk, phonetic_us,
                json.dumps(insert_defs, ensure_ascii=False),
                json.dumps(tags_data, ensure_ascii=False),
                context_sentence, clean_ctx_trans, ai_analysis, source_app,
                1 if is_collected else 0, now, now if is_collected else None
            ))

        cursor.execute("SELECT * FROM words WHERE id = ?", (word_id,))
        saved_row = cursor.fetchone()
        return _row_to_dict(saved_row) if saved_row else {}


def update_enrichment_if_collected(
    word: str,
    definitions: List[Dict[str, str]],
    context_sentence: str = "",
    context_translation: str = "",
    ai_analysis: str = "",
    phonetic_uk: str = "",
    phonetic_us: str = "",
    tags: Optional[List[str]] = None,
) -> bool:
    """后台异步补全专用原子更新：仅当词条仍处于已收藏态 (is_collected=1) 时回写，绝不篡改收藏状态或时间序。"""
    word_clean = (word or "").strip()
    if not word_clean:
        return False
    word_id = f"word_{word_clean.lower()}"
    clean_defs, has_rich_def = _sanitize_definitions(word_clean, definitions)
    defs_json_update = json.dumps(clean_defs, ensure_ascii=False) if has_rich_def else ""
    tags_json_update = json.dumps(tags, ensure_ascii=False) if tags else ""
    ctx_trans_safe = (context_translation or "")
    clean_ctx_trans = "" if ctx_trans_safe.strip() in _CTX_PLACEHOLDERS else ctx_trans_safe

    with db_session(immediate=True) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE words SET
                phonetic_uk = COALESCE(NULLIF(?, ''), phonetic_uk),
                phonetic_us = COALESCE(NULLIF(?, ''), phonetic_us),
                definitions_json = COALESCE(NULLIF(?, ''), definitions_json),
                tags_json = COALESCE(NULLIF(?, ''), tags_json),
                context_sentence = COALESCE(NULLIF(?, ''), context_sentence),
                context_translation = COALESCE(NULLIF(?, ''), context_translation),
                ai_analysis = COALESCE(NULLIF(?, ''), ai_analysis)
            WHERE id = ? AND is_collected = 1
        """, (
            phonetic_uk or "",
            phonetic_us or "",
            defs_json_update,
            tags_json_update,
            context_sentence or "",
            clean_ctx_trans,
            ai_analysis or "",
            word_id,
        ))
        return cursor.rowcount > 0

def get_word(word_id: str) -> Optional[Dict[str, Any]]:
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM words WHERE id = ?", (word_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_dict(row)

def toggle_collection(word_id: str) -> bool:
    with db_session(immediate=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_collected FROM words WHERE id = ?", (word_id,))
        row = cursor.fetchone()
        if not row:
            return False
        new_state = 0 if row["is_collected"] == 1 else 1
        now = int(time.time() * 1000) if new_state == 1 else None
        cursor.execute("UPDATE words SET is_collected = ?, collected_at = ? WHERE id = ?", (new_state, now, word_id))
        return bool(new_state)


def set_word_collected(word_id: str, collected: bool) -> bool:
    """幂等设置指定词条收藏态，避免 Undo 撤销误将已重新收藏的词再次取消收藏。"""
    with db_session(immediate=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_collected, collected_at FROM words WHERE id = ?", (word_id,))
        row = cursor.fetchone()
        if not row:
            return False
        target = 1 if collected else 0
        if int(row["is_collected"] or 0) == target:
            return bool(target)
        now = int(time.time() * 1000) if target == 1 else None
        cursor.execute("UPDATE words SET is_collected = ?, collected_at = ? WHERE id = ?", (target, now, word_id))
        return bool(target)

def get_all_collected() -> List[Dict[str, Any]]:
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM words WHERE is_collected = 1 ORDER BY collected_at DESC")
        rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

def delete_word(word_id: str):
    with db_session(immediate=True) as conn:
        conn.execute("DELETE FROM words WHERE id = ?", (word_id,))

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for text_key in (
        "id", "word", "phonetic_uk", "phonetic_us",
        "context_sentence", "context_translation", "ai_analysis", "source_app",
    ):
        if d.get(text_key) is None:
            d[text_key] = ""
    try:
        parsed_defs = json.loads(d.get("definitions_json") or "[]")
        d["definitions"] = parsed_defs if isinstance(parsed_defs, list) else []
    except (ValueError, TypeError):
        d["definitions"] = []
    try:
        parsed_tags = json.loads(d.get("tags_json") or "[]")
        d["tags"] = parsed_tags if isinstance(parsed_tags, list) else []
    except (ValueError, TypeError):
        d["tags"] = []
    d["is_collected"] = bool(d.get("is_collected", 0))
    return d


def get_setting(key: str, default: Any = None) -> Any:
    """从 SQLite app_settings 读取 JSON 配置，读取失败时安全回退默认值。"""
    if not key:
        return default
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row or not row["value_json"]:
                return default
            return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(key: str, value: Any) -> bool:
    """持久化 JSON 配置至 SQLite app_settings，与生词表 words 物理隔离。"""
    if not key:
        return False
    try:
        payload = json.dumps(value, ensure_ascii=False)
        now = int(time.time() * 1000)
        with db_session(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, payload, now),
            )
        return True
    except Exception:
        return False
