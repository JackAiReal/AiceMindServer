from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def get_db_url() -> str:
    return str(os.getenv('AICEMIND_DB_URL') or '').strip()


def is_sqlite_url(db_url: str) -> bool:
    return db_url.startswith('sqlite:///')


def resolve_sqlite_path(default_path: Path) -> Path:
    db_url = get_db_url()
    if is_sqlite_url(db_url):
        raw = db_url.replace('sqlite:///', '', 1)
        return Path(raw).expanduser().resolve()
    return default_path.expanduser().resolve()


def describe_runtime(default_path: Path) -> dict[str, str]:
    db_url = get_db_url()
    sqlite_path = resolve_sqlite_path(default_path)
    if not db_url:
        return {
            'engine': 'sqlite',
            'mode': 'default',
            'dbUrl': '',
            'sqlitePath': str(sqlite_path),
            'warning': '',
        }

    if is_sqlite_url(db_url):
        return {
            'engine': 'sqlite',
            'mode': 'db_url',
            'dbUrl': db_url,
            'sqlitePath': str(sqlite_path),
            'warning': '',
        }

    return {
        'engine': 'external',
        'mode': 'unsupported_runtime_fallback',
        'dbUrl': db_url,
        'sqlitePath': str(sqlite_path),
        'warning': 'AICEMIND_DB_URL 运行时目前仅支持 sqlite:///；MySQL/PostgreSQL 通过 db_ops.py 迁移/备份脚本支持。',
    }


def connect_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    for pragma in (
        'PRAGMA journal_mode=WAL',
        'PRAGMA synchronous=NORMAL',
        'PRAGMA foreign_keys=ON',
        'PRAGMA busy_timeout=15000',
    ):
        try:
            conn.execute(pragma)
        except Exception:
            pass
    return conn
