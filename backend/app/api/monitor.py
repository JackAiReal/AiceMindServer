from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter

from app.api.deps import *  # noqa: F401,F403

router = APIRouter()


def _cutoff_time(minutes: int) -> str:
    return (datetime.now() - timedelta(minutes=max(1, int(minutes or 1)))).strftime('%Y-%m-%d %H:%M:%S')


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    arr = sorted(float(v or 0) for v in values)
    if len(arr) == 1:
        return arr[0]
    rank = (len(arr) - 1) * max(0.0, min(1.0, p))
    lo = int(rank)
    hi = min(lo + 1, len(arr) - 1)
    frac = rank - lo
    return arr[lo] * (1 - frac) + arr[hi] * frac


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

    cutoff = _cutoff_time(minutes)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT method, path, status_code, success, latency_ms, created_at
                FROM request_metrics
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                ''',
                (cutoff, int(limit)),
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
                WHERE created_at >= ?
                ''',
                (cutoff,),
            ).fetchone()

    total = int((summary_row['total'] if summary_row else 0) or 0)
    success_count = int((summary_row['success_count'] if summary_row else 0) or 0)
    success_rate = (success_count / total) if total > 0 else 1.0
    p95 = _percentile([float(r['latency_ms'] or 0) for r in rows], 0.95)

    return _ok(
        {
            'windowMinutes': int(minutes),
            'summary': {
                'total': total,
                'successCount': success_count,
                'successRate': round(success_rate, 6),
                'avgLatencyMs': round(float((summary_row['avg_latency'] if summary_row else 0) or 0), 2),
                'maxLatencyMs': round(float((summary_row['max_latency'] if summary_row else 0) or 0), 2),
                'p95LatencyMs': round(float(p95), 2),
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
                ORDER BY created_at DESC
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


@router.get('/system/monitor/dashboard-summary')
def dashboard_summary(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    cutoff_24h = _cutoff_time(24 * 60)
    cutoff_7d = _cutoff_time(7 * 24 * 60)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row

            total_users = int((conn.execute('SELECT COUNT(1) AS c FROM user_accounts').fetchone()['c']) or 0)
            active_members = int((conn.execute("SELECT COUNT(1) AS c FROM member_users WHERE member_status = 'active'").fetchone()['c']) or 0)

            order_row = conn.execute(
                '''
                SELECT
                    COUNT(1) AS total_orders,
                    SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid_orders,
                    SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS paid_amount
                FROM orders
                '''
            ).fetchone()

            login_24h = int(
                (
                    conn.execute(
                        "SELECT COUNT(1) AS c FROM audit_logs WHERE action = 'auth.login_success' AND created_at >= ?",
                        (cutoff_24h,),
                    ).fetchone()['c']
                )
                or 0
            )

            err_24h = int(
                (
                    conn.execute('SELECT COUNT(1) AS c FROM error_events WHERE created_at >= ?', (cutoff_24h,)).fetchone()['c']
                )
                or 0
            )

            req_row = conn.execute(
                '''
                SELECT
                    COUNT(1) AS total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count
                FROM request_metrics
                WHERE created_at >= ?
                ''',
                (cutoff_24h,),
            ).fetchone()

            req_rows = conn.execute(
                'SELECT latency_ms FROM request_metrics WHERE created_at >= ? ORDER BY created_at DESC LIMIT 2000',
                (cutoff_24h,),
            ).fetchall()
            p95 = _percentile([float(r['latency_ms'] or 0) for r in req_rows], 0.95)

            backtest_total = conn.execute(
                "SELECT SUM(amount) AS v FROM billing_usage_ledger WHERE feature_code = 'backtest.run'"
            ).fetchone()
            backtest_runs = int((backtest_total['v'] if backtest_total else 0) or 0)

            # 近7天收入 / 登录趋势
            day_rows = conn.execute(
                '''
                SELECT SUBSTR(created_at, 1, 10) AS day_key,
                       SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS revenue,
                       SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid_count
                FROM orders
                WHERE created_at >= ?
                GROUP BY SUBSTR(created_at, 1, 10)
                ORDER BY day_key ASC
                ''',
                (cutoff_7d,),
            ).fetchall()
            login_rows = conn.execute(
                '''
                SELECT SUBSTR(created_at, 1, 10) AS day_key, COUNT(1) AS cnt
                FROM audit_logs
                WHERE action = 'auth.login_success' AND created_at >= ?
                GROUP BY SUBSTR(created_at, 1, 10)
                ORDER BY day_key ASC
                ''',
                (cutoff_7d,),
            ).fetchall()

    total_orders = int((order_row['total_orders'] if order_row else 0) or 0)
    paid_orders = int((order_row['paid_orders'] if order_row else 0) or 0)
    paid_amount = round(float((order_row['paid_amount'] if order_row else 0) or 0), 2)
    req_total = int((req_row['total'] if req_row else 0) or 0)
    req_success = int((req_row['success_count'] if req_row else 0) or 0)
    req_success_rate = (req_success / req_total) if req_total > 0 else 1.0

    revenue_by_day = {str(r['day_key'] or ''): round(float(r['revenue'] or 0), 2) for r in day_rows}
    login_by_day = {str(r['day_key'] or ''): int(r['cnt'] or 0) for r in login_rows}
    days = sorted(set(revenue_by_day.keys()) | set(login_by_day.keys()))

    return _ok(
        {
            'overview': {
                'totalUsers': total_users,
                'activeMembers': active_members,
                'totalOrders': total_orders,
                'paidOrders': paid_orders,
                'paidAmount': paid_amount,
                'backtestRuns': backtest_runs,
                'login24h': login_24h,
                'error24h': err_24h,
                'requestSuccessRate24h': round(req_success_rate, 6),
                'p95LatencyMs24h': round(float(p95), 2),
            },
            'trend7d': {
                'days': days,
                'revenue': [revenue_by_day.get(d, 0.0) for d in days],
                'logins': [login_by_day.get(d, 0) for d in days],
            },
        }
    )


@router.get('/system/monitor/kpi-summary')
def monitor_kpi_summary(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    # 默认阈值（可后续做成可配置）
    threshold_error_rate = 0.02
    threshold_p95_latency_ms = 1500.0
    threshold_payment_success_rate = 0.90

    cutoff = _cutoff_time(24 * 60)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            req = conn.execute(
                'SELECT COUNT(1) AS total, SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS err FROM request_metrics WHERE created_at >= ?',
                (cutoff,),
            ).fetchone()
            req_rows = conn.execute(
                'SELECT latency_ms FROM request_metrics WHERE created_at >= ? ORDER BY created_at DESC LIMIT 2000',
                (cutoff,),
            ).fetchall()

            pay = conn.execute(
                "SELECT COUNT(1) AS total, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()

    req_total = int((req['total'] if req else 0) or 0)
    req_err = int((req['err'] if req else 0) or 0)
    error_rate = (req_err / req_total) if req_total > 0 else 0.0
    p95 = _percentile([float(r['latency_ms'] or 0) for r in req_rows], 0.95)

    pay_total = int((pay['total'] if pay else 0) or 0)
    pay_paid = int((pay['paid'] if pay else 0) or 0)
    payment_success_rate = (pay_paid / pay_total) if pay_total > 0 else 1.0

    return _ok(
        {
            'windowHours': 24,
            'kpi': {
                'errorRate': round(error_rate, 6),
                'p95LatencyMs': round(float(p95), 2),
                'paymentSuccessRate': round(payment_success_rate, 6),
            },
            'threshold': {
                'errorRate': threshold_error_rate,
                'p95LatencyMs': threshold_p95_latency_ms,
                'paymentSuccessRate': threshold_payment_success_rate,
            },
            'healthy': bool(
                error_rate <= threshold_error_rate
                and p95 <= threshold_p95_latency_ms
                and payment_success_rate >= threshold_payment_success_rate
            ),
        }
    )


@router.post('/system/monitor/check-and-alert')
def monitor_check_and_alert(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    cutoff = _cutoff_time(24 * 60)
    threshold_error_rate = 0.02
    threshold_p95_latency_ms = 1500.0
    threshold_payment_success_rate = 0.90

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            req = conn.execute(
                'SELECT COUNT(1) AS total, SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS err FROM request_metrics WHERE created_at >= ?',
                (cutoff,),
            ).fetchone()
            req_rows = conn.execute(
                'SELECT latency_ms FROM request_metrics WHERE created_at >= ? ORDER BY created_at DESC LIMIT 2000',
                (cutoff,),
            ).fetchall()
            pay = conn.execute(
                "SELECT COUNT(1) AS total, SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid FROM orders WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()

            req_total = int((req['total'] if req else 0) or 0)
            req_err = int((req['err'] if req else 0) or 0)
            error_rate = (req_err / req_total) if req_total > 0 else 0.0
            p95 = _percentile([float(r['latency_ms'] or 0) for r in req_rows], 0.95)

            pay_total = int((pay['total'] if pay else 0) or 0)
            pay_paid = int((pay['paid'] if pay else 0) or 0)
            payment_success_rate = (pay_paid / pay_total) if pay_total > 0 else 1.0

            alerts: list[str] = []
            if error_rate > threshold_error_rate:
                alerts.append(f'错误率过高: {error_rate:.2%} > {threshold_error_rate:.2%}')
            if p95 > threshold_p95_latency_ms:
                alerts.append(f'P95 延迟过高: {p95:.2f}ms > {threshold_p95_latency_ms:.2f}ms')
            if payment_success_rate < threshold_payment_success_rate:
                alerts.append(
                    f'支付成功率过低: {payment_success_rate:.2%} < {threshold_payment_success_rate:.2%}'
                )

            if alerts:
                _send_observability_alert(
                    conn,
                    title='[生产告警] AiceMind 关键指标异常',
                    content='\n'.join(alerts),
                    payload={
                        'errorRate': round(error_rate, 6),
                        'p95LatencyMs': round(float(p95), 2),
                        'paymentSuccessRate': round(payment_success_rate, 6),
                        'operator': str(user.get('username') or user.get('id') or ''),
                    },
                )
            conn.commit()

    return _ok({'alerts': alerts, 'triggered': bool(alerts)})


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
                ORDER BY a.created_at DESC
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
                ORDER BY l.created_at DESC
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
                ORDER BY p.created_at DESC
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
