from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post('/system/order/close-expired')
def close_expired_orders(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            closed = _close_timeout_orders(
                conn,
                actor_account_id=str(user.get('id') or ''),
                reason='manual close expired orders',
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.close_expired',
                'order',
                '*',
                {'count': len(closed)},
            )
            conn.commit()

    return _ok({'count': len(closed), 'items': closed}, message='已处理超时订单')

@router.post('/commerce/order/create-pay')
def commerce_create_pay_order(body: CommerceCreatePayBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    provider = str(body.provider or '').strip().lower() or 'alipay'
    if provider not in ('alipay', 'wechat'):
        return _fail('仅支持 alipay 或 wechat')

    plan_code = str(body.planCode or '').strip()
    if not plan_code:
        return _fail('缺少套餐编码')

    settings = _get_payment_settings(mask_secret=False)
    if provider == 'alipay':
        if not settings.get('alipayEnabled'):
            return _fail('支付宝支付尚未启用')
        required = ['alipayAppId', 'alipayMerchantId', 'alipayAppPrivateKey', 'alipayPublicKey', 'alipayNotifyUrl']
        missing = [key for key in required if not str(settings.get(key) or '').strip()]
        if missing:
            return _fail(f'支付宝配置不完整: {", ".join(missing)}')
    else:
        if not settings.get('wechatEnabled'):
            return _fail('微信支付尚未启用')
        required = ['wechatAppId', 'wechatMerchantId', 'wechatApiV3Key', 'wechatPrivateKey', 'wechatSerialNo', 'wechatNotifyUrl']
        missing = [key for key in required if not str(settings.get(key) or '').strip()]
        if missing:
            return _fail(f'微信支付配置不完整: {", ".join(missing)}')

    account_id = str(user.get('id') or '').strip()
    now_str = _now_str()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row

            account_row = _resolve_account(conn, account_id)
            if not account_row:
                return _fail('当前账号不存在')

            plan_row = _resolve_plan(conn, plan_code)
            if not plan_row:
                return _fail('套餐不存在')
            if str(plan_row['status'] or '').strip().lower() != 'active':
                return _fail('套餐未启用，请选择其他套餐')

            amount = float(plan_row['price'] or 0)
            if amount <= 0:
                return _fail('该套餐金额无效，暂不可购买')

            _close_timeout_orders(conn, account_id=account_id)

            recent_trade = _find_recent_unpaid_trade_by_plan(
                conn,
                account_id=account_id,
                plan_code=plan_code,
                provider=provider,
                window_minutes=_ORDER_IDEMPOTENCY_WINDOW_MINUTES,
            )
            if recent_trade:
                payload_text = str(recent_trade['callback_payload'] or '')
                qr_code_reuse = _extract_qr_from_trade_payload(payload_text)
                gateway_response_reuse = _safe_json_loads(payload_text).get('gatewayResponse') or {}
                request_payload_reuse = _safe_json_loads(payload_text).get('request') or {}
                return _ok(
                    {
                        'orderId': str(recent_trade['order_id'] or ''),
                        'orderNo': str(recent_trade['order_no'] or ''),
                        'planCode': str(recent_trade['plan_code'] or plan_code),
                        'planName': str(recent_trade['plan_name'] or plan_code),
                        'amount': round(float(recent_trade['amount'] or amount), 2),
                        'currency': str(recent_trade['currency'] or 'CNY'),
                        'provider': str(recent_trade['provider'] or provider),
                        'tradeId': str(recent_trade['id'] or ''),
                        'outTradeNo': str(recent_trade['out_trade_no'] or ''),
                        'status': 'created',
                        'expireAt': str(recent_trade['expire_at'] or ''),
                        'qrCode': qr_code_reuse,
                        'requestPayload': request_payload_reuse,
                        'gatewayResponse': gateway_response_reuse,
                        'idempotent': True,
                        'message': '检测到短时间内重复创建，已返回已有待支付订单',
                    }
                )

            order_id = uuid.uuid4().hex
            order_no = _generate_unique_order_no(conn, 'UORD')
            order_note = '[USER_BUY] 用户自助升级套餐'
            expire_at = _order_expire_at_str(_ORDER_EXPIRE_MINUTES)

            conn.execute(
                '''
                INSERT INTO orders (
                    id, order_no, account_id, plan_code, amount, currency,
                    channel, status, paid_at, expire_at, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', '', ?, ?, ?, ?)
                ''',
                (
                    order_id,
                    order_no,
                    account_id,
                    plan_code,
                    round(amount, 2),
                    'CNY',
                    provider,
                    expire_at,
                    order_note,
                    now_str,
                    now_str,
                ),
            )

            _append_order_state_event(
                conn,
                order_id=order_id,
                order_no=order_no,
                from_status='',
                to_status='created',
                actor_account_id=account_id,
                reason='user create pay order',
                source='commerce.order.create_pay',
                detail={'planCode': plan_code, 'amount': round(amount, 2)},
            )

            out_trade_no = _generate_unique_out_trade_no(conn, 'UPAY')
            trade_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO payment_trades (
                    id, order_id, order_no, account_id, provider,
                    out_trade_no, amount, currency, status,
                    payer_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
                ''',
                (
                    trade_id,
                    order_id,
                    order_no,
                    account_id,
                    provider,
                    out_trade_no,
                    round(amount, 2),
                    'CNY',
                    account_id,
                    now_str,
                    now_str,
                ),
            )

            try:
                if provider == 'wechat':
                    gateway_response, qr_code, request_payload = _wechat_native_precreate(
                        settings,
                        out_trade_no=out_trade_no,
                        amount=round(amount, 2),
                        subject=str(plan_row['name'] or plan_code),
                    )
                else:
                    gateway_response, qr_code, request_payload = _alipay_precreate(
                        settings,
                        out_trade_no=out_trade_no,
                        amount=round(amount, 2),
                        subject=str(plan_row['name'] or plan_code),
                        body=f"AiceMind 套餐升级 {plan_code}",
                    )
            except Exception as e:
                conn.execute(
                    "UPDATE payment_trades SET status = 'failed', updated_at = ? WHERE id = ?",
                    (_now_str(), trade_id),
                )
                _audit_log(
                    conn,
                    account_id,
                    'commerce.order.create_pay_failed',
                    'payment_trade',
                    trade_id,
                    {'orderNo': order_no, 'planCode': plan_code, 'provider': provider, 'reason': str(e)},
                )
                conn.commit()
                provider_label = '微信' if provider == 'wechat' else '支付宝'
                return _fail(f'{provider_label}预下单失败: {e}')

            conn.execute(
                'UPDATE payment_trades SET callback_payload = ?, updated_at = ? WHERE id = ?',
                (
                    json.dumps(
                        {
                            'request': request_payload,
                            'gatewayResponse': gateway_response,
                            'scene': 'user_recharge',
                        },
                        ensure_ascii=False,
                    ),
                    _now_str(),
                    trade_id,
                ),
            )

            _audit_log(
                conn,
                account_id,
                'commerce.order.create_pay',
                'order',
                order_id,
                {
                    'orderNo': order_no,
                    'planCode': plan_code,
                    'tradeId': trade_id,
                    'outTradeNo': out_trade_no,
                    'provider': provider,
                    'amount': round(amount, 2),
                },
            )
            conn.commit()

    return _ok(
        {
            'orderId': order_id,
            'orderNo': order_no,
            'planCode': plan_code,
            'planName': str(plan_row['name'] or plan_code),
            'amount': round(amount, 2),
            'currency': 'CNY',
            'provider': provider,
            'tradeId': trade_id,
            'outTradeNo': out_trade_no,
            'status': 'created',
            'expireAt': expire_at,
            'qrCode': qr_code,
            'requestPayload': request_payload,
            'gatewayResponse': gateway_response,
            'message': '订单已创建，请扫码支付',
        }
    )

@router.get('/system/order/list')
def list_orders(
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
                SELECT o.id, o.order_no, o.account_id, o.plan_code, o.amount, o.currency,
                       o.channel, o.status, o.paid_at, o.expire_at, o.note, o.created_at,
                       u.username, u.real_name, u.email,
                       p.name AS plan_name,
                       COALESCE((
                           SELECT SUM(r.amount)
                           FROM order_refunds r
                           WHERE r.order_id = o.id AND r.status IN ('created', 'success', 'processed')
                       ), 0) AS refunded_amount,
                       (
                           SELECT e.to_status FROM order_state_events e
                           WHERE e.order_id = o.id
                           ORDER BY datetime(e.created_at) DESC
                           LIMIT 1
                       ) AS latest_state,
                       (
                           SELECT e.created_at FROM order_state_events e
                           WHERE e.order_id = o.id
                           ORDER BY datetime(e.created_at) DESC
                           LIMIT 1
                       ) AS latest_state_at
                FROM orders o
                LEFT JOIN user_accounts u ON u.id = o.account_id
                LEFT JOIN plans p ON p.code = o.plan_code
                ORDER BY datetime(o.created_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'orderNo': r['order_no'],
                'accountId': r['account_id'],
                'username': r['username'] or '',
                'realName': r['real_name'] or '',
                'email': r['email'] or '',
                'planCode': r['plan_code'],
                'planName': r['plan_name'] or r['plan_code'],
                'amount': float(r['amount'] or 0),
                'currency': r['currency'],
                'channel': r['channel'],
                'status': r['status'],
                'paidAt': r['paid_at'],
                'expireAt': r['expire_at'] or '',
                'note': r['note'],
                'createdAt': r['created_at'],
                'refundedAmount': float(r['refunded_amount'] or 0),
                'refundableAmount': max(0.0, float(r['amount'] or 0) - float(r['refunded_amount'] or 0)),
                'latestState': r['latest_state'] or r['status'],
                'latestStateAt': r['latest_state_at'] or r['created_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/order/create')
def create_order(body: OrderCreateBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    plan_code = str(body.planCode or '').strip()
    if not account_id or not plan_code:
        return _fail('账号和套餐必填')

    status = str(body.status or 'created').strip() or 'created'
    if status not in ('created', 'paid', 'cancelled'):
        return _fail('订单状态无效')

    if float(body.amount or 0) < 0:
        return _fail('金额不能为负数')

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

            now_str = _now_str()
            order_no = _generate_unique_order_no(conn, 'ORD')
            paid_at = now_str if status == 'paid' else ''
            expire_at = _order_expire_at_str(_ORDER_EXPIRE_MINUTES) if status == 'created' else ''

            row_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO orders (
                    id, order_no, account_id, plan_code, amount, currency,
                    channel, status, paid_at, expire_at, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row_id,
                    order_no,
                    account_id,
                    plan_code,
                    float(body.amount or 0),
                    str(body.currency or 'CNY').strip() or 'CNY',
                    str(body.channel or 'manual').strip() or 'manual',
                    status,
                    paid_at,
                    expire_at,
                    str(body.note or '').strip(),
                    now_str,
                    now_str,
                ),
            )

            if status == 'paid':
                new_expire = _apply_plan_to_account(conn, account_row, plan_row, paid_at)
            else:
                new_expire = None

            _append_order_state_event(
                conn,
                order_id=row_id,
                order_no=order_no,
                from_status='',
                to_status=status,
                actor_account_id=str(user.get('id') or ''),
                reason='create',
                source='system.order.create',
                detail={'planCode': plan_code, 'amount': float(body.amount or 0)},
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.create',
                'order',
                row_id,
                {'orderNo': order_no, 'status': status, 'planCode': plan_code, 'expireTime': new_expire},
            )
            conn.commit()

    return _ok({'orderNo': order_no})

@router.post('/system/order/mark-paid')
def mark_order_paid(body: OrderMarkPaidBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    order_id = str(body.orderId or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                '''
                SELECT id, order_no, account_id, plan_code, status
                FROM orders
                WHERE id = ?
                LIMIT 1
                ''',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            account_row = _resolve_account(conn, str(order_row['account_id'] or ''))
            if not account_row:
                return _fail('订单账号不存在')

            plan_row = _resolve_plan(conn, str(order_row['plan_code'] or ''))
            if not plan_row:
                return _fail('订单套餐不存在')

            current_status = str(order_row['status'] or '')
            if current_status == 'paid':
                return _ok(True, message='订单已是已支付状态')
            if current_status in ('refunded', 'refund_partial'):
                return _fail('订单已进入退款流程，不能重复标记支付')
            if current_status == 'cancelled':
                return _fail('订单已取消，不能标记支付')

            now_str = _now_str()
            from_status = current_status
            conn.execute(
                'UPDATE orders SET status = ?, paid_at = ?, updated_at = ? WHERE id = ?',
                ('paid', now_str, now_str, order_id),
            )

            _append_order_state_event(
                conn,
                order_id=order_id,
                order_no=str(order_row['order_no'] or ''),
                from_status=from_status,
                to_status='paid',
                actor_account_id=str(user.get('id') or ''),
                reason='mark paid',
                source='system.order.mark_paid',
                detail={},
            )

            new_expire = _apply_plan_to_account(conn, account_row, plan_row, now_str)

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.mark_paid',
                'order',
                order_id,
                {'orderNo': order_row['order_no'], 'expireTime': new_expire},
            )
            conn.commit()

    return _ok(True)

@router.post('/system/order/cancel')
def cancel_order(body: OrderCancelBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    order_id = str(body.orderId or '').strip()
    reason = str(body.reason or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                'SELECT id, order_no, status, paid_at FROM orders WHERE id = ? LIMIT 1',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            current_status = str(order_row['status'] or '')
            if current_status == 'cancelled':
                return _ok(True, message='订单已取消')
            if current_status in {'paid', 'refund_partial', 'refunded'} or str(order_row['paid_at'] or '').strip():
                return _fail('已支付订单请走退款流程')

            now = _now_str()
            conn.execute(
                'UPDATE orders SET status = ?, updated_at = ? WHERE id = ?',
                ('cancelled', now, order_id),
            )
            _append_order_state_event(
                conn,
                order_id=order_id,
                order_no=str(order_row['order_no'] or ''),
                from_status=current_status,
                to_status='cancelled',
                actor_account_id=str(user.get('id') or ''),
                reason=reason or 'cancel order',
                source='system.order.cancel',
                detail={},
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.cancel',
                'order',
                order_id,
                {'orderNo': order_row['order_no'], 'reason': reason},
            )
            conn.commit()

    return _ok(True, message='订单已取消')

@router.post('/system/order/mark-exception')
def mark_order_exception(body: OrderMarkExceptionBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    order_id = str(body.orderId or '').strip()
    reason = str(body.reason or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                'SELECT id, order_no, status FROM orders WHERE id = ? LIMIT 1',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            current_status = str(order_row['status'] or '')
            if current_status == 'exception':
                return _ok(True, message='订单已是异常状态')
            if current_status in {'cancelled', 'refunded'}:
                return _fail('当前状态不允许标记异常')

            now = _now_str()
            conn.execute(
                'UPDATE orders SET status = ?, updated_at = ? WHERE id = ?',
                ('exception', now, order_id),
            )
            _append_order_state_event(
                conn,
                order_id=order_id,
                order_no=str(order_row['order_no'] or ''),
                from_status=current_status,
                to_status='exception',
                actor_account_id=str(user.get('id') or ''),
                reason=reason or 'mark exception',
                source='system.order.mark_exception',
                detail={},
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.mark_exception',
                'order',
                order_id,
                {'orderNo': order_row['order_no'], 'reason': reason},
            )
            _notify_payment_alert(
                conn,
                category='order_exception',
                title='[订单异常告警] 有订单被标记为异常',
                content=f"订单号: {order_row['order_no']}\n原因: {reason or '未填写'}",
                payload={
                    'orderId': order_id,
                    'orderNo': str(order_row['order_no'] or ''),
                    'operator': str(user.get('username') or user.get('id') or ''),
                    'reason': reason,
                },
                level='warning',
            )
            conn.commit()

    return _ok(True, message='订单已标记异常')

@router.post('/system/order/recover')
def recover_order(body: OrderRecoverBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    order_id = str(body.orderId or '').strip()
    reason = str(body.reason or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                'SELECT id, order_no, status, paid_at FROM orders WHERE id = ? LIMIT 1',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            current_status = str(order_row['status'] or '')
            if current_status != 'exception':
                return _fail('只有异常订单才可恢复')

            has_paid = bool(str(order_row['paid_at'] or '').strip())
            target_status = 'paid' if has_paid else 'created'
            now = _now_str()
            conn.execute(
                'UPDATE orders SET status = ?, updated_at = ? WHERE id = ?',
                (target_status, now, order_id),
            )
            _append_order_state_event(
                conn,
                order_id=order_id,
                order_no=str(order_row['order_no'] or ''),
                from_status='exception',
                to_status=target_status,
                actor_account_id=str(user.get('id') or ''),
                reason=reason or 'recover order',
                source='system.order.recover',
                detail={},
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.recover',
                'order',
                order_id,
                {'orderNo': order_row['order_no'], 'targetStatus': target_status, 'reason': reason},
            )
            conn.commit()

    return _ok({'status': target_status}, message='订单已恢复')

@router.post('/system/order/refund')
def refund_order(body: OrderRefundBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    order_id = str(body.orderId or '').strip()
    provider = str(body.provider or 'manual').strip() or 'manual'
    reason = str(body.reason or '').strip()
    external_refund_no = str(body.externalRefundNo or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                '''
                SELECT id, order_no, account_id, plan_code, amount, currency, status, paid_at
                FROM orders
                WHERE id = ?
                LIMIT 1
                ''',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            paid_at = str(order_row['paid_at'] or '').strip()
            if not paid_at:
                return _fail('订单尚未支付，不能退款')

            current_status = str(order_row['status'] or '')
            if current_status in {'cancelled', 'created'}:
                return _fail('当前订单状态不可退款')

            order_amount = float(order_row['amount'] or 0)
            refunded_amount = _sum_refunded_amount(conn, order_id)
            refundable_amount = max(0.0, order_amount - refunded_amount)
            if refundable_amount <= 0.000001:
                return _ok(
                    {
                        'orderId': order_id,
                        'refundedAmount': round(refunded_amount, 2),
                        'refundableAmount': 0.0,
                        'status': 'refunded',
                    },
                    message='订单已全额退款',
                )

            req_amount = body.amount
            refund_amount = refundable_amount if req_amount is None else float(req_amount or 0)
            if refund_amount <= 0:
                return _fail('退款金额必须大于 0')
            if refund_amount - refundable_amount > 0.000001:
                return _fail(f'退款金额超限，最多可退 {refundable_amount:.2f}')

            provider_key = str(provider or 'manual').strip().lower() or 'manual'
            refund_gateway_detail: dict[str, Any] = {}

            if provider_key == 'alipay':
                settings = _get_payment_settings(mask_secret=False)
                required = ['alipayAppId', 'alipayMerchantId', 'alipayAppPrivateKey', 'alipayPublicKey']
                missing = [key for key in required if not str(settings.get(key) or '').strip()]
                if missing:
                    return _fail(f"支付宝配置不完整: {', '.join(missing)}")

                paid_trade = conn.execute(
                    '''
                    SELECT out_trade_no
                    FROM payment_trades
                    WHERE order_id = ? AND provider = 'alipay' AND status = 'paid'
                    ORDER BY datetime(created_at) DESC
                    LIMIT 1
                    ''',
                    (order_id,),
                ).fetchone()
                if not paid_trade or not str(paid_trade['out_trade_no'] or '').strip():
                    return _fail('未找到可退款的支付宝交易单号')

                out_trade_no = str(paid_trade['out_trade_no'] or '').strip()
                refund_request_no = external_refund_no or f"RF{uuid.uuid4().hex[:24]}"
                try:
                    _, biz = _alipay_trade_refund(
                        settings,
                        out_trade_no=out_trade_no,
                        refund_amount=refund_amount,
                        out_request_no=refund_request_no,
                        reason=reason,
                    )
                except Exception as e:
                    _enqueue_refund_retry_job(
                        conn,
                        order_id=order_id,
                        provider='alipay',
                        out_trade_no=out_trade_no,
                        amount=refund_amount,
                        currency=str(order_row['currency'] or 'CNY'),
                        reason=reason,
                        external_refund_no=refund_request_no,
                        last_error=str(e),
                        next_retry_after_minutes=5,
                    )
                    _notify_payment_alert(
                        conn,
                        category='payment_refund',
                        title='[退款告警] 支付宝退款请求失败',
                        content=f"订单号: {order_row['order_no']}\n错误: {e}",
                        payload={'orderId': order_id, 'outTradeNo': out_trade_no, 'refundAmount': refund_amount},
                        level='error',
                    )
                    conn.commit()
                    return _fail(f'支付宝退款失败: {e}')

                if str(biz.get('code') or '').strip() != '10000':
                    msg = str(biz.get('sub_msg') or biz.get('msg') or '支付宝退款失败')
                    _enqueue_refund_retry_job(
                        conn,
                        order_id=order_id,
                        provider='alipay',
                        out_trade_no=out_trade_no,
                        amount=refund_amount,
                        currency=str(order_row['currency'] or 'CNY'),
                        reason=reason,
                        external_refund_no=refund_request_no,
                        last_error=msg,
                        next_retry_after_minutes=8,
                    )
                    _notify_payment_alert(
                        conn,
                        category='payment_refund',
                        title='[退款告警] 支付宝退款返回失败',
                        content=f"订单号: {order_row['order_no']}\n错误: {msg}",
                        payload={'orderId': order_id, 'outTradeNo': out_trade_no, 'refundAmount': refund_amount, 'response': biz},
                        level='error',
                    )
                    conn.commit()
                    return _fail(msg)

                external_refund_no = refund_request_no
                refund_gateway_detail = {
                    'outTradeNo': out_trade_no,
                    'tradeNo': str(biz.get('trade_no') or ''),
                    'refundFee': str(biz.get('refund_fee') or ''),
                    'gmtRefundPay': str(biz.get('gmt_refund_pay') or ''),
                }

            now = _now_str()
            refund_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO order_refunds (
                    id, order_id, order_no, account_id, provider,
                    amount, currency, status, reason, external_refund_no,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?, ?, ?, ?)
                ''',
                (
                    refund_id,
                    order_id,
                    str(order_row['order_no'] or ''),
                    str(order_row['account_id'] or ''),
                    provider_key,
                    round(refund_amount, 2),
                    str(order_row['currency'] or 'CNY'),
                    reason,
                    external_refund_no,
                    now,
                    now,
                ),
            )

            new_refunded_amount = _sum_refunded_amount(conn, order_id)
            target_status = _compute_order_refund_status(order_amount, new_refunded_amount)

            rollback_detail: dict[str, Any] = {}
            if target_status == 'refunded':
                rollback_detail = _rollback_subscription_after_full_refund(
                    conn,
                    order_row=order_row,
                    actor_account_id=str(user.get('id') or ''),
                    reason=reason,
                )

            if current_status != target_status:
                conn.execute(
                    'UPDATE orders SET status = ?, updated_at = ? WHERE id = ?',
                    (target_status, _now_str(), order_id),
                )
                _append_order_state_event(
                    conn,
                    order_id=order_id,
                    order_no=str(order_row['order_no'] or ''),
                    from_status=current_status,
                    to_status=target_status,
                    actor_account_id=str(user.get('id') or ''),
                    reason=reason or 'refund',
                    source='system.order.refund',
                    detail={
                        'refundId': refund_id,
                        'refundAmount': round(refund_amount, 2),
                        'refundedAmount': round(new_refunded_amount, 2),
                        'rollback': rollback_detail,
                        'gateway': refund_gateway_detail,
                    },
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'order.refund',
                'order',
                order_id,
                {
                    'orderNo': order_row['order_no'],
                    'refundId': refund_id,
                    'refundAmount': round(refund_amount, 2),
                    'refundedAmount': round(new_refunded_amount, 2),
                    'targetStatus': target_status,
                    'provider': provider_key,
                    'externalRefundNo': external_refund_no,
                    'rollback': rollback_detail,
                    'gateway': refund_gateway_detail,
                },
            )
            conn.commit()

    return _ok(
        {
            'refundId': refund_id,
            'orderId': order_id,
            'status': target_status,
            'refundAmount': round(refund_amount, 2),
            'refundedAmount': round(new_refunded_amount, 2),
            'refundableAmount': max(0.0, round(order_amount - new_refunded_amount, 2)),
            'rollback': rollback_detail,
            'gateway': refund_gateway_detail,
        },
        message='退款处理完成',
    )

@router.get('/system/order/refund/list')
def list_order_refunds(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(200, ge=1, le=1000),
    orderId: str = Query('', description='按订单ID过滤'),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    if str(orderId or '').strip():
        where.append('r.order_id = ?')
        params.append(str(orderId).strip())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT r.id, r.order_id, r.order_no, r.account_id, r.provider,
                       r.amount, r.currency, r.status, r.reason, r.external_refund_no,
                       r.created_at,
                       u.username, u.real_name, u.email
                FROM order_refunds r
                LEFT JOIN user_accounts u ON u.id = r.account_id
                {where_sql}
                ORDER BY datetime(r.created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'orderId': r['order_id'],
                'orderNo': r['order_no'],
                'accountId': r['account_id'],
                'username': r['username'] or '',
                'realName': r['real_name'] or '',
                'email': r['email'] or '',
                'provider': r['provider'],
                'amount': float(r['amount'] or 0),
                'currency': r['currency'],
                'status': r['status'],
                'reason': r['reason'] or '',
                'externalRefundNo': r['external_refund_no'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/order/state-events')
def list_order_state_events(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(200, ge=1, le=1000),
    orderId: str = Query('', description='按订单ID过滤'),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    if str(orderId or '').strip():
        where.append('e.order_id = ?')
        params.append(str(orderId).strip())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT e.id, e.order_id, e.order_no, e.from_status, e.to_status,
                       e.reason, e.actor_account_id, e.source, e.detail, e.created_at,
                       u.username AS actor_username
                FROM order_state_events e
                LEFT JOIN user_accounts u ON u.id = e.actor_account_id
                {where_sql}
                ORDER BY datetime(e.created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'orderId': r['order_id'],
                'orderNo': r['order_no'],
                'fromStatus': r['from_status'],
                'toStatus': r['to_status'],
                'reason': r['reason'] or '',
                'actorAccountId': r['actor_account_id'] or '',
                'actorUsername': r['actor_username'] or '',
                'source': r['source'] or '',
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )
