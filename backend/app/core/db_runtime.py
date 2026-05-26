from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urlparse

try:
    import pymysql  # type: ignore
except Exception:  # pragma: no cover
    pymysql = None


def get_db_url() -> str:
    return str(os.getenv('AICEMIND_DB_URL') or '').strip()


def is_sqlite_url(db_url: str) -> bool:
    return db_url.startswith('sqlite:///')


def is_mysql_url(db_url: str) -> bool:
    return db_url.startswith('mysql://')


def get_db_engine(db_url: str | None = None) -> str:
    url = (db_url or get_db_url()).strip()
    if not url or is_sqlite_url(url):
        return 'sqlite'
    if is_mysql_url(url):
        return 'mysql'
    return 'external'


def resolve_sqlite_path(default_path: Path) -> Path:
    db_url = get_db_url()
    if is_sqlite_url(db_url):
        raw = db_url.replace('sqlite:///', '', 1)
        return Path(raw).expanduser().resolve()
    return default_path.expanduser().resolve()


def _parse_mysql_dsn(db_url: str) -> dict[str, Any]:
    parsed = urlparse(db_url)
    if parsed.scheme != 'mysql':
        raise ValueError('Only mysql:// DSN is supported for MySQL runtime')
    db_name = (parsed.path or '/').lstrip('/')
    if not db_name:
        raise ValueError('MySQL database name is required in AICEMIND_DB_URL')

    query = dict(
        kv.split('=', 1) if '=' in kv else (kv, '')
        for kv in (parsed.query or '').split('&')
        if kv
    )

    return {
        'host': parsed.hostname or '127.0.0.1',
        'port': int(parsed.port or 3306),
        'user': unquote(parsed.username or ''),
        'password': unquote(parsed.password or ''),
        'database': unquote(db_name),
        'charset': query.get('charset') or 'utf8mb4',
        'connect_timeout': int(query.get('connect_timeout') or 8),
        'read_timeout': int(query.get('read_timeout') or 20),
        'write_timeout': int(query.get('write_timeout') or 20),
        'autocommit': False,
    }


def describe_runtime(default_path: Path) -> dict[str, str]:
    db_url = get_db_url()
    sqlite_path = resolve_sqlite_path(default_path)
    engine = get_db_engine(db_url)

    if engine == 'sqlite':
        return {
            'engine': 'sqlite',
            'mode': 'db_url' if db_url else 'default',
            'dbUrl': db_url,
            'sqlitePath': str(sqlite_path),
            'warning': '',
        }

    if engine == 'mysql':
        return {
            'engine': 'mysql',
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
        'warning': 'AICEMIND_DB_URL 运行时仅支持 sqlite:/// 或 mysql://。',
    }


class CompatRow:
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = list(columns)
        self._values = tuple(values)
        self._map = {self._columns[i]: self._values[i] for i in range(min(len(self._columns), len(self._values)))}

    def __getitem__(self, key: int | str):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def get(self, key: str, default: Any = None):
        return self._map.get(key, default)

    def keys(self):
        return self._map.keys()


class CompatCursor:
    def __init__(self, rows: list[CompatRow], rowcount: int = 0):
        self._rows = rows
        self._idx = 0
        self.rowcount = rowcount

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        if self._idx == 0:
            self._idx = len(self._rows)
            return self._rows
        out = self._rows[self._idx :]
        self._idx = len(self._rows)
        return out


def _replace_qmark_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == '?' and not in_single and not in_double:
            out.append('%s')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _convert_on_conflict(sql: str) -> str:
    if 'ON CONFLICT' not in sql.upper():
        return sql
    sql = re.sub(r'ON\s+CONFLICT\s*\([^\)]*\)\s+DO\s+UPDATE\s+SET', 'ON DUPLICATE KEY UPDATE', sql, flags=re.I)
    sql = re.sub(r'\bexcluded\.([a-zA-Z0-9_]+)', r'VALUES(\1)', sql, flags=re.I)
    return sql


def _convert_create_table_sql_for_mysql(sql: str) -> str:
    if 'CREATE TABLE' not in sql.upper():
        return sql

    # 去掉 SQLite CHECK 约束（MySQL 兼容性更稳）
    sql = re.sub(r'\s+CHECK\s*\([^\)]*\)', '', sql, flags=re.I)
    sql = sql.replace('REAL', 'DOUBLE')

    indexed_key_words = {
        'id', 'user_id', 'account_id', 'order_id', 'order_no', 'trade_id', 'out_trade_no',
        'event_key', 'token_digest', 'code', 'username', 'email', 'plan_code', 'status',
        'doc_type', 'login_key', 'provider', 'run_id', 'category', 'action', 'path', 'method',
    }
    long_text_words = {
        'content', 'detail', 'payload', 'template', 'private_key', 'public_key',
        'note', 'description', 'request', 'response', 'message',
    }

    lines = sql.splitlines()
    out_lines: list[str] = []
    for line in lines:
        raw = line
        m = re.match(r'^(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s+TEXT\b(.*)$', line)
        if m:
            indent, col, tail = m.groups()
            lc = col.lower()
            if ('PRIMARY KEY' in tail.upper()) or ('UNIQUE' in tail.upper()) or lc in indexed_key_words or lc.endswith('_id'):
                line = f"{indent}{col} VARCHAR(191){tail}"
            elif any(k in lc for k in long_text_words):
                line = f"{indent}{col} LONGTEXT{tail}"
            else:
                line = f"{indent}{col} VARCHAR(191){tail}"
        out_lines.append(line)

    return '\n'.join(out_lines)


