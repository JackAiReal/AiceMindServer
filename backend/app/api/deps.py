from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import smtplib
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl

import requests

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.core.db_runtime import connect_sqlite, describe_runtime, resolve_sqlite_path
from app.core.entitlement import (
    get_account_identity,
    get_billing_context,
    get_entitlement_for_account,
    get_entitlement_policy,
    get_feature_usage,
    is_entitlement_active,
    upsert_entitlement_policy,
)


_DB_LOCK = threading.Lock()
# 运行期默认跳过重复初始化，避免与主后端共享 SQLite 时出现锁竞争
_DB_READY = True
_DB_PATH = resolve_sqlite_path(Path(__file__).resolve().parents[2] / 'data' / 'admin_console.db')
_DB_RUNTIME = describe_runtime(_DB_PATH)


def _db_connect():
    return connect_sqlite(_DB_PATH)


def _execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
    retries: int = 6,
    base_delay: float = 0.08,
):
    """SQLite 写操作重试，缓解多进程并发时的短暂锁冲突。"""
    for i in range(max(1, int(retries))):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if 'locked' not in str(e).lower() or i >= retries - 1:
                raise
            time.sleep(base_delay * (i + 1))

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_NO_EXPIRE_TIME = '2099-12-31 23:59:59'
_DEFAULT_VERIFY_SUBJECT_TEMPLATE = '【{{app_name}}】邮箱验证码'
_DEFAULT_VERIFY_BODY_TEMPLATE = (
    '你好，{{nickname_or_email}}：\n\n'
    '你正在注册 {{app_name}}，本次验证码为：{{code}}\n'
    '验证码在 {{expire_minutes}} 分钟内有效，请勿泄露给他人。\n\n'
    '请求邮箱：{{email}}\n'
    '发送时间：{{now}}\n\n'
    '如果这不是你的操作，请忽略本邮件。\n'
    '{{app_name}} 团队'
)

_ADMIN_SEED_USERS = {
    'superadmin': {
        'password': 'Qaz12356789',
        'realName': 'Super Admin',
        'roles': ['super'],
        'email': 'superadmin@aicemind.com',
        'homePath': '/user/list',
    },
    'admin': {
        'password': '123456',
        'realName': 'Admin',
        'roles': ['admin'],
        'email': 'admin@aicemind.com',
        'homePath': '/user/list',
    },
}

# token -> user_snapshot
_ADMIN_TOKENS: dict[str, dict[str, Any]] = {}

_PASSWORD_HASH_PREFIX = 'pbkdf2_sha256'
_PASSWORD_HASH_ITERATIONS = 240_000
_LOGIN_FAIL_MAX = 5
_LOGIN_FAIL_WINDOW_MINUTES = 15
_LOGIN_LOCK_MINUTES = 15
_ADMIN_SESSION_TTL_HOURS = 24
_ORDER_EXPIRE_MINUTES = 30
_ORDER_IDEMPOTENCY_WINDOW_MINUTES = 10
_RENEWAL_REMINDER_DAYS = (7, 3, 1)

_DEFAULT_SECURITY_POLICY = {
    'passwordMinLength': 8,
    'passwordRequireLetter': True,
    'passwordRequireDigit': True,
    'passwordRequireSpecial': False,
    'loginFailMax': 5,
    'loginFailWindowMinutes': 15,
    'loginLockMinutes': 15,
    'sessionTtlHours': 24,
    'forceLogoutOnPasswordReset': True,
}

_LEGAL_DOC_DEFAULTS: dict[str, dict[str, str]] = {
    'terms': {
        'title': 'AiceMind 用户协议',
        'content': '请在此维护用户协议正文。',
    },
    'privacy': {
        'title': 'AiceMind 隐私政策',
        'content': '请在此维护隐私政策正文。',
    },
    'risk_disclaimer': {
        'title': 'AiceMind 风险免责声明',
        'content': '回测结果不构成任何投资建议，市场有风险，投资需谨慎。',
    },
}


class AdminLoginBody(BaseModel):
    username: str
    password: str
    totpCode: str = ''


class MemberCreateBody(BaseModel):
    userNickname: str
    userId: str
    email: str = ''
    memberLevel: str = 'basic'
    memberStatus: str = 'active'
    startTime: str = ''
    expireTime: str = ''
    points: int = 0


class MemberUpdateBody(BaseModel):
    id: str
    userNickname: str
    userId: str
    email: str = ''
    memberLevel: str = 'basic'
    memberStatus: str = 'active'
    startTime: str = ''
    expireTime: str = ''
    points: int = 0


class ToggleStatusBody(BaseModel):
    id: str
    status: str


class ExtendExpireBody(BaseModel):
    id: str
    days: Optional[int] = None
    expireTime: Optional[str] = None


class EmailSettingsBody(BaseModel):
    smtpHost: str = ''
    smtpPort: int = 465
    smtpUsername: str = ''
    smtpPassword: str = ''
    fromEmail: str = ''
    fromName: str = 'AiceMind'
    useTLS: bool = False
    useSSL: bool = True
    verifySubjectTemplate: str = _DEFAULT_VERIFY_SUBJECT_TEMPLATE
    verifyBodyTemplate: str = _DEFAULT_VERIFY_BODY_TEMPLATE


class SendTestEmailBody(EmailSettingsBody):
    testEmail: str


class SendEmailCodeBody(BaseModel):
    email: str


class ForgotPasswordSendCodeBody(BaseModel):
    email: str


class ForgotPasswordResetBody(BaseModel):
    email: str
    code: str
    newPassword: str
    confirmPassword: str


class TwoFAEnableBody(BaseModel):
    code: str


class TwoFADisableBody(BaseModel):
    code: str


class RegisterByEmailBody(BaseModel):
    email: str
    code: str
    nickname: str
    password: str
    confirmPassword: str
    inviteCode: Optional[str] = None


class RevokeSessionBody(BaseModel):
    sessionId: str


class RevokeAccountSessionsBody(BaseModel):
    accountId: str


class UnlockLoginAttemptBody(BaseModel):
    loginKey: str


class PlanBody(BaseModel):
    id: Optional[str] = None
    code: str
    name: str
    price: float
    durationDays: int
    level: str = 'basic'
    status: str = 'active'
    description: str = ''
    dailyPointsRefresh: int = 0
    backtestPointMultiplier: int = 1


class SubscriptionUpsertBody(BaseModel):
    accountId: str
    planCode: str
    status: str = 'active'
    startTime: str = ''
    expireTime: str = ''


class OrderCreateBody(BaseModel):
    accountId: str
    planCode: str
    amount: float
    currency: str = 'CNY'
    channel: str = 'manual'
    status: str = 'created'
    note: str = ''


class OrderMarkPaidBody(BaseModel):
    orderId: str


class PlanToggleStatusBody(BaseModel):
    id: str
    status: str


class PaymentSettingsBody(BaseModel):
    alipayEnabled: bool = False
    alipayAppId: str = ''
    alipayMerchantId: str = ''
    alipayAppPrivateKey: str = ''
    alipayPublicKey: str = ''
    alipayGateway: str = 'https://openapi.alipay.com/gateway.do'
    alipayNotifyUrl: str = ''
    alipayReturnUrl: str = ''
    alipaySignType: str = 'RSA2'

    wechatEnabled: bool = False
    wechatAppId: str = ''
    wechatMerchantId: str = ''
    wechatApiV3Key: str = ''
    wechatPrivateKey: str = ''
    wechatSerialNo: str = ''
    wechatGateway: str = 'https://api.mch.weixin.qq.com'
    wechatNotifyUrl: str = ''
    wechatReturnUrl: str = ''

    paymentAlertEnabled: bool = False
    paymentAlertEmails: str = ''
    paymentAlertWebhook: str = ''


class PaymentTestPayBody(BaseModel):
    provider: str
    amount: float = 0.01
    currency: str = 'CNY'
    description: str = '支付配置测试'


class PaymentInitiateBody(BaseModel):
    orderId: str
    provider: str
    payerId: str = ''


class BillingPolicyBody(BaseModel):
    level: str
    policy: dict[str, Any] = {}


class PaymentReconcileRunBody(BaseModel):
    provider: str = 'alipay'
    reconcileDate: str = ''


class PaymentRepairBody(BaseModel):
    outTradeNo: str
    provider: str = 'alipay'


class PaymentAlertTestBody(BaseModel):
    title: str = '支付告警测试'
    content: str = '这是一条支付告警测试消息。'
    level: str = 'warning'


class ObservabilitySettingsBody(BaseModel):
    sentryDsn: str = ''
    alertWebhook: str = ''
    alertEmails: str = ''


class LegalDocSaveBody(BaseModel):
    docType: str
    title: str
    content: str
    version: str = ''
    effectiveAt: str = ''


class AccountDeleteRequestBody(BaseModel):
    reason: str = ''


class AccountDeleteProcessBody(BaseModel):
    requestId: str
    action: str
    note: str = ''


class RenewalReminderRunBody(BaseModel):
    dryRun: bool = False
    includeExpiredRecall: bool = True


class CommerceCreatePayBody(BaseModel):
    planCode: str
    provider: str = 'alipay'


class OrderCancelBody(BaseModel):
    orderId: str
    reason: str = ''


class OrderMarkExceptionBody(BaseModel):
    orderId: str
    reason: str = ''


class OrderRecoverBody(BaseModel):
    orderId: str
    reason: str = ''


class OrderRefundBody(BaseModel):
    orderId: str
    amount: Optional[float] = None
    reason: str = ''
    provider: str = 'manual'
    externalRefundNo: str = ''


class ChangePasswordBody(BaseModel):
    oldPassword: str
    newPassword: str
    confirmPassword: str


class ResetPasswordBody(BaseModel):
    accountId: str
    newPassword: str
    forceLogout: Optional[bool] = None


class SecurityPolicyBody(BaseModel):
    passwordMinLength: int = 8
    passwordRequireLetter: bool = True
    passwordRequireDigit: bool = True
    passwordRequireSpecial: bool = False
    loginFailMax: int = 5
    loginFailWindowMinutes: int = 15
    loginLockMinutes: int = 15
    sessionTtlHours: int = 24
    forceLogoutOnPasswordReset: bool = True


class PointsAdjustBody(BaseModel):
    accountId: str
    delta: int
    reason: str = ''


class PaginationQuery(BaseModel):
    limit: int = 50
    offset: int = 0


def _ok(data: Any = None, message: str = 'ok'):
    return {'code': 0, 'data': data, 'message': message}


def _fail(message: str, code: int = -1):
    return {'code': code, 'data': None, 'message': message}


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _default_expire_str() -> str:
    return (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')


def _order_expire_at_str(minutes: int = _ORDER_EXPIRE_MINUTES) -> str:
    ttl = int(minutes or _ORDER_EXPIRE_MINUTES)
    if ttl <= 0:
        ttl = _ORDER_EXPIRE_MINUTES
    return (datetime.now() + timedelta(minutes=ttl)).strftime('%Y-%m-%d %H:%M:%S')


def _parse_dt(value: str) -> Optional[datetime]:
    value = (value or '').strip()
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _safe_json_loads(text: str) -> dict[str, Any]:
    raw = str(text or '').strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _extract_qr_from_trade_payload(payload_text: str) -> str:
    payload = _safe_json_loads(payload_text)
    qr = str(payload.get('qrCode') or '').strip()
    if qr:
        return qr

    gateway = payload.get('gatewayResponse')
    if isinstance(gateway, dict):
        qr = str(gateway.get('qr_code') or '').strip()
        if qr:
            return qr

    request = payload.get('request')
    if isinstance(request, dict):
        biz_content_raw = request.get('biz_content')
        if isinstance(biz_content_raw, str):
            biz_content = _safe_json_loads(biz_content_raw)
            qr = str(biz_content.get('qr_code') or '').strip()
            if qr:
                return qr

    return ''


def _mask_secret(secret: str) -> str:
    if not secret:
        return ''
    if len(secret) <= 6:
        return '*' * len(secret)
    return secret[:2] + '*' * (len(secret) - 4) + secret[-2:]


def _normalize_roles(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or '[]')
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
    except Exception:
        pass
    return ['user']


def _is_password_hashed(value: str) -> bool:
    return str(value or '').startswith(f'{_PASSWORD_HASH_PREFIX}$')


def _hash_password(raw_password: str) -> str:
    password = str(raw_password or '')
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode('ascii').rstrip('=')
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        _PASSWORD_HASH_ITERATIONS,
    )
    encoded = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    return f'{_PASSWORD_HASH_PREFIX}${_PASSWORD_HASH_ITERATIONS}${salt}${encoded}'


def _verify_password(raw_password: str, stored_password: str) -> bool:
    stored = str(stored_password or '')
    raw = str(raw_password or '')

    if not _is_password_hashed(stored):
        return hmac.compare_digest(stored, raw)

    try:
        _, iterations_str, salt, digest_text = stored.split('$', 3)
        iterations = int(iterations_str)
    except Exception:
        return False

    calc = hashlib.pbkdf2_hmac(
        'sha256',
        raw.encode('utf-8'),
        salt.encode('utf-8'),
        iterations,
    )
    calc_text = base64.urlsafe_b64encode(calc).decode('ascii').rstrip('=')
    return hmac.compare_digest(calc_text, digest_text)


def _upsert_hashed_password(user_id: str, raw_password: str):
    if not user_id:
        return

    hashed = _hash_password(raw_password)
    with _db_connect() as conn:
        conn.execute(
            'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
            (hashed, _now_str(), user_id),
        )
        conn.commit()


def _normalize_login_key(login: str) -> str:
    return str(login or '').strip().lower()


def _runtime_security_limits(conn: sqlite3.Connection) -> dict[str, Any]:
    defaults = {
        'loginFailMax': int(_DEFAULT_SECURITY_POLICY['loginFailMax']),
        'loginFailWindowMinutes': int(_DEFAULT_SECURITY_POLICY['loginFailWindowMinutes']),
        'loginLockMinutes': int(_DEFAULT_SECURITY_POLICY['loginLockMinutes']),
        'sessionTtlHours': int(_DEFAULT_SECURITY_POLICY['sessionTtlHours']),
    }

    try:
        row = conn.execute(
            '''
            SELECT login_fail_max, login_fail_window_minutes, login_lock_minutes, session_ttl_hours
            FROM security_policy
            WHERE id = 1
            LIMIT 1
            '''
        ).fetchone()
    except Exception:
        row = None

    if not row:
        return defaults

    return {
        'loginFailMax': max(3, min(int(row['login_fail_max'] or defaults['loginFailMax']), 20)),
        'loginFailWindowMinutes': max(1, min(int(row['login_fail_window_minutes'] or defaults['loginFailWindowMinutes']), 120)),
        'loginLockMinutes': max(1, min(int(row['login_lock_minutes'] or defaults['loginLockMinutes']), 240)),
        'sessionTtlHours': max(1, min(int(row['session_ttl_hours'] or defaults['sessionTtlHours']), 168)),
    }


