from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from app.core.db_runtime import connect_sqlite, resolve_sqlite_path


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _db_path() -> Path:
    return resolve_sqlite_path(Path(__file__).resolve().parents[2] / 'data' / 'admin_console.db')


MigrationUp = Callable[[object], None]
MigrationDown = Callable[[object], None]


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up: MigrationUp
    down: Optional[MigrationDown] = None


MIGRATION_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    applied_at VARCHAR(32) NOT NULL DEFAULT '',
    rolled_back_at VARCHAR(32) NOT NULL DEFAULT ''
)
'''


def _checksum(m: Migration) -> str:
    payload = f"{m.version}:{m.name}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _migration_0001_baseline_legacy_bootstrap(_conn):
    from app.api import deps as legacy_deps

    legacy_deps._DB_READY = False
    legacy_deps._ensure_db()


def _migration_0002_rbac_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS rbac_permissions (
            code VARCHAR(128) PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            resource VARCHAR(64) NOT NULL DEFAULT '',
            action VARCHAR(64) NOT NULL DEFAULT '',
            description LONGTEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            updated_at VARCHAR(32) NOT NULL,
            created_at VARCHAR(32) NOT NULL
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS rbac_roles (
            id VARCHAR(64) PRIMARY KEY,
            code VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(128) NOT NULL,
            description LONGTEXT NOT NULL,
            is_system INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            updated_at VARCHAR(32) NOT NULL,
            created_at VARCHAR(32) NOT NULL
        )
        '''
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS rbac_role_permissions (
            id VARCHAR(64) PRIMARY KEY,
            role_code VARCHAR(64) NOT NULL,
            permission_code VARCHAR(128) NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            UNIQUE(role_code, permission_code)
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_rbac_role_permissions_role ON rbac_role_permissions(role_code, permission_code)'
    )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS rbac_account_roles (
            id VARCHAR(64) PRIMARY KEY,
            account_id VARCHAR(64) NOT NULL,
            role_code VARCHAR(64) NOT NULL,
            created_at VARCHAR(32) NOT NULL,
            UNIQUE(account_id, role_code)
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_rbac_account_roles_account ON rbac_account_roles(account_id, role_code)'
    )


def _migration_0002_rbac_schema_down(conn):
    conn.execute('DROP TABLE IF EXISTS rbac_account_roles')
    conn.execute('DROP TABLE IF EXISTS rbac_role_permissions')
    conn.execute('DROP TABLE IF EXISTS rbac_roles')
    conn.execute('DROP TABLE IF EXISTS rbac_permissions')


def _migration_0003_backup_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS backup_snapshots (
            id VARCHAR(64) PRIMARY KEY,
            filename VARCHAR(255) NOT NULL,
            engine VARCHAR(32) NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 VARCHAR(128) NOT NULL DEFAULT '',
            status VARCHAR(16) NOT NULL DEFAULT 'ok',
            note LONGTEXT NOT NULL,
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at VARCHAR(32) NOT NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_backup_snapshots_created ON backup_snapshots(created_at DESC)')


def _migration_0003_backup_schema_down(conn):
    conn.execute('DROP TABLE IF EXISTS backup_snapshots')


MIGRATIONS: list[Migration] = [
    Migration('0001', 'baseline_legacy_bootstrap', _migration_0001_baseline_legacy_bootstrap, None),
    Migration('0002', 'rbac_schema', _migration_0002_rbac_schema, _migration_0002_rbac_schema_down),
    Migration('0003', 'backup_schema', _migration_0003_backup_schema, _migration_0003_backup_schema_down),
]


def _ensure_migration_table(conn):
    conn.execute(MIGRATION_TABLE_SQL)
    conn.commit()


def _row_get(row, key: str, idx: int, default=''):
    try:
        return row[key]
    except Exception:
        try:
            return row[idx]
        except Exception:
            return default


def _applied_map(conn) -> dict[str, dict]:
    try:
        rows = conn.execute(
            '''
            SELECT version, name, checksum, status, applied_at, rolled_back_at
            FROM schema_migrations
            '''
        ).fetchall()
    except Exception:
        return {}

    result: dict[str, dict] = {}
    for row in rows or []:
        version = str(_row_get(row, 'version', 0, '') or '')
        if not version:
            continue
        result[version] = {
            'name': str(_row_get(row, 'name', 1, '') or ''),
            'checksum': str(_row_get(row, 'checksum', 2, '') or ''),
            'status': str(_row_get(row, 'status', 3, '') or ''),
            'appliedAt': str(_row_get(row, 'applied_at', 4, '') or ''),
            'rolledBackAt': str(_row_get(row, 'rolled_back_at', 5, '') or ''),
        }
    return result


def migrate_up() -> list[str]:
    applied_versions: list[str] = []
    with connect_sqlite(_db_path()) as conn:
        _ensure_migration_table(conn)
        state = _applied_map(conn)

        for m in sorted(MIGRATIONS, key=lambda x: x.version):
            existing = state.get(m.version)
            if existing and existing.get('status') == 'applied':
                continue

            m.up(conn)
            conn.execute(
                '''
                INSERT INTO schema_migrations(version, name, checksum, status, applied_at, rolled_back_at)
                VALUES (?, ?, ?, 'applied', ?, '')
                ON CONFLICT(version) DO UPDATE SET
                    name = excluded.name,
                    checksum = excluded.checksum,
                    status = 'applied',
                    applied_at = excluded.applied_at,
                    rolled_back_at = ''
                ''',
                (m.version, m.name, _checksum(m), _now_str()),
            )
            conn.commit()
            applied_versions.append(m.version)

    return applied_versions


def migrate_down(steps: int = 1) -> list[str]:
    rolled_back: list[str] = []
    steps = max(1, int(steps or 1))

    with connect_sqlite(_db_path()) as conn:
        _ensure_migration_table(conn)
        state = _applied_map(conn)

        applied = [m for m in sorted(MIGRATIONS, key=lambda x: x.version) if state.get(m.version, {}).get('status') == 'applied']

        for m in reversed(applied[:steps]):
            if m.down is None:
                raise RuntimeError(f'Migration {m.version} ({m.name}) does not support rollback')

            m.down(conn)
            conn.execute(
                '''
                UPDATE schema_migrations
                SET status = 'rolled_back', rolled_back_at = ?
                WHERE version = ?
                ''',
                (_now_str(), m.version),
            )
            conn.commit()
            rolled_back.append(m.version)

    return rolled_back


def migration_status() -> list[dict]:
    with connect_sqlite(_db_path()) as conn:
        _ensure_migration_table(conn)
        state = _applied_map(conn)

    result: list[dict] = []
    for m in sorted(MIGRATIONS, key=lambda x: x.version):
        row = state.get(m.version, {})
        result.append(
            {
                'version': m.version,
                'name': m.name,
                'status': row.get('status') or 'pending',
                'appliedAt': row.get('appliedAt') or '',
                'rolledBackAt': row.get('rolledBackAt') or '',
                'rollbackable': bool(m.down is not None),
            }
        )
    return result