def _convert_create_index_sql_for_mysql(sql: str) -> str:
    # MySQL 低版本对 IF NOT EXISTS 兼容性差，运行时通过异常吞掉重复索引
    return re.sub(r'CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS', 'CREATE INDEX', sql, flags=re.I)


def _is_ignorable_mysql_error(sql: str, err: Exception) -> bool:
    code = getattr(err, 'args', [None])[0]
    up_sql = sql.upper()
    # 1061: Duplicate key name; 1060: Duplicate column name; 1050: Table exists
    if code in {1050, 1060, 1061, 1091}:
        return True
    # 1071: 索引键过长（历史表结构可能为 VARCHAR(255)）；忽略该索引以继续初始化
    if code == 1071 and 'CREATE INDEX' in up_sql:
        return True
    if 'CREATE INDEX' in up_sql and 'duplicate' in str(err).lower():
        return True
    return False


class CompatConnection:
    def __init__(self, raw_conn: Any, engine: str):
        self._raw = raw_conn
        self._engine = engine
        self._row_factory = None
        self.total_changes = 0

    @property
    def row_factory(self):
        if self._engine == 'sqlite':
            return getattr(self._raw, 'row_factory', None)
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        if self._engine == 'sqlite':
            try:
                self._raw.row_factory = value
            except Exception:
                pass
        self._row_factory = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.commit()
            except Exception:
                pass
        else:
            try:
                self.rollback()
            except Exception:
                pass
        self.close()

    def _transform_sql(self, sql: str) -> str:
        s = (sql or '').strip()
        if self._engine != 'mysql':
            return sql

        if s.upper().startswith('PRAGMA '):
            return '__NOOP__'

        s = _convert_on_conflict(s)
        # SQLite: ORDER BY datetime(col)；MySQL 下直接按字符串时间列排序即可
        s = re.sub(r'\bdatetime\s*\(\s*([a-zA-Z0-9_\.]+)\s*\)', r'\1', s, flags=re.I)
        if s.upper().startswith('CREATE TABLE'):
            s = _convert_create_table_sql_for_mysql(s)
        elif s.upper().startswith('CREATE INDEX'):
            s = _convert_create_index_sql_for_mysql(s)

        s = _replace_qmark_placeholders(s)
        return s

    def execute(self, sql: str, params: Sequence[Any] = ()):
        if self._engine == 'sqlite':
            cur = self._raw.execute(sql, tuple(params or ()))
            self.total_changes = int(getattr(self._raw, 'total_changes', self.total_changes) or 0)
            return cur

        raw_sql = (sql or '').strip()
        if raw_sql.upper().startswith('PRAGMA TABLE_INFO('):
            table = raw_sql[18:].rstrip(')').strip().strip("'\"")
            cur = self._raw.cursor()
            cur.execute(f'SHOW COLUMNS FROM `{table}`')
            rows = cur.fetchall()
            mapped = [CompatRow(['cid', 'name'], [i, r[0]]) for i, r in enumerate(rows)]
            return CompatCursor(mapped, rowcount=len(mapped))

        transformed = self._transform_sql(sql)
        if transformed == '__NOOP__':
            return CompatCursor([], rowcount=0)

        cur = self._raw.cursor()
        try:
            cur.execute(transformed, tuple(params or ()))
        except Exception as e:
            if _is_ignorable_mysql_error(transformed, e):
                return CompatCursor([], rowcount=0)
            raise

        desc = cur.description or []
        if not desc:
            try:
                self.total_changes += max(0, int(cur.rowcount or 0))
            except Exception:
                pass
            return CompatCursor([], rowcount=int(cur.rowcount or 0))

        col_names = [str(d[0]) for d in desc]
        raw_rows = list(cur.fetchall() or [])
        rows = [CompatRow(col_names, r) for r in raw_rows]
        return CompatCursor(rows, rowcount=int(cur.rowcount or len(rows) or 0))

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]):
        if self._engine == 'sqlite':
            cur = self._raw.executemany(sql, list(seq_of_params))
            self.total_changes = int(getattr(self._raw, 'total_changes', self.total_changes) or 0)
            return cur

        transformed = self._transform_sql(sql)
        cur = self._raw.cursor()
        try:
            cur.executemany(transformed, list(seq_of_params))
        except Exception as e:
            if _is_ignorable_mysql_error(transformed, e):
                return CompatCursor([], rowcount=0)
            raise
        self.total_changes += max(0, int(cur.rowcount or 0))
        return CompatCursor([], rowcount=int(cur.rowcount or 0))

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        return self._raw.close()


def connect_sqlite(path: Path):
    db_url = get_db_url().strip()
    engine = get_db_engine(db_url)

    if engine == 'mysql':
        if pymysql is None:
            raise RuntimeError('MySQL runtime requires dependency: PyMySQL')
        cfg = _parse_mysql_dsn(db_url)
        raw = pymysql.connect(**cfg)
        return CompatConnection(raw, engine='mysql')

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
    return CompatConnection(conn, engine='sqlite')
