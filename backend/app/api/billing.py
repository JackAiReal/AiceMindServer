from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

def _resolve_billing_account_id(user: dict[str, Any]) -> str:
    account_id = str(user.get('id') or '').strip()
    username = str(user.get('username') or '').strip()
    email = str(user.get('email') or '').strip().lower()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                '''
                SELECT id
                FROM user_accounts
                WHERE id = ? OR username = ? OR lower(email) = ?
                LIMIT 1
                ''',
                (account_id, username, email),
            ).fetchone()
    return str(row['id'] or '').strip() if row else ''


@router.post('/billing/feature/check')
def billing_feature_check(body: BillingFeatureConsumeBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    account_id = _resolve_billing_account_id(user)
    if not account_id:
        return _fail('账号不存在')

    result = check_feature_access(
        account_id,
        str(body.featureCode or '').strip(),
        consume_amount=max(0, int(body.amount or 0)),
    )
    if not result.get('allowed'):
        return _fail(str(result.get('reason') or '权益校验失败'), result)
    return _ok(result)


@router.post('/billing/feature/consume')
def billing_feature_consume(body: BillingFeatureConsumeBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    account_id = _resolve_billing_account_id(user)
    if not account_id:
        return _fail('账号不存在')

    result = consume_feature_quota(
        account_id,
        str(body.featureCode or '').strip(),
        amount=max(0, int(body.amount or 0)),
        source=str(body.source or '').strip(),
        ref_id=str(body.refId or '').strip(),
        detail=body.detail or {},
    )
    if not result.get('allowed'):
        return _fail(str(result.get('reason') or '权益扣减失败'), result)
    return _ok(result)


@router.get('/system/billing/context')
def billing_context(accountId: str = Query(..., description='账号ID'), authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(accountId or '').strip()
    if not account_id:
        return _fail('缺少账号ID')

    return _ok(get_billing_context(account_id))

@router.get('/system/billing/policy')
def billing_policy(level: str = Query('basic'), authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    return _ok(get_entitlement_policy(level))

@router.post('/system/billing/policy/upsert')
def billing_policy_upsert(body: BillingPolicyBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    policy = upsert_entitlement_policy(str(body.level or 'basic'), body.policy or {})

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'billing.policy.upsert',
                'entitlement_policy',
                str(policy.get('level') or ''),
                policy,
            )
            conn.commit()

    return _ok(policy)

@router.get('/system/billing/usage')
def billing_usage(
    accountId: str = Query(..., description='账号ID'),
    featureCode: str = Query('backtest.run', description='功能编码'),
    period: str = Query('', description='YYYY-MM，默认当前月'),
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(accountId or '').strip()
    feature_code = str(featureCode or '').strip()
    period_key = str(period or '').strip() or datetime.now().strftime('%Y-%m')
    if not account_id or not feature_code:
        return _fail('参数错误')

    used = get_feature_usage(account_id, feature_code, period_key)
    return _ok({'accountId': account_id, 'featureCode': feature_code, 'period': period_key, 'used': used})

@router.get('/system/billing/ledger/list')
def list_billing_ledger(
    authorization: Optional[str] = Header(default=None),
    featureCode: str = Query('', description='功能编码，可选 chat.message/backtest.run'),
    period: str = Query('', description='周期：YYYY-MM 或 YYYY-MM-DD'),
    accountId: str = Query('', description='账号ID'),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []

    feature = str(featureCode or '').strip()
    if feature:
        where.append('l.feature_code = ?')
        params.append(feature)

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

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        get_entitlement_policy('basic')  # 触发账单相关表的懒初始化
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT
                    l.id, l.account_id, l.feature_code, l.amount, l.period_key,
                    l.source, l.ref_id, l.detail, l.created_at,
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
                'featureCode': r['feature_code'],
                'amount': int(r['amount'] or 0),
                'periodKey': r['period_key'],
                'source': r['source'] or '',
                'refId': r['ref_id'] or '',
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )
