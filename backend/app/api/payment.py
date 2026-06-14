from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import *  # noqa: F401,F403
from app.api.member import _handle_payment_callback

router = APIRouter()

@router.get('/system/payment-settings')
def get_payment_settings(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    return _ok(_get_payment_settings(mask_secret=False))

@router.post('/system/payment-settings/save')
def save_payment_settings(body: PaymentSettingsBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    settings = _coerce_payment_settings_from_body(body)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                UPDATE payment_settings
                SET
                    alipay_enabled = ?,
                    alipay_app_id = ?,
                    alipay_merchant_id = ?,
                    alipay_app_private_key = ?,
                    alipay_public_key = ?,
                    alipay_gateway = ?,
                    alipay_notify_url = ?,
                    alipay_return_url = ?,
                    alipay_sign_type = ?,
                    wechat_enabled = ?,
                    wechat_app_id = ?,
                    wechat_merchant_id = ?,
                    wechat_api_v3_key = ?,
                    wechat_private_key = ?,
                    wechat_serial_no = ?,
                    wechat_gateway = ?,
                    wechat_notify_url = ?,
                    wechat_return_url = ?,
                    payment_alert_enabled = ?,
                    payment_alert_emails = ?,
                    payment_alert_webhook = ?,
                    updated_at = ?
                WHERE id = 1
                ''',
                (
                    1 if settings['alipayEnabled'] else 0,
                    settings['alipayAppId'],
                    settings['alipayMerchantId'],
                    settings['alipayAppPrivateKey'],
                    settings['alipayPublicKey'],
                    settings['alipayGateway'],
                    settings['alipayNotifyUrl'],
                    settings['alipayReturnUrl'],
                    settings['alipaySignType'],
                    1 if settings['wechatEnabled'] else 0,
                    settings['wechatAppId'],
                    settings['wechatMerchantId'],
                    settings['wechatApiV3Key'],
                    settings['wechatPrivateKey'],
                    settings['wechatSerialNo'],
                    settings['wechatGateway'],
                    settings['wechatNotifyUrl'],
                    settings['wechatReturnUrl'],
                    1 if settings['paymentAlertEnabled'] else 0,
                    settings['paymentAlertEmails'],
                    settings['paymentAlertWebhook'],
                    _now_str(),
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'payment.settings.save',
                'payment_settings',
                '1',
                {
                    'alipayEnabled': settings['alipayEnabled'],
                    'wechatEnabled': settings['wechatEnabled'],
                    'alipayMerchantId': settings['alipayMerchantId'],
                    'wechatMerchantId': settings['wechatMerchantId'],
                    'paymentAlertEnabled': settings['paymentAlertEnabled'],
                    'paymentAlertEmails': settings['paymentAlertEmails'],
                },
            )
            conn.commit()

    return _ok(True, message='支付设置已保存')

@router.post('/system/payment/test-pay')
def test_payment(body: PaymentTestPayBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    provider = str(body.provider or '').strip().lower()
    if provider not in ('alipay', 'wechat'):
        return _fail('仅支持 alipay 或 wechat')

    amount = float(body.amount or 0)
    if amount <= 0:
        return _fail('测试金额必须大于 0')

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
        required = ['wechatAppId', 'wechatMerchantId', 'wechatApiV3Key', 'wechatPrivateKey', 'wechatSerialNo']
        missing = [key for key in required if not str(settings.get(key) or '').strip()]
        if missing:
            return _fail(f'微信配置不完整: {", ".join(missing)}')

    now = datetime.now()
    now_str = _now_str()
    subject = str(body.description or '支付配置测试').strip() or '支付配置测试'

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row

            # 确保测试套餐存在
            test_plan_code = 'test_pay_001'
            plan_row = _resolve_plan(conn, test_plan_code)
            if not plan_row:
                conn.execute(
                    '''
                    INSERT INTO plans (
                        id, code, name, price, duration_days, level, status, description, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        uuid.uuid4().hex,
                        test_plan_code,
                        '支付联调测试套餐',
                        0.01,
                        1,
                        'basic',
                        'active',
                        '仅用于支付联调测试，不计入正式会员权益',
                        now_str,
                        now_str,
                    ),
                )

            # 生成测试订单
            order_id = uuid.uuid4().hex
            order_no = _generate_unique_order_no(conn, 'TORD')
            note = '[TEST_PAY] 支付配置测试订单'
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
                    str(user.get('id') or ''),
                    test_plan_code,
                    round(amount, 2),
                    str(body.currency or 'CNY').strip() or 'CNY',
                    provider,
                    expire_at,
                    note,
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
                actor_account_id=str(user.get('id') or ''),
                reason='test pay create',
                source='system.payment.test_pay',
                detail={'provider': provider, 'amount': round(amount, 2), 'isTestOrder': True},
            )

            # 生成测试交易
            out_trade_no = _generate_unique_out_trade_no(conn, 'TPAY')
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
                    str(user.get('id') or ''),
                    provider,
                    out_trade_no,
                    round(amount, 2),
                    str(body.currency or 'CNY').strip() or 'CNY',
                    str(user.get('id') or ''),
                    now_str,
                    now_str,
                ),
            )

            gateway = settings.get('alipayGateway') if provider == 'alipay' else settings.get('wechatGateway')
            qr_code = ''
            request_payload: dict[str, Any] = {}
            biz_response: dict[str, Any] = {}

            try:
                if provider == 'alipay':
                    biz_response, qr_code, request_payload = _alipay_precreate(
                        settings,
                        out_trade_no=out_trade_no,
                        amount=round(amount, 2),
                        subject=subject,
                        body='AiceMind 支付联调测试订单',
                    )
                else:
                    biz_response, qr_code, request_payload = _wechat_native_precreate(
                        settings,
                        out_trade_no=out_trade_no,
                        amount=round(amount, 2),
                        subject=subject,
                        currency=str(body.currency or 'CNY').strip() or 'CNY',
                    )
            except Exception as e:
                conn.execute(
                    "UPDATE payment_trades SET status = 'failed', updated_at = ? WHERE id = ?",
                    (_now_str(), trade_id),
                )
                conn.commit()
                provider_label = '支付宝' if provider == 'alipay' else '微信支付'
                return _fail(f'{provider_label}预下单失败: {e}')

            conn.execute(
                'UPDATE payment_trades SET callback_payload = ?, updated_at = ? WHERE id = ?',
                (
                    json.dumps(
                        {
                            'request': request_payload,
                            'gatewayResponse': biz_response,
                            'isTestPay': True,
                        },
                        ensure_ascii=False,
                    ),
                    _now_str(),
                    trade_id,
                ),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'payment.test_pay',
                'payment_trade',
                trade_id,
                {
                    'provider': provider,
                    'amount': round(amount, 2),
                    'tradeId': trade_id,
                    'orderId': order_id,
                    'orderNo': order_no,
                    'outTradeNo': out_trade_no,
                    'isTestOrder': True,
                },
            )
            conn.commit()

    result = {
        'provider': provider,
        'amount': round(amount, 2),
        'currency': str(body.currency or 'CNY').strip() or 'CNY',
        'orderId': order_id,
        'orderNo': order_no,
        'tradeId': trade_id,
        'outTradeNo': out_trade_no,
        'gateway': gateway,
        'requestPayload': request_payload,
        'qrCode': qr_code,
        'isTestOrder': True,
        'message': '测试订单已生成，请扫码完成支付',
    }
    return _ok(result)

