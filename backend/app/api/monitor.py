from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.get('/system/observability/settings')
def get_observability_settings(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)
    return _ok(_get_observability_settings(mask_secret=False))

@router.get('/system/runtime/db')
def get_runtime_db_status(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)
    payload = dict(_DB_RUNTIME)
    payload['sqlitePath'] = str(_DB_PATH)
    payload['exists'] = _DB_PATH.exists()
    payload['sizeBytes'] = int(_DB_PATH.stat().st_size) if _DB_PATH.exists() else 0
    return _ok(payload)

@router.post('/system/observability/settings/save')
def save_observability_settings(body: ObservabilitySettingsBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    sentry_dsn = str(body.sentryDsn or '').strip()
    alert_webhook = str(body.alertWebhook or '').strip()
    alert_emails = str(body.alertEmails or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                UPDATE observability_settings
                SET sentry_dsn = ?, alert_webhook = ?, alert_emails = ?, updated_at = ?
                WHERE id = 1
                ''',
                (sentry_dsn, alert_webhook, alert_emails, _now_str()),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'observability.settings.save',
                'observability_settings',
                '1',
                {'hasSentry': bool(sentry_dsn), 'hasWebhook': bool(alert_webhook), 'alertEmails': alert_emails},
            )
            conn.commit()

    return _ok(True, message='观测设置已保存')

@router.get('/system/monitor/requests')
def monitor_request_metrics(
    authorization: Optional[str] = Header(default=None),
    minutes: int = Query(60, ge=1, le=1440),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            if _DB_RUNTIME.get('engine') == 'mysql':
                rows = conn.execute(
                    '''
                    SELECT method, path, status_code, success, latency_ms, created_at
                    FROM request_metrics
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL ? MINUTE)
                    ORDER BY created_at DESC
                    LIMIT ?
                    ''',
                    (int(minutes), int(limit)),
                ).fetchall()

                summary_row = conn.execute(
                    '''
                    SELECT
                        COUNT(1) AS total,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                        AVG(latency_ms) AS avg_latency,
                        MAX(latency_ms) AS max_latency,
                        SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS server_error_count
                    FROM request_metrics
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL ? MINUTE)
                    ''',
                    (int(minutes),),
                ).fetchone()
            else:
                rows = conn.execute(
                    '''
                    SELECT method, path, status_code, success, latency_ms, created_at
                    FROM request_metrics
                    WHERE datetime(created_at) >= datetime('now', ?)
                    ORDER BY datetime(created_at) DESC
                    LIMIT ?
                    ''',
                    (f'-{int(minutes)} minutes', int(limit)),
                ).fetchall()

                summary_row = conn.execute(
                    '''
                    SELECT
                        COUNT(1) AS total,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                        AVG(latency_ms) AS avg_latency,
                        MAX(latency_ms) AS max_latency,
                        SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS server_error_count
                    FROM request_metrics
                    WHERE datetime(created_at) >= datetime('now', ?)
                    ''',
                    (f'-{int(minutes)} minutes',),
                ).fetchone()

    total = int((summary_row['total'] if summary_row else 0) or 0)
    success_count = int((summary_row['success_count'] if summary_row else 0) or 0)
    success_rate = (success_count / total) if total > 0 else 1.0

    return _ok(
        {
            'windowMinutes': int(minutes),
            'summary': {
                'total': total,
                'successCount': success_count,
                'successRate': round(success_rate, 6),
                'avgLatencyMs': round(float((summary_row['avg_latency'] if summary_row else 0) or 0), 2),
                'maxLatencyMs': round(float((summary_row['max_latency'] if summary_row else 0) or 0), 2),
                'serverErrorCount': int((summary_row['server_error_count'] if summary_row else 0) or 0),
            },
            'items': [
                {
                    'method': r['method'],
                    'path': r['path'],
                    'statusCode': int(r['status_code'] or 0),
                    'success': bool(int(r['success'] or 0)),
                    'latencyMs': round(float(r['latency_ms'] or 0), 2),
                    'createdAt': r['created_at'],
                }
                for r in rows
            ],
        }
    )

@router.get('/system/monitor/errors')
def monitor_error_events(
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
                SELECT id, source, level, message, detail, path, created_at
                FROM error_events
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'source': r['source'],
                'level': r['level'],
                'message': r['message'],
                'detail': r['detail'] or '',
                'path': r['path'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/monitor/error/test')
def test_observability_alert(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            _send_observability_alert(
                conn,
                title='[观测告警测试] AiceMind Backend',
                content='这是一条观测告警测试消息。',
                payload={'operator': str(user.get('username') or user.get('id') or '')},
            )
            conn.commit()

    return _ok(True, message='观测告警测试已触发')

@router.get('/system/monitor/user-actions')
def monitor_user_actions(
    authorization: Optional[str] = Header(default=None),
    action: str = Query('', description='动作筛选'),
    accountId: str = Query('', description='账号ID筛选'),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []

    action_key = str(action or '').strip()
    if action_key:
        where.append('a.action = ?')
        params.append(action_key)

    account_id = str(accountId or '').strip()
    if account_id:
        where.append('(a.actor_account_id = ? OR a.target_id = ?)')
        params.extend([account_id, account_id])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT
                    a.id, a.actor_account_id, a.action, a.target_type, a.target_id,
                    a.detail, a.created_at,
                    u.username, u.real_name
                FROM audit_logs a
                LEFT JOIN user_accounts u ON u.id = a.actor_account_id
                {where_sql}
                ORDER BY datetime(a.created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'actorAccountId': r['actor_account_id'],
                'actorUsername': r['username'] or '',
                'actorRealName': r['real_name'] or '',
                'action': r['action'],
                'targetType': r['target_type'],
                'targetId': r['target_id'],
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/monitor/backtest-records')
def monitor_backtest_records(
    authorization: Optional[str] = Header(default=None),
    period: str = Query('', description='周期：YYYY-MM 或 YYYY-MM-DD'),
    accountId: str = Query('', description='账号ID筛选'),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = ["l.feature_code = 'backtest.run'"]
    params: list[Any] = []

    period_key = str(period or '').strip()
    if period_key:
        if len(period_key) == 7:
            where.append('l.period_key LIKE ?')
            params.append(f'{period_key}%')
        else:
            where.append('l.period_key = ?')
            params.append(period_key)

    account_id = str(accountId or '').strip()
    if account_id:
        where.append('l.account_id = ?')
        params.append(account_id)

    where_sql = f"WHERE {' AND '.join(where)}"

    with _DB_LOCK:
        _ensure_db()
        get_entitlement_policy('basic')  # 触发账单相关表的懒初始化
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT
                    l.id, l.account_id, l.amount, l.period_key, l.source, l.ref_id, l.detail, l.created_at,
                    u.username, u.real_name, u.email
                FROM billing_usage_ledger l
                LEFT JOIN user_accounts u ON u.id = l.account_id
                {where_sql}
                ORDER BY datetime(l.created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'accountId': r['account_id'],
                'username': r['username'] or '',
                'realName': r['real_name'] or '',
                'email': r['email'] or '',
                'runs': int(r['amount'] or 0),
                'periodKey': r['period_key'],
                'source': r['source'] or '',
                'refId': r['ref_id'] or '',
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/monitor/points-records')
def monitor_points_records(
    authorization: Optional[str] = Header(default=None),
    accountId: str = Query('', description='账号ID筛选'),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []

    account_id = str(accountId or '').strip()
    if account_id:
        where.append('p.account_id = ?')
        params.append(account_id)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT
                    p.id, p.account_id, p.username,
                    p.delta, p.points_before, p.points_after,
                    p.reason, p.source, p.ref_id,
                    p.actor_account_id, p.created_at,
                    u.username AS actor_username
                FROM points_ledger p
                LEFT JOIN user_accounts u ON u.id = p.actor_account_id
                {where_sql}
                ORDER BY datetime(p.created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'accountId': r['account_id'],
                'username': r['username'] or '',
                'delta': int(r['delta'] or 0),
                'pointsBefore': int(r['points_before'] or 0),
                'pointsAfter': int(r['points_after'] or 0),
                'reason': r['reason'] or '',
                'source': r['source'] or '',
                'refId': r['ref_id'] or '',
                'actorAccountId': r['actor_account_id'] or '',
                'actorUsername': r['actor_username'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )
