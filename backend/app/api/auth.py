from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post('/auth/login')
def admin_login(body: AdminLoginBody, request: Request):
    login_key = _normalize_login_key(body.username)
    if not login_key:
        return _fail('请输入账号')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            lock_state = _get_login_lock_state(conn, login_key)
            if int(lock_state.get('locked_seconds') or 0) > 0:
                wait_sec = int(lock_state['locked_seconds'])
                wait_min = max(1, (wait_sec + 59) // 60)
                return _fail(f'登录失败次数过多，请 {wait_min} 分钟后再试')

            user = _get_user_by_login(body.username)
            if not user or not _verify_password(body.password, user.get('password') or ''):
                _record_login_failure(conn, login_key)
                if user and str(user.get('id') or '').strip():
                    _audit_log(
                        conn,
                        str(user.get('id') or ''),
                        'auth.login_failed',
                        'account',
                        str(user.get('id') or ''),
                        {'login': body.username},
                    )
                conn.commit()
                return _fail('账号或密码错误')

            _clear_login_failures(conn, login_key)

            sec_row = conn.execute(
                'SELECT totp_enabled, totp_secret, email FROM user_accounts WHERE id = ? LIMIT 1',
                (str(user.get('id') or ''),),
            ).fetchone()
            totp_enabled = bool(int((sec_row['totp_enabled'] if sec_row else 0) or 0))
            totp_secret = str((sec_row['totp_secret'] if sec_row else '') or '').strip()
            if totp_enabled:
                totp_code = str(body.totpCode or '').strip()
                if not _verify_totp_code(totp_secret, totp_code):
                    _audit_log(
                        conn,
                        str(user.get('id') or ''),
                        'auth.login_failed_2fa',
                        'account',
                        str(user.get('id') or ''),
                        {'login': body.username},
                    )
                    conn.commit()
                    return _fail('二步验证码错误')

            if not _is_password_hashed(user.get('password') or ''):
                conn.execute(
                    'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
                    (_hash_password(body.password), _now_str(), user['id']),
                )

            entitlement = _load_user_entitlement(user)

            login_ip = _client_ip_from_request(request)
            user_agent = str((request.headers.get('user-agent') if request else '') or '')[:500]
            risk_level, risk_reason = _detect_login_risk(conn, str(user.get('id') or ''), login_ip, user_agent)
            risk_notified = False
            if risk_level == 'high' and risk_reason:
                email_addr = str((sec_row['email'] if sec_row else user.get('email')) or '').strip().lower()
                if _EMAIL_RE.match(email_addr):
                    email_settings = _get_email_settings(mask_secret=False)
                    err = _validate_email_settings(email_settings)
                    if not err:
                        try:
                            _send_email(
                                email_settings,
                                email_addr,
                                '【AiceMind】异地/新设备登录提醒',
                                f"账号 {user.get('username')} 检测到高风险登录\nIP: {login_ip or '-'}\n设备: {user_agent or '-'}\n原因: {risk_reason}\n时间: {_now_str()}\n\n如非本人，请立刻修改密码并开启2FA。",
                            )
                            risk_notified = True
                        except Exception:
                            risk_notified = False
                _record_login_risk_event(
                    conn,
                    account_id=str(user.get('id') or ''),
                    username=str(user.get('username') or ''),
                    login_ip=login_ip,
                    user_agent=user_agent,
                    risk_level=risk_level,
                    risk_reason=risk_reason,
                    notified=risk_notified,
                )

            token = _create_admin_session(conn, str(user.get('id') or ''))
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'auth.login_success',
                'account',
                str(user.get('id') or ''),
                {
                    'login': body.username,
                    'loginIp': login_ip,
                    'userAgent': user_agent,
                    'riskLevel': risk_level,
                    'riskReason': risk_reason,
                },
            )
            conn.commit()

    user_snapshot = {
        'id': user['id'],
        'username': user['username'],
        'realName': user['realName'],
        'roles': user['roles'],
        'email': user['email'],
        'homePath': user['homePath'],
        'entitlement': entitlement,
    }
    _ADMIN_TOKENS[token] = user_snapshot

    return _ok({'accessToken': token, 'entitlement': entitlement})

