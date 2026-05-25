from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post('/account/delete-request')
def create_account_delete_request(body: AccountDeleteRequestBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    account_id = str(user.get('id') or '').strip()
    if not account_id:
        return _fail('账号不存在')

    reason = str(body.reason or '').strip()
    now = _now_str()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            pending = conn.execute(
                '''
                SELECT id FROM account_deletion_requests
                WHERE account_id = ? AND status = 'pending'
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                ''',
                (account_id,),
            ).fetchone()
            if pending:
                return _ok({'requestId': pending['id'], 'status': 'pending'}, message='你已有待处理的注销申请')

            req_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO account_deletion_requests (
                    id, account_id, username, email, reason, status, request_detail,
                    review_note, reviewed_by, reviewed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, '', '', '', ?, ?)
                ''',
                (
                    req_id,
                    account_id,
                    str(user.get('username') or ''),
                    str(user.get('email') or ''),
                    reason,
                    json.dumps({'from': 'self_service'}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            _audit_log(
                conn,
                account_id,
                'account.delete_request.create',
                'account_deletion_requests',
                req_id,
                {'reason': reason},
            )
            conn.commit()

    return _ok({'requestId': req_id, 'status': 'pending'}, message='注销申请已提交')

@router.get('/account/delete-request/list')
def list_my_account_delete_requests(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(20, ge=1, le=100),
):
    user = _require_user(authorization)
    account_id = str(user.get('id') or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, reason, status, request_detail, review_note, reviewed_by, reviewed_at, created_at, updated_at
                FROM account_deletion_requests
                WHERE account_id = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                ''',
                (account_id, int(limit)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'reason': r['reason'] or '',
                'status': r['status'] or '',
                'requestDetail': r['request_detail'] or '',
                'reviewNote': r['review_note'] or '',
                'reviewedBy': r['reviewed_by'] or '',
                'reviewedAt': r['reviewed_at'] or '',
                'createdAt': r['created_at'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.get('/account/data-export')
def export_my_account_data(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    account_id = str(user.get('id') or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            payload = _collect_account_export_payload(conn, account_id)

    return _ok(payload)

@router.get('/system/account/delete-request/list')
def list_account_delete_requests(
    authorization: Optional[str] = Header(default=None),
    status: str = Query('', description='pending/approved/rejected/completed'),
    limit: int = Query(200, ge=1, le=1000),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    status_key = str(status or '').strip().lower()
    if status_key:
        where.append('status = ?')
        params.append(status_key)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT id, account_id, username, email, reason, status,
                       request_detail, review_note, reviewed_by, reviewed_at,
                       created_at, updated_at
                FROM account_deletion_requests
                {where_sql}
                ORDER BY datetime(created_at) DESC
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
                'email': r['email'] or '',
                'reason': r['reason'] or '',
                'status': r['status'] or '',
                'requestDetail': r['request_detail'] or '',
                'reviewNote': r['review_note'] or '',
                'reviewedBy': r['reviewed_by'] or '',
                'reviewedAt': r['reviewed_at'] or '',
                'createdAt': r['created_at'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.post('/system/account/delete-request/process')
def process_account_delete_request(body: AccountDeleteProcessBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    req_id = str(body.requestId or '').strip()
    action = str(body.action or '').strip().lower()
    note = str(body.note or '').strip()
    if not req_id:
        return _fail('缺少 requestId')
    if action not in {'approve', 'reject', 'complete'}:
        return _fail('action 必须是 approve/reject/complete')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM account_deletion_requests WHERE id = ? LIMIT 1',
                (req_id,),
            ).fetchone()
            if not row:
                return _fail('申请不存在')

            current_status = str(row['status'] or '').strip().lower()
            if current_status in {'rejected', 'completed'}:
                return _fail('该申请已结束，不能重复处理')

            next_status = {'approve': 'approved', 'reject': 'rejected', 'complete': 'completed'}[action]

            if action == 'complete':
                if current_status != 'approved':
                    return _fail('仅已批准申请可执行 complete')

                target_account_id = str(row['account_id'] or '').strip()
                target_username = str(row['username'] or '').strip()
                target_email = str(row['email'] or '').strip().lower()

                conn.execute('DELETE FROM subscriptions WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM orders WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM order_refunds WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM payment_trades WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM inapp_notifications WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM points_ledger WHERE account_id = ?', (target_account_id,))
                conn.execute('DELETE FROM auth_sessions WHERE account_id = ?', (target_account_id,))

                if target_username:
                    conn.execute('DELETE FROM member_users WHERE user_id = ?', (target_username,))
                conn.execute('DELETE FROM user_accounts WHERE id = ?', (target_account_id,))

                # best effort 清理邮箱验证码
                if target_email:
                    conn.execute('DELETE FROM email_codes WHERE lower(email) = ?', (target_email,))

                for token, snapshot in list(_ADMIN_TOKENS.items()):
                    if str(snapshot.get('id') or '') == target_account_id:
                        _ADMIN_TOKENS.pop(token, None)

            conn.execute(
                '''
                UPDATE account_deletion_requests
                SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                ''',
                (next_status, note, str(user.get('id') or ''), _now_str(), _now_str(), req_id),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'account.delete_request.process',
                'account_deletion_requests',
                req_id,
                {'action': action, 'note': note},
            )
            conn.commit()

    return _ok(True, message='处理完成')

@router.get('/user/info')
def admin_user_info(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    entitlement = user.get('entitlement') or _load_user_entitlement(user)

    return _ok(
        {
            'userId': user['id'],
            'username': user['username'],
            'realName': user['realName'],
            'roles': user['roles'],
            'email': user['email'],
            'homePath': user['homePath'],
            'avatar': f"https://avatar.vercel.sh/{user['username']}",
            'entitlement': entitlement,
        }
    )

@router.get('/menu/all')
def admin_menu_all(authorization: Optional[str] = Header(default=None)):
    user, _ = _require_entitled_user(authorization)

    menus = [_build_dashboard_menu()]
    roles = set(user.get('roles') or [])
    if {'super', 'admin'} & roles:
        menus.append(_build_user_manage_menu())
        system_menu = _build_system_menu()
        system_groups = list(system_menu.get('children') or [])
        if system_groups:
            menus.extend(system_groups)
        else:
            menus.append(system_menu)

    return _ok(menus)

@router.get('/system/account/list')
def list_accounts(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(200, ge=1, le=500),
):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        data = _query_accounts(limit=limit)
    return _ok(data)