@router.post('/system/payment/initiate')
def initiate_payment(body: PaymentInitiateBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    provider = str(body.provider or '').strip().lower()
    if provider not in ('alipay', 'wechat'):
        return _fail('仅支持 alipay 或 wechat')

    order_id = str(body.orderId or '').strip()
    if not order_id:
        return _fail('缺少订单ID')

    settings = _get_payment_settings(mask_secret=False)

    if provider == 'alipay':
        if not settings.get('alipayEnabled'):
            return _fail('支付宝支付尚未启用')
        required = ['alipayAppId', 'alipayMerchantId', 'alipayAppPrivateKey', 'alipayPublicKey']
        missing = [key for key in required if not str(settings.get(key) or '').strip()]
        if missing:
            return _fail(f'支付宝配置不完整: {", ".join(missing)}')
        secret = str(settings.get('alipayAppPrivateKey') or '')
    else:
        if not settings.get('wechatEnabled'):
            return _fail('微信支付尚未启用')
        required = ['wechatAppId', 'wechatMerchantId', 'wechatApiV3Key', 'wechatPrivateKey', 'wechatSerialNo']
        missing = [key for key in required if not str(settings.get(key) or '').strip()]
        if missing:
            return _fail(f'微信配置不完整: {", ".join(missing)}')
        secret = str(settings.get('wechatApiV3Key') or '')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            order_row = conn.execute(
                '''
                SELECT id, order_no, account_id, amount, currency, status
                FROM orders
                WHERE id = ?
                LIMIT 1
                ''',
                (order_id,),
            ).fetchone()
            if not order_row:
                return _fail('订单不存在')

            if str(order_row['status'] or '') == 'paid':
                return _fail('订单已支付，无需发起支付')

            amount = float(order_row['amount'] or 0)
            if amount <= 0:
                return _fail('订单金额必须大于 0')

            out_trade_no = _generate_unique_out_trade_no(conn, f"{provider[:1].upper()}PAY")
            trade_id = uuid.uuid4().hex
            now_str = _now_str()
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
                    str(order_row['id'] or ''),
                    str(order_row['order_no'] or ''),
                    str(order_row['account_id'] or ''),
                    provider,
                    out_trade_no,
                    amount,
                    str(order_row['currency'] or 'CNY'),
                    str(body.payerId or '').strip(),
                    now_str,
                    now_str,
                ),
            )

            trade_row = conn.execute('SELECT * FROM payment_trades WHERE id = ? LIMIT 1', (trade_id,)).fetchone()
            request_payload = _build_payment_request_payload(provider, trade_row, settings)
            request_payload['sign'] = _sign_payload(request_payload, secret)

            conn.execute(
                'UPDATE payment_trades SET callback_payload = ?, updated_at = ? WHERE id = ?',
                (json.dumps({'request': request_payload}, ensure_ascii=False), _now_str(), trade_id),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'payment.initiate',
                'payment_trade',
                trade_id,
                {'provider': provider, 'orderId': order_id, 'outTradeNo': out_trade_no},
            )
            conn.commit()

    gateway = settings.get('alipayGateway') if provider == 'alipay' else settings.get('wechatGateway')
    return _ok(
        {
            'tradeId': trade_id,
            'orderId': order_id,
            'provider': provider,
            'outTradeNo': out_trade_no,
            'gateway': gateway,
            'requestPayload': request_payload,
        },
        message='支付已发起，请将请求提交到第三方网关',
    )