@router.post('/auth/logout')
def admin_logout(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.lower().startswith('bearer '):
        token = authorization.split(' ', 1)[1].strip()
        if token:
            with _DB_LOCK:
                _ensure_db()
                with _db_connect() as conn:
                    conn.row_factory = sqlite3.Row
                    row = _query_active_session(conn, token)
                    if row:
                        _revoke_admin_session(conn, token)
                        _audit_log(
                            conn,
                            str(row['account_id'] or ''),
                            'auth.logout',
                            'session',
                            str(row['id'] or ''),
                            {},
                        )
                        conn.commit()
        _ADMIN_TOKENS.pop(token, None)
    return _ok(True)

@router.post('/auth/change-password')
def auth_change_password(body: ChangePasswordBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    old_password = str(body.oldPassword or '')
    new_password = str(body.newPassword or '')
    confirm_password = str(body.confirmPassword or '')

    if not old_password:
        return _fail('请输入旧密码')
    if not new_password:
        return _fail('请输入新密码')
    if new_password != confirm_password:
        return _fail('两次输入的新密码不一致')

    policy = _get_security_policy()
    pwd_err = _validate_password_with_policy(new_password, policy)
    if pwd_err:
        return _fail(pwd_err)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT id, password FROM user_accounts WHERE id = ? LIMIT 1',
                (str(user.get('id') or ''),),
            ).fetchone()
            if not row:
                return _fail('账号不存在')

            if not _verify_password(old_password, str(row['password'] or '')):
                return _fail('旧密码错误')

            if _verify_password(new_password, str(row['password'] or '')):
                return _fail('新密码不能与旧密码相同')

            conn.execute(
                'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
                (_hash_password(new_password), _now_str(), str(row['id'])),
            )

            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE account_id = ? AND revoked_at = ''",
                (_now_str(), _now_str(), str(row['id'])),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'auth.change_password',
                'account',
                str(row['id']),
                {},
            )
            conn.commit()

    for token, snapshot in list(_ADMIN_TOKENS.items()):
        if str(snapshot.get('id') or '') == str(user.get('id') or ''):
            _ADMIN_TOKENS.pop(token, None)

    return _ok(True, message='密码已修改，请重新登录')

@router.get('/auth/2fa/status')
def auth_2fa_status(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT totp_enabled, totp_secret FROM user_accounts WHERE id = ? LIMIT 1',
                (str(user.get('id') or ''),),
            ).fetchone()

    enabled = bool(int((row['totp_enabled'] if row else 0) or 0))
    has_secret = bool(str((row['totp_secret'] if row else '') or '').strip())
    return _ok({'enabled': enabled, 'hasSecret': has_secret})

@router.post('/auth/2fa/setup')
def auth_2fa_setup(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    secret = _totp_random_secret()
    issuer = 'AiceMind'
    label = str(user.get('email') or user.get('username') or 'account').strip()
    otp_uri = f"otpauth://totp/{issuer}:{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                'UPDATE user_accounts SET totp_secret = ?, totp_enabled = 0, updated_at = ? WHERE id = ?',
                (secret, _now_str(), str(user.get('id') or '')),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'auth.2fa.setup',
                'account',
                str(user.get('id') or ''),
                {},
            )
            conn.commit()

    return _ok({'secret': secret, 'otpauthUrl': otp_uri, 'manualKey': secret}, message='请在验证器中添加后完成校验启用')

