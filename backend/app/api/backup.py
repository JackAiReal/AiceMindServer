from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

_BACKUP_DIR = Path(__file__).resolve().parents[2] / 'backups'


class BackupCleanupBody(BaseModel):
    keepDays: int = 7


class BackupRestoreBody(BaseModel):
    backupId: str
    confirmPhrase: str


def _ensure_backup_table(conn: sqlite3.Connection):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS backup_snapshots (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            engine TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ok',
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_backup_snapshots_created ON backup_snapshots(created_at DESC)')


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute('SHOW TABLES').fetchall()
    names: list[str] = []
    for row in rows or []:
        try:
            names.append(str(row[0]))
        except Exception:
            keys = list(getattr(row, 'keys', lambda: [])())
            if keys:
                names.append(str(row[keys[0]]))
    if names:
        return [x for x in names if x and not x.startswith('sqlite_')]

    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name ASC").fetchall()
    out: list[str] = []
    for r in rows or []:
        n = str(r['name'] if isinstance(r, dict) or hasattr(r, '__getitem__') else r[0])
        if n and not n.startswith('sqlite_'):
            out.append(n)
    return out


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    keys = []
    try:
        keys = list(row.keys())
    except Exception:
        pass

    if keys:
        return {str(k): row[k] for k in keys}

    # fallback: tuple row without keys（极少出现）
    if isinstance(row, (list, tuple)):
        return {str(i): row[i] for i in range(len(row))}
    return {}


@router.post('/system/backup/create')
def create_backup_snapshot(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.security.manage')

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_id = uuid.uuid4().hex
    filename = f'backup-{now}-{backup_id[:8]}.json.gz'
    file_path = _BACKUP_DIR / filename

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _ensure_backup_table(conn)

            tables = _list_tables(conn)
            payload: dict[str, Any] = {
                'meta': {
                    'backupId': backup_id,
                    'createdAt': _now_str(),
                    'engine': str((_DB_RUNTIME or {}).get('engine') or ''),
                    'createdBy': str(user.get('username') or user.get('id') or ''),
                    'tables': tables,
                },
                'tables': {},
            }

            for t in tables:
                if t == 'backup_snapshots':
                    continue
                rows = conn.execute(f'SELECT * FROM {t}').fetchall()
                payload['tables'][t] = [_row_to_dict(r) for r in rows]

            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            gz = gzip.compress(raw)
            file_path.write_bytes(gz)

            sha256 = hashlib.sha256(gz).hexdigest()
            conn.execute(
                '''
                INSERT INTO backup_snapshots (
                    id, filename, engine, size_bytes, sha256, status, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, 'ok', '', ?, ?)
                ''',
                (
                    backup_id,
                    filename,
                    str((_DB_RUNTIME or {}).get('engine') or ''),
                    int(len(gz)),
                    sha256,
                    str(user.get('username') or user.get('id') or ''),
                    _now_str(),
                ),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'backup.snapshot.create',
                'backup_snapshots',
                backup_id,
                {'filename': filename, 'sizeBytes': len(gz), 'tableCount': len(payload.get('tables') or {})},
            )
            conn.commit()

    return _ok({'backupId': backup_id, 'filename': filename, 'sizeBytes': int(file_path.stat().st_size)})


@router.get('/system/backup/list')
def list_backup_snapshots(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=500),
):
    user = _require_user(authorization)
    _require_permission(user, 'system.security.manage')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _ensure_backup_table(conn)
            rows = conn.execute(
                '''
                SELECT id, filename, engine, size_bytes, sha256, status, note, created_by, created_at
                FROM backup_snapshots
                ORDER BY created_at DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': str(r['id'] or ''),
                'filename': str(r['filename'] or ''),
                'engine': str(r['engine'] or ''),
                'sizeBytes': int(r['size_bytes'] or 0),
                'sha256': str(r['sha256'] or ''),
                'status': str(r['status'] or ''),
                'note': str(r['note'] or ''),
                'createdBy': str(r['created_by'] or ''),
                'createdAt': str(r['created_at'] or ''),
            }
            for r in rows
        ]
    )


@router.get('/system/backup/download')
def download_backup_snapshot(
    backupId: str = Query('', alias='backupId'),
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_permission(user, 'system.security.manage')

    bid = str(backupId or '').strip()
    if not bid:
        return _fail('backupId 不能为空')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _ensure_backup_table(conn)
            row = conn.execute('SELECT filename FROM backup_snapshots WHERE id = ? LIMIT 1', (bid,)).fetchone()

    if not row:
        return _fail('备份不存在')

    fp = _BACKUP_DIR / str(row['filename'] or '')
    if not fp.exists():
        return _fail('备份文件不存在')

    data = fp.read_bytes()
    return StreamingResponse(
        BytesIO(data),
        media_type='application/gzip',
        headers={'Content-Disposition': f'attachment; filename="{fp.name}"'},
    )


@router.post('/system/backup/restore-dry-run')
def restore_backup_dry_run(body: BackupRestoreBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.security.manage')

    bid = str(body.backupId or '').strip()
    if not bid:
        return _fail('backupId 不能为空')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _ensure_backup_table(conn)
            row = conn.execute('SELECT filename, sha256 FROM backup_snapshots WHERE id = ? LIMIT 1', (bid,)).fetchone()

    if not row:
        return _fail('备份不存在')

    fp = _BACKUP_DIR / str(row['filename'] or '')
    if not fp.exists():
        return _fail('备份文件不存在')

    raw = fp.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    payload = json.loads(gzip.decompress(raw).decode('utf-8'))
    tables = payload.get('tables') if isinstance(payload, dict) else {}
    if not isinstance(tables, dict):
        return _fail('备份格式无效')

    table_stats = {k: len(v) if isinstance(v, list) else 0 for k, v in tables.items()}

    return _ok(
        {
            'backupId': bid,
            'sha256': sha,
            'sha256Matched': sha == str(row['sha256'] or ''),
            'tableCount': len(table_stats),
            'tableStats': table_stats,
            'readyToRestore': True,
        }
    )


@router.post('/system/backup/cleanup')
def cleanup_backups(body: BackupCleanupBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.security.manage')

    keep_days = max(1, int(body.keepDays or 7))
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d %H:%M:%S')

    removed = 0
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _ensure_backup_table(conn)
            rows = conn.execute('SELECT id, filename FROM backup_snapshots WHERE created_at < ?', (cutoff,)).fetchall()
            for r in rows or []:
                fp = _BACKUP_DIR / str(r['filename'] or '')
                if fp.exists():
                    try:
                        fp.unlink()
                    except Exception:
                        pass
                conn.execute('DELETE FROM backup_snapshots WHERE id = ?', (str(r['id'] or ''),))
                removed += 1
            conn.commit()

    return _ok({'removed': removed, 'cutoff': cutoff})
