from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.get('/system/security/sessions')
def list_security_sessions(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user = _require_user(authorization)
    _require_admin(user)

    current_digest = ''
    if authorization and authorization.lower().startswith('bearer '):
        current_digest = _token_digest(authorization.split(' ', 1)[1].strip())

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT s.id, s.account_id, s.created_at, s.expire_at, s.revoked_at,
                       s.last_active_at, s.token_digest,
                       u.username, u.real_name, u.email
                FROM auth_sessions s
                LEFT JOIN user_accounts u ON u.id = s.account_id
                ORDER BY datetime(s.last_active_at) DESC
                LIMIT ? OFFSET ?
                ''',
                (int(limit), int(offset)),
            ).fetchall()

            result = []
            now = datetime.now()
            for row in rows:
                expire_dt = _parse_dt(str(row['expire_at'] or ''))
                revoked_at = str(row['revoked_at'] or '')
                result.append(
                    {
                        'id': row['id'],
                        'accountId': row['account_id'],
                        'username': row['username'] or '',
                        'realName': row['real_name'] or '',
                        'email': row['email'] or '',
                        'createdAt': row['created_at'],
                        'lastActiveAt': row['last_active_at'],
                        'expireAt': row['expire_at'],
                        'revokedAt': revoked_at,
                        'isRevoked': bool(revoked_at),
                        'isExpired': bool(expire_dt and expire_dt <= now),
                        'isCurrent': bool(current_digest) and str(row['token_digest'] or '') == current_digest,
                    }
                )

    return _ok(result)

@router.post('/system/security/revoke-session')
def revoke_security_session(body: RevokeSessionBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    session_id = str(body.sessionId or '').strip()
    if not session_id:
        return _fail('缺少会话ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT id, account_id, revoked_at FROM auth_sessions WHERE id = ? LIMIT 1', (session_id,)).fetchone()
            if not row:
                return _fail('会话不存在')
            if str(row['revoked_at'] or '').strip():
                return _ok(True, message='会话已失效')

            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE id = ? AND revoked_at = ''",
                (_now_str(), _now_str(), session_id),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'security.revoke_session',
                'session',
                session_id,
                {'accountId': row['account_id']},
            )
            conn.commit()

    return _ok(True)

@router.post('/system/security/revoke-account-sessions')
def revoke_account_sessions(body: RevokeAccountSessionsBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    if not account_id:
        return _fail('缺少账号ID')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            account = conn.execute(
                'SELECT id, username FROM user_accounts WHERE id = ? LIMIT 1',
                (account_id,),
            ).fetchone()
            if not account:
                return _fail('账号不存在')

            now = _now_str()
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE account_id = ? AND revoked_at = ''",
                (now, now, account_id),
            )
            affected = int(conn.total_changes or 0)
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'security.revoke_account_sessions',
                'account',
                account_id,
                {'username': account['username'], 'affectedSessions': affected},
            )
            conn.commit()

    for token, snapshot in list(_ADMIN_TOKENS.items()):
        if str(snapshot.get('id') or '') == account_id:
            _ADMIN_TOKENS.pop(token, None)

    return _ok({'affectedSessions': affected}, message='账号会话已全部撤销')

@router.post('/system/security/unlock-login-attempt')
def unlock_login_attempt(body: UnlockLoginAttemptBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    login_key = _normalize_login_key(body.loginKey)
    if not login_key:
        return _fail('缺少登录标识')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            row = conn.execute(
                'SELECT login_key FROM login_attempts WHERE login_key = ? LIMIT 1',
                (login_key,),
            ).fetchone()
            if not row:
                return _ok(True, message='该登录标识未被锁定')

            _clear_login_failures(conn, login_key)
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'security.unlock_login_attempt',
                'login_attempt',
                login_key,
                {},
            )
            conn.commit()

    return _ok(True, message='登录限制已解除')

@router.get('/system/security/login-attempts')
def list_login_attempts(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=500),
):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT login_key, fail_count, first_fail_at, locked_until, updated_at
                FROM login_attempts
                WHERE fail_count > 0
                ORDER BY datetime(updated_at) DESC
                LIMIT ?
                ''',
                (int(limit),),
            ).fetchall()

    return _ok(
        [
            {
                'loginKey': r['login_key'],
                'failCount': int(r['fail_count'] or 0),
                'firstFailAt': r['first_fail_at'],
                'lockedUntil': r['locked_until'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/security/login-risk-events')
def list_login_risk_events(
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
                SELECT id, account_id, username, login_ip, user_agent,
                       risk_level, risk_reason, city_hint, notified, created_at
                FROM login_risk_events
                ORDER BY datetime(created_at) DESC
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
                'loginIp': r['login_ip'] or '',
                'userAgent': r['user_agent'] or '',
                'riskLevel': r['risk_level'] or 'low',
                'riskReason': r['risk_reason'] or '',
                'cityHint': r['city_hint'] or '',
                'notified': bool(int(r['notified'] or 0)),
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )

@router.get('/system/security/policy')
def get_security_policy(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)
    return _ok(_get_security_policy())

@router.post('/system/security/policy/save')
def save_security_policy(body: SecurityPolicyBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    policy = _coerce_security_policy_from_body(body)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                UPDATE security_policy
                SET password_min_length = ?,
                    password_require_letter = ?,
                    password_require_digit = ?,
                    password_require_special = ?,
                    login_fail_max = ?,
                    login_fail_window_minutes = ?,
                    login_lock_minutes = ?,
                    session_ttl_hours = ?,
                    force_logout_on_password_reset = ?,
                    updated_at = ?
                WHERE id = 1
                ''',
                (
                    int(policy['passwordMinLength']),
                    1 if policy['passwordRequireLetter'] else 0,
                    1 if policy['passwordRequireDigit'] else 0,
                    1 if policy['passwordRequireSpecial'] else 0,
                    int(policy['loginFailMax']),
                    int(policy['loginFailWindowMinutes']),
                    int(policy['loginLockMinutes']),
                    int(policy['sessionTtlHours']),
                    1 if policy['forceLogoutOnPasswordReset'] else 0,
                    _now_str(),
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'security.policy.save',
                'security_policy',
                '1',
                policy,
            )
            conn.commit()

    return _ok(policy, message='安全策略已保存')

@router.post('/system/security/reset-password')
def reset_account_password(body: ResetPasswordBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    new_password = str(body.newPassword or '')
    if not account_id:
        return _fail('缺少账号ID')

    policy = _get_security_policy()
    pwd_err = _validate_password_with_policy(new_password, policy)
    if pwd_err:
        return _fail(pwd_err)

    policy = _get_security_policy()
    force_logout = bool(policy.get('forceLogoutOnPasswordReset')) if body.forceLogout is None else bool(body.forceLogout)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            account = conn.execute(
                'SELECT id, username FROM user_accounts WHERE id = ? LIMIT 1',
                (account_id,),
            ).fetchone()
            if not account:
                return _fail('账号不存在')

            conn.execute(
                'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
                (_hash_password(new_password), _now_str(), account_id),
            )

            if force_logout:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE account_id = ? AND revoked_at = ''",
                    (_now_str(), _now_str(), account_id),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'security.reset_password',
                'account',
                account_id,
                {'username': account['username'], 'forceLogout': force_logout},
            )
            conn.commit()

    if force_logout:
        for token, snapshot in list(_ADMIN_TOKENS.items()):
            if str(snapshot.get('id') or '') == account_id:
                _ADMIN_TOKENS.pop(token, None)

    return _ok(True, message='密码重置成功')