@router.post('/auth/2fa/enable')
def auth_2fa_enable(body: TwoFAEnableBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    code = str(body.code or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT totp_secret, totp_enabled FROM user_accounts WHERE id = ? LIMIT 1',
                (str(user.get('id') or ''),),
            ).fetchone()
            secret = str((row['totp_secret'] if row else '') or '').strip()
            if not secret:
                return _fail('请先执行2FA初始化')
            if not _verify_totp_code(secret, code):
                return _fail('验证码错误')

            conn.execute(
                'UPDATE user_accounts SET totp_enabled = 1, updated_at = ? WHERE id = ?',
                (_now_str(), str(user.get('id') or '')),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'auth.2fa.enable',
                'account',
                str(user.get('id') or ''),
                {},
            )
            conn.commit()

    return _ok(True, message='2FA 已启用')

@router.post('/auth/2fa/disable')
def auth_2fa_disable(body: TwoFADisableBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    code = str(body.code or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT totp_secret, totp_enabled FROM user_accounts WHERE id = ? LIMIT 1',
                (str(user.get('id') or ''),),
            ).fetchone()
            enabled = bool(int((row['totp_enabled'] if row else 0) or 0))
            secret = str((row['totp_secret'] if row else '') or '').strip()
            if not enabled:
                return _ok(True, message='2FA 当前未启用')
            if not secret or not _verify_totp_code(secret, code):
                return _fail('验证码错误')

            conn.execute(
                'UPDATE user_accounts SET totp_enabled = 0, totp_secret = ?, updated_at = ? WHERE id = ?',
                ('', _now_str(), str(user.get('id') or '')),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'auth.2fa.disable',
                'account',
                str(user.get('id') or ''),
                {},
            )
            conn.commit()

    return _ok(True, message='2FA 已关闭')

@router.post('/auth/send-email-code')
def send_email_code(body: SendEmailCodeBody):
    email = (body.email or '').strip().lower()
    if not _EMAIL_RE.match(email):
        return _fail('请输入有效邮箱')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute(
                'SELECT 1 FROM user_accounts WHERE lower(email) = ?',
                (email,),
            ).fetchone()
            if exists:
                return _fail('该邮箱已注册，请直接登录')

    settings = _get_email_settings(mask_secret=False)
    err = _validate_email_settings(settings)
    if err:
        return _fail(err)

    code = ''.join(str(random.randint(0, 9)) for _ in range(6))
    expire_minutes = 10
    now = datetime.now()
    expire_at = (now + timedelta(minutes=expire_minutes)).strftime('%Y-%m-%d %H:%M:%S')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                INSERT INTO email_codes (
                    id, email, purpose, code, expires_at, used, updated_at, created_at
                ) VALUES (?, ?, 'register', ?, ?, 0, ?, ?)
                ''',
                (uuid.uuid4().hex, email, code, expire_at, _now_str(), _now_str()),
            )
            conn.commit()

    try:
        _send_register_code_email(settings, email, code, expire_minutes=expire_minutes)
    except Exception as e:
        return _fail(f'验证码发送失败: {e}')

    return _ok(True, message='验证码已发送，请注意查收邮箱')

@router.post('/auth/send-reset-code')
def send_reset_password_code(body: ForgotPasswordSendCodeBody):
    email = (body.email or '').strip().lower()
    if not _EMAIL_RE.match(email):
        return _fail('请输入有效邮箱')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute(
                'SELECT 1 FROM user_accounts WHERE lower(email) = ?',
                (email,),
            ).fetchone()
            if not exists:
                # 安全考虑：避免邮箱枚举
                return _ok(True, message='验证码已发送，请注意查收邮箱')

    settings = _get_email_settings(mask_secret=False)
    err = _validate_email_settings(settings)
    if err:
        return _fail(err)

    code = ''.join(str(random.randint(0, 9)) for _ in range(6))
    expire_minutes = 10
    expire_at = (datetime.now() + timedelta(minutes=expire_minutes)).strftime('%Y-%m-%d %H:%M:%S')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                INSERT INTO email_codes (
                    id, email, purpose, code, expires_at, used, updated_at, created_at
                ) VALUES (?, ?, 'reset_password', ?, ?, 0, ?, ?)
                ''',
                (uuid.uuid4().hex, email, code, expire_at, _now_str(), _now_str()),
            )
            conn.commit()

    try:
        _send_reset_password_code_email(settings, email, code, expire_minutes=expire_minutes)
    except Exception as e:
        return _fail(f'验证码发送失败: {e}')

    return _ok(True, message='验证码已发送，请注意查收邮箱')

@router.post('/auth/reset-password')
def reset_password_by_email(body: ForgotPasswordResetBody):
    email = (body.email or '').strip().lower()
    code = (body.code or '').strip()
    new_password = str(body.newPassword or '')
    confirm_password = str(body.confirmPassword or '')

    if not _EMAIL_RE.match(email):
        return _fail('请输入有效邮箱')
    if not re.fullmatch(r'\d{6}', code):
        return _fail('验证码格式错误')
    if new_password != confirm_password:
        return _fail('两次输入的密码不一致')

    policy = _get_security_policy()
    pwd_err = _validate_password_with_policy(new_password, policy)
    if pwd_err:
        return _fail(pwd_err)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            user_row = conn.execute(
                'SELECT id, password FROM user_accounts WHERE lower(email) = ? LIMIT 1',
                (email,),
            ).fetchone()
            if not user_row:
                return _fail('账号不存在')

            code_row = conn.execute(
                '''
                SELECT id, code, expires_at, used
                FROM email_codes
                WHERE lower(email) = ? AND purpose = 'reset_password'
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                ''',
                (email,),
            ).fetchone()
            if not code_row:
                return _fail('请先发送验证码')
            if int(code_row['used'] or 0) == 1:
                return _fail('验证码已失效，请重新发送')
            if str(code_row['code'] or '') != code:
                return _fail('验证码错误')

            expire_at = _parse_dt(str(code_row['expires_at'] or ''))
            if expire_at is None or expire_at <= datetime.now():
                return _fail('验证码已过期，请重新发送')

            if _verify_password(new_password, str(user_row['password'] or '')):
                return _fail('新密码不能与旧密码相同')

            now = _now_str()
            conn.execute(
                'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
                (_hash_password(new_password), now, str(user_row['id'] or '')),
            )
            conn.execute(
                'UPDATE email_codes SET used = 1, updated_at = ? WHERE id = ?',
                (now, str(code_row['id'] or '')),
            )
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE account_id = ? AND revoked_at = ''",
                (now, now, str(user_row['id'] or '')),
            )
            _audit_log(
                conn,
                str(user_row['id'] or ''),
                'auth.reset_password',
                'account',
                str(user_row['id'] or ''),
                {'by': 'email_code'},
            )
            conn.commit()

    for token, snapshot in list(_ADMIN_TOKENS.items()):
        if str(snapshot.get('email') or '').strip().lower() == email:
            _ADMIN_TOKENS.pop(token, None)

    return _ok(True, message='密码已重置，请重新登录')

