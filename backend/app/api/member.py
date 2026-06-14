from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()


def _resolve_member_row_by_any_id(conn: sqlite3.Connection, raw_id: str):
    target_id = str(raw_id or '').strip()
    if not target_id:
        return None, None

    conn.row_factory = sqlite3.Row

    member_row = conn.execute(
        '''
        SELECT id, user_nickname, user_id, email, member_level, member_status,
               start_time, expire_time, points, updated_at, created_at
        FROM member_users
        WHERE id = ?
        LIMIT 1
        ''',
        (target_id,),
    ).fetchone()
    if member_row:
        return member_row, None

    account_row = conn.execute(
        '''
        SELECT id, username, real_name, email, roles
        FROM user_accounts
        WHERE id = ?
        LIMIT 1
        ''',
        (target_id,),
    ).fetchone()
    if not account_row:
        return None, None

    username = str(account_row['username'] or '').strip()
    email = str(account_row['email'] or '').strip().lower()
    member_row = conn.execute(
        '''
        SELECT id, user_nickname, user_id, email, member_level, member_status,
               start_time, expire_time, points, updated_at, created_at
        FROM member_users
        WHERE user_id = ? OR lower(email) = ?
        LIMIT 1
        ''',
        (username, email),
    ).fetchone()
    return member_row, account_row