def _login_lock_seconds(locked_until: Optional[str]) -> int:
    dt = _parse_dt(locked_until or '')
    if not dt:
        return 0
    remain = int((dt - datetime.now()).total_seconds())
    return remain if remain > 0 else 0


def _get_login_lock_state(conn: sqlite3.Connection, login_key: str) -> dict[str, Any]:
    row = conn.execute(
        '''
        SELECT fail_count, first_fail_at, locked_until
        FROM login_attempts
        WHERE login_key = ?
        LIMIT 1
        ''',
        (login_key,),
    ).fetchone()

    if not row:
        return {'fail_count': 0, 'first_fail_at': '', 'locked_until': '', 'locked_seconds': 0}

    fail_count = int(row['fail_count'] or 0)
    first_fail_at = str(row['first_fail_at'] or '')
    locked_until = str(row['locked_until'] or '')
    locked_seconds = _login_lock_seconds(locked_until)
    if locked_seconds <= 0 and locked_until:
        _execute_with_retry(
            conn,
            'UPDATE login_attempts SET locked_until = ?, updated_at = ? WHERE login_key = ?',
            ('', _now_str(), login_key),
        )
        conn.commit()

    return {
        'fail_count': fail_count,
        'first_fail_at': first_fail_at,
        'locked_until': locked_until if locked_seconds > 0 else '',
        'locked_seconds': locked_seconds,
    }


def _record_login_failure(conn: sqlite3.Connection, login_key: str):
    now = datetime.now()
    now_str = _now_str()
    state = _get_login_lock_state(conn, login_key)
    limits = _runtime_security_limits(conn)

    fail_count = int(state.get('fail_count') or 0)
    first_fail_at = str(state.get('first_fail_at') or '')
    first_dt = _parse_dt(first_fail_at)

    if not first_dt or (now - first_dt).total_seconds() > int(limits['loginFailWindowMinutes']) * 60:
        fail_count = 0
        first_fail_at = now_str

    fail_count += 1
    locked_until = ''
    if fail_count >= int(limits['loginFailMax']):
        locked_until = (now + timedelta(minutes=int(limits['loginLockMinutes']))).strftime('%Y-%m-%d %H:%M:%S')

    _execute_with_retry(
        conn,
        '''
        INSERT INTO login_attempts (
            login_key, fail_count, first_fail_at, locked_until, updated_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(login_key) DO UPDATE SET
            fail_count = excluded.fail_count,
            first_fail_at = excluded.first_fail_at,
            locked_until = excluded.locked_until,
            updated_at = excluded.updated_at
        ''',
        (login_key, fail_count, first_fail_at, locked_until, now_str, now_str),
    )


def _clear_login_failures(conn: sqlite3.Connection, login_key: str):
    _execute_with_retry(conn, 'DELETE FROM login_attempts WHERE login_key = ?', (login_key,))


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _create_admin_session(conn: sqlite3.Connection, account_id: str) -> str:
    token = uuid.uuid4().hex
    now = datetime.now()
    now_str = _now_str()
    limits = _runtime_security_limits(conn)
    ttl_hours = int(limits.get('sessionTtlHours') or _DEFAULT_SECURITY_POLICY['sessionTtlHours'])
    expire_at = (now + timedelta(hours=max(1, ttl_hours))).strftime('%Y-%m-%d %H:%M:%S')
    _execute_with_retry(
        conn,
        '''
        INSERT INTO auth_sessions (
            id, account_id, token_digest, created_at, expire_at,
            revoked_at, last_active_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            account_id,
            _token_digest(token),
            now_str,
            expire_at,
            now_str,
            now_str,
        ),
    )
    return token


def _revoke_admin_session(conn: sqlite3.Connection, token: str):
    digest = _token_digest(token)
    conn.execute(
        '''
        UPDATE auth_sessions
        SET revoked_at = ?, updated_at = ?
        WHERE token_digest = ? AND revoked_at = ''
        ''',
        (_now_str(), _now_str(), digest),
    )


def _query_active_session(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    digest = _token_digest(token)
    now = _now_str()
    row = conn.execute(
        '''
        SELECT id, account_id, expire_at, revoked_at
        FROM auth_sessions
        WHERE token_digest = ?
        LIMIT 1
        ''',
        (digest,),
    ).fetchone()
    if not row:
        return None

    revoked_at = str(row['revoked_at'] or '')
    expire_at = str(row['expire_at'] or '')
    if revoked_at:
        return None

    expire_dt = _parse_dt(expire_at)
    if not expire_dt or expire_dt <= datetime.now():
        try:
            _execute_with_retry(
                conn,
                "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE id = ? AND revoked_at = ''",
                (now, now, row['id']),
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            # 共享 SQLite 时可能被其他进程短暂占锁，过期会话标记失败不影响本次鉴权结论
            if 'locked' not in str(e).lower():
                raise
        return None

    # 心跳更新时间采用 best-effort，避免锁冲突导致整条业务请求失败
    try:
        _execute_with_retry(
            conn,
            'UPDATE auth_sessions SET last_active_at = ?, updated_at = ? WHERE id = ?',
            (now, now, row['id']),
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        if 'locked' not in str(e).lower():
            raise
    return row


def _audit_log(conn: sqlite3.Connection, actor_account_id: str, action: str, target_type: str = '', target_id: str = '', detail: Any = None):
    detail_text = ''
    if detail is not None:
        try:
            detail_text = json.dumps(detail, ensure_ascii=False)
        except Exception:
            detail_text = str(detail)

    now = _now_str()
    conn.execute(
        '''
        INSERT INTO audit_logs (
            id, actor_account_id, action, target_type, target_id, detail,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            actor_account_id,
            action,
            target_type,
            target_id,
            detail_text,
            now,
            now,
        ),
    )


def _load_user_entitlement(user: dict[str, Any]) -> dict[str, Any]:
    account_id = str(user.get('id') or '').strip()
    if not account_id:
        return {
            'level': 'none',
            'status': 'inactive',
            'expire_at': '',
            'is_active': False,
            'reason': '账号不存在',
        }

    return get_entitlement_for_account(account_id)


