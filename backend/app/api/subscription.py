from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.get('/system/subscription/list')
def list_subscriptions(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT s.id, s.account_id, s.plan_code, s.status, s.start_time, s.expire_time, s.updated_at,
                       u.username, u.real_name, u.email,
                       p.name AS plan_name, p.level AS plan_level
                FROM subscriptions s
                LEFT JOIN user_accounts u ON u.id = s.account_id
                LEFT JOIN plans p ON p.code = s.plan_code
                ORDER BY datetime(s.updated_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'accountId': r['account_id'],
                'username': r['username'] or '',
                'realName': r['real_name'] or '',
                'email': r['email'] or '',
                'planCode': r['plan_code'],
                'planName': r['plan_name'] or r['plan_code'],
                'planLevel': r['plan_level'] or 'basic',
                'status': r['status'],
                'startTime': r['start_time'],
                'expireTime': r['expire_time'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/subscription/upsert')
def upsert_subscription(body: SubscriptionUpsertBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    plan_code = str(body.planCode or '').strip()
    if not account_id or not plan_code:
        return _fail('账号和套餐必填')

    status = str(body.status or 'active').strip() or 'active'
    if status not in ('active', 'disabled', 'expired'):
        return _fail('无效状态')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            account_row = _resolve_account(conn, account_id)
            if not account_row:
                return _fail('账号不存在')

            plan_row = _resolve_plan(conn, plan_code)
            if not plan_row:
                return _fail('套餐不存在')

            start_dt = _parse_dt(body.startTime) if str(body.startTime or '').strip() else datetime.now()
            if not start_dt:
                return _fail('开始时间格式错误')

            expire_dt = _parse_dt(body.expireTime) if str(body.expireTime or '').strip() else None
            if expire_dt is None:
                expire_dt = start_dt + timedelta(days=int(plan_row['duration_days'] or 30))

            start_time = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            expire_time = expire_dt.strftime('%Y-%m-%d %H:%M:%S')
            now = _now_str()

            conn.execute(
                '''
                INSERT INTO subscriptions (
                    id, account_id, plan_code, status, start_time, expire_time, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, plan_code) DO UPDATE SET
                    status = excluded.status,
                    start_time = excluded.start_time,
                    expire_time = excluded.expire_time,
                    updated_at = excluded.updated_at
                ''',
                (uuid.uuid4().hex, account_id, plan_code, status, start_time, expire_time, now, now),
            )

            _sync_member_for_account(
                conn,
                account_row=account_row,
                level=str(plan_row['level'] or 'basic'),
                status=status,
                start_time=start_time,
                expire_time=expire_time,
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'subscription.upsert',
                'subscription',
                account_id,
                {'planCode': plan_code, 'status': status, 'expireTime': expire_time},
            )
            conn.commit()

    return _ok(True)