@router.post('/system/member/renewal/remind/run')
def run_member_renewal_reminder(body: RenewalReminderRunBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    dry_run = bool(body.dryRun)
    include_expired_recall = bool(body.includeExpiredRecall)

    email_settings = _get_email_settings(mask_secret=False)
    email_enabled = bool(str(email_settings.get('smtpHost') or '').strip() and str(email_settings.get('fromEmail') or '').strip())

    now_dt = datetime.now()
    summary = {
        'dryRun': dry_run,
        'emailEnabled': email_enabled,
        'inappCount': 0,
        'emailCount': 0,
        'expiredRecallCount': 0,
        'renewalCount': 0,
        'skippedCount': 0,
    }

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT
                    u.id AS account_id,
                    u.username,
                    u.real_name,
                    u.email,
                    m.member_level,
                    m.member_status,
                    m.expire_time
                FROM user_accounts u
                LEFT JOIN member_users m ON m.user_id = u.username
                WHERE m.expire_time IS NOT NULL AND m.expire_time != ''
                ORDER BY datetime(m.expire_time) ASC
                '''
            ).fetchall()

            for row in rows:
                account_id = str(row['account_id'] or '').strip()
                username = str(row['username'] or '').strip()
                email = str(row['email'] or '').strip().lower()
                member_level = str(row['member_level'] or 'basic').strip() or 'basic'
                expire_time = str(row['expire_time'] or '').strip()
                expire_dt = _parse_dt(expire_time)
                if not account_id or not expire_dt:
                    continue

                days_left = (expire_dt.date() - now_dt.date()).days
                reminder_type = ''
                days_key = days_left

                if days_left in _RENEWAL_REMINDER_DAYS:
                    reminder_type = 'renewal'
                    summary['renewalCount'] += 1
                elif include_expired_recall and days_left < 0:
                    reminder_type = 'expired_recall'
                    days_key = -1
                    summary['expiredRecallCount'] += 1
                else:
                    summary['skippedCount'] += 1
                    continue

                if reminder_type == 'renewal':
                    title = f'会员将在 {days_left} 天后到期'
                    content = f"账号 {username} 的 {member_level} 会员将于 {expire_time} 到期，请及时续费避免功能中断。"
                else:
                    title = '会员已过期，续费后可恢复完整权益'
                    content = f"账号 {username} 的 {member_level} 会员已于 {expire_time} 过期，现在续费可立即恢复权限。"

                inapp_exists = conn.execute(
                    '''
                    SELECT 1 FROM renewal_reminder_logs
                    WHERE account_id = ? AND expire_time = ? AND days_left = ?
                      AND reminder_type = ? AND channel = 'inapp'
                    LIMIT 1
                    ''',
                    (account_id, expire_time, int(days_key), reminder_type),
                ).fetchone()

                if not inapp_exists:
                    summary['inappCount'] += 1
                    if not dry_run:
                        _record_inapp_notification(conn, account_id, title, content, 'renewal')
                        now = _now_str()
                        conn.execute(
                            '''
                            INSERT INTO renewal_reminder_logs (
                                id, account_id, username, email, member_level, expire_time,
                                days_left, reminder_type, channel,
                                title, content, status, detail, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'inapp', ?, ?, 'sent', '', ?, ?)
                            ''',
                            (
                                uuid.uuid4().hex,
                                account_id,
                                username,
                                email,
                                member_level,
                                expire_time,
                                int(days_key),
                                reminder_type,
                                title,
                                content,
                                now,
                                now,
                            ),
                        )

                if email and email_enabled:
                    email_exists = conn.execute(
                        '''
                        SELECT 1 FROM renewal_reminder_logs
                        WHERE account_id = ? AND expire_time = ? AND days_left = ?
                          AND reminder_type = ? AND channel = 'email'
                        LIMIT 1
                        ''',
                        (account_id, expire_time, int(days_key), reminder_type),
                    ).fetchone()
                    if not email_exists:
                        summary['emailCount'] += 1
                        if not dry_run:
                            email_status = 'sent'
                            detail = ''
                            try:
                                _send_email(email_settings, email, title, content)
                            except Exception as e:
                                email_status = 'failed'
                                detail = str(e)
                            now = _now_str()
                            conn.execute(
                                '''
                                INSERT INTO renewal_reminder_logs (
                                    id, account_id, username, email, member_level, expire_time,
                                    days_left, reminder_type, channel,
                                    title, content, status, detail, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'email', ?, ?, ?, ?, ?, ?)
                                ''',
                                (
                                    uuid.uuid4().hex,
                                    account_id,
                                    username,
                                    email,
                                    member_level,
                                    expire_time,
                                    int(days_key),
                                    reminder_type,
                                    title,
                                    content,
                                    email_status,
                                    detail,
                                    now,
                                    now,
                                ),
                            )

            if not dry_run:
                _audit_log(
                    conn,
                    str(user.get('id') or ''),
                    'member.renewal_reminder.run',
                    'renewal_reminder',
                    '*',
                    summary,
                )
            conn.commit()

    return _ok(summary, message='续费提醒任务执行完成')

@router.get('/notice/list')
def list_my_notices(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(50, ge=1, le=200),
):
    user = _require_user(authorization)
    account_id = str(user.get('id') or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, title, content, type, read_at, created_at
                FROM inapp_notifications
                WHERE account_id = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (account_id, int(limit)),
            ).fetchall()
            unread_row = conn.execute(
                "SELECT COUNT(1) AS c FROM inapp_notifications WHERE account_id = ? AND (read_at = '' OR read_at IS NULL)",
                (account_id,),
            ).fetchone()

    return _ok(
        {
            'items': [
                {
                    'id': r['id'],
                    'title': r['title'],
                    'content': r['content'],
                    'type': r['type'],
                    'readAt': r['read_at'] or '',
                    'createdAt': r['created_at'],
                }
                for r in rows
            ],
            'unreadCount': int((unread_row['c'] if unread_row else 0) or 0),
        }
    )

@router.post('/notice/read-all')
def read_all_my_notices(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    account_id = str(user.get('id') or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            now = _now_str()
            conn.execute(
                "UPDATE inapp_notifications SET read_at = ?, updated_at = ? WHERE account_id = ? AND (read_at = '' OR read_at IS NULL)",
                (now, now, account_id),
            )
            conn.commit()

    return _ok(True)


async def _handle_payment_callback(provider: str, request: Request):
    provider_key = str(provider or '').strip().lower()

    payload: dict[str, Any] = {}
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    if not payload:
        try:
            raw_body = await request.body()
            body_text = raw_body.decode('utf-8', errors='ignore') if raw_body else ''
            if body_text:
                payload = {k: v for k, v in parse_qsl(body_text, keep_blank_values=True)}
        except Exception:
            payload = {}

    if not payload:
        payload = dict(request.query_params)

    settings = _get_payment_settings(mask_secret=False)
    if provider_key == 'wechat' and isinstance(payload.get('resource'), (dict, str)):
        payload = _wechat_decrypt_notification_resource(payload.get('resource'), str(settings.get('wechatApiV3Key') or '')) or payload

    out_trade_no, amount, status_text, gateway_trade_no, event_key = _extract_payment_notify_payload(provider_key, payload)
    if not out_trade_no:
        return _fail('缺少 out_trade_no')

    verified = _verify_callback_signature(
        provider_key,
        payload,
        settings,
        wechat_resource_verified=bool(payload.get('_wechat_resource_verified')),
    )

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            existing_event = conn.execute(
                'SELECT id, processed, processed_message FROM payment_events WHERE event_key = ? LIMIT 1',
                (event_key,),
            ).fetchone()
            if existing_event:
                return _ok(
                    {
                        'idempotent': True,
                        'eventKey': event_key,
                        'processed': bool(int(existing_event['processed'] or 0)),
                        'message': existing_event['processed_message'] or 'already processed',
                    }
                )

            now = _now_str()
            conn.execute(
                '''
                INSERT INTO payment_events (
                    id, provider, event_key, out_trade_no, status,
                    payload, verified, processed, processed_message,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?)
                ''',
                (
                    uuid.uuid4().hex,
                    provider_key,
                    event_key,
                    out_trade_no,
                    status_text,
                    json.dumps(payload, ensure_ascii=False),
                    1 if verified else 0,
                    now,
                    now,
                ),
            )

            if not verified:
                conn.execute(
                    'UPDATE payment_events SET processed = 1, processed_message = ?, updated_at = ? WHERE event_key = ?',
                    ('signature verify failed', _now_str(), event_key),
                )
                _notify_payment_alert(
                    conn,
                    category='payment_callback_failed',
                    title='[支付回调告警] 签名校验失败',
                    content=f"provider={provider_key} outTradeNo={out_trade_no} eventKey={event_key}",
                    payload={'provider': provider_key, 'outTradeNo': out_trade_no, 'eventKey': event_key, 'reason': 'signature verify failed'},
                    level='error',
                )
                conn.commit()
                return _fail('签名验证失败')

            trade_row = conn.execute(
                'SELECT * FROM payment_trades WHERE out_trade_no = ? AND provider = ? LIMIT 1',
                (out_trade_no, provider_key),
            ).fetchone()
            if not trade_row:
                conn.execute(
                    'UPDATE payment_events SET processed = 1, processed_message = ?, updated_at = ? WHERE event_key = ?',
                    ('trade not found', _now_str(), event_key),
                )
                _enqueue_payment_callback_retry_job(
                    conn,
                    event_key=event_key,
                    provider=provider_key,
                    out_trade_no=out_trade_no,
                    payload=payload,
                    reason='trade_not_found',
                    next_retry_after_minutes=2,
                )
                _notify_payment_alert(
                    conn,
                    category='payment_callback_failed',
                    title='[支付回调告警] 本地交易不存在',
                    content=f"provider={provider_key} outTradeNo={out_trade_no} eventKey={event_key}",
                    payload={'provider': provider_key, 'outTradeNo': out_trade_no, 'eventKey': event_key, 'reason': 'trade not found'},
                    level='error',
                )
                conn.commit()
                return _fail('交易不存在')

            trade_amount = float(trade_row['amount'] or 0)
            if amount > 0 and abs(trade_amount - amount) > 0.01:
                conn.execute(
                    'UPDATE payment_events SET processed = 1, processed_message = ?, updated_at = ? WHERE event_key = ?',
                    (f'amount mismatch: expected {trade_amount}, got {amount}', _now_str(), event_key),
                )
                _enqueue_payment_callback_retry_job(
                    conn,
                    event_key=event_key,
                    provider=provider_key,
                    out_trade_no=out_trade_no,
                    payload=payload,
                    reason='amount_mismatch',
                    next_retry_after_minutes=10,
                )
                _notify_payment_alert(
                    conn,
                    category='payment_callback_failed',
                    title='[支付回调告警] 金额校验失败',
                    content=f"provider={provider_key} outTradeNo={out_trade_no} expected={trade_amount} got={amount}",
                    payload={'provider': provider_key, 'outTradeNo': out_trade_no, 'eventKey': event_key, 'expected': trade_amount, 'actual': amount},
                    level='error',
                )
                conn.commit()
                return _fail('金额校验失败')

            if _is_payment_success(provider_key, status_text):
                _apply_paid_trade(
                    conn,
                    trade_row,
                    callback_payload=payload,
                    verified=verified,
                    gateway_trade_no=gateway_trade_no,
                    provider_status=status_text,
                )
                processed_message = 'paid'
            else:
                conn.execute(
                    'UPDATE payment_trades SET status = ?, callback_payload = ?, callback_verified = ?, callback_at = ?, gateway_trade_no = ?, updated_at = ? WHERE id = ?',
                    (
                        'failed',
                        json.dumps(payload, ensure_ascii=False),
                        1 if verified else 0,
                        _now_str(),
                        gateway_trade_no,
                        _now_str(),
                        str(trade_row['id'] or ''),
                    ),
                )
                processed_message = f'ignored status: {status_text}'
                _notify_payment_alert(
                    conn,
                    category='payment_callback_failed',
                    title='[支付回调告警] 非成功支付状态',
                    content=f"provider={provider_key} outTradeNo={out_trade_no} status={status_text}",
                    payload={'provider': provider_key, 'outTradeNo': out_trade_no, 'eventKey': event_key, 'status': status_text},
                    level='warning',
                )

            conn.execute(
                'UPDATE payment_events SET processed = 1, processed_message = ?, updated_at = ? WHERE event_key = ?',
                (processed_message, _now_str(), event_key),
            )
            conn.commit()

    return _ok({'eventKey': event_key, 'outTradeNo': out_trade_no, 'status': status_text, 'verified': verified})

@router.post('/system/member/points/adjust')
def adjust_member_points(body: PointsAdjustBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    if not account_id:
        return _fail('缺少账号ID')

    delta = int(body.delta or 0)
    if delta == 0:
        return _fail('积分变更值不能为 0')

    reason = str(body.reason or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            account_row = _resolve_account(conn, account_id)
            if not account_row:
                return _fail('账号不存在')

            try:
                result = _adjust_member_points(
                    conn,
                    account_row=account_row,
                    delta=delta,
                    actor_account_id=str(user.get('id') or ''),
                    reason=reason,
                    source='system.member.points.adjust',
                    ref_id='',
                )
            except ValueError as e:
                return _fail(str(e))

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.points.adjust',
                'member_user',
                account_id,
                {
                    'delta': delta,
                    'before': result['before'],
                    'after': result['after'],
                    'reason': reason,
                },
            )
            conn.commit()

    return _ok(result)

@router.get('/system/member/list')
def list_members(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        data = _query_members()
    return _ok(data)

@router.post('/system/member/create')
def create_member(body: MemberCreateBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    nickname = (body.userNickname or '').strip()
    user_id = (body.userId or '').strip()
    if not nickname or not user_id:
        return _fail('用户昵称和用户ID必填')

    now = _now_str()
    start_time = (body.startTime or '').strip() or now
    expire_time = (body.expireTime or '').strip() or _default_expire_str()

    if _parse_dt(start_time) is None:
        return _fail('开始时间格式错误，请使用 YYYY-MM-DD HH:mm:ss')
    if _parse_dt(expire_time) is None:
        return _fail('过期时间格式错误，请使用 YYYY-MM-DD HH:mm:ss')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute(
                'SELECT 1 FROM member_users WHERE user_id = ?',
                (user_id,),
            ).fetchone()
            if exists:
                return _fail('用户ID已存在')

            row_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO member_users (
                    id, user_nickname, user_id, email,
                    member_level, member_status,
                    start_time, expire_time, points,
                    updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row_id,
                    nickname,
                    user_id,
                    (body.email or '').strip(),
                    (body.memberLevel or 'basic').strip() or 'basic',
                    (body.memberStatus or 'active').strip() or 'active',
                    start_time,
                    expire_time,
                    int(body.points or 0),
                    now,
                    now,
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.create',
                'member_user',
                row_id,
                {'userId': user_id, 'email': (body.email or '').strip()},
            )
            conn.commit()
    return _ok(True)

@router.post('/system/member/update')
def update_member(body: MemberUpdateBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = (body.id or '').strip()
    nickname = (body.userNickname or '').strip()
    user_id = (body.userId or '').strip()
    if not row_id:
        return _fail('缺少用户ID')
    if not nickname or not user_id:
        return _fail('用户昵称和用户ID必填')

    start_time = (body.startTime or '').strip() or _now_str()
    expire_time = (body.expireTime or '').strip() or _default_expire_str()

    if _parse_dt(start_time) is None:
        return _fail('开始时间格式错误，请使用 YYYY-MM-DD HH:mm:ss')
    if _parse_dt(expire_time) is None:
        return _fail('过期时间格式错误，请使用 YYYY-MM-DD HH:mm:ss')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            member_row, account_row = _resolve_member_row_by_any_id(conn, row_id)
            actual_member_id = str(member_row['id'] or '').strip() if member_row else ''

            if not actual_member_id:
                if not account_row:
                    return _fail('用户不存在')
                _sync_member_for_account(
                    conn,
                    account_row=account_row,
                    level=(body.memberLevel or 'basic').strip() or 'basic',
                    status=(body.memberStatus or 'active').strip() or 'active',
                    start_time=start_time,
                    expire_time=expire_time,
                )
                member_row, _ = _resolve_member_row_by_any_id(conn, row_id)
                actual_member_id = str(member_row['id'] or '').strip() if member_row else ''
                if not actual_member_id:
                    return _fail('用户不存在')

            duplicate = conn.execute(
                'SELECT 1 FROM member_users WHERE user_id = ? AND id != ?',
                (user_id, actual_member_id),
            ).fetchone()
            if duplicate:
                return _fail('用户ID已存在')

            conn.execute(
                '''
                UPDATE member_users
                SET user_nickname = ?,
                    user_id = ?,
                    email = ?,
                    member_level = ?,
                    member_status = ?,
                    start_time = ?,
                    expire_time = ?,
                    points = ?,
                    updated_at = ?
                WHERE id = ?
                ''',
                (
                    nickname,
                    user_id,
                    (body.email or '').strip(),
                    (body.memberLevel or 'basic').strip() or 'basic',
                    (body.memberStatus or 'active').strip() or 'active',
                    start_time,
                    expire_time,
                    int(body.points or 0),
                    _now_str(),
                    actual_member_id,
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.update',
                'member_user',
                actual_member_id,
                {'userId': user_id, 'email': (body.email or '').strip()},
            )
            conn.commit()
    return _ok(True)

@router.post('/system/member/toggle-status')
def toggle_member_status(body: ToggleStatusBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = (body.id or '').strip()
    status = (body.status or '').strip() or 'disabled'
    if status not in ('active', 'disabled', 'expired'):
        return _fail('无效状态')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            member_row, account_row = _resolve_member_row_by_any_id(conn, row_id)
            actual_member_id = str(member_row['id'] or '').strip() if member_row else ''

            if not actual_member_id:
                if not account_row:
                    return _fail('用户不存在')
                now = _now_str()
                expire_time = _default_expire_str() if status == 'active' else now
                start_time = now
                _sync_member_for_account(
                    conn,
                    account_row=account_row,
                    level='basic',
                    status=status,
                    start_time=start_time,
                    expire_time=expire_time,
                )
                member_row, _ = _resolve_member_row_by_any_id(conn, row_id)
                actual_member_id = str(member_row['id'] or '').strip() if member_row else ''
                if not actual_member_id:
                    return _fail('用户不存在')
            else:
                conn.execute(
                    'UPDATE member_users SET member_status = ?, updated_at = ? WHERE id = ?',
                    (status, _now_str(), actual_member_id),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.toggle_status',
                'member_user',
                actual_member_id,
                {'status': status, 'sourceId': row_id},
            )
            conn.commit()
    return _ok(True)

@router.post('/system/member/extend-expire')
def extend_member_expire(body: ExtendExpireBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = (body.id or '').strip()
    if not row_id:
        return _fail('缺少用户ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            member_row, account_row = _resolve_member_row_by_any_id(conn, row_id)
            actual_member_id = str(member_row['id'] or '').strip() if member_row else ''

            if not actual_member_id:
                if not account_row:
                    return _fail('用户不存在')
                _sync_member_for_account(
                    conn,
                    account_row=account_row,
                    level='basic',
                    status='active',
                    start_time=_now_str(),
                    expire_time=_default_expire_str(),
                )
                member_row, _ = _resolve_member_row_by_any_id(conn, row_id)
                actual_member_id = str(member_row['id'] or '').strip() if member_row else ''
                if not actual_member_id:
                    return _fail('用户不存在')

            now = datetime.now()
            if body.expireTime and body.expireTime.strip():
                parsed = _parse_dt(body.expireTime)
                if parsed is None:
                    return _fail('指定时间格式错误，请使用 YYYY-MM-DD HH:mm:ss')
                new_expire_dt = parsed
            else:
                days = int(body.days or 0)
                if days <= 0:
                    return _fail('延长天数必须大于 0')
                current_expire = _parse_dt(member_row['expire_time']) or now
                base = current_expire if current_expire > now else now
                new_expire_dt = base + timedelta(days=days)

            new_expire = new_expire_dt.strftime('%Y-%m-%d %H:%M:%S')
            status = 'active' if new_expire_dt > now else 'expired'

            conn.execute(
                '''
                UPDATE member_users
                SET expire_time = ?,
                    member_status = ?,
                    updated_at = ?
                WHERE id = ?
                ''',
                (new_expire, status, _now_str(), actual_member_id),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.extend_expire',
                'member_user',
                actual_member_id,
                {'expireTime': new_expire, 'status': status, 'sourceId': row_id},
            )
            conn.commit()
    return _ok({'expireTime': new_expire})

@router.delete('/system/member/{member_id}')
def delete_member(member_id: str, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = (member_id or '').strip()
    if not row_id:
        return _fail('缺少用户ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            member_row, account_row = _resolve_member_row_by_any_id(conn, row_id)
            if not member_row:
                return _fail('用户不存在')

            actual_member_id = str(member_row['id'] or '').strip()
            user_id = str(member_row['user_id'] or '').strip()
            email = str(member_row['email'] or '').strip().lower()

            conn.execute('DELETE FROM member_users WHERE id = ?', (actual_member_id,))

            if not account_row:
                if user_id and email:
                    account_row = conn.execute(
                        'SELECT id FROM user_accounts WHERE lower(username) = ? OR lower(email) = ? LIMIT 1',
                        (user_id.lower(), email),
                    ).fetchone()
                elif user_id:
                    account_row = conn.execute(
                        'SELECT id FROM user_accounts WHERE lower(username) = ? LIMIT 1',
                        (user_id.lower(),),
                    ).fetchone()
                elif email:
                    account_row = conn.execute(
                        'SELECT id FROM user_accounts WHERE lower(email) = ? LIMIT 1',
                        (email,),
                    ).fetchone()

            if user_id and email:
                conn.execute(
                    'DELETE FROM user_accounts WHERE lower(username) = ? OR lower(email) = ?',
                    (user_id.lower(), email),
                )
            elif user_id:
                conn.execute(
                    'DELETE FROM user_accounts WHERE lower(username) = ?',
                    (user_id.lower(),),
                )
            elif email:
                conn.execute(
                    'DELETE FROM user_accounts WHERE lower(email) = ?',
                    (email,),
                )

            if account_row and account_row['id']:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE account_id = ? AND revoked_at = ''",
                    (_now_str(), _now_str(), str(account_row['id'])),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.delete',
                'member_user',
                actual_member_id,
                {'userId': user_id, 'email': email, 'sourceId': row_id},
            )
            conn.commit()

    # 清理该账号已签发 token，避免被删除账号继续使用旧 token 访问
    remove_tokens: list[str] = []
    for token, token_user in _ADMIN_TOKENS.items():
        token_username = str(token_user.get('username') or '').strip().lower()
        token_email = str(token_user.get('email') or '').strip().lower()
        if (user_id and token_username == user_id.lower()) or (email and token_email == email):
            remove_tokens.append(token)
    for token in remove_tokens:
        _ADMIN_TOKENS.pop(token, None)

    return _ok(True)
