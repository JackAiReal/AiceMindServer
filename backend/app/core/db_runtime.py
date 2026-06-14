from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

try:
    import pymysql  # type: ignore
except Exception:  # pragma: no cover
    pymysql = None


def get_db_url() -> str:
    return str(os.getenv('AICEMIND_DB_URL') or '').strip()


def is_mysql_url(db_url: str) -> bool:
    return db_url.startswith('mysql://') or db_url.startswith('mysql+pymysql://')


def require_mysql_db_url() -> str:
    db_url = get_db_url()
    if not db_url:
        raise RuntimeError('AICEMIND_DB_URL is required and must be mysql:// or mysql+pymysql://')
    if not is_mysql_url(db_url):
        raise RuntimeError('AICEMIND_DB_URL must be mysql:// or mysql+pymysql://; sqlite fallback has been removed')
    return db_url


def describe_runtime(default_path: Path | None = None) -> dict[str, str]:
    db_url = require_mysql_db_url()
    return {
        'engine': 'mysql',
        'mode': 'db_url',
        'dbUrl': db_url,
        'sqlitePath': '',
        'warning': '',
    }


class MySQLRow:
    def __init__(self, data: dict):
        self._data = dict(data)
        self._keys = list(self._data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()

    def __contains__(self, item):
        return item in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return repr(self._data)


class StaticResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class MySQLResult:
    def __init__(self, cursor, row_factory=None):
        self._cursor = cursor
        self._row_factory = row_factory

    def _wrap(self, row):
        if row is None:
            return None
        if self._row_factory is sqlite3.Row and isinstance(row, dict):
            return MySQLRow(row)
        return row

    def fetchone(self):
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        return [self._wrap(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class MySQLConnection:
    def __init__(self, db_url: str):
        if pymysql is None:
            raise RuntimeError('PyMySQL 未安装，请先安装 pymysql')

        parsed = _parse_mysql_url(db_url)
        self._conn = pymysql.connect(
            host=parsed['host'],
            port=parsed['port'],
            user=parsed['user'],
            password=parsed['password'],
            database=parsed['database'],
            charset=parsed['charset'],
            autocommit=False,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )
        self.row_factory = None

    def execute(self, sql: str, params=None):
        params = tuple(params or ())
        special = _handle_mysql_special(self._conn, sql, params)
        if special is not None:
            return special

        transformed = _translate_mysql_sql(sql)
        cursor_cls = pymysql.cursors.DictCursor if self.row_factory is sqlite3.Row else pymysql.cursors.Cursor
        cursor = self._conn.cursor(cursor=cursor_cls)
        cursor.execute(transformed, params)
        return MySQLResult(cursor, self.row_factory)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


def _parse_mysql_url(db_url: str) -> dict:
    normalized = db_url.replace('mysql+pymysql://', 'mysql://', 1)
    parsed = urlparse(normalized)
    query = dict(parse_qsl(parsed.query))
    return {
        'host': parsed.hostname or '127.0.0.1',
        'port': int(parsed.port or 3306),
        'user': unquote(parsed.username or ''),
        'password': unquote(parsed.password or ''),
        'database': (parsed.path or '/').lstrip('/'),
        'charset': query.get('charset', 'utf8mb4'),
    }


def _translate_mysql_sql(sql: str) -> str:
    transformed = sql

    # SQLite 常用的 datetime(alias.col) / datetime(col) 在 MySQL 不存在，直接去掉包装
    transformed = re.sub(
        r'datetime\(\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\s*\)',
        r'\1',
        transformed,
        flags=re.IGNORECASE,
    )

    # datetime(COALESCE(...)) -> COALESCE(...)
    transformed = re.sub(
        r'datetime\(\s*(COALESCE\([^)]*\))\s*\)',
        r'\1',
        transformed,
        flags=re.IGNORECASE,
    )

    # SQLite datetime('now') / datetime("now") -> MySQL NOW()
    transformed = re.sub(
        r"datetime\(\s*['\"]now['\"]\s*\)",
        'NOW()',
        transformed,
        flags=re.IGNORECASE,
    )

    transformed = re.sub(r'\bINSERT\s+OR\s+REPLACE\b', 'REPLACE', transformed, flags=re.IGNORECASE)

    conflict_pattern = re.compile(
        r'ON\s+CONFLICT\s*\(([^)]+)\)\s*DO\s+UPDATE\s+SET\s+(.*)',
        re.IGNORECASE | re.DOTALL,
    )
    match = conflict_pattern.search(transformed)
    if match:
        update_clause = match.group(2).strip().rstrip(';')
        update_clause = re.sub(
            r'excluded\.([a-zA-Z0-9_]+)',
            lambda m: f'VALUES({m.group(1)})',
            update_clause,
            flags=re.IGNORECASE,
        )
        transformed = conflict_pattern.sub(f'ON DUPLICATE KEY UPDATE {update_clause}', transformed)

    transformed = transformed.replace('?', '%s')
    return transformed


def _handle_mysql_special(conn, sql: str, params: tuple):
    stripped = sql.strip()
    upper = stripped.upper()

    if upper.startswith('PRAGMA '):
        pragma_match = re.match(r'PRAGMA\s+table_info\(([^)]+)\)', stripped, flags=re.IGNORECASE)
        if pragma_match:
            table_name = pragma_match.group(1).strip().strip('"`\'')
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s
                ORDER BY ORDINAL_POSITION ASC
                ''',
                (table_name,),
            )
            rows = []
            for idx, row in enumerate(cursor.fetchall()):
                rows.append(
                    (
                        idx,
                        row[0],
                        row[1],
                        0 if str(row[2]).upper() == 'YES' else 1,
                        row[3],
                        1 if str(row[4]).upper() == 'PRI' else 0,
                    )
                )
            cursor.close()
            return StaticResult(rows)
        return StaticResult([])

    if upper.startswith('CREATE TABLE IF NOT EXISTS'):
        return StaticResult([])
    if upper.startswith('CREATE INDEX IF NOT EXISTS'):
        return StaticResult([])
    if upper.startswith('ALTER TABLE ') and ' ADD COLUMN ' in upper:
        return StaticResult([])

    return None


def connect_mysql():
    return MySQLConnection(require_mysql_db_url())