def _require_entitled_user(authorization: Optional[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    user = _require_user(authorization)
    entitlement = _load_user_entitlement(user)
    user['entitlement'] = entitlement

    if not is_entitlement_active(entitlement):
        raise HTTPException(status_code=403, detail=entitlement.get('reason') or '会员不可用，请先续费')

    return user, entitlement


def _ensure_db():
    global _DB_READY
    if _DB_READY:
        return

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db_connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS member_users (
                id TEXT PRIMARY KEY,
                user_nickname TEXT NOT NULL,
                user_id TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL DEFAULT '',
                member_level TEXT NOT NULL DEFAULT 'basic',
                member_status TEXT NOT NULL DEFAULT 'active',
                start_time TEXT NOT NULL,
                expire_time TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS user_accounts (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                real_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                roles TEXT NOT NULL,
                home_path TEXT NOT NULL,
                totp_enabled INTEGER NOT NULL DEFAULT 0,
                totp_secret TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        user_account_columns = {row[1] for row in conn.execute("PRAGMA table_info(user_accounts)").fetchall()}
        if 'totp_enabled' not in user_account_columns:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0")
        if 'totp_secret' not in user_account_columns:
            conn.execute("ALTER TABLE user_accounts ADD COLUMN totp_secret TEXT NOT NULL DEFAULT ''")

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS login_attempts (
                login_key TEXT PRIMARY KEY,
                fail_count INTEGER NOT NULL DEFAULT 0,
                first_fail_at TEXT NOT NULL DEFAULT '',
                locked_until TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                token_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expire_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                last_active_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_auth_sessions_account ON auth_sessions(account_id)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS login_risk_events (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                login_ip TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'low',
                risk_reason TEXT NOT NULL DEFAULT '',
                city_hint TEXT NOT NULL DEFAULT '',
                notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_login_risk_events_account ON login_risk_events(account_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_metrics (
                id TEXT PRIMARY KEY,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                latency_ms REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_request_metrics_created ON request_metrics(created_at DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_request_metrics_path ON request_metrics(path, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS error_events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'backend',
                level TEXT NOT NULL DEFAULT 'error',
                message TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events(created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS observability_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                sentry_dsn TEXT NOT NULL DEFAULT '',
                alert_webhook TEXT NOT NULL DEFAULT '',
                alert_emails TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                actor_account_id TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_account_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                duration_days INTEGER NOT NULL DEFAULT 30,
                level TEXT NOT NULL DEFAULT 'basic',
                status TEXT NOT NULL DEFAULT 'active',
                description TEXT NOT NULL DEFAULT '',
                daily_points_refresh INTEGER NOT NULL DEFAULT 0,
                backtest_point_multiplier INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )

        plan_columns = {row[1] for row in conn.execute("PRAGMA table_info(plans)").fetchall()}
        if 'daily_points_refresh' not in plan_columns:
            conn.execute("ALTER TABLE plans ADD COLUMN daily_points_refresh INTEGER NOT NULL DEFAULT 0")
        if 'backtest_point_multiplier' not in plan_columns:
            conn.execute("ALTER TABLE plans ADD COLUMN backtest_point_multiplier INTEGER NOT NULL DEFAULT 1")

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                plan_code TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                start_time TEXT NOT NULL,
                expire_time TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, plan_code)
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_subscriptions_account ON subscriptions(account_id, updated_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                order_no TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                plan_code TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                channel TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'created',
                paid_at TEXT NOT NULL DEFAULT '',
                expire_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, created_at DESC)')
        order_columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        if 'expire_at' not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN expire_at TEXT NOT NULL DEFAULT ''")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_status_expire ON orders(status, expire_at)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS order_refunds (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                order_no TEXT NOT NULL,
                account_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'manual',
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                status TEXT NOT NULL DEFAULT 'created',
                reason TEXT NOT NULL DEFAULT '',
                external_refund_no TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_order_refunds_order ON order_refunds(order_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS order_state_events (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                order_no TEXT NOT NULL,
                from_status TEXT NOT NULL DEFAULT '',
                to_status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                actor_account_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_order_state_events_order ON order_state_events(order_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS security_policy (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                password_min_length INTEGER NOT NULL DEFAULT 8,
                password_require_letter INTEGER NOT NULL DEFAULT 1,
                password_require_digit INTEGER NOT NULL DEFAULT 1,
                password_require_special INTEGER NOT NULL DEFAULT 0,
                login_fail_max INTEGER NOT NULL DEFAULT 5,
                login_fail_window_minutes INTEGER NOT NULL DEFAULT 15,
                login_lock_minutes INTEGER NOT NULL DEFAULT 15,
                session_ttl_hours INTEGER NOT NULL DEFAULT 24,
                force_logout_on_password_reset INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        security_policy_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(security_policy)").fetchall()
        }
        if 'login_fail_max' not in security_policy_columns:
            conn.execute("ALTER TABLE security_policy ADD COLUMN login_fail_max INTEGER NOT NULL DEFAULT 5")
        if 'login_fail_window_minutes' not in security_policy_columns:
            conn.execute("ALTER TABLE security_policy ADD COLUMN login_fail_window_minutes INTEGER NOT NULL DEFAULT 15")
        if 'login_lock_minutes' not in security_policy_columns:
            conn.execute("ALTER TABLE security_policy ADD COLUMN login_lock_minutes INTEGER NOT NULL DEFAULT 15")
        if 'session_ttl_hours' not in security_policy_columns:
            conn.execute("ALTER TABLE security_policy ADD COLUMN session_ttl_hours INTEGER NOT NULL DEFAULT 24")
        if 'force_logout_on_password_reset' not in security_policy_columns:
            conn.execute("ALTER TABLE security_policy ADD COLUMN force_logout_on_password_reset INTEGER NOT NULL DEFAULT 1")

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS email_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                smtp_host TEXT NOT NULL DEFAULT '',
                smtp_port INTEGER NOT NULL DEFAULT 465,
                smtp_username TEXT NOT NULL DEFAULT '',
                smtp_password TEXT NOT NULL DEFAULT '',
                from_email TEXT NOT NULL DEFAULT '',
                from_name TEXT NOT NULL DEFAULT 'AiceMind',
                use_tls INTEGER NOT NULL DEFAULT 0,
                use_ssl INTEGER NOT NULL DEFAULT 1,
                verify_subject_template TEXT NOT NULL DEFAULT '',
                verify_body_template TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        email_setting_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(email_settings)").fetchall()
        }
        if 'verify_subject_template' not in email_setting_columns:
            conn.execute(
                "ALTER TABLE email_settings ADD COLUMN verify_subject_template TEXT NOT NULL DEFAULT ''"
            )
        if 'verify_body_template' not in email_setting_columns:
            conn.execute(
                "ALTER TABLE email_settings ADD COLUMN verify_body_template TEXT NOT NULL DEFAULT ''"
            )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                alipay_enabled INTEGER NOT NULL DEFAULT 0,
                alipay_app_id TEXT NOT NULL DEFAULT '',
                alipay_merchant_id TEXT NOT NULL DEFAULT '',
                alipay_app_private_key TEXT NOT NULL DEFAULT '',
                alipay_public_key TEXT NOT NULL DEFAULT '',
                alipay_gateway TEXT NOT NULL DEFAULT 'https://openapi.alipay.com/gateway.do',
                alipay_notify_url TEXT NOT NULL DEFAULT '',
                alipay_return_url TEXT NOT NULL DEFAULT '',
                alipay_sign_type TEXT NOT NULL DEFAULT 'RSA2',
                wechat_enabled INTEGER NOT NULL DEFAULT 0,
                wechat_app_id TEXT NOT NULL DEFAULT '',
                wechat_merchant_id TEXT NOT NULL DEFAULT '',
                wechat_api_v3_key TEXT NOT NULL DEFAULT '',
                wechat_private_key TEXT NOT NULL DEFAULT '',
                wechat_serial_no TEXT NOT NULL DEFAULT '',
                wechat_gateway TEXT NOT NULL DEFAULT 'https://api.mch.weixin.qq.com',
                wechat_notify_url TEXT NOT NULL DEFAULT '',
                wechat_return_url TEXT NOT NULL DEFAULT '',
                payment_alert_enabled INTEGER NOT NULL DEFAULT 0,
                payment_alert_emails TEXT NOT NULL DEFAULT '',
                payment_alert_webhook TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        payment_setting_columns = {row[1] for row in conn.execute("PRAGMA table_info(payment_settings)").fetchall()}
        if 'payment_alert_enabled' not in payment_setting_columns:
            conn.execute("ALTER TABLE payment_settings ADD COLUMN payment_alert_enabled INTEGER NOT NULL DEFAULT 0")
        if 'payment_alert_emails' not in payment_setting_columns:
            conn.execute("ALTER TABLE payment_settings ADD COLUMN payment_alert_emails TEXT NOT NULL DEFAULT ''")
        if 'payment_alert_webhook' not in payment_setting_columns:
            conn.execute("ALTER TABLE payment_settings ADD COLUMN payment_alert_webhook TEXT NOT NULL DEFAULT ''")

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_trades (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                order_no TEXT NOT NULL,
                account_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                out_trade_no TEXT NOT NULL UNIQUE,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                status TEXT NOT NULL DEFAULT 'created',
                payer_id TEXT NOT NULL DEFAULT '',
                gateway_trade_no TEXT NOT NULL DEFAULT '',
                callback_payload TEXT NOT NULL DEFAULT '',
                callback_verified INTEGER NOT NULL DEFAULT 0,
                callback_at TEXT NOT NULL DEFAULT '',
                paid_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_trades_order ON payment_trades(order_id, created_at DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_trades_account ON payment_trades(account_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_events (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                out_trade_no TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                verified INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                processed_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_reconcile_runs (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                reconcile_date TEXT NOT NULL,
                local_paid_count INTEGER NOT NULL DEFAULT 0,
                local_paid_amount REAL NOT NULL DEFAULT 0,
                callback_paid_count INTEGER NOT NULL DEFAULT 0,
                callback_paid_amount REAL NOT NULL DEFAULT 0,
                mismatch_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'done',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, reconcile_date)
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_reconcile_items (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                provider TEXT NOT NULL,
                out_trade_no TEXT NOT NULL DEFAULT '',
                order_no TEXT NOT NULL DEFAULT '',
                local_amount REAL NOT NULL DEFAULT 0,
                callback_amount REAL NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_reconcile_items_run ON payment_reconcile_items(run_id, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_alert_logs (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'warning',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '',
                sent_email INTEGER NOT NULL DEFAULT 0,
                sent_webhook INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_alert_logs_created ON payment_alert_logs(created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS payment_callback_retry_jobs (
                id TEXT PRIMARY KEY,
                event_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                out_trade_no TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 8,
                status TEXT NOT NULL DEFAULT 'pending',
                next_retry_at TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(event_key, reason, status)
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_payment_callback_retry_jobs_due ON payment_callback_retry_jobs(status, next_retry_at)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS refund_retry_jobs (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                out_trade_no TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CNY',
                reason TEXT NOT NULL DEFAULT '',
                external_refund_no TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 8,
                status TEXT NOT NULL DEFAULT 'pending',
                next_retry_at TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_refund_retry_jobs_due ON refund_retry_jobs(status, next_retry_at)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS email_codes (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                purpose TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_email_codes_email_purpose ON email_codes(email, purpose, created_at DESC)'
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS renewal_reminder_logs (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                member_level TEXT NOT NULL DEFAULT '',
                expire_time TEXT NOT NULL DEFAULT '',
                days_left INTEGER NOT NULL,
                reminder_type TEXT NOT NULL DEFAULT 'renewal',
                channel TEXT NOT NULL DEFAULT 'inapp',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'sent',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, expire_time, days_left, reminder_type, channel)
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_renewal_reminder_logs_account ON renewal_reminder_logs(account_id, created_at DESC)'
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS inapp_notifications (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'info',
                read_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_inapp_notifications_account ON inapp_notifications(account_id, created_at DESC)'
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS legal_docs (
                doc_type TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '',
                effective_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS account_deletion_requests (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                request_detail TEXT NOT NULL DEFAULT '',
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_account_deletion_requests_account ON account_deletion_requests(account_id, created_at DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_account_deletion_requests_status ON account_deletion_requests(status, created_at DESC)')

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS points_ledger (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                delta INTEGER NOT NULL,
                points_before INTEGER NOT NULL DEFAULT 0,
                points_after INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                ref_id TEXT NOT NULL DEFAULT '',
                actor_account_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_points_ledger_account ON points_ledger(account_id, created_at DESC)'
        )

        now = _now_str()

        # seed 合规文档
        for doc_type, info in _LEGAL_DOC_DEFAULTS.items():
            exists_doc = conn.execute(
                'SELECT doc_type FROM legal_docs WHERE doc_type = ? LIMIT 1',
                (doc_type,),
            ).fetchone()
            if exists_doc:
                continue
            conn.execute(
                '''
                INSERT INTO legal_docs (
                    doc_type, title, content, version, effective_at, updated_at, created_at
                ) VALUES (?, ?, ?, 'v1', ?, ?, ?)
                ''',
                (
                    doc_type,
                    str(info.get('title') or ''),
                    str(info.get('content') or ''),
                    now,
                    now,
                    now,
                ),
            )

        # seed 套餐
        default_plans = [
            ('basic_month', '基础版月付', 99.0, 30, 'basic', 'active', '基础版 30 天', 50, 1),
            ('pro_month', 'Pro 月付', 199.0, 30, 'pro', 'active', 'Pro 版 30 天', 120, 1),
            ('svip_year', 'SVIP 年付', 1999.0, 365, 'svip', 'active', 'SVIP 版 365 天', 300, 1),
        ]
        for code, name, price, duration_days, level, status, desc, daily_points_refresh, backtest_point_multiplier in default_plans:
            exists = conn.execute(
                'SELECT id, daily_points_refresh, backtest_point_multiplier FROM plans WHERE code = ? LIMIT 1',
                (code,),
            ).fetchone()
            if exists:
                conn.execute(
                    '''
                    UPDATE plans
                    SET daily_points_refresh = CASE WHEN daily_points_refresh <= 0 THEN ? ELSE daily_points_refresh END,
                        backtest_point_multiplier = CASE WHEN backtest_point_multiplier <= 0 THEN ? ELSE backtest_point_multiplier END,
                        updated_at = updated_at
                    WHERE id = ?
                    ''',
                    (int(daily_points_refresh), max(1, int(backtest_point_multiplier)), str(exists[0] or '')),
                )
                continue
            conn.execute(
                '''
                INSERT INTO plans (
                    id, code, name, price, duration_days, level, status, description,
                    daily_points_refresh, backtest_point_multiplier,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    uuid.uuid4().hex,
                    code,
                    name,
                    float(price),
                    int(duration_days),
                    level,
                    status,
                    desc,
                    int(daily_points_refresh),
                    max(1, int(backtest_point_multiplier)),
                    now,
                    now,
                ),
            )

        # seed 管理账号
        for username, info in _ADMIN_SEED_USERS.items():
            row = conn.execute(
                'SELECT id, password FROM user_accounts WHERE username = ? OR email = ? LIMIT 1',
                (username, info['email']),
            ).fetchone()
            if row:
                stored_password = str(row[1] or '')
                if not _is_password_hashed(stored_password):
                    conn.execute(
                        'UPDATE user_accounts SET password = ?, updated_at = ? WHERE id = ?',
                        (_hash_password(stored_password), now, row[0]),
                    )
                continue

            conn.execute(
                '''
                INSERT INTO user_accounts (
                    id, username, password, real_name, email,
                    roles, home_path, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    uuid.uuid4().hex,
                    username,
                    _hash_password(info['password']),
                    info['realName'],
                    info['email'],
                    json.dumps(info['roles'], ensure_ascii=False),
                    info['homePath'],
                    now,
                    now,
                ),
            )

        # seed 会员用户列表
        count = conn.execute('SELECT COUNT(1) FROM member_users').fetchone()[0]
        if count == 0:
            seed_rows = [
                (
                    uuid.uuid4().hex,
                    'Super Admin',
                    'superadmin',
                    'superadmin@aicemind.com',
                    'svip',
                    'active',
                    now,
                    _NO_EXPIRE_TIME,
                    999,
                    now,
                    now,
                ),
                (
                    uuid.uuid4().hex,
                    'Admin',
                    'admin',
                    'admin@aicemind.com',
                    'vip',
                    'active',
                    now,
                    _NO_EXPIRE_TIME,
                    200,
                    now,
                    now,
                ),
            ]
            conn.executemany(
                '''
                INSERT INTO member_users (
                    id, user_nickname, user_id, email,
                    member_level, member_status,
                    start_time, expire_time, points,
                    updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                seed_rows,
            )

        # seed 邮箱设置空行
        row = conn.execute('SELECT 1 FROM email_settings WHERE id = 1').fetchone()
        if not row:
            conn.execute(
                '''
                INSERT INTO email_settings (
                    id, smtp_host, smtp_port, smtp_username, smtp_password,
                    from_email, from_name, use_tls, use_ssl,
                    verify_subject_template, verify_body_template,
                    updated_at, created_at
                ) VALUES (1, '', 465, '', '', '', 'AiceMind', 0, 1, ?, ?, ?, ?)
                ''',
                (
                    _DEFAULT_VERIFY_SUBJECT_TEMPLATE,
                    _DEFAULT_VERIFY_BODY_TEMPLATE,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                '''
                UPDATE email_settings
                SET verify_subject_template = CASE
                        WHEN verify_subject_template = '' THEN ?
                        ELSE verify_subject_template
                    END,
                    verify_body_template = CASE
                        WHEN verify_body_template = '' THEN ?
                        ELSE verify_body_template
                    END,
                    updated_at = CASE
                        WHEN verify_subject_template = '' OR verify_body_template = '' THEN ?
                        ELSE updated_at
                    END
                WHERE id = 1
                ''',
                (
                    _DEFAULT_VERIFY_SUBJECT_TEMPLATE,
                    _DEFAULT_VERIFY_BODY_TEMPLATE,
                    now,
                ),
            )

        # seed 支付配置空行
        pay_row = conn.execute('SELECT 1 FROM payment_settings WHERE id = 1').fetchone()
        if not pay_row:
            conn.execute(
                '''
                INSERT INTO payment_settings (
                    id,
                    alipay_enabled, alipay_app_id, alipay_merchant_id,
                    alipay_app_private_key, alipay_public_key,
                    alipay_gateway, alipay_notify_url, alipay_return_url, alipay_sign_type,
                    wechat_enabled, wechat_app_id, wechat_merchant_id,
                    wechat_api_v3_key, wechat_private_key, wechat_serial_no,
                    wechat_gateway, wechat_notify_url, wechat_return_url,
                    payment_alert_enabled, payment_alert_emails, payment_alert_webhook,
                    updated_at, created_at
                ) VALUES (
                    1,
                    0, '', '',
                    '', '',
                    'https://openapi.alipay.com/gateway.do', '', '', 'RSA2',
                    0, '', '',
                    '', '', '',
                    'https://api.mch.weixin.qq.com', '', '',
                    0, '', '',
                    ?, ?
                )
                ''',
                (now, now),
            )

        # seed 观测配置
        obs_row = conn.execute('SELECT 1 FROM observability_settings WHERE id = 1').fetchone()
        if not obs_row:
            conn.execute(
                '''
                INSERT INTO observability_settings (
                    id, sentry_dsn, alert_webhook, alert_emails, updated_at, created_at
                ) VALUES (1, '', '', '', ?, ?)
                ''',
                (now, now),
            )

        # seed 安全策略
        sec_row = conn.execute('SELECT 1 FROM security_policy WHERE id = 1').fetchone()
        if not sec_row:
            conn.execute(
                '''
                INSERT INTO security_policy (
                    id, password_min_length, password_require_letter,
                    password_require_digit, password_require_special,
                    login_fail_max, login_fail_window_minutes, login_lock_minutes,
                    session_ttl_hours, force_logout_on_password_reset,
                    updated_at, created_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    int(_DEFAULT_SECURITY_POLICY['passwordMinLength']),
                    1 if _DEFAULT_SECURITY_POLICY['passwordRequireLetter'] else 0,
                    1 if _DEFAULT_SECURITY_POLICY['passwordRequireDigit'] else 0,
                    1 if _DEFAULT_SECURITY_POLICY['passwordRequireSpecial'] else 0,
                    int(_DEFAULT_SECURITY_POLICY['loginFailMax']),
                    int(_DEFAULT_SECURITY_POLICY['loginFailWindowMinutes']),
                    int(_DEFAULT_SECURITY_POLICY['loginLockMinutes']),
                    int(_DEFAULT_SECURITY_POLICY['sessionTtlHours']),
                    1 if _DEFAULT_SECURITY_POLICY['forceLogoutOnPasswordReset'] else 0,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                '''
                UPDATE security_policy
                SET login_fail_max = CASE WHEN login_fail_max <= 0 THEN ? ELSE login_fail_max END,
                    login_fail_window_minutes = CASE WHEN login_fail_window_minutes <= 0 THEN ? ELSE login_fail_window_minutes END,
                    login_lock_minutes = CASE WHEN login_lock_minutes <= 0 THEN ? ELSE login_lock_minutes END,
                    session_ttl_hours = CASE WHEN session_ttl_hours <= 0 THEN ? ELSE session_ttl_hours END,
                    force_logout_on_password_reset = CASE
                        WHEN force_logout_on_password_reset NOT IN (0, 1) THEN ?
                        ELSE force_logout_on_password_reset
                    END,
                    updated_at = updated_at
                WHERE id = 1
                ''',
                (
                    int(_DEFAULT_SECURITY_POLICY['loginFailMax']),
                    int(_DEFAULT_SECURITY_POLICY['loginFailWindowMinutes']),
                    int(_DEFAULT_SECURITY_POLICY['loginLockMinutes']),
                    int(_DEFAULT_SECURITY_POLICY['sessionTtlHours']),
                    1 if _DEFAULT_SECURITY_POLICY['forceLogoutOnPasswordReset'] else 0,
                ),
            )

        conn.commit()


def _query_members() -> list[dict[str, Any]]:
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            '''
            SELECT
                id,
                user_nickname,
                user_id,
                email,
                member_level,
                member_status,
                start_time,
                expire_time,
                points,
                updated_at
            FROM member_users
            ORDER BY datetime(updated_at) DESC
            '''
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                'id': row['id'],
                'userNickname': row['user_nickname'],
                'userId': row['user_id'],
                'email': row['email'],
                'memberLevel': row['member_level'],
                'memberStatus': row['member_status'],
                'startTime': row['start_time'],
                'expireTime': row['expire_time'],
                'points': int(row['points'] or 0),
                'updatedAt': row['updated_at'],
            }
        )
    return result


def _query_accounts(limit: int = 200) -> list[dict[str, Any]]:
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            '''
            SELECT id, username, real_name, email, roles, updated_at, created_at
            FROM user_accounts
            ORDER BY datetime(updated_at) DESC
            LIMIT ?
            ''',
            (max(1, min(int(limit or 200), 500)),),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        identity = {
            'id': row['id'],
            'username': row['username'],
            'realName': row['real_name'],
            'email': row['email'],
            'roles': _normalize_roles(row['roles']),
        }
        entitlement = _load_user_entitlement(identity)
        result.append(
            {
                **identity,
                'entitlement': entitlement,
                'updatedAt': row['updated_at'],
                'createdAt': row['created_at'],
            }
        )
    return result


def _resolve_plan(conn: sqlite3.Connection, plan_code: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        '''
        SELECT id, code, name, price, duration_days, level, status, description,
               daily_points_refresh, backtest_point_multiplier
        FROM plans
        WHERE code = ?
        LIMIT 1
        ''',
        (str(plan_code or '').strip(),),
    ).fetchone()


def _resolve_account(conn: sqlite3.Connection, account_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        '''
        SELECT id, username, real_name, email, roles
        FROM user_accounts
        WHERE id = ?
        LIMIT 1
        ''',
        (str(account_id or '').strip(),),
    ).fetchone()


def _adjust_member_points(
    conn: sqlite3.Connection,
    account_row: sqlite3.Row,
    delta: int,
    actor_account_id: str,
    reason: str = '',
    source: str = 'system.points.adjust',
    ref_id: str = '',
) -> dict[str, Any]:
    username = str(account_row['username'] or '').strip()
    email = str(account_row['email'] or '').strip().lower()

    member_row = conn.execute(
        '''
        SELECT id, points
        FROM member_users
        WHERE user_id = ? OR lower(email) = ?
        LIMIT 1
        ''',
        (username, email),
    ).fetchone()

    if not member_row:
        now = _now_str()
        member_id = uuid.uuid4().hex
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
                member_id,
                str(account_row['real_name'] or username or 'User').strip(),
                username,
                email,
                'basic',
                'active',
                now,
                _default_expire_str(),
                0,
                now,
                now,
            ),
        )
        before_points = 0
        member_id_val = member_id
    else:
        before_points = int(member_row['points'] or 0)
        member_id_val = str(member_row['id'] or '')

    after_points = before_points + int(delta or 0)
    if after_points < 0:
        raise ValueError('积分不足，无法扣减')

    now = _now_str()
    conn.execute(
        'UPDATE member_users SET points = ?, updated_at = ? WHERE id = ?',
        (after_points, now, member_id_val),
    )

    conn.execute(
        '''
        INSERT INTO points_ledger (
            id, account_id, username, delta,
            points_before, points_after,
            reason, source, ref_id,
            actor_account_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            str(account_row['id'] or ''),
            username,
            int(delta or 0),
            before_points,
            after_points,
            str(reason or '').strip(),
            str(source or '').strip(),
            str(ref_id or '').strip(),
            str(actor_account_id or '').strip(),
            now,
        ),
    )

    return {
        'accountId': str(account_row['id'] or ''),
        'username': username,
        'before': before_points,
        'after': after_points,
        'delta': int(delta or 0),
    }


def _sync_member_for_account(
    conn: sqlite3.Connection,
    account_row: sqlite3.Row,
    level: str,
    status: str,
    start_time: str,
    expire_time: str,
):
    username = str(account_row['username'] or '').strip()
    email = str(account_row['email'] or '').strip().lower()
    nickname = str(account_row['real_name'] or username or email or 'User').strip()

    existing = conn.execute(
        'SELECT id FROM member_users WHERE user_id = ? OR lower(email) = ? LIMIT 1',
        (username, email),
    ).fetchone()

    now = _now_str()
    if existing:
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
                updated_at = ?
            WHERE id = ?
            ''',
            (
                nickname,
                username,
                email,
                level,
                status,
                start_time,
                expire_time,
                now,
                existing['id'],
            ),
        )
    else:
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
                uuid.uuid4().hex,
                nickname,
                username,
                email,
                level,
                status,
                start_time,
                expire_time,
                0,
                now,
                now,
            ),
        )

    _DB_READY = True


def _apply_plan_to_account(
    conn: sqlite3.Connection,
    account_row: sqlite3.Row,
    plan_row: sqlite3.Row,
    paid_at_str: str,
) -> str:
    paid_dt = _parse_dt(paid_at_str) or datetime.now()
    duration_days = int(plan_row['duration_days'] or 30)
    if duration_days <= 0:
        duration_days = 30

    account_id = str(account_row['id'])
    plan_code = str(plan_row['code'])

    existing = conn.execute(
        '''
        SELECT id, start_time, expire_time
        FROM subscriptions
        WHERE account_id = ? AND plan_code = ?
        LIMIT 1
        ''',
        (account_id, plan_code),
    ).fetchone()

    base_dt = paid_dt
    if existing:
        old_expire = _parse_dt(str(existing['expire_time'] or ''))
        if old_expire and old_expire > base_dt:
            base_dt = old_expire

    new_expire_dt = base_dt + timedelta(days=duration_days)
    new_expire = new_expire_dt.strftime('%Y-%m-%d %H:%M:%S')
    start_time = paid_at_str
    if existing and str(existing['start_time'] or '').strip():
        start_time = str(existing['start_time'])

    now = _now_str()
    conn.execute(
        '''
        INSERT INTO subscriptions (
            id, account_id, plan_code, status, start_time, expire_time, created_at, updated_at
        ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
        ON CONFLICT(account_id, plan_code) DO UPDATE SET
            status = 'active',
            start_time = excluded.start_time,
            expire_time = excluded.expire_time,
            updated_at = excluded.updated_at
        ''',
        (uuid.uuid4().hex, account_id, plan_code, start_time, new_expire, now, now),
    )

    _sync_member_for_account(
        conn,
        account_row=account_row,
        level=str(plan_row['level'] or 'basic'),
        status='active',
        start_time=start_time,
        expire_time=new_expire,
    )

    return new_expire


def _get_user_by_login(login: str) -> Optional[dict[str, Any]]:
    _ensure_db()
    key = (login or '').strip().lower()
    if not key:
        return None

    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT id, username, password, real_name, email, roles, home_path
            FROM user_accounts
            WHERE lower(username) = ? OR lower(email) = ?
            LIMIT 1
            ''',
            (key, key),
        ).fetchone()

    if not row:
        return None

    return {
        'id': row['id'],
        'username': row['username'],
        'password': row['password'],
        'realName': row['real_name'],
        'email': row['email'],
        'roles': _normalize_roles(row['roles']),
        'homePath': row['home_path'] or '/workspace',
    }


def _get_email_settings(mask_secret: bool = False) -> dict[str, Any]:
    _ensure_db()
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT smtp_host, smtp_port, smtp_username, smtp_password,
                   from_email, from_name, use_tls, use_ssl,
                   verify_subject_template, verify_body_template
            FROM email_settings
            WHERE id = 1
            '''
        ).fetchone()

    if not row:
        return {
            'smtpHost': '',
            'smtpPort': 465,
            'smtpUsername': '',
            'smtpPassword': '',
            'fromEmail': '',
            'fromName': 'AiceMind',
            'useTLS': False,
            'useSSL': True,
            'verifySubjectTemplate': _DEFAULT_VERIFY_SUBJECT_TEMPLATE,
            'verifyBodyTemplate': _DEFAULT_VERIFY_BODY_TEMPLATE,
        }

    password = str(row['smtp_password'] or '')
    if mask_secret:
        password = _mask_secret(password)

    subject_template = str(row['verify_subject_template'] or '').strip() or _DEFAULT_VERIFY_SUBJECT_TEMPLATE
    body_template = str(row['verify_body_template'] or '').strip() or _DEFAULT_VERIFY_BODY_TEMPLATE

    return {
        'smtpHost': str(row['smtp_host'] or ''),
        'smtpPort': int(row['smtp_port'] or 465),
        'smtpUsername': str(row['smtp_username'] or ''),
        'smtpPassword': password,
        'fromEmail': str(row['from_email'] or ''),
        'fromName': str(row['from_name'] or 'AiceMind'),
        'useTLS': bool(int(row['use_tls'] or 0)),
        'useSSL': bool(int(row['use_ssl'] or 0)),
        'verifySubjectTemplate': subject_template,
        'verifyBodyTemplate': body_template,
    }


def _coerce_email_settings_from_body(body: EmailSettingsBody) -> dict[str, Any]:
    return {
        'smtpHost': (body.smtpHost or '').strip(),
        'smtpPort': int(body.smtpPort or 465),
        'smtpUsername': (body.smtpUsername or '').strip(),
        'smtpPassword': str(body.smtpPassword or ''),
        'fromEmail': (body.fromEmail or '').strip().lower(),
        'fromName': (body.fromName or 'AiceMind').strip() or 'AiceMind',
        'useTLS': bool(body.useTLS),
        'useSSL': bool(body.useSSL),
        'verifySubjectTemplate': (body.verifySubjectTemplate or '').strip() or _DEFAULT_VERIFY_SUBJECT_TEMPLATE,
        'verifyBodyTemplate': (body.verifyBodyTemplate or '').strip() or _DEFAULT_VERIFY_BODY_TEMPLATE,
    }


def _get_payment_settings(mask_secret: bool = False) -> dict[str, Any]:
    _ensure_db()
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT
                alipay_enabled, alipay_app_id, alipay_merchant_id,
                alipay_app_private_key, alipay_public_key,
                alipay_gateway, alipay_notify_url, alipay_return_url, alipay_sign_type,
                wechat_enabled, wechat_app_id, wechat_merchant_id,
                wechat_api_v3_key, wechat_private_key, wechat_serial_no,
                wechat_gateway, wechat_notify_url, wechat_return_url,
                payment_alert_enabled, payment_alert_emails, payment_alert_webhook
            FROM payment_settings
            WHERE id = 1
            '''
        ).fetchone()

    if not row:
        return {
            'alipayEnabled': False,
            'alipayAppId': '',
            'alipayMerchantId': '',
            'alipayAppPrivateKey': '',
            'alipayPublicKey': '',
            'alipayGateway': 'https://openapi.alipay.com/gateway.do',
            'alipayNotifyUrl': '',
            'alipayReturnUrl': '',
            'alipaySignType': 'RSA2',
            'wechatEnabled': False,
            'wechatAppId': '',
            'wechatMerchantId': '',
            'wechatApiV3Key': '',
            'wechatPrivateKey': '',
            'wechatSerialNo': '',
            'wechatGateway': 'https://api.mch.weixin.qq.com',
            'wechatNotifyUrl': '',
            'wechatReturnUrl': '',
            'paymentAlertEnabled': False,
            'paymentAlertEmails': '',
            'paymentAlertWebhook': '',
        }

    alipay_private_key = str(row['alipay_app_private_key'] or '')
    wechat_api_v3_key = str(row['wechat_api_v3_key'] or '')
    wechat_private_key = str(row['wechat_private_key'] or '')

    if mask_secret:
        alipay_private_key = _mask_secret(alipay_private_key)
        wechat_api_v3_key = _mask_secret(wechat_api_v3_key)
        wechat_private_key = _mask_secret(wechat_private_key)

    return {
        'alipayEnabled': bool(int(row['alipay_enabled'] or 0)),
        'alipayAppId': str(row['alipay_app_id'] or ''),
        'alipayMerchantId': str(row['alipay_merchant_id'] or ''),
        'alipayAppPrivateKey': alipay_private_key,
        'alipayPublicKey': str(row['alipay_public_key'] or ''),
        'alipayGateway': str(row['alipay_gateway'] or 'https://openapi.alipay.com/gateway.do'),
        'alipayNotifyUrl': str(row['alipay_notify_url'] or ''),
        'alipayReturnUrl': str(row['alipay_return_url'] or ''),
        'alipaySignType': str(row['alipay_sign_type'] or 'RSA2'),
        'wechatEnabled': bool(int(row['wechat_enabled'] or 0)),
        'wechatAppId': str(row['wechat_app_id'] or ''),
        'wechatMerchantId': str(row['wechat_merchant_id'] or ''),
        'wechatApiV3Key': wechat_api_v3_key,
        'wechatPrivateKey': wechat_private_key,
        'wechatSerialNo': str(row['wechat_serial_no'] or ''),
        'wechatGateway': str(row['wechat_gateway'] or 'https://api.mch.weixin.qq.com'),
        'wechatNotifyUrl': str(row['wechat_notify_url'] or ''),
        'wechatReturnUrl': str(row['wechat_return_url'] or ''),
        'paymentAlertEnabled': bool(int(row['payment_alert_enabled'] or 0)),
        'paymentAlertEmails': str(row['payment_alert_emails'] or ''),
        'paymentAlertWebhook': str(row['payment_alert_webhook'] or ''),
    }


def _coerce_payment_settings_from_body(body: PaymentSettingsBody) -> dict[str, Any]:
    return {
        'alipayEnabled': bool(body.alipayEnabled),
        'alipayAppId': (body.alipayAppId or '').strip(),
        'alipayMerchantId': (body.alipayMerchantId or '').strip(),
        'alipayAppPrivateKey': str(body.alipayAppPrivateKey or '').strip(),
        'alipayPublicKey': str(body.alipayPublicKey or '').strip(),
        'alipayGateway': (body.alipayGateway or 'https://openapi.alipay.com/gateway.do').strip() or 'https://openapi.alipay.com/gateway.do',
        'alipayNotifyUrl': (body.alipayNotifyUrl or '').strip(),
        'alipayReturnUrl': (body.alipayReturnUrl or '').strip(),
        'alipaySignType': (body.alipaySignType or 'RSA2').strip() or 'RSA2',
        'wechatEnabled': bool(body.wechatEnabled),
        'wechatAppId': (body.wechatAppId or '').strip(),
        'wechatMerchantId': (body.wechatMerchantId or '').strip(),
        'wechatApiV3Key': str(body.wechatApiV3Key or '').strip(),
        'wechatPrivateKey': str(body.wechatPrivateKey or '').strip(),
        'wechatSerialNo': (body.wechatSerialNo or '').strip(),
        'wechatGateway': (body.wechatGateway or 'https://api.mch.weixin.qq.com').strip() or 'https://api.mch.weixin.qq.com',
        'wechatNotifyUrl': (body.wechatNotifyUrl or '').strip(),
        'wechatReturnUrl': (body.wechatReturnUrl or '').strip(),
        'paymentAlertEnabled': bool(body.paymentAlertEnabled),
        'paymentAlertEmails': str(body.paymentAlertEmails or '').strip(),
        'paymentAlertWebhook': str(body.paymentAlertWebhook or '').strip(),
    }


def _get_security_policy() -> dict[str, Any]:
    _ensure_db()
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT password_min_length, password_require_letter,
                   password_require_digit, password_require_special,
                   login_fail_max, login_fail_window_minutes,
                   login_lock_minutes, session_ttl_hours,
                   force_logout_on_password_reset
            FROM security_policy
            WHERE id = 1
            '''
        ).fetchone()

    if not row:
        return dict(_DEFAULT_SECURITY_POLICY)

    return {
        'passwordMinLength': max(6, min(int(row['password_min_length'] or _DEFAULT_SECURITY_POLICY['passwordMinLength']), 64)),
        'passwordRequireLetter': bool(int(row['password_require_letter'] or 0)),
        'passwordRequireDigit': bool(int(row['password_require_digit'] or 0)),
        'passwordRequireSpecial': bool(int(row['password_require_special'] or 0)),
        'loginFailMax': max(3, min(int(row['login_fail_max'] or _DEFAULT_SECURITY_POLICY['loginFailMax']), 20)),
        'loginFailWindowMinutes': max(1, min(int(row['login_fail_window_minutes'] or _DEFAULT_SECURITY_POLICY['loginFailWindowMinutes']), 120)),
        'loginLockMinutes': max(1, min(int(row['login_lock_minutes'] or _DEFAULT_SECURITY_POLICY['loginLockMinutes']), 240)),
        'sessionTtlHours': max(1, min(int(row['session_ttl_hours'] or _DEFAULT_SECURITY_POLICY['sessionTtlHours']), 168)),
        'forceLogoutOnPasswordReset': bool(int(row['force_logout_on_password_reset'] if row['force_logout_on_password_reset'] is not None else 1)),
    }


def _coerce_security_policy_from_body(body: SecurityPolicyBody) -> dict[str, Any]:
    return {
        'passwordMinLength': max(6, min(int(body.passwordMinLength or 8), 64)),
        'passwordRequireLetter': bool(body.passwordRequireLetter),
        'passwordRequireDigit': bool(body.passwordRequireDigit),
        'passwordRequireSpecial': bool(body.passwordRequireSpecial),
        'loginFailMax': max(3, min(int(body.loginFailMax or 5), 20)),
        'loginFailWindowMinutes': max(1, min(int(body.loginFailWindowMinutes or 15), 120)),
        'loginLockMinutes': max(1, min(int(body.loginLockMinutes or 15), 240)),
        'sessionTtlHours': max(1, min(int(body.sessionTtlHours or 24), 168)),
        'forceLogoutOnPasswordReset': bool(body.forceLogoutOnPasswordReset),
    }


def _validate_password_with_policy(password: str, policy: dict[str, Any]) -> Optional[str]:
    raw = str(password or '')
    min_len = max(6, int(policy.get('passwordMinLength') or 8))
    if len(raw) < min_len:
        return f'密码长度至少 {min_len} 位'

    if bool(policy.get('passwordRequireLetter')) and not re.search(r'[A-Za-z]', raw):
        return '密码需包含字母'

    if bool(policy.get('passwordRequireDigit')) and not re.search(r'\d', raw):
        return '密码需包含数字'

    if bool(policy.get('passwordRequireSpecial')) and not re.search(r'[^A-Za-z0-9]', raw):
        return '密码需包含特殊字符'

    return None


def _append_order_state_event(
    conn: sqlite3.Connection,
    order_id: str,
    order_no: str,
    from_status: str,
    to_status: str,
    actor_account_id: str,
    reason: str = '',
    source: str = '',
    detail: Any = None,
):
    detail_text = ''
    if detail is not None:
        try:
            detail_text = json.dumps(detail, ensure_ascii=False)
        except Exception:
            detail_text = str(detail)

    conn.execute(
        '''
        INSERT INTO order_state_events (
            id, order_id, order_no, from_status, to_status,
            reason, actor_account_id, source, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            str(order_id or ''),
            str(order_no or ''),
            str(from_status or ''),
            str(to_status or ''),
            str(reason or ''),
            str(actor_account_id or ''),
            str(source or ''),
            detail_text,
            _now_str(),
        ),
    )


def _generate_unique_order_no(conn: sqlite3.Connection, prefix: str = 'ORD') -> str:
    tag = ''.join(ch for ch in str(prefix or 'ORD').upper() if ch.isalnum()) or 'ORD'
    for _ in range(12):
        candidate = f"{tag}{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}{random.randint(1000, 9999)}"
        exists = conn.execute('SELECT 1 FROM orders WHERE order_no = ? LIMIT 1', (candidate,)).fetchone()
        if not exists:
            return candidate
    return f"{tag}{uuid.uuid4().hex[:20].upper()}"


def _generate_unique_out_trade_no(conn: sqlite3.Connection, prefix: str = 'PAY') -> str:
    tag = ''.join(ch for ch in str(prefix or 'PAY').upper() if ch.isalnum()) or 'PAY'
    for _ in range(12):
        candidate = f"{tag}{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}{random.randint(1000, 9999)}"
        exists = conn.execute('SELECT 1 FROM payment_trades WHERE out_trade_no = ? LIMIT 1', (candidate,)).fetchone()
        if not exists:
            return candidate
    return f"{tag}{uuid.uuid4().hex[:20].upper()}"


def _sum_refunded_amount(conn: sqlite3.Connection, order_id: str) -> float:
    row = conn.execute(
        '''
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM order_refunds
        WHERE order_id = ? AND status IN ('created', 'success', 'processed')
        ''',
        (str(order_id or ''),),
    ).fetchone()
    return float((row['total'] if row else 0) or 0)


def _compute_order_refund_status(order_amount: float, refunded_amount: float) -> str:
    total = float(order_amount or 0)
    refunded = float(refunded_amount or 0)
    if refunded <= 0.000001:
        return 'paid'
    if refunded + 0.000001 < total:
        return 'refund_partial'
    return 'refunded'


def _rollback_subscription_after_full_refund(
    conn: sqlite3.Connection,
    order_row: sqlite3.Row,
    actor_account_id: str,
    reason: str = '',
) -> dict[str, Any]:
    account_row = _resolve_account(conn, str(order_row['account_id'] or ''))
    plan_row = _resolve_plan(conn, str(order_row['plan_code'] or ''))
    if not account_row or not plan_row:
        return {}

    sub_row = conn.execute(
        '''
        SELECT id, status, start_time, expire_time
        FROM subscriptions
        WHERE account_id = ? AND plan_code = ?
        LIMIT 1
        ''',
        (str(order_row['account_id'] or ''), str(order_row['plan_code'] or '')),
    ).fetchone()
    if not sub_row:
        return {}

    now_dt = datetime.now()
    now_str = _now_str()
    duration_days = max(1, int(plan_row['duration_days'] or 30))
    expire_dt = _parse_dt(str(sub_row['expire_time'] or '')) or now_dt
    rollback_expire_dt = expire_dt - timedelta(days=duration_days)

    if rollback_expire_dt <= now_dt:
        new_status = 'expired'
        new_expire = now_str
        member_level = 'basic'
        member_status = 'expired'
    else:
        new_status = 'active'
        new_expire = rollback_expire_dt.strftime('%Y-%m-%d %H:%M:%S')
        member_level = str(plan_row['level'] or 'basic')
        member_status = 'active'

    start_time = str(sub_row['start_time'] or '').strip() or now_str
    conn.execute(
        'UPDATE subscriptions SET status = ?, expire_time = ?, updated_at = ? WHERE id = ?',
        (new_status, new_expire, now_str, str(sub_row['id'] or '')),
    )

    _sync_member_for_account(
        conn,
        account_row=account_row,
        level=member_level,
        status=member_status,
        start_time=start_time,
        expire_time=new_expire,
    )

    return {
        'subscriptionId': str(sub_row['id'] or ''),
        'planCode': str(order_row['plan_code'] or ''),
        'subscriptionStatus': new_status,
        'expireTime': new_expire,
        'reason': reason,
        'actorAccountId': actor_account_id,
    }


def _compact_payload_for_sign(payload: dict[str, Any]) -> str:
    parts = []
    for key in sorted(payload.keys()):
        if key in {'sign', 'signature'}:
            continue
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        parts.append(f'{key}={value}')
    return '&'.join(parts)


def _sign_payload(payload: dict[str, Any], secret: str) -> str:
    text = _compact_payload_for_sign(payload)
    return hmac.new(str(secret or '').encode('utf-8'), text.encode('utf-8'), hashlib.sha256).hexdigest()


def _normalize_pem_key(raw_key: str, key_type: str) -> str:
    text = str(raw_key or '').strip().replace('\r', '')
    if not text:
        return ''

    if '-----BEGIN ' in text and '-----END ' in text:
        return text

    compact = ''.join(text.split())
    if not compact:
        return ''

    lines = [compact[i:i + 64] for i in range(0, len(compact), 64)]
    if key_type == 'public':
        return '-----BEGIN PUBLIC KEY-----\n' + '\n'.join(lines) + '\n-----END PUBLIC KEY-----'
    if key_type == 'rsa_private':
        return '-----BEGIN RSA PRIVATE KEY-----\n' + '\n'.join(lines) + '\n-----END RSA PRIVATE KEY-----'
    return '-----BEGIN PRIVATE KEY-----\n' + '\n'.join(lines) + '\n-----END PRIVATE KEY-----'


def _openssl_sign_sha256_base64(text: str, private_key_pem: str) -> str:
    if not private_key_pem:
        raise ValueError('缺少应用私钥')

    data = str(text or '').encode('utf-8')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as key_file:
        key_file.write(private_key_pem)
        key_path = key_file.name

    with tempfile.NamedTemporaryFile(mode='wb', delete=False) as data_file:
        data_file.write(data)
        data_path = data_file.name

    try:
        result = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-sign', key_path, data_path],
            capture_output=True,
            check=True,
        )
        return base64.b64encode(result.stdout).decode('utf-8')
    finally:
        try:
            os.remove(key_path)
        except Exception:
            pass
        try:
            os.remove(data_path)
        except Exception:
            pass


def _openssl_verify_sha256_base64(text: str, signature_b64: str, public_key_pem: str) -> bool:
    if not signature_b64 or not public_key_pem:
        return False

    try:
        signature = base64.b64decode(str(signature_b64).strip())
    except Exception:
        return False

    data = str(text or '').encode('utf-8')

    with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as pub_file:
        pub_file.write(public_key_pem)
        pub_path = pub_file.name

    with tempfile.NamedTemporaryFile(mode='wb', delete=False) as data_file:
        data_file.write(data)
        data_path = data_file.name

    with tempfile.NamedTemporaryFile(mode='wb', delete=False) as sig_file:
        sig_file.write(signature)
        sig_path = sig_file.name

    try:
        verify_result = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-verify', pub_path, '-signature', sig_path, data_path],
            capture_output=True,
            check=False,
        )
        return verify_result.returncode == 0
    finally:
        for path in (pub_path, data_path, sig_path):
            try:
                os.remove(path)
            except Exception:
                pass


def _alipay_sign_text(payload: dict[str, Any], include_sign_type: bool = True) -> str:
    items: list[str] = []
    for key in sorted(payload.keys()):
        if key == 'sign':
            continue
        if not include_sign_type and key == 'sign_type':
            continue

        value = payload.get(key)
        if value is None:
            continue
        text = str(value)
        if text == '':
            continue
        items.append(f'{key}={text}')
    return '&'.join(items)


def _alipay_sign_payload(payload: dict[str, Any], app_private_key: str) -> str:
    sign_text = _alipay_sign_text(payload)
    private_key_pem = _normalize_pem_key(app_private_key, 'private')
    try:
        return _openssl_sign_sha256_base64(sign_text, private_key_pem)
    except Exception:
        # 兼容部分 RSA 私钥格式
        rsa_private_pem = _normalize_pem_key(app_private_key, 'rsa_private')
        return _openssl_sign_sha256_base64(sign_text, rsa_private_pem)


def _alipay_verify_payload(payload: dict[str, Any], alipay_public_key: str) -> bool:
    sign = str(payload.get('sign') or '').strip()
    if not sign:
        return False

    sign_text = _alipay_sign_text(payload, include_sign_type=False)
    public_key_pem = _normalize_pem_key(alipay_public_key, 'public')
    return _openssl_verify_sha256_base64(sign_text, sign, public_key_pem)


def _alipay_precreate(
    settings: dict[str, Any],
    out_trade_no: str,
    amount: float,
    subject: str,
    body: str = '',
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    gateway = str(settings.get('alipayGateway') or 'https://openapi.alipay.com/gateway.do').strip()
    if not gateway:
        gateway = 'https://openapi.alipay.com/gateway.do'

    notify_url = str(settings.get('alipayNotifyUrl') or '').strip()
    if not notify_url:
        raise ValueError('支付宝异步回调地址不能为空')

    biz_content: dict[str, Any] = {
        'out_trade_no': out_trade_no,
        'total_amount': f'{float(amount):.2f}',
        'subject': subject,
        'product_code': 'FACE_TO_FACE_PAYMENT',
    }
    body_text = str(body or '').strip()
    if body_text:
        biz_content['body'] = body_text

    request_payload: dict[str, Any] = {
        'app_id': str(settings.get('alipayAppId') or '').strip(),
        'method': 'alipay.trade.precreate',
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': str(settings.get('alipaySignType') or 'RSA2').strip() or 'RSA2',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
        'notify_url': notify_url,
        'biz_content': json.dumps(biz_content, ensure_ascii=False, separators=(',', ':')),
    }

    request_payload['sign'] = _alipay_sign_payload(request_payload, str(settings.get('alipayAppPrivateKey') or ''))

    # 支付宝网关对 charset 解析较敏感：参数放 URL 查询串可避免“验签出错/charset”问题
    response = requests.get(gateway, params=request_payload, timeout=20)
    response.raise_for_status()
    response_data = response.json() if response.text else {}
    biz_response = response_data.get('alipay_trade_precreate_response') or {}

    if str(biz_response.get('code') or '') != '10000':
        msg = str(biz_response.get('sub_msg') or biz_response.get('msg') or '支付宝预下单失败')
        raise ValueError(msg)

    qr_code = str(biz_response.get('qr_code') or '').strip()
    if not qr_code:
        raise ValueError('支付宝未返回二维码内容')

    return biz_response, qr_code, request_payload


def _verify_callback_signature(provider: str, payload: dict[str, Any], settings: dict[str, Any]) -> bool:
    provider_key = str(provider or '').strip().lower()
    if provider_key not in {'alipay', 'wechat'}:
        return False

    if provider_key == 'alipay':
        return _alipay_verify_payload(payload, str(settings.get('alipayPublicKey') or ''))

    provided = str(payload.get('sign') or payload.get('signature') or '').strip().lower()
    if not provided:
        return False

    secret = str(settings.get('wechatApiV3Key') or '')
    if not secret:
        return False

    expected = _sign_payload(payload, secret)
    return hmac.compare_digest(expected, provided)


def _build_payment_request_payload(provider: str, trade_row: sqlite3.Row, settings: dict[str, Any]) -> dict[str, Any]:
    provider_key = str(provider or '').strip().lower()
    out_trade_no = str(trade_row['out_trade_no'] or '')
    amount = float(trade_row['amount'] or 0)
    description = f"AiceMind 订单支付 {trade_row['order_no']}"

    if provider_key == 'alipay':
        return {
            'app_id': settings.get('alipayAppId') or '',
            'merchant_id': settings.get('alipayMerchantId') or '',
            'method': 'alipay.trade.page.pay',
            'charset': 'utf-8',
            'sign_type': settings.get('alipaySignType') or 'RSA2',
            'notify_url': settings.get('alipayNotifyUrl') or '',
            'return_url': settings.get('alipayReturnUrl') or '',
            'biz_content': {
                'out_trade_no': out_trade_no,
                'total_amount': f'{amount:.2f}',
                'subject': description,
                'product_code': 'FAST_INSTANT_TRADE_PAY',
            },
        }

    return {
        'appid': settings.get('wechatAppId') or '',
        'mchid': settings.get('wechatMerchantId') or '',
        'description': description,
        'out_trade_no': out_trade_no,
        'notify_url': settings.get('wechatNotifyUrl') or '',
        'amount': {
            'total': int(round(amount * 100)),
            'currency': str(trade_row['currency'] or 'CNY'),
        },
    }


def _extract_payment_notify_payload(provider: str, payload: dict[str, Any]) -> tuple[str, float, str, str, str]:
    provider_key = str(provider or '').strip().lower()

    if provider_key == 'alipay':
        out_trade_no = str(payload.get('out_trade_no') or '')
        amount = float(payload.get('total_amount') or payload.get('amount') or 0)
        status = str(payload.get('trade_status') or payload.get('status') or '').strip().upper()
        gateway_trade_no = str(payload.get('trade_no') or '')
        event_key = str(payload.get('notify_id') or gateway_trade_no or f'{out_trade_no}:{status}:{amount}')
        return out_trade_no, amount, status, gateway_trade_no, event_key

    out_trade_no = str(payload.get('out_trade_no') or payload.get('outTradeNo') or '')
    amount_cents = payload.get('amount_total')
    if amount_cents is None and isinstance(payload.get('amount'), dict):
        amount_cents = payload.get('amount', {}).get('total')
    if amount_cents is None:
        amount_cents = payload.get('total')
    amount = float(amount_cents or 0) / 100.0
    status = str(payload.get('trade_state') or payload.get('status') or '').strip().upper()
    gateway_trade_no = str(payload.get('transaction_id') or payload.get('trade_no') or '')
    event_key = str(payload.get('event_id') or gateway_trade_no or f'{out_trade_no}:{status}:{amount}')
    return out_trade_no, amount, status, gateway_trade_no, event_key


def _is_payment_success(provider: str, status_text: str) -> bool:
    status = str(status_text or '').strip().upper()
    if str(provider or '').strip().lower() == 'alipay':
        return status in {'TRADE_SUCCESS', 'TRADE_FINISHED', 'SUCCESS', 'PAID'}
    return status in {'SUCCESS', 'TRADE_SUCCESS', 'PAID'}


def _parse_reconcile_date_or_default(value: str) -> str:
    text = str(value or '').strip()
    if text:
        dt = _parse_dt(text)
        if not dt:
            raise ValueError('对账日期格式错误，请使用 YYYY-MM-DD')
        return dt.strftime('%Y-%m-%d')

    return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')


def _event_paid_amount(provider: str, payload_text: str) -> float:
    provider_key = str(provider or '').strip().lower()
    if not payload_text:
        return 0.0
    try:
        payload = json.loads(payload_text)
    except Exception:
        return 0.0

    if not isinstance(payload, dict):
        return 0.0

    if provider_key == 'alipay':
        return float(payload.get('total_amount') or payload.get('amount') or 0)

    amount_cents = payload.get('amount_total')
    if amount_cents is None and isinstance(payload.get('amount'), dict):
        amount_cents = payload.get('amount', {}).get('total')
    if amount_cents is None:
        amount_cents = payload.get('total')
    return float(amount_cents or 0) / 100.0


def _human_limit(value: Any, unit: str = '') -> str:
    try:
        num = int(value)
    except Exception:
        num = 0
    if num < 0:
        return f'不限{unit}'
    return f'{num}{unit}' if unit else str(num)


def _build_policy_rights_tips(policy: dict[str, Any]) -> list[str]:
    p = policy or {}
    tips: list[str] = []

    if bool(p.get('chat_enabled')):
        tips.append(f"智能对话：{_human_limit(p.get('chat_monthly_limit'), '次/月')} · {_human_limit(p.get('chat_daily_limit'), '次/日')}")
    else:
        tips.append('智能对话：未开通')

    if bool(p.get('backtest_enabled')):
        tips.append(f"策略回测：{_human_limit(p.get('backtest_monthly_limit'), '次/月')} · {_human_limit(p.get('backtest_daily_limit'), '次/日')}")
    else:
        tips.append('策略回测：未开通')

    tips.append(f"单次回测股票上限：{_human_limit(p.get('max_backtest_stocks'), '只')}")
    tips.append(f"回测时间跨度上限：{_human_limit(p.get('max_backtest_days'), '天')}")
    tips.append(f"回测积分倍率：x{max(1, int(p.get('backtest_point_multiplier', 1) or 1))}")
    tips.append(f"每日积分刷新：{_human_limit(p.get('daily_points_refresh'), '分/日')}")
    tips.append('报告下载：已开通' if bool(p.get('report_download_enabled')) else '报告下载：未开通')

    return tips


def _run_payment_reconcile(
    conn: sqlite3.Connection,
    provider: str,
    reconcile_date: str,
    actor_account_id: str = '',
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row

    provider_key = str(provider or '').strip().lower()
    if provider_key not in {'alipay', 'wechat'}:
        raise ValueError('仅支持 alipay 或 wechat')

    date_key = _parse_reconcile_date_or_default(reconcile_date)
    range_start = f'{date_key} 00:00:00'
    range_end = f'{date_key} 23:59:59'

    # 清理同日旧结果（重跑）
    old_run = conn.execute(
        'SELECT id FROM payment_reconcile_runs WHERE provider = ? AND reconcile_date = ? LIMIT 1',
        (provider_key, date_key),
    ).fetchone()
    if old_run:
        conn.execute('DELETE FROM payment_reconcile_items WHERE run_id = ?', (str(old_run['id'] or ''),))
        conn.execute('DELETE FROM payment_reconcile_runs WHERE id = ?', (str(old_run['id'] or ''),))

    local_rows = conn.execute(
        '''
        SELECT t.out_trade_no, t.order_no, t.amount
        FROM payment_trades t
        WHERE t.provider = ?
          AND t.status = 'paid'
          AND t.paid_at >= ? AND t.paid_at <= ?
        ''',
        (provider_key, range_start, range_end),
    ).fetchall()

    event_rows = conn.execute(
        '''
        SELECT e.out_trade_no, e.payload
        FROM payment_events e
        WHERE e.provider = ?
          AND e.processed = 1
          AND e.processed_message = 'paid'
          AND e.created_at >= ? AND e.created_at <= ?
        ''',
        (provider_key, range_start, range_end),
    ).fetchall()

    local_map = {str(r['out_trade_no'] or ''): r for r in local_rows if str(r['out_trade_no'] or '').strip()}
    event_map = {str(r['out_trade_no'] or ''): r for r in event_rows if str(r['out_trade_no'] or '').strip()}

    local_paid_count = len(local_rows)
    local_paid_amount = round(sum(float(r['amount'] or 0) for r in local_rows), 2)
    callback_paid_count = len(event_rows)
    callback_paid_amount = round(
        sum(_event_paid_amount(provider_key, str(r['payload'] or '')) for r in event_rows),
        2,
    )

    mismatch_items: list[dict[str, Any]] = []

    for out_trade_no, local in local_map.items():
        if out_trade_no not in event_map:
            mismatch_items.append(
                {
                    'itemType': 'missing_callback_event',
                    'outTradeNo': out_trade_no,
                    'orderNo': str(local['order_no'] or ''),
                    'localAmount': float(local['amount'] or 0),
                    'callbackAmount': 0.0,
                    'detail': '本地支付成功，但对账日内未收到回调成功事件',
                }
            )
            continue

        callback_amount = _event_paid_amount(provider_key, str(event_map[out_trade_no]['payload'] or ''))
        local_amount = float(local['amount'] or 0)
        if abs(local_amount - callback_amount) > 0.01:
            mismatch_items.append(
                {
                    'itemType': 'amount_mismatch',
                    'outTradeNo': out_trade_no,
                    'orderNo': str(local['order_no'] or ''),
                    'localAmount': local_amount,
                    'callbackAmount': callback_amount,
                    'detail': '本地支付金额与回调金额不一致',
                }
            )

    for out_trade_no, event in event_map.items():
        if out_trade_no in local_map:
            continue
        mismatch_items.append(
            {
                'itemType': 'orphan_callback_event',
                'outTradeNo': out_trade_no,
                'orderNo': '',
                'localAmount': 0.0,
                'callbackAmount': _event_paid_amount(provider_key, str(event['payload'] or '')),
                'detail': '对账日内存在回调成功事件，但本地无对应支付成功交易',
            }
        )

    now = _now_str()
    run_id = uuid.uuid4().hex
    summary = {
        'provider': provider_key,
        'reconcileDate': date_key,
        'localPaidCount': local_paid_count,
        'localPaidAmount': local_paid_amount,
        'callbackPaidCount': callback_paid_count,
        'callbackPaidAmount': callback_paid_amount,
        'mismatchCount': len(mismatch_items),
    }

    conn.execute(
        '''
        INSERT INTO payment_reconcile_runs (
            id, provider, reconcile_date,
            local_paid_count, local_paid_amount,
            callback_paid_count, callback_paid_amount,
            mismatch_count, status, detail, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'done', ?, ?, ?)
        ''',
        (
            run_id,
            provider_key,
            date_key,
            local_paid_count,
            local_paid_amount,
            callback_paid_count,
            callback_paid_amount,
            len(mismatch_items),
            json.dumps(summary, ensure_ascii=False),
            now,
            now,
        ),
    )

    for item in mismatch_items:
        conn.execute(
            '''
            INSERT INTO payment_reconcile_items (
                id, run_id, item_type, provider,
                out_trade_no, order_no, local_amount, callback_amount,
                detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                uuid.uuid4().hex,
                run_id,
                item['itemType'],
                provider_key,
                item['outTradeNo'],
                item['orderNo'],
                float(item['localAmount'] or 0),
                float(item['callbackAmount'] or 0),
                str(item['detail'] or ''),
                now,
            ),
        )

    _audit_log(
        conn,
        actor_account_id,
        'payment.reconcile.run',
        'payment_reconcile',
        run_id,
        summary,
    )

    summary['id'] = run_id
    summary['items'] = mismatch_items
    return summary


def _apply_paid_trade(
    conn: sqlite3.Connection,
    trade_row: sqlite3.Row,
    callback_payload: dict[str, Any],
    verified: bool,
    gateway_trade_no: str,
    provider_status: str,
):
    now = _now_str()
    trade_id = str(trade_row['id'] or '')
    order_id = str(trade_row['order_id'] or '')

    if str(trade_row['status'] or '') != 'paid':
        conn.execute(
            '''
            UPDATE payment_trades
            SET status = 'paid',
                callback_payload = ?,
                callback_verified = ?,
                callback_at = ?,
                paid_at = ?,
                gateway_trade_no = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (
                json.dumps(callback_payload, ensure_ascii=False),
                1 if verified else 0,
                now,
                now,
                gateway_trade_no,
                now,
                trade_id,
            ),
        )

    order_row = conn.execute(
        'SELECT id, order_no, account_id, plan_code, status, note FROM orders WHERE id = ? LIMIT 1',
        (order_id,),
    ).fetchone()
    if not order_row:
        return

    if str(order_row['status'] or '') != 'paid':
        conn.execute(
            'UPDATE orders SET status = ?, paid_at = ?, updated_at = ? WHERE id = ?',
            ('paid', now, now, order_id),
        )

        note_text = str(order_row['note'] or '')
        is_test_order = '[TEST_PAY]' in note_text

        if is_test_order:
            new_expire = None
        else:
            account_row = _resolve_account(conn, str(order_row['account_id'] or ''))
            plan_row = _resolve_plan(conn, str(order_row['plan_code'] or ''))
            if account_row and plan_row:
                new_expire = _apply_plan_to_account(conn, account_row, plan_row, now)
            else:
                new_expire = None

        _audit_log(
            conn,
            str(order_row['account_id'] or ''),
            'payment.callback_paid',
            'order',
            order_id,
            {
                'orderNo': order_row['order_no'],
                'providerStatus': provider_status,
                'gatewayTradeNo': gateway_trade_no,
                'expireTime': new_expire,
                'isTestOrder': is_test_order,
            },
        )


def _close_timeout_orders(
    conn: sqlite3.Connection,
    account_id: str = '',
    actor_account_id: str = 'system-auto',
    reason: str = 'order timeout auto close',
) -> list[dict[str, Any]]:
    now = _now_str()
    params: list[Any] = [now]
    where = ["o.status = 'created'", "o.expire_at != ''", 'datetime(o.expire_at) <= datetime(?)']
    if str(account_id or '').strip():
        where.append('o.account_id = ?')
        params.append(str(account_id or '').strip())

    rows = conn.execute(
        f'''
        SELECT o.id, o.order_no, o.account_id, o.expire_at, o.note
        FROM orders o
        WHERE {' AND '.join(where)}
        ORDER BY datetime(o.created_at) ASC
        LIMIT 500
        ''',
        tuple(params),
    ).fetchall()

    closed: list[dict[str, Any]] = []
    for row in rows:
        order_id = str(row['id'] or '')
        order_no = str(row['order_no'] or '')
        current = conn.execute('SELECT status FROM orders WHERE id = ? LIMIT 1', (order_id,)).fetchone()
        if not current or str(current['status'] or '') != 'created':
            continue

        note = str(row['note'] or '').strip()
        timeout_mark = '[AUTO_TIMEOUT]'
        next_note = note if timeout_mark in note else (f"{note} {timeout_mark}".strip())

        conn.execute(
            'UPDATE orders SET status = ?, updated_at = ?, note = ? WHERE id = ?',
            ('cancelled', now, next_note, order_id),
        )
        conn.execute(
            "UPDATE payment_trades SET status = ?, updated_at = ? WHERE order_id = ? AND status IN ('created', 'pending')",
            ('timeout', now, order_id),
        )
        _append_order_state_event(
            conn,
            order_id=order_id,
            order_no=order_no,
            from_status='created',
            to_status='cancelled',
            actor_account_id=actor_account_id,
            reason=reason,
            source='system.order.auto_timeout',
            detail={'expireAt': str(row['expire_at'] or '')},
        )

        closed.append({'orderId': order_id, 'orderNo': order_no, 'expireAt': str(row['expire_at'] or '')})

    return closed


def _find_recent_unpaid_trade_by_plan(
    conn: sqlite3.Connection,
    account_id: str,
    plan_code: str,
    provider: str,
    window_minutes: int = _ORDER_IDEMPOTENCY_WINDOW_MINUTES,
) -> Optional[sqlite3.Row]:
    minutes = max(1, int(window_minutes or _ORDER_IDEMPOTENCY_WINDOW_MINUTES))
    return conn.execute(
        '''
        SELECT
            t.id, t.order_id, t.order_no, t.out_trade_no, t.provider,
            t.amount, t.currency, t.status AS trade_status,
            t.callback_payload, t.created_at AS trade_created_at,
            o.status AS order_status, o.expire_at,
            p.name AS plan_name, p.code AS plan_code
        FROM payment_trades t
        JOIN orders o ON o.id = t.order_id
        LEFT JOIN plans p ON p.code = o.plan_code
        WHERE t.account_id = ?
          AND o.plan_code = ?
          AND t.provider = ?
          AND o.status = 'created'
          AND t.status IN ('created', 'pending')
          AND datetime(t.created_at) >= datetime('now', ?)
          AND (o.expire_at = '' OR datetime(o.expire_at) > datetime('now'))
        ORDER BY datetime(t.created_at) DESC
        LIMIT 1
        ''',
        (account_id, plan_code, provider, f'-{minutes} minutes'),
    ).fetchone()


def _alipay_trade_query(settings: dict[str, Any], out_trade_no: str) -> tuple[dict[str, Any], dict[str, Any]]:
    gateway = str(settings.get('alipayGateway') or 'https://openapi.alipay.com/gateway.do').strip()
    if not gateway:
        gateway = 'https://openapi.alipay.com/gateway.do'

    biz_content = {'out_trade_no': str(out_trade_no or '').strip()}
    request_payload: dict[str, Any] = {
        'app_id': str(settings.get('alipayAppId') or '').strip(),
        'method': 'alipay.trade.query',
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': str(settings.get('alipaySignType') or 'RSA2').strip() or 'RSA2',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
        'biz_content': json.dumps(biz_content, ensure_ascii=False, separators=(',', ':')),
    }
    request_payload['sign'] = _alipay_sign_payload(request_payload, str(settings.get('alipayAppPrivateKey') or ''))

    response = requests.get(gateway, params=request_payload, timeout=20)
    response.raise_for_status()
    response_data = response.json() if response.text else {}
    biz_response = response_data.get('alipay_trade_query_response') or {}
    return response_data, biz_response


def _alipay_trade_refund(
    settings: dict[str, Any],
    out_trade_no: str,
    refund_amount: float,
    out_request_no: str,
    reason: str = '',
) -> tuple[dict[str, Any], dict[str, Any]]:
    gateway = str(settings.get('alipayGateway') or 'https://openapi.alipay.com/gateway.do').strip()
    if not gateway:
        gateway = 'https://openapi.alipay.com/gateway.do'

    biz_content: dict[str, Any] = {
        'out_trade_no': str(out_trade_no or '').strip(),
        'refund_amount': f'{float(refund_amount):.2f}',
        'out_request_no': str(out_request_no or '').strip(),
    }
    if str(reason or '').strip():
        biz_content['refund_reason'] = str(reason or '').strip()[:256]

    request_payload: dict[str, Any] = {
        'app_id': str(settings.get('alipayAppId') or '').strip(),
        'method': 'alipay.trade.refund',
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': str(settings.get('alipaySignType') or 'RSA2').strip() or 'RSA2',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
        'biz_content': json.dumps(biz_content, ensure_ascii=False, separators=(',', ':')),
    }
    request_payload['sign'] = _alipay_sign_payload(request_payload, str(settings.get('alipayAppPrivateKey') or ''))

    response = requests.get(gateway, params=request_payload, timeout=20)
    response.raise_for_status()
    response_data = response.json() if response.text else {}
    biz_response = response_data.get('alipay_trade_refund_response') or {}
    return response_data, biz_response


def _record_inapp_notification(
    conn: sqlite3.Connection,
    account_id: str,
    title: str,
    content: str,
    ntype: str = 'renewal',
):
    now = _now_str()
    conn.execute(
        '''
        INSERT INTO inapp_notifications (
            id, account_id, title, content, type, read_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
        ''',
        (uuid.uuid4().hex, account_id, title, content, ntype, now, now),
    )


def _require_user(authorization: Optional[str]) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')

    token = authorization.split(' ', 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail='Unauthorized')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            session_row = _query_active_session(conn, token)
            if not session_row:
                raise HTTPException(status_code=401, detail='Unauthorized')

            account_id = str(session_row['account_id'] or '').strip()
            user = get_account_identity(account_id)
            if not user:
                raise HTTPException(status_code=401, detail='Unauthorized')

    user['entitlement'] = _load_user_entitlement(user)
    _ADMIN_TOKENS[token] = user
    return user


def _require_admin(user: dict[str, Any]):
    roles = set(user.get('roles') or [])
    if not ({'super', 'admin'} & roles):
        raise HTTPException(status_code=403, detail='Forbidden')


def _build_dashboard_menu() -> dict[str, Any]:
    return {
        'meta': {
            'icon': 'lucide:layout-dashboard',
            'order': -1,
            'title': 'page.dashboard.title',
        },
        'name': 'Dashboard',
        'path': '/dashboard',
        'redirect': '/workspace',
        'children': [
            {
                'name': 'Analytics',
                'path': '/analytics',
                'component': '/dashboard/analytics/index',
                'meta': {
                    'affixTab': True,
                    'icon': 'lucide:area-chart',
                    'title': 'page.dashboard.analytics',
                },
            },
            {
                'name': 'Workspace',
                'path': '/workspace',
                'component': '/dashboard/workspace/index',
                'meta': {
                    'icon': 'carbon:workspace',
                    'title': 'page.dashboard.workspace',
                },
            },
        ],
    }


def _build_user_manage_menu() -> dict[str, Any]:
    return {
        'meta': {
            'icon': 'mdi:account-group',
            'keepAlive': True,
            'order': 100,
            'title': '用户管理',
        },
        'name': 'UserManagement',
        'path': '/user',
        'redirect': '/user/list',
        'children': [
            {
                'name': 'UserList',
                'path': '/user/list',
                'component': '/user/list/index',
                'meta': {
                    'icon': 'mdi:account-multiple',
                    'title': '用户列表',
                },
            }
        ],
    }


def _build_system_menu() -> dict[str, Any]:
    return {
        'meta': {
            'icon': 'carbon:settings',
            'keepAlive': True,
            'order': 200,
            'title': '系统设置',
        },
        'name': 'SystemSettings',
        'path': '/system',
        'redirect': '/system/email-settings',
        'children': [
            {
                'name': 'SystemBaseConfigGroup',
                'path': '/system/base-config',
                'redirect': '/system/email-settings',
                'meta': {
                    'icon': 'mdi:cog-outline',
                    'title': '基础配置',
                },
                'children': [
                    {
                        'name': 'SystemEmailSettings',
                        'path': '/system/email-settings',
                        'component': '/system/email-settings/index',
                        'meta': {
                            'icon': 'mdi:email-cog-outline',
                            'title': '邮箱设置',
                        },
                    },
                    {
                        'name': 'SystemTools',
                        'path': '/system/tools',
                        'component': '/system/system-tools/index',
                        'meta': {
                            'icon': 'mdi:wrench-cog-outline',
                            'title': '系统工具',
                        },
                    },
                ],
            },
            {
                'name': 'SystemCommerceGroup',
                'path': '/system/commerce',
                'redirect': '/system/plans',
                'meta': {
                    'icon': 'mdi:store-outline',
                    'title': '商业化管理',
                },
                'children': [
                    {
                        'name': 'SystemPaymentSettings',
                        'path': '/system/payment-settings',
                        'component': '/system/payment-settings/index',
                        'meta': {
                            'icon': 'mdi:credit-card-settings-outline',
                            'title': '支付设置',
                        },
                    },
                    {
                        'name': 'SystemPlans',
                        'path': '/system/plans',
                        'component': '/system/plans/index',
                        'meta': {
                            'icon': 'mdi:card-account-details-outline',
                            'title': '套餐管理',
                        },
                    },
                    {
                        'name': 'SystemSubscriptions',
                        'path': '/system/subscriptions',
                        'component': '/system/subscriptions/index',
                        'meta': {
                            'icon': 'mdi:calendar-check-outline',
                            'title': '订阅管理',
                        },
                    },
                    {
                        'name': 'SystemOrders',
                        'path': '/system/orders',
                        'component': '/system/orders/index',
                        'meta': {
                            'icon': 'mdi:cash-multiple',
                            'title': '订单管理',
                        },
                    },
                ],
            },
            {
                'name': 'SystemSecurityComplianceGroup',
                'path': '/system/security-compliance',
                'redirect': '/system/security-center',
                'meta': {
                    'icon': 'mdi:shield-check-outline',
                    'title': '安全与合规',
                },
                'children': [
                    {
                        'name': 'SystemSecurityCenter',
                        'path': '/system/security-center',
                        'component': '/system/security-center/index',
                        'meta': {
                            'icon': 'mdi:shield-account-outline',
                            'title': '安全中心',
                        },
                    },
                    {
                        'name': 'SystemAuditLogs',
                        'path': '/system/audit-logs',
                        'component': '/system/audit-logs/index',
                        'meta': {
                            'icon': 'mdi:file-document-edit-outline',
                            'title': '审计日志',
                        },
                    },
                    {
                        'name': 'SystemLegalCompliance',
                        'path': '/system/legal-compliance',
                        'component': '/system/legal-compliance/index',
                        'meta': {
                            'icon': 'mdi:file-certificate-outline',
                            'title': '合规文档',
                        },
                    },
                    {
                        'name': 'SystemAccountDeletion',
                        'path': '/system/account-deletion',
                        'component': '/system/account-deletion/index',
                        'meta': {
                            'icon': 'mdi:account-remove-outline',
                            'title': '账号注销审批',
                        },
                    },
                ],
            },
            {
                'name': 'SystemOpsMonitorGroup',
                'path': '/system/ops-monitor',
                'redirect': '/system/monitor-user-actions',
                'meta': {
                    'icon': 'mdi:chart-line',
                    'title': '运营与监控',
                },
                'children': [
                    {
                        'name': 'SystemMonitorUserActions',
                        'path': '/system/monitor-user-actions',
                        'component': '/system/monitor-user-actions/index',
                        'meta': {
                            'icon': 'mdi:history',
                            'title': '用户操作记录',
                        },
                    },
                    {
                        'name': 'SystemMonitorBacktestRecords',
                        'path': '/system/monitor-backtest-records',
                        'component': '/system/monitor-backtest-records/index',
                        'meta': {
                            'icon': 'mdi:chart-line',
                            'title': '回测全局记录',
                        },
                    },
                    {
                        'name': 'SystemMonitorPoints',
                        'path': '/system/monitor-points',
                        'component': '/system/monitor-points/index',
                        'meta': {
                            'icon': 'mdi:star-circle-outline',
                            'title': '积分流水监控',
                        },
                    },
                    {
                        'name': 'SystemObservability',
                        'path': '/system/observability',
                        'component': '/system/observability/index',
                        'meta': {
                            'icon': 'mdi:chart-timeline-variant',
                            'title': '观测与告警',
                        },
                    },
                ],
            },
        ],
    }


def _unique_username_from_email(conn: sqlite3.Connection, email: str) -> str:
    base = re.sub(r'[^a-z0-9_]', '', email.split('@')[0].lower())
    if not base:
        base = 'user'

    candidate = base
    i = 0
    while True:
        exists = conn.execute(
            'SELECT 1 FROM user_accounts WHERE username = ?',
            (candidate,),
        ).fetchone()
        if not exists:
            return candidate
        i += 1
        candidate = f'{base}{i}'


def _ensure_member_for_user(conn: sqlite3.Connection, username: str, email: str, nickname: str):
    exists = conn.execute(
        'SELECT 1 FROM member_users WHERE user_id = ?',
        (username,),
    ).fetchone()
    if exists:
        return

    now = _now_str()
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
            uuid.uuid4().hex,
            nickname,
            username,
            email,
            'basic',
            'active',
            now,
            _NO_EXPIRE_TIME,
            0,
            now,
            now,
        ),
    )


def _validate_email_settings(settings: dict[str, Any]) -> Optional[str]:
    required = ['smtpHost', 'smtpPort', 'smtpUsername', 'smtpPassword', 'fromEmail']
    if any(not str(settings.get(k) or '').strip() for k in required):
        return '请先在系统设置中完善邮箱参数'

    from_email = str(settings.get('fromEmail') or '').strip().lower()
    if from_email and not _EMAIL_RE.match(from_email):
        return '发件邮箱格式错误'

    return None


def _render_template(template: str, variables: dict[str, Any]) -> str:
    content = template or ''
    for key, value in variables.items():
        content = content.replace(f'{{{{{key}}}}}', str(value))
    return content


def _send_email(settings: dict[str, Any], to_email: str, subject: str, content: str):
    msg = EmailMessage()
    from_name = (settings.get('fromName') or 'AiceMind').strip() or 'AiceMind'
    from_email = (settings.get('fromEmail') or '').strip()
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{from_email}>' if from_email else from_name
    msg['To'] = to_email
    msg.set_content(content)

    host = (settings.get('smtpHost') or '').strip()
    port = int(settings.get('smtpPort') or 465)
    username = (settings.get('smtpUsername') or '').strip()
    password = str(settings.get('smtpPassword') or '')
    use_ssl = bool(settings.get('useSSL'))
    use_tls = bool(settings.get('useTLS'))

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=15) as server:
            if username:
                server.login(username, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(msg)


def _build_register_mail(settings: dict[str, Any], email: str, code: str, expire_minutes: int):
    variables = {
        'app_name': 'AiceMind',
        'code': code,
        'expire_minutes': expire_minutes,
        'email': email,
        'nickname_or_email': email,
        'now': _now_str(),
    }

    subject_template = str(settings.get('verifySubjectTemplate') or '').strip() or _DEFAULT_VERIFY_SUBJECT_TEMPLATE
    body_template = str(settings.get('verifyBodyTemplate') or '').strip() or _DEFAULT_VERIFY_BODY_TEMPLATE
    subject = _render_template(subject_template, variables)
    content = _render_template(body_template, variables)
    return subject, content


def _send_register_code_email(settings: dict[str, Any], to_email: str, code: str, expire_minutes: int = 10):
    subject, content = _build_register_mail(settings, to_email, code, expire_minutes)
    _send_email(settings, to_email, subject, content)


def _build_reset_password_mail(settings: dict[str, Any], email: str, code: str, expire_minutes: int):
    subject = f"【AiceMind】重置密码验证码"
    content = (
        f"你好，\n\n"
        f"你正在进行密码重置，本次验证码为：{code}\n"
        f"验证码将在 {expire_minutes} 分钟后失效。\n\n"
        f"请求邮箱：{email}\n"
        f"发送时间：{_now_str()}\n\n"
        f"如果不是你本人操作，请忽略此邮件并尽快修改密码。"
    )
    return subject, content


def _send_reset_password_code_email(settings: dict[str, Any], to_email: str, code: str, expire_minutes: int = 10):
    subject, content = _build_reset_password_mail(settings, to_email, code, expire_minutes)
    _send_email(settings, to_email, subject, content)


def _totp_random_secret() -> str:
    raw = os.urandom(20)
    return base64.b32encode(raw).decode('utf-8').replace('=', '')


def _totp_code(secret: str, ts: Optional[int] = None, step: int = 30, digits: int = 6) -> str:
    secret_text = str(secret or '').strip().replace(' ', '').upper()
    if not secret_text:
        return ''
    pad = '=' * ((8 - (len(secret_text) % 8)) % 8)
    key = base64.b32decode(secret_text + pad, casefold=True)
    counter = int((ts if ts is not None else int(time.time())) // step)
    msg = counter.to_bytes(8, 'big')
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = ((digest[offset] & 0x7F) << 24) | ((digest[offset + 1] & 0xFF) << 16) | ((digest[offset + 2] & 0xFF) << 8) | (digest[offset + 3] & 0xFF)
    code = str(code_int % (10 ** digits)).zfill(digits)
    return code


def _verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    c = str(code or '').strip()
    if not re.fullmatch(r'\d{6}', c):
        return False
    now_ts = int(time.time())
    for delta in range(-int(window), int(window) + 1):
        if _totp_code(secret, ts=now_ts + delta * 30) == c:
            return True
    return False


def _client_ip_from_request(request: Optional[Request]) -> str:
    if request is None:
        return ''
    forwarded = str(request.headers.get('x-forwarded-for') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = str(request.headers.get('x-real-ip') or '').strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return str(request.client.host)
    return ''


def _detect_login_risk(conn: sqlite3.Connection, account_id: str, login_ip: str, user_agent: str) -> tuple[str, str]:
    if not account_id:
        return 'low', ''
    row = conn.execute(
        '''
        SELECT detail
        FROM audit_logs
        WHERE action = 'auth.login_success' AND actor_account_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 1
        ''',
        (account_id,),
    ).fetchone()
    if not row:
        return 'low', ''

    try:
        detail = json.loads(str(row['detail'] or '{}'))
    except Exception:
        detail = {}

    prev_ip = str(detail.get('loginIp') or '').strip()
    prev_ua = str(detail.get('userAgent') or '').strip()

    risk_reasons: list[str] = []
    if prev_ip and login_ip and prev_ip != login_ip:
        risk_reasons.append(f'登录IP变更: {prev_ip} -> {login_ip}')
    if prev_ua and user_agent and prev_ua != user_agent:
        risk_reasons.append('登录设备/浏览器发生变化')

    if not risk_reasons:
        return 'low', ''
    return 'high', '; '.join(risk_reasons)


def _record_login_risk_event(
    conn: sqlite3.Connection,
    account_id: str,
    username: str,
    login_ip: str,
    user_agent: str,
    risk_level: str,
    risk_reason: str,
    notified: bool,
):
    conn.execute(
        '''
        INSERT INTO login_risk_events (
            id, account_id, username, login_ip, user_agent,
            risk_level, risk_reason, city_hint, notified, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            account_id,
            username,
            login_ip,
            user_agent,
            risk_level,
            risk_reason,
            1 if notified else 0,
            _now_str(),
        ),
    )


def _get_observability_settings(mask_secret: bool = False) -> dict[str, Any]:
    _ensure_db()
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT sentry_dsn, alert_webhook, alert_emails
            FROM observability_settings
            WHERE id = 1
            '''
        ).fetchone()
    if not row:
        return {'sentryDsn': '', 'alertWebhook': '', 'alertEmails': ''}

    sentry = str(row['sentry_dsn'] or '')
    webhook = str(row['alert_webhook'] or '')
    if mask_secret:
        sentry = _mask_secret(sentry)
        webhook = _mask_secret(webhook)

    return {
        'sentryDsn': sentry,
        'alertWebhook': webhook,
        'alertEmails': str(row['alert_emails'] or ''),
    }


def _send_observability_alert(conn: sqlite3.Connection, title: str, content: str, payload: Optional[dict[str, Any]] = None):
    settings = _get_observability_settings(mask_secret=False)
    emails = _parse_alert_emails(str(settings.get('alertEmails') or ''))
    webhook = str(settings.get('alertWebhook') or '').strip()

    sent_email = False
    sent_webhook = False
    errors: list[str] = []

    if emails:
        email_settings = _get_email_settings(mask_secret=False)
        err = _validate_email_settings(email_settings)
        if err:
            errors.append(err)
        else:
            for to_email in emails:
                try:
                    _send_email(email_settings, to_email, title, content)
                    sent_email = True
                except Exception as e:
                    errors.append(f'email {to_email}: {e}')

    if webhook:
        try:
            requests.post(webhook, json={'title': title, 'content': content, 'payload': payload or {}, 'createdAt': _now_str()}, timeout=8)
            sent_webhook = True
        except Exception as e:
            errors.append(f'webhook: {e}')

    conn.execute(
        '''
        INSERT INTO error_events (id, source, level, message, detail, path, created_at)
        VALUES (?, 'observability', 'warning', ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            title,
            json.dumps({'content': content, 'payload': payload or {}, 'errors': errors, 'sentEmail': sent_email, 'sentWebhook': sent_webhook}, ensure_ascii=False),
            '',
            _now_str(),
        ),
    )


def _record_request_metric(method: str, path: str, status_code: int, latency_ms: float):
    if path.startswith('/api/admin/system/monitor/requests'):
        return
    success = 1 if 200 <= int(status_code) < 400 else 0
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                INSERT INTO request_metrics (id, method, path, status_code, success, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (uuid.uuid4().hex, method, path, int(status_code), success, float(latency_ms or 0), _now_str()),
            )
            conn.commit()


def _record_error_event(source: str, message: str, detail: Any = None, path: str = '', level: str = 'error'):
    detail_text = ''
    if detail is not None:
        try:
            detail_text = json.dumps(detail, ensure_ascii=False)
        except Exception:
            detail_text = str(detail)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                INSERT INTO error_events (id, source, level, message, detail, path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (uuid.uuid4().hex, str(source or 'backend'), str(level or 'error'), str(message or ''), detail_text, str(path or ''), _now_str()),
            )
            conn.commit()


def _normalize_legal_doc_type(doc_type: str) -> str:
    key = str(doc_type or '').strip().lower()
    aliases = {
        'terms': 'terms',
        'user_agreement': 'terms',
        'agreement': 'terms',
        'privacy': 'privacy',
        'privacy_policy': 'privacy',
        'risk': 'risk_disclaimer',
        'risk_disclaimer': 'risk_disclaimer',
    }
    return aliases.get(key, key)


def _load_legal_doc(conn: sqlite3.Connection, doc_type: str) -> Optional[sqlite3.Row]:
    key = _normalize_legal_doc_type(doc_type)
    if not key:
        return None
    conn.row_factory = sqlite3.Row
    return conn.execute(
        '''
        SELECT doc_type, title, content, version, effective_at, updated_at, created_at
        FROM legal_docs
        WHERE doc_type = ?
        LIMIT 1
        ''',
        (key,),
    ).fetchone()


def _serialize_legal_doc(row: sqlite3.Row) -> dict[str, Any]:
    return {
        'docType': row['doc_type'],
        'title': row['title'] or '',
        'content': row['content'] or '',
        'version': row['version'] or '',
        'effectiveAt': row['effective_at'] or '',
        'updatedAt': row['updated_at'] or '',
        'createdAt': row['created_at'] or '',
    }


def _collect_account_export_payload(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row

    account = conn.execute(
        '''
        SELECT id, username, real_name, email, roles, home_path, created_at, updated_at
        FROM user_accounts
        WHERE id = ?
        LIMIT 1
        ''',
        (account_id,),
    ).fetchone()

    member = conn.execute(
        '''
        SELECT id, user_nickname, user_id, email, member_level, member_status,
               start_time, expire_time, points, created_at, updated_at
        FROM member_users
        WHERE user_id = (SELECT username FROM user_accounts WHERE id = ? LIMIT 1)
        LIMIT 1
        ''',
        (account_id,),
    ).fetchone()

    subscriptions = conn.execute(
        '''
        SELECT id, account_id, plan_code, status, start_time, expire_time, created_at, updated_at
        FROM subscriptions
        WHERE account_id = ?
        ORDER BY datetime(updated_at) DESC
        ''',
        (account_id,),
    ).fetchall()

    orders = conn.execute(
        '''
        SELECT id, order_no, plan_code, amount, currency, channel, status, paid_at,
               expire_at, note, created_at, updated_at
        FROM orders
        WHERE account_id = ?
        ORDER BY datetime(created_at) DESC
        ''',
        (account_id,),
    ).fetchall()

    points = conn.execute(
        '''
        SELECT id, delta, points_before, points_after, reason, source, ref_id, created_at
        FROM points_ledger
        WHERE account_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 500
        ''',
        (account_id,),
    ).fetchall()

    notices = conn.execute(
        '''
        SELECT id, title, content, type, read_at, created_at, updated_at
        FROM inapp_notifications
        WHERE account_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 500
        ''',
        (account_id,),
    ).fetchall()

    return {
        'account': dict(account) if account else {},
        'member': dict(member) if member else {},
        'subscriptions': [dict(r) for r in subscriptions],
        'orders': [dict(r) for r in orders],
        'pointsLedger': [dict(r) for r in points],
        'notifications': [dict(r) for r in notices],
        'exportedAt': _now_str(),
    }


def _parse_alert_emails(text: str) -> list[str]:
    raw = str(text or '').strip()
    if not raw:
        return []
    parts = re.split(r'[;,\s]+', raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        email = str(item or '').strip().lower()
        if not email or email in seen:
            continue
        if not _EMAIL_RE.match(email):
            continue
        seen.add(email)
        out.append(email)
    return out


def _record_payment_alert_log(
    conn: sqlite3.Connection,
    category: str,
    level: str,
    title: str,
    content: str,
    payload: Optional[dict[str, Any]] = None,
    sent_email: bool = False,
    sent_webhook: bool = False,
):
    now = _now_str()
    conn.execute(
        '''
        INSERT INTO payment_alert_logs (
            id, category, level, title, content, payload,
            sent_email, sent_webhook, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            str(category or 'payment').strip() or 'payment',
            str(level or 'warning').strip() or 'warning',
            str(title or '支付告警').strip() or '支付告警',
            str(content or '').strip(),
            json.dumps(payload or {}, ensure_ascii=False),
            1 if sent_email else 0,
            1 if sent_webhook else 0,
            now,
        ),
    )


def _enqueue_payment_callback_retry_job(
    conn: sqlite3.Connection,
    *,
    event_key: str,
    provider: str,
    out_trade_no: str,
    payload: dict[str, Any],
    reason: str,
    next_retry_after_minutes: int = 2,
):
    key = str(event_key or '').strip()
    if not key:
        return

    reason_key = str(reason or '').strip() or 'unknown'
    exists = conn.execute(
        """
        SELECT id FROM payment_callback_retry_jobs
        WHERE event_key = ? AND reason = ? AND status = 'pending'
        LIMIT 1
        """,
        (key, reason_key),
    ).fetchone()
    if exists:
        return

    now = _now_str()
    due = (datetime.now() + timedelta(minutes=max(1, int(next_retry_after_minutes)))).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        '''
        INSERT INTO payment_callback_retry_jobs (
            id, event_key, provider, out_trade_no, reason, payload,
            retry_count, max_retries, status, next_retry_at, last_error,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 8, 'pending', ?, '', ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            key,
            str(provider or '').strip().lower(),
            str(out_trade_no or '').strip(),
            reason_key,
            json.dumps(payload or {}, ensure_ascii=False),
            due,
            now,
            now,
        ),
    )


def _enqueue_refund_retry_job(
    conn: sqlite3.Connection,
    *,
    order_id: str,
    provider: str,
    out_trade_no: str,
    amount: float,
    currency: str,
    reason: str,
    external_refund_no: str,
    last_error: str,
    next_retry_after_minutes: int = 5,
):
    oid = str(order_id or '').strip()
    if not oid:
        return

    now = _now_str()
    due = (datetime.now() + timedelta(minutes=max(1, int(next_retry_after_minutes)))).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        '''
        INSERT INTO refund_retry_jobs (
            id, order_id, provider, out_trade_no, amount, currency, reason,
            external_refund_no, retry_count, max_retries, status, next_retry_at,
            last_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 8, 'pending', ?, ?, ?, ?)
        ''',
        (
            uuid.uuid4().hex,
            oid,
            str(provider or '').strip().lower() or 'alipay',
            str(out_trade_no or '').strip(),
            round(float(amount or 0), 2),
            str(currency or 'CNY').strip() or 'CNY',
            str(reason or '').strip(),
            str(external_refund_no or '').strip(),
            due,
            str(last_error or '').strip()[:1000],
            now,
            now,
        ),
    )


def _notify_payment_alert(
    conn: sqlite3.Connection,
    category: str,
    title: str,
    content: str,
    payload: Optional[dict[str, Any]] = None,
    level: str = 'warning',
    force: bool = False,
) -> dict[str, Any]:
    settings = _get_payment_settings(mask_secret=False)
    enabled = bool(settings.get('paymentAlertEnabled'))
    recipients = _parse_alert_emails(str(settings.get('paymentAlertEmails') or ''))
    webhook = str(settings.get('paymentAlertWebhook') or '').strip()

    should_send = force or enabled
    sent_email = False
    sent_webhook = False
    errors: list[str] = []

    if should_send:
        if recipients:
            email_settings = _get_email_settings(mask_secret=False)
            err = _validate_email_settings(email_settings)
            if err:
                errors.append(f'邮件告警未发送：{err}')
            else:
                for to_email in recipients:
                    try:
                        _send_email(email_settings, to_email, title, content)
                        sent_email = True
                    except Exception as e:
                        errors.append(f'邮件发送失败({to_email}): {e}')

        if webhook:
            try:
                requests.post(
                    webhook,
                    json={
                        'category': category,
                        'level': level,
                        'title': title,
                        'content': content,
                        'payload': payload or {},
                        'createdAt': _now_str(),
                    },
                    timeout=8,
                )
                sent_webhook = True
            except Exception as e:
                errors.append(f'Webhook发送失败: {e}')

    _record_payment_alert_log(
        conn,
        category=category,
        level=level,
        title=title,
        content=content,
        payload=(payload or {}) | {'errors': errors},
        sent_email=sent_email,
        sent_webhook=sent_webhook,
    )

    return {
        'enabled': enabled,
        'sentEmail': sent_email,
        'sentWebhook': sent_webhook,
        'recipients': recipients,
        'webhook': webhook,
        'errors': errors,
    }


# 允许 `from app.api.deps import *` 导入下划线辅助函数（保持旧逻辑调用不变）
__all__ = [name for name in globals().keys() if not name.startswith('__')]
