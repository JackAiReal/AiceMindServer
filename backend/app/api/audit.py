from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.get('/system/audit/logs')
def list_audit_logs(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: str = Query('', description='按动作过滤'),
    actor: str = Query('', description='按操作者ID过滤'),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    if action.strip():
        where.append('action = ?')
        params.append(action.strip())
    if actor.strip():
        where.append('actor_account_id = ?')
        params.append(actor.strip())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT id, actor_account_id, action, target_type, target_id, detail, created_at
                FROM audit_logs
                {where_sql}
                ORDER BY datetime(created_at) DESC
                LIMIT ? OFFSET ?
                ''',
                (*params, int(limit), int(offset)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'actorAccountId': r['actor_account_id'],
                'action': r['action'],
                'targetType': r['target_type'],
                'targetId': r['target_id'],
                'detail': r['detail'],
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )
