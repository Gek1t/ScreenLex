"""
ScreenLex 统一结构化轮转日志模块
- 日志落盘位置：%APPDATA%\\ScreenLex\\screenlex.log（UTF-8，单文件最大 1 MB，保留 3 份历史备份）
- 与生词本数据库共用持久化目录，免疫 PyInstaller 重新打包清空 dist/
"""
from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_LOG_DIR: Optional[str] = None
_LOG_FILE: Optional[str] = None


def get_log_dir() -> str:
    global _LOG_DIR
    if _LOG_DIR is None:
        app_data = os.environ.get("APPDATA")
        if app_data:
            base = os.path.join(app_data, "ScreenLex")
        else:
            base = os.path.join(os.path.expanduser("~"), ".screenlex")
        os.makedirs(base, exist_ok=True)
        _LOG_DIR = base
    return _LOG_DIR


def get_log_file() -> str:
    global _LOG_FILE
    if _LOG_FILE is None:
        _LOG_FILE = os.path.join(get_log_dir(), "screenlex.log")
    return _LOG_FILE


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化 ScreenLex 根日志器（线程安全且幂等，多次调用不会重复挂 Handler）。"""
    global _INITIALIZED
    root = logging.getLogger("screenlex")
    root.setLevel(level)
    with _INIT_LOCK:
        if _INITIALIZED:
            return root
        _INITIALIZED = True
        try:
            log_path = get_log_file()
            handler = RotatingFileHandler(
                log_path,
                maxBytes=1024 * 1024,
                backupCount=3,
                encoding="utf-8",
                delay=True,
            )
            fmt = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(fmt)
            root.addHandler(handler)
        except Exception:
            # 极端权限受限环境下静默降级为 NullHandler，绝不阻塞主程序启动
            root.addHandler(logging.NullHandler())
    return root


def get_logger(name: str = "app") -> logging.Logger:
    if not _INITIALIZED:
        setup_logging()
    full_name = name if name.startswith("screenlex") else f"screenlex.{name}"
    return logging.getLogger(full_name)


def open_log_dir() -> bool:
    """在 Windows 资源管理器中打开日志目录。"""
    try:
        d = get_log_dir()
        os.makedirs(d, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(d)  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return False