@router.get('/system/payment/trade/list')
def list_payment_trades(
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
                SELECT
                    t.id, t.order_id, t.order_no, t.account_id, t.provider,
                    t.out_trade_no, t.amount, t.currency, t.status,
                    t.payer_id, t.gateway_trade_no, t.callback_verified,
                    t.callback_at, t.paid_at, t.created_at,
                    u.username, u.real_name, u.email
                FROM payment_trades t
                LEFT JOIN user_accounts u ON u.id = t.account_id
                ORDER BY datetime(t.created_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
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
                'outTradeNo': r['out_trade_no'],
                'amount': float(r['amount'] or 0),
                'currency': r['currency'],
                'status': r['status'],
                'payerId': r['payer_id'],
                'gatewayTradeNo': r['gateway_trade_no'],
                'callbackVerified': bool(int(r['callback_verified'] or 0)),
                'callbackAt': r['callback_at'],
                'paidAt': r['paid_at'],
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/payment/trade/detail')
def payment_trade_detail(
    authorization: Optional[str] = Header(default=None),
    tradeId: str = Query('', description='交易ID'),
    outTradeNo: str = Query('', description='商户交易号'),
):
    user = _require_user(authorization)
    _require_admin(user)

    trade_id = str(tradeId or '').strip()
    out_trade_no = str(outTradeNo or '').strip()
    if not trade_id and not out_trade_no:
        return _fail('缺少 tradeId 或 outTradeNo')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            if trade_id:
                trade_row = conn.execute(
                    '''
                    SELECT
                        t.id, t.order_id, t.order_no, t.account_id, t.provider,
                        t.out_trade_no, t.amount, t.currency, t.status,
                        t.payer_id, t.gateway_trade_no, t.callback_verified,
                        t.callback_at, t.paid_at, t.created_at,
                        o.status AS order_status, o.paid_at AS order_paid_at, o.note AS order_note
                    FROM payment_trades t
                    LEFT JOIN orders o ON o.id = t.order_id
                    WHERE t.id = ?
                    LIMIT 1
                    ''',
                    (trade_id,),
                ).fetchone()
            else:
                trade_row = conn.execute(
                    '''
                    SELECT
                        t.id, t.order_id, t.order_no, t.account_id, t.provider,
                        t.out_trade_no, t.amount, t.currency, t.status,
                        t.payer_id, t.gateway_trade_no, t.callback_verified,
                        t.callback_at, t.paid_at, t.created_at,
                        o.status AS order_status, o.paid_at AS order_paid_at, o.note AS order_note
                    FROM payment_trades t
                    LEFT JOIN orders o ON o.id = t.order_id
                    WHERE t.out_trade_no = ?
                    LIMIT 1
                    ''',
                    (out_trade_no,),
                ).fetchone()

            # 微信支付测试/普通下单：在前端轮询详情时顺便主动查单，避免完全依赖异步回调
            if trade_row and str(trade_row['provider'] or '').strip().lower() == 'wechat' and str(trade_row['status'] or '').strip().lower() not in {'paid', 'success'}:
                try:
                    settings = _get_payment_settings(mask_secret=False)
                    query_result = _wechat_query_trade(settings, str(trade_row['out_trade_no'] or '').strip())
                    trade_state = str(query_result.get('trade_state') or '').strip().upper()
                    amount_info = query_result.get('amount') if isinstance(query_result.get('amount'), dict) else {}
                    total_cents = amount_info.get('total')
                    paid_amount = float(total_cents or 0) / 100.0
                    if trade_state in {'SUCCESS', 'TRADE_SUCCESS', 'PAID'}:
                        _apply_paid_trade(
                            conn,
                            trade_row,
                            callback_payload=query_result,
                            verified=True,
                            gateway_trade_no=str(query_result.get('transaction_id') or ''),
                            provider_status=trade_state,
                        )
                        conn.commit()
                    elif trade_state in {'CLOSED', 'REVOKED', 'PAYERROR'}:
                        conn.execute(
                            'UPDATE payment_trades SET status = ?, callback_payload = ?, callback_verified = ?, callback_at = ?, gateway_trade_no = ?, updated_at = ? WHERE id = ?',
                            (
                                'failed',
                                json.dumps(query_result, ensure_ascii=False),
                                1,
                                _now_str(),
                                str(query_result.get('transaction_id') or ''),
                                _now_str(),
                                str(trade_row['id'] or ''),
                            ),
                        )
                        conn.commit()
                except Exception:
                    pass

                # 重新读取最新状态，返回给前端轮询
                if trade_id:
                    trade_row = conn.execute(
                        '''
                        SELECT
                            t.id, t.order_id, t.order_no, t.account_id, t.provider,
                            t.out_trade_no, t.amount, t.currency, t.status,
                            t.payer_id, t.gateway_trade_no, t.callback_verified,
                            t.callback_at, t.paid_at, t.created_at,
                            o.status AS order_status, o.paid_at AS order_paid_at, o.note AS order_note
                        FROM payment_trades t
                        LEFT JOIN orders o ON o.id = t.order_id
                        WHERE t.id = ?
                        LIMIT 1
                        ''',
                        (trade_id,),
                    ).fetchone()
                else:
                    trade_row = conn.execute(
                        '''
                        SELECT
                            t.id, t.order_id, t.order_no, t.account_id, t.provider,
                            t.out_trade_no, t.amount, t.currency, t.status,
                            t.payer_id, t.gateway_trade_no, t.callback_verified,
                            t.callback_at, t.paid_at, t.created_at,
                            o.status AS order_status, o.paid_at AS order_paid_at, o.note AS order_note
                        FROM payment_trades t
                        LEFT JOIN orders o ON o.id = t.order_id
                        WHERE t.out_trade_no = ?
                        LIMIT 1
                        ''',
                        (out_trade_no,),
                    ).fetchone()

    if not trade_row:
        return _fail('交易不存在')

    return _ok(
        {
            'id': trade_row['id'],
            'orderId': trade_row['order_id'],
            'orderNo': trade_row['order_no'],
            'accountId': trade_row['account_id'],
            'provider': trade_row['provider'],
            'outTradeNo': trade_row['out_trade_no'],
            'amount': float(trade_row['amount'] or 0),
            'currency': trade_row['currency'],
            'status': trade_row['status'],
            'payerId': trade_row['payer_id'],
            'gatewayTradeNo': trade_row['gateway_trade_no'],
            'callbackVerified': bool(int(trade_row['callback_verified'] or 0)),
            'callbackAt': trade_row['callback_at'],
            'paidAt': trade_row['paid_at'],
            'createdAt': trade_row['created_at'],
            'orderStatus': trade_row['order_status'] or '',
            'orderPaidAt': trade_row['order_paid_at'] or '',
            'orderNote': trade_row['order_note'] or '',
        }
    )

@router.get('/system/payment/event/list')
def list_payment_events(
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
                SELECT id, provider, event_key, out_trade_no, status,
                       verified, processed, processed_message, created_at, updated_at
                FROM payment_events
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'provider': r['provider'],
                'eventKey': r['event_key'],
                'outTradeNo': r['out_trade_no'],
                'status': r['status'],
                'verified': bool(int(r['verified'] or 0)),
                'processed': bool(int(r['processed'] or 0)),
                'processedMessage': r['processed_message'] or '',
                'createdAt': r['created_at'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/payment/alert/list')
def list_payment_alert_logs(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, category, level, title, content, payload,
                       sent_email, sent_webhook, created_at
                FROM payment_alert_logs
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'category': r['category'],
                'level': r['level'],
                'title': r['title'],
                'content': r['content'],
                'payload': json.loads(r['payload'] or '{}') if str(r['payload'] or '').strip() else {},
                'sentEmail': bool(int(r['sent_email'] or 0)),
                'sentWebhook': bool(int(r['sent_webhook'] or 0)),
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/payment/alert/test')
def send_payment_alert_test(body: PaymentAlertTestBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    title = str(body.title or '').strip() or '支付告警测试'
    content = str(body.content or '').strip() or '这是一条支付告警测试消息。'
    level = str(body.level or 'warning').strip() or 'warning'

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            result = _notify_payment_alert(
                conn,
                category='payment_test',
                title=title,
                content=content,
                payload={'operator': str(user.get('username') or user.get('id') or '')},
                level=level,
                force=True,
            )
            conn.commit()

    return _ok(result, message='测试告警已触发')

@router.post('/system/payment/reconcile/run')
def run_payment_reconcile(body: PaymentReconcileRunBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    provider = str(body.provider or 'alipay').strip().lower() or 'alipay'
    reconcile_date = str(body.reconcileDate or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            try:
                summary = _run_payment_reconcile(
                    conn,
                    provider=provider,
                    reconcile_date=reconcile_date,
                    actor_account_id=str(user.get('id') or ''),
                )
                alert_result = None
                if int(summary.get('mismatchCount') or 0) > 0:
                    title = f"[支付对账告警] {provider} {summary.get('reconcileDate')} 存在差异"
                    content = (
                        f"对账差异 {summary.get('mismatchCount')} 条\n"
                        f"本地成功: {summary.get('localPaidCount')} / {summary.get('localPaidAmount')}\n"
                        f"回调成功: {summary.get('callbackPaidCount')} / {summary.get('callbackPaidAmount')}"
                    )
                    alert_result = _notify_payment_alert(
                        conn,
                        category='payment_reconcile',
                        title=title,
                        content=content,
                        payload=summary,
                        level='warning',
                    )
                conn.commit()
            except ValueError as e:
                return _fail(str(e))

    if alert_result is not None:
        summary['alert'] = alert_result
    return _ok(summary, message='支付对账完成')

@router.get('/system/payment/reconcile/list')
def list_payment_reconcile_runs(
    authorization: Optional[str] = Header(default=None),
    provider: str = Query('', description='支付渠道'),
    limit: int = Query(30, ge=1, le=200),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    provider_key = str(provider or '').strip().lower()
    if provider_key:
        where.append('provider = ?')
        params.append(provider_key)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT id, provider, reconcile_date,
                       local_paid_count, local_paid_amount,
                       callback_paid_count, callback_paid_amount,
                       mismatch_count, status, detail, created_at, updated_at
                FROM payment_reconcile_runs
                {where_sql}
                ORDER BY reconcile_date DESC, datetime(created_at) DESC
                LIMIT ?
                ''',
                (*params, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'provider': r['provider'],
                'reconcileDate': r['reconcile_date'],
                'localPaidCount': int(r['local_paid_count'] or 0),
                'localPaidAmount': float(r['local_paid_amount'] or 0),
                'callbackPaidCount': int(r['callback_paid_count'] or 0),
                'callbackPaidAmount': float(r['callback_paid_amount'] or 0),
                'mismatchCount': int(r['mismatch_count'] or 0),
                'status': r['status'],
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/payment/reconcile/items')
def list_payment_reconcile_items(
    authorization: Optional[str] = Header(default=None),
    runId: str = Query(..., description='对账运行ID'),
    limit: int = Query(500, ge=1, le=2000),
):
    user = _require_user(authorization)
    _require_admin(user)

    run_id = str(runId or '').strip()
    if not run_id:
        return _fail('缺少 runId')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, run_id, item_type, provider,
                       out_trade_no, order_no, local_amount, callback_amount,
                       detail, created_at
                FROM payment_reconcile_items
                WHERE run_id = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (run_id, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'runId': r['run_id'],
                'itemType': r['item_type'],
                'provider': r['provider'],
                'outTradeNo': r['out_trade_no'],
                'orderNo': r['order_no'],
                'localAmount': float(r['local_amount'] or 0),
                'callbackAmount': float(r['callback_amount'] or 0),
                'detail': r['detail'] or '',
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/payment/repair')
def repair_payment_by_out_trade_no(body: PaymentRepairBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    out_trade_no = str(body.outTradeNo or '').strip()
    provider = str(body.provider or 'alipay').strip().lower() or 'alipay'
    if not out_trade_no:
        return _fail('缺少 outTradeNo')
    if provider != 'alipay':
        return _fail('当前仅支持支付宝补单')

    settings = _get_payment_settings(mask_secret=False)
    required = ['alipayAppId', 'alipayMerchantId', 'alipayAppPrivateKey', 'alipayPublicKey']
    missing = [key for key in required if not str(settings.get(key) or '').strip()]
    if missing:
        return _fail(f'支付宝配置不完整: {", ".join(missing)}')

    try:
        _, biz = _alipay_trade_query(settings, out_trade_no)
    except Exception as e:
        return _fail(f'支付宝查单失败: {e}')

    if str(biz.get('code') or '').strip() != '10000':
        msg = str(biz.get('sub_msg') or biz.get('msg') or '支付宝查单失败')
        return _fail(msg)

    trade_status = str(biz.get('trade_status') or '').strip().upper()
    gateway_trade_no = str(biz.get('trade_no') or '').strip()
    amount_text = str(biz.get('total_amount') or '').strip()
    try:
        gateway_amount = float(amount_text) if amount_text else 0.0
    except Exception:
        gateway_amount = 0.0

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row

            trade_row = conn.execute(
                'SELECT * FROM payment_trades WHERE out_trade_no = ? AND provider = ? LIMIT 1',
                (out_trade_no, provider),
            ).fetchone()
            if not trade_row:
                return _fail('本地交易不存在，无法补单')

            local_amount = float(trade_row['amount'] or 0)
            if gateway_amount > 0 and abs(local_amount - gateway_amount) > 0.01:
                return _fail(f'金额不一致，本地 {local_amount:.2f} / 网关 {gateway_amount:.2f}')

            is_success = trade_status in {'TRADE_SUCCESS', 'TRADE_FINISHED'}
            if is_success:
                _apply_paid_trade(
                    conn,
                    trade_row,
                    callback_payload={
                        'source': 'manual_repair',
                        'provider': provider,
                        'out_trade_no': out_trade_no,
                        'queryResponse': biz,
                    },
                    verified=True,
                    gateway_trade_no=gateway_trade_no,
                    provider_status=trade_status or 'TRADE_SUCCESS',
                )
                result_status = 'repaired_paid'
            else:
                result_status = f'not_paid:{trade_status or "UNKNOWN"}'

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'payment.repair',
                'payment_trade',
                str(trade_row['id'] or ''),
                {
                    'provider': provider,
                    'outTradeNo': out_trade_no,
                    'tradeStatus': trade_status,
                    'resultStatus': result_status,
                    'gatewayTradeNo': gateway_trade_no,
                },
            )
            conn.commit()

    return _ok(
        {
            'provider': provider,
            'outTradeNo': out_trade_no,
            'tradeStatus': trade_status,
            'gatewayTradeNo': gateway_trade_no,
            'resultStatus': result_status,
            'isPaid': result_status == 'repaired_paid',
            'gatewayResponse': biz,
        }
    )

async def _safe_payment_callback(provider: str, request: Request):
    provider_key = str(provider or '').strip().lower()
    try:
        result = await _handle_payment_callback(provider_key, request)
    except Exception as e:
        logger.exception('payment callback error: provider=%s', provider_key)
        try:
            with _DB_LOCK:
                _ensure_db()
                with _db_connect() as conn:
                    _notify_payment_alert(
                        conn,
                        category='payment_callback_failed',
                        title='[支付回调告警] 回调处理异常',
                        content=f'provider={provider_key} error={e}',
                        payload={'provider': provider_key, 'error': str(e)},
                        level='error',
                    )
                    conn.commit()
        except Exception:
            logger.exception('payment callback alert log failed: provider=%s', provider_key)

        # 支付宝要求回调应答为 success；异常时也给 200 防止网关无限重试
        if provider_key == 'alipay':
            return PlainTextResponse('success', status_code=200)

        if provider_key == 'wechat':
            return JSONResponse({'code': 'FAIL', 'message': 'callback accepted with error'}, status_code=200)

        return _ok({'accepted': True, 'provider': provider_key, 'error': str(e)}, message='callback accepted with error')

    # 支付宝要求 success 纯文本应答，避免网关重试风暴
    if provider_key == 'alipay':
        return PlainTextResponse('success', status_code=200)

    if provider_key == 'wechat':
        return JSONResponse({'code': 'SUCCESS', 'message': '成功'}, status_code=200)

    return result


@router.post('/payment/callback/alipay')
async def payment_callback_alipay(request: Request):
    return await _safe_payment_callback('alipay', request)


@router.get('/payment/callback/alipay')
async def payment_callback_alipay_get(request: Request):
    return await _safe_payment_callback('alipay', request)


@router.post('/payment/callback/wechat')
async def payment_callback_wechat(request: Request):
    return await _safe_payment_callback('wechat', request)


@router.get('/payment/callback/wechat')
async def payment_callback_wechat_get(request: Request):
    return await _safe_payment_callback('wechat', request)

@router.get('/commerce/payment/status')
def commerce_payment_status(
    authorization: Optional[str] = Header(default=None),
    tradeId: str = Query('', description='交易ID'),
    outTradeNo: str = Query('', description='商户交易号'),
):
    user = _require_user(authorization)

    account_id = str(user.get('id') or '').strip()
    trade_id = str(tradeId or '').strip()
    out_trade_no = str(outTradeNo or '').strip()
    if not trade_id and not out_trade_no:
        return _fail('缺少 tradeId 或 outTradeNo')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _close_timeout_orders(conn, account_id=account_id)

            if trade_id:
                trade_row = conn.execute(
                    '''
                    SELECT t.id, t.order_id, t.order_no, t.account_id, t.provider,
                           t.out_trade_no, t.amount, t.currency, t.status,
                           t.gateway_trade_no, t.callback_verified, t.callback_at, t.paid_at, t.created_at,
                           o.status AS order_status, o.paid_at AS order_paid_at, o.expire_at AS order_expire_at,
                           p.name AS plan_name, p.code AS plan_code
                    FROM payment_trades t
                    LEFT JOIN orders o ON o.id = t.order_id
                    LEFT JOIN plans p ON p.code = o.plan_code
                    WHERE t.id = ? AND t.account_id = ?
                    LIMIT 1
                    ''',
                    (trade_id, account_id),
                ).fetchone()
            else:
                trade_row = conn.execute(
                    '''
                    SELECT t.id, t.order_id, t.order_no, t.account_id, t.provider,
                           t.out_trade_no, t.amount, t.currency, t.status,
                           t.gateway_trade_no, t.callback_verified, t.callback_at, t.paid_at, t.created_at,
                           o.status AS order_status, o.paid_at AS order_paid_at, o.expire_at AS order_expire_at,
                           p.name AS plan_name, p.code AS plan_code
                    FROM payment_trades t
                    LEFT JOIN orders o ON o.id = t.order_id
                    LEFT JOIN plans p ON p.code = o.plan_code
                    WHERE t.out_trade_no = ? AND t.account_id = ?
                    LIMIT 1
                    ''',
                    (out_trade_no, account_id),
                ).fetchone()

            if not trade_row:
                return _fail('交易不存在')

            trade_status_now = str(trade_row['status'] or '').strip().lower()
            order_status_now = str(trade_row['order_status'] or '').strip().lower()
            provider_now = str(trade_row['provider'] or '').strip().lower()
            if provider_now == 'wechat' and trade_status_now != 'paid' and order_status_now != 'paid':
                try:
                    settings = _get_payment_settings(mask_secret=False)
                    query_result = _wechat_query_trade(settings, str(trade_row['out_trade_no'] or '').strip())
                    gateway_state = str(query_result.get('trade_state') or '').strip().upper()
                    if gateway_state == 'SUCCESS':
                        _apply_paid_trade(
                            conn,
                            trade_row,
                            callback_payload={
                                'source': 'status_query_repair',
                                'provider': 'wechat',
                                'out_trade_no': str(trade_row['out_trade_no'] or ''),
                                'queryResponse': query_result,
                            },
                            verified=True,
                            gateway_trade_no=str(query_result.get('transaction_id') or ''),
                            provider_status='SUCCESS',
                        )
                        conn.commit()
                        if trade_id:
                            trade_row = conn.execute(
                                '''
                                SELECT t.id, t.order_id, t.order_no, t.account_id, t.provider,
                                       t.out_trade_no, t.amount, t.currency, t.status,
                                       t.gateway_trade_no, t.callback_verified, t.callback_at, t.paid_at, t.created_at,
                                       o.status AS order_status, o.paid_at AS order_paid_at, o.expire_at AS order_expire_at,
                                       p.name AS plan_name, p.code AS plan_code
                                FROM payment_trades t
                                LEFT JOIN orders o ON o.id = t.order_id
                                LEFT JOIN plans p ON p.code = o.plan_code
                                WHERE t.id = ? AND t.account_id = ?
                                LIMIT 1
                                ''',
                                (trade_id, account_id),
                            ).fetchone()
                        else:
                            trade_row = conn.execute(
                                '''
                                SELECT t.id, t.order_id, t.order_no, t.account_id, t.provider,
                                       t.out_trade_no, t.amount, t.currency, t.status,
                                       t.gateway_trade_no, t.callback_verified, t.callback_at, t.paid_at, t.created_at,
                                       o.status AS order_status, o.paid_at AS order_paid_at, o.expire_at AS order_expire_at,
                                       p.name AS plan_name, p.code AS plan_code
                                FROM payment_trades t
                                LEFT JOIN orders o ON o.id = t.order_id
                                LEFT JOIN plans p ON p.code = o.plan_code
                                WHERE t.out_trade_no = ? AND t.account_id = ?
                                LIMIT 1
                                ''',
                                (out_trade_no, account_id),
                            ).fetchone()
                except Exception:
                    logger.exception('wechat status query repair failed: out_trade_no=%s', str(trade_row['out_trade_no'] or ''))

            event_row = conn.execute(
                '''
                SELECT status, verified, processed, processed_message, created_at
                FROM payment_events
                WHERE out_trade_no = ? AND provider = ?
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                ''',
                (str(trade_row['out_trade_no'] or ''), str(trade_row['provider'] or '')),
            ).fetchone()

    trade_status = str(trade_row['status'] or '').strip().lower()
    order_status = str(trade_row['order_status'] or '').strip().lower()
    is_paid = trade_status == 'paid' or order_status == 'paid'
    order_expire_at = str(trade_row['order_expire_at'] or '').strip()
    is_expired = (not is_paid) and (trade_status == 'timeout' or order_status == 'cancelled')

    billing_context: dict[str, Any] = {}
    try:
        billing_context = get_billing_context(account_id)
    except Exception:
        billing_context = {}

    entitlement = billing_context.get('entitlement') or {}
    policy = billing_context.get('policy') or {}
    rights_tips = _build_policy_rights_tips(policy) if is_paid else []

    return _ok(
        {
            'tradeId': trade_row['id'],
            'orderId': trade_row['order_id'],
            'orderNo': trade_row['order_no'],
            'provider': trade_row['provider'],
            'outTradeNo': trade_row['out_trade_no'],
            'amount': float(trade_row['amount'] or 0),
            'currency': trade_row['currency'] or 'CNY',
            'tradeStatus': trade_row['status'],
            'orderStatus': trade_row['order_status'] or '',
            'expireAt': order_expire_at,
            'isExpired': is_expired,
            'canRetry': bool(is_expired and not is_paid),
            'paidAt': trade_row['paid_at'] or trade_row['order_paid_at'] or '',
            'callbackAt': trade_row['callback_at'] or '',
            'callbackVerified': bool(int(trade_row['callback_verified'] or 0)),
            'gatewayTradeNo': trade_row['gateway_trade_no'] or '',
            'planCode': trade_row['plan_code'] or '',
            'planName': trade_row['plan_name'] or trade_row['plan_code'] or '',
            'isPaid': is_paid,
            'entitlement': entitlement,
            'policy': policy,
            'rightsTips': rights_tips,
            'activationMessage': '支付成功，套餐权益已下发并立即生效' if is_paid else '',
            'expireMessage': '订单已过期，请重新下单' if is_expired else '',
            'event': {
                'status': event_row['status'] if event_row else '',
                'verified': bool(int(event_row['verified'] or 0)) if event_row else False,
                'processed': bool(int(event_row['processed'] or 0)) if event_row else False,
                'processedMessage': event_row['processed_message'] if event_row else '',
                'createdAt': event_row['created_at'] if event_row else '',
            },
        }
    )