@router.post('/auth/register')
def register_by_email(body: RegisterByEmailBody):
    email = (body.email or '').strip().lower()
    code = (body.code or '').strip()
    nickname = (body.nickname or '').strip()
    password = str(body.password or '')
    confirm_password = str(body.confirmPassword or '')

    if not _EMAIL_RE.match(email):
        return _fail('请输入有效邮箱')
    if not re.fullmatch(r'\d{6}', code):
        return _fail('验证码格式错误')
    if not nickname:
        return _fail('请填写昵称')

    policy = _get_security_policy()
    pwd_err = _validate_password_with_policy(password, policy)
    if pwd_err:
        return _fail(pwd_err)

    if password != confirm_password:
        return _fail('两次输入的密码不一致')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row

            exists = conn.execute(
                'SELECT 1 FROM user_accounts WHERE lower(email) = ?',
                (email,),
            ).fetchone()
            if exists:
                return _fail('该邮箱已注册')

            code_row = conn.execute(
                '''
                SELECT id, code, expires_at, used
                FROM email_codes
                WHERE lower(email) = ? AND purpose = 'register'
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                ''',
                (email,),
            ).fetchone()

            if not code_row:
                return _fail('请先发送验证码')
            if int(code_row['used'] or 0) == 1:
                return _fail('验证码已失效，请重新发送')
            if code_row['code'] != code:
                return _fail('验证码错误')

            expire_at = _parse_dt(code_row['expires_at'])
            if expire_at is None or expire_at <= datetime.now():
                return _fail('验证码已过期，请重新发送')

            username = _unique_username_from_email(conn, email)
            now = _now_str()
            user_id = uuid.uuid4().hex

            conn.execute(
                '''
                INSERT INTO user_accounts (
                    id, username, password, real_name, email,
                    roles, home_path, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    user_id,
                    username,
                    _hash_password(password),
                    nickname,
                    email,
                    json.dumps(['user'], ensure_ascii=False),
                    '/workspace',
                    now,
                    now,
                ),
            )

            _ensure_member_for_user(conn, username, email, nickname)

            conn.execute(
                'UPDATE email_codes SET used = 1, updated_at = ? WHERE id = ?',
                (_now_str(), code_row['id']),
            )
            conn.commit()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            token = _create_admin_session(conn, user_id)
            _audit_log(
                conn,
                user_id,
                'auth.register',
                'account',
                user_id,
                {'email': email, 'username': username},
            )
            conn.commit()

    user_snapshot = {
        'id': user_id,
        'username': username,
        'realName': nickname,
        'roles': ['user'],
        'email': email,
        'homePath': '/workspace',
    }
    user_snapshot['entitlement'] = _load_user_entitlement(user_snapshot)
    _ADMIN_TOKENS[token] = user_snapshot

    return _ok(
        {
            'accessToken': token,
            'username': username,
            'entitlement': user_snapshot['entitlement'],
        },
        message='注册成功',
    )

@router.get('/auth/validate')
def admin_validate(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    entitlement = user.get('entitlement') or _load_user_entitlement(user)

    if not is_entitlement_active(entitlement):
        return _ok(
            {
                'valid': False,
                'userId': user.get('id'),
                'username': user.get('username'),
                'entitlement': entitlement,
            },
            message=entitlement.get('reason') or '会员不可用',
        )

    return _ok(
        {
            'valid': True,
            'userId': user.get('id'),
            'username': user.get('username'),
            'entitlement': entitlement,
        }
    )

@router.get('/auth/codes')
def admin_codes(authorization: Optional[str] = Header(default=None)):
    user, _ = _require_entitled_user(authorization)

    roles = set(user.get('roles') or [])
    if {'super', 'admin'} & roles:
        return _ok(['AC_100100', 'AC_100110', 'AC_100120', 'AC_100010'])
    return _ok(['AC_100010'])
