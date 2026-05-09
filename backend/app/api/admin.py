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

router = APIRouter()

_DB_LOCK = threading.Lock()
_DB_PATH = resolve_sqlite_path(Path(__file__).resolve().parents[2] / 'data' / 'admin_console.db')
_DB_RUNTIME = describe_runtime(_DB_PATH)


def _db_connect():
    return connect_sqlite(_DB_PATH)

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
        conn.execute(
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

    conn.execute(
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
    conn.execute('DELETE FROM login_attempts WHERE login_key = ?', (login_key,))


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _create_admin_session(conn: sqlite3.Connection, account_id: str) -> str:
    token = uuid.uuid4().hex
    now = datetime.now()
    now_str = _now_str()
    limits = _runtime_security_limits(conn)
    ttl_hours = int(limits.get('sessionTtlHours') or _DEFAULT_SECURITY_POLICY['sessionTtlHours'])
    expire_at = (now + timedelta(hours=max(1, ttl_hours))).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
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
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ?, updated_at = ? WHERE id = ? AND revoked_at = ''",
            (now, now, row['id']),
        )
        conn.commit()
        return None

    conn.execute(
        'UPDATE auth_sessions SET last_active_at = ?, updated_at = ? WHERE id = ?',
        (now, now, row['id']),
    )
    conn.commit()
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
                'name': 'SystemEmailSettings',
                'path': '/system/email-settings',
                'component': '/system/email-settings/index',
                'meta': {
                    'icon': 'mdi:email-cog-outline',
                    'title': '邮箱设置',
                },
            },
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


@router.get('/public/legal-docs')
def public_legal_docs(docType: str = Query('', description='terms/privacy/risk_disclaimer')):
    key = _normalize_legal_doc_type(docType)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            if key:
                row = _load_legal_doc(conn, key)
                if not row:
                    return _fail('文档不存在')
                return _ok(_serialize_legal_doc(row))

            rows = conn.execute(
                '''
                SELECT doc_type, title, content, version, effective_at, updated_at, created_at
                FROM legal_docs
                ORDER BY doc_type ASC
                '''
            ).fetchall()

    return _ok([_serialize_legal_doc(r) for r in rows])


@router.get('/system/legal-docs')
def list_legal_docs(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT doc_type, title, content, version, effective_at, updated_at, created_at
                FROM legal_docs
                ORDER BY doc_type ASC
                '''
            ).fetchall()

    return _ok([_serialize_legal_doc(r) for r in rows])


@router.post('/system/legal-docs/save')
def save_legal_doc(body: LegalDocSaveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    doc_type = _normalize_legal_doc_type(body.docType)
    if doc_type not in {'terms', 'privacy', 'risk_disclaimer'}:
        return _fail('不支持的文档类型')

    title = str(body.title or '').strip()
    content = str(body.content or '').strip()
    if not title or not content:
        return _fail('标题和内容不能为空')

    now = _now_str()
    effective_at = str(body.effectiveAt or '').strip() or now
    version = str(body.version or '').strip() or f"v{now.replace('-', '').replace(':', '').replace(' ', '')}"

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute('SELECT doc_type FROM legal_docs WHERE doc_type = ? LIMIT 1', (doc_type,)).fetchone()
            if exists:
                conn.execute(
                    '''
                    UPDATE legal_docs
                    SET title = ?, content = ?, version = ?, effective_at = ?, updated_at = ?
                    WHERE doc_type = ?
                    ''',
                    (title, content, version, effective_at, now, doc_type),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO legal_docs (doc_type, title, content, version, effective_at, updated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (doc_type, title, content, version, effective_at, now, now),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'legal_doc.save',
                'legal_docs',
                doc_type,
                {'version': version, 'effectiveAt': effective_at},
            )
            conn.commit()

    return _ok(True, message='合规文档已保存')


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


@router.get('/auth/codes')
def admin_codes(authorization: Optional[str] = Header(default=None)):
    user, _ = _require_entitled_user(authorization)

    roles = set(user.get('roles') or [])
    if {'super', 'admin'} & roles:
        return _ok(['AC_100100', 'AC_100110', 'AC_100120', 'AC_100010'])
    return _ok(['AC_100010'])


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
        menus.append(_build_system_menu())

    return _ok(menus)


@router.get('/system/email-settings')
def get_email_settings(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    return _ok(_get_email_settings(mask_secret=False))


@router.post('/system/email-settings/send-test')
def send_test_email(
    body: SendTestEmailBody,
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_admin(user)

    test_email = (body.testEmail or '').strip().lower()
    if not _EMAIL_RE.match(test_email):
        return _fail('测试邮箱格式错误')

    settings = _coerce_email_settings_from_body(body)
    err = _validate_email_settings(settings)
    if err:
        return _fail(err)

    subject, content = _build_register_mail(
        settings,
        test_email,
        code='123456',
        expire_minutes=10,
    )

    content += (
        '\n\n——\n'
        '这是一封系统测试邮件，用于验证 SMTP 链路是否打通。\n'
        f'测试收件邮箱：{test_email}'
    )

    try:
        _send_email(settings, test_email, subject, content)
    except Exception as e:
        return _fail(f'测试邮件发送失败: {e}')

    return _ok(True, message='测试邮件发送成功，请检查收件箱')


@router.post('/system/email-settings/save')
def save_email_settings(
    body: EmailSettingsBody,
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_admin(user)

    settings = _coerce_email_settings_from_body(body)
    err = _validate_email_settings(settings)
    if err:
        return _fail(err)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.execute(
                '''
                UPDATE email_settings
                SET smtp_host = ?,
                    smtp_port = ?,
                    smtp_username = ?,
                    smtp_password = ?,
                    from_email = ?,
                    from_name = ?,
                    use_tls = ?,
                    use_ssl = ?,
                    verify_subject_template = ?,
                    verify_body_template = ?,
                    updated_at = ?
                WHERE id = 1
                ''',
                (
                    settings['smtpHost'],
                    settings['smtpPort'],
                    settings['smtpUsername'],
                    settings['smtpPassword'],
                    settings['fromEmail'],
                    settings['fromName'],
                    1 if settings['useTLS'] else 0,
                    1 if settings['useSSL'] else 0,
                    settings['verifySubjectTemplate'],
                    settings['verifyBodyTemplate'],
                    _now_str(),
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'system.email_settings.save',
                'email_settings',
                '1',
                {'smtpHost': settings['smtpHost'], 'fromEmail': settings['fromEmail']},
            )
            conn.commit()

    return _ok(True, message='邮箱设置已保存')


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

            if provider == 'alipay':
                try:
                    biz_response, qr_code, request_payload = _alipay_precreate(
                        settings,
                        out_trade_no=out_trade_no,
                        amount=round(amount, 2),
                        subject=subject,
                        body='AiceMind 支付联调测试订单',
                    )
                except Exception as e:
                    conn.execute(
                        "UPDATE payment_trades SET status = 'failed', updated_at = ? WHERE id = ?",
                        (_now_str(), trade_id),
                    )
                    conn.commit()
                    return _fail(f'支付宝预下单失败: {e}')
            else:
                request_payload = {
                    'appid': settings['wechatAppId'],
                    'mchid': settings['wechatMerchantId'],
                    'description': subject,
                    'out_trade_no': out_trade_no,
                    'notify_url': settings.get('wechatNotifyUrl') or '',
                    'amount': {
                        'total': int(round(amount * 100)),
                        'currency': str(body.currency or 'CNY').strip() or 'CNY',
                    },
                }

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

    out_trade_no, amount, status_text, gateway_trade_no, event_key = _extract_payment_notify_payload(provider_key, payload)
    if not out_trade_no:
        return _fail('缺少 out_trade_no')

    settings = _get_payment_settings(mask_secret=False)
    verified = _verify_callback_signature(provider_key, payload, settings)

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


@router.post('/payment/callback/alipay')
async def payment_callback_alipay(request: Request):
    return await _handle_payment_callback('alipay', request)


@router.get('/payment/callback/alipay')
async def payment_callback_alipay_get(request: Request):
    return await _handle_payment_callback('alipay', request)


@router.post('/payment/callback/wechat')
async def payment_callback_wechat(request: Request):
    return await _handle_payment_callback('wechat', request)


@router.get('/payment/callback/wechat')
async def payment_callback_wechat_get(request: Request):
    return await _handle_payment_callback('wechat', request)


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
            exists = conn.execute(
                'SELECT 1 FROM member_users WHERE id = ?',
                (row_id,),
            ).fetchone()
            if not exists:
                return _fail('用户不存在')

            duplicate = conn.execute(
                'SELECT 1 FROM member_users WHERE user_id = ? AND id != ?',
                (user_id, row_id),
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
                    row_id,
                ),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.update',
                'member_user',
                row_id,
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
            exists = conn.execute('SELECT 1 FROM member_users WHERE id = ?', (row_id,)).fetchone()
            if not exists:
                return _fail('用户不存在')
            conn.execute(
                'UPDATE member_users SET member_status = ?, updated_at = ? WHERE id = ?',
                (status, _now_str(), row_id),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.toggle_status',
                'member_user',
                row_id,
                {'status': status},
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
            row = conn.execute(
                'SELECT expire_time FROM member_users WHERE id = ?',
                (row_id,),
            ).fetchone()
            if not row:
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
                current_expire = _parse_dt(row['expire_time']) or now
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
                (new_expire, status, _now_str(), row_id),
            )
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'member.extend_expire',
                'member_user',
                row_id,
                {'expireTime': new_expire, 'status': status},
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
            row = conn.execute(
                'SELECT user_id, email FROM member_users WHERE id = ?',
                (row_id,),
            ).fetchone()
            if not row:
                return _fail('用户不存在')

            user_id = str(row['user_id'] or '').strip()
            email = str(row['email'] or '').strip().lower()

            conn.execute('DELETE FROM member_users WHERE id = ?', (row_id,))

            account_row = None
            if user_id and email:
                account_row = conn.execute(
                    'SELECT id FROM user_accounts WHERE lower(username) = ? OR lower(email) = ? LIMIT 1',
                    (user_id.lower(), email),
                ).fetchone()
                conn.execute(
                    'DELETE FROM user_accounts WHERE lower(username) = ? OR lower(email) = ?',
                    (user_id.lower(), email),
                )
            elif user_id:
                account_row = conn.execute(
                    'SELECT id FROM user_accounts WHERE lower(username) = ? LIMIT 1',
                    (user_id.lower(),),
                ).fetchone()
                conn.execute(
                    'DELETE FROM user_accounts WHERE lower(username) = ?',
                    (user_id.lower(),),
                )
            elif email:
                account_row = conn.execute(
                    'SELECT id FROM user_accounts WHERE lower(email) = ? LIMIT 1',
                    (email,),
                ).fetchone()
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
                row_id,
                {'userId': user_id, 'email': email},
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


@router.get('/system/audit/logs')
def list_audit_logs(
    authorization: Optional[str] = Header(default=None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    action: str = Query('', description='按动作过滤'),
    actor: str = Query('', description='按操作者ID过滤'),
):
    user = _require_user(authorization)
    _require_admin(user)

    where = []
    params: list[Any] = []
    if action.strip():
        where.append('action = ?')
        params.append(action.strip())
    if actor.strip():
        where.append('actor_account_id = ?')
        params.append(actor.strip())

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT id, actor_account_id, action, target_type, target_id, detail, created_at
                FROM audit_logs
                {where_sql}
                ORDER BY datetime(created_at) DESC
                LIMIT ? OFFSET ?
                ''',
                (*params, int(limit), int(offset)),
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'actorAccountId': r['actor_account_id'],
                'action': r['action'],
                'targetType': r['target_type'],
                'targetId': r['target_id'],
                'detail': r['detail'],
                'createdAt': r['created_at'],
            }
            for r in rows
        ]
    )


@router.get('/commerce/plan/list')
def commerce_list_plans(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, code, name, price, duration_days, level, status, description,
                       daily_points_refresh, backtest_point_multiplier, updated_at
                FROM plans
                WHERE status = 'active'
                ORDER BY price ASC, duration_days ASC, datetime(updated_at) DESC
                '''
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'code': r['code'],
                'name': r['name'],
                'price': float(r['price'] or 0),
                'durationDays': int(r['duration_days'] or 0),
                'level': r['level'] or 'basic',
                'status': r['status'],
                'description': r['description'] or '',
                'dailyPointsRefresh': int(r['daily_points_refresh'] or 0),
                'backtestPointMultiplier': max(1, int(r['backtest_point_multiplier'] or 1)),
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )


@router.post('/commerce/order/create-pay')
def commerce_create_pay_order(body: CommerceCreatePayBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)

    provider = str(body.provider or '').strip().lower() or 'alipay'
    if provider not in ('alipay', 'wechat'):
        return _fail('仅支持 alipay 或 wechat')
    if provider == 'wechat':
        return _fail('当前版本仅支持支付宝扫码支付')

    plan_code = str(body.planCode or '').strip()
    if not plan_code:
        return _fail('缺少套餐编码')

    settings = _get_payment_settings(mask_secret=False)
    if not settings.get('alipayEnabled'):
        return _fail('支付宝支付尚未启用')

    required = ['alipayAppId', 'alipayMerchantId', 'alipayAppPrivateKey', 'alipayPublicKey', 'alipayNotifyUrl']
    missing = [key for key in required if not str(settings.get(key) or '').strip()]
    if missing:
        return _fail(f'支付宝配置不完整: {", ".join(missing)}')

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
                    {'orderNo': order_no, 'planCode': plan_code, 'reason': str(e)},
                )
                conn.commit()
                return _fail(f'支付宝预下单失败: {e}')

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


@router.get('/system/plan/list')
def list_plans(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT id, code, name, price, duration_days, level, status, description,
                       daily_points_refresh, backtest_point_multiplier, updated_at
                FROM plans
                ORDER BY datetime(updated_at) DESC
                '''
            ).fetchall()

    return _ok(
        [
            {
                'id': r['id'],
                'code': r['code'],
                'name': r['name'],
                'price': float(r['price'] or 0),
                'durationDays': int(r['duration_days'] or 0),
                'level': r['level'],
                'status': r['status'],
                'description': r['description'],
                'dailyPointsRefresh': int(r['daily_points_refresh'] or 0),
                'backtestPointMultiplier': max(1, int(r['backtest_point_multiplier'] or 1)),
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )


@router.post('/system/plan/create')
def create_plan(body: PlanBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    code = str(body.code or '').strip()
    name = str(body.name or '').strip()
    if not code or not name:
        return _fail('套餐编码和名称必填')
    if body.durationDays <= 0:
        return _fail('套餐时长必须大于 0')
    if body.price < 0:
        return _fail('套餐价格不能为负数')

    daily_points_refresh = max(0, int(body.dailyPointsRefresh or 0))
    backtest_point_multiplier = max(1, int(body.backtestPointMultiplier or 1))

    now = _now_str()
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute('SELECT 1 FROM plans WHERE code = ? LIMIT 1', (code,)).fetchone()
            if exists:
                return _fail('套餐编码已存在')
            row_id = uuid.uuid4().hex
            conn.execute(
                '''
                INSERT INTO plans (
                    id, code, name, price, duration_days, level, status, description,
                    daily_points_refresh, backtest_point_multiplier,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row_id,
                    code,
                    name,
                    float(body.price),
                    int(body.durationDays),
                    str(body.level or 'basic').strip() or 'basic',
                    str(body.status or 'active').strip() or 'active',
                    str(body.description or '').strip(),
                    daily_points_refresh,
                    backtest_point_multiplier,
                    now,
                    now,
                ),
            )

            level_key = str(body.level or 'basic').strip() or 'basic'
            current_policy = get_entitlement_policy(level_key)
            merged_policy = {
                **current_policy,
                'daily_points_refresh': daily_points_refresh,
                'backtest_point_multiplier': backtest_point_multiplier,
            }
            upsert_entitlement_policy(level_key, merged_policy)

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'plan.create',
                'plan',
                row_id,
                {
                    'code': code,
                    'dailyPointsRefresh': daily_points_refresh,
                    'backtestPointMultiplier': backtest_point_multiplier,
                },
            )
            conn.commit()

    return _ok(True)


@router.post('/system/plan/update')
def update_plan(body: PlanBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = str(body.id or '').strip()
    code = str(body.code or '').strip()
    name = str(body.name or '').strip()
    if not row_id:
        return _fail('缺少套餐ID')
    if not code or not name:
        return _fail('套餐编码和名称必填')
    if body.durationDays <= 0:
        return _fail('套餐时长必须大于 0')
    if body.price < 0:
        return _fail('套餐价格不能为负数')

    daily_points_refresh = max(0, int(body.dailyPointsRefresh or 0))
    backtest_point_multiplier = max(1, int(body.backtestPointMultiplier or 1))

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute('SELECT 1 FROM plans WHERE id = ? LIMIT 1', (row_id,)).fetchone()
            if not exists:
                return _fail('套餐不存在')
            duplicate = conn.execute('SELECT 1 FROM plans WHERE code = ? AND id != ? LIMIT 1', (code, row_id)).fetchone()
            if duplicate:
                return _fail('套餐编码已存在')

            conn.execute(
                '''
                UPDATE plans
                SET code = ?, name = ?, price = ?, duration_days = ?, level = ?, status = ?, description = ?,
                    daily_points_refresh = ?, backtest_point_multiplier = ?, updated_at = ?
                WHERE id = ?
                ''',
                (
                    code,
                    name,
                    float(body.price),
                    int(body.durationDays),
                    str(body.level or 'basic').strip() or 'basic',
                    str(body.status or 'active').strip() or 'active',
                    str(body.description or '').strip(),
                    daily_points_refresh,
                    backtest_point_multiplier,
                    _now_str(),
                    row_id,
                ),
            )

            level_key = str(body.level or 'basic').strip() or 'basic'
            current_policy = get_entitlement_policy(level_key)
            merged_policy = {
                **current_policy,
                'daily_points_refresh': daily_points_refresh,
                'backtest_point_multiplier': backtest_point_multiplier,
            }
            upsert_entitlement_policy(level_key, merged_policy)

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'plan.update',
                'plan',
                row_id,
                {
                    'code': code,
                    'dailyPointsRefresh': daily_points_refresh,
                    'backtestPointMultiplier': backtest_point_multiplier,
                },
            )
            conn.commit()

    return _ok(True)


@router.post('/system/plan/toggle-status')
def toggle_plan_status(body: PlanToggleStatusBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    row_id = str(body.id or '').strip()
    status = str(body.status or '').strip() or 'disabled'
    if status not in ('active', 'disabled'):
        return _fail('无效状态')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute('SELECT 1 FROM plans WHERE id = ? LIMIT 1', (row_id,)).fetchone()
            if not exists:
                return _fail('套餐不存在')
            conn.execute('UPDATE plans SET status = ?, updated_at = ? WHERE id = ?', (status, _now_str(), row_id))
            _audit_log(conn, str(user.get('id') or ''), 'plan.toggle_status', 'plan', row_id, {'status': status})
            conn.commit()

    return _ok(True)


@router.get('/system/subscription/list')
def list_subscriptions(
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
                SELECT s.id, s.account_id, s.plan_code, s.status, s.start_time, s.expire_time, s.updated_at,
                       u.username, u.real_name, u.email,
                       p.name AS plan_name, p.level AS plan_level
                FROM subscriptions s
                LEFT JOIN user_accounts u ON u.id = s.account_id
                LEFT JOIN plans p ON p.code = s.plan_code
                ORDER BY datetime(s.updated_at) DESC
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
                'realName': r['real_name'] or '',
                'email': r['email'] or '',
                'planCode': r['plan_code'],
                'planName': r['plan_name'] or r['plan_code'],
                'planLevel': r['plan_level'] or 'basic',
                'status': r['status'],
                'startTime': r['start_time'],
                'expireTime': r['expire_time'],
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
    )


@router.post('/system/subscription/upsert')
def upsert_subscription(body: SubscriptionUpsertBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    account_id = str(body.accountId or '').strip()
    plan_code = str(body.planCode or '').strip()
    if not account_id or not plan_code:
        return _fail('账号和套餐必填')

    status = str(body.status or 'active').strip() or 'active'
    if status not in ('active', 'disabled', 'expired'):
        return _fail('无效状态')

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

            start_dt = _parse_dt(body.startTime) if str(body.startTime or '').strip() else datetime.now()
            if not start_dt:
                return _fail('开始时间格式错误')

            expire_dt = _parse_dt(body.expireTime) if str(body.expireTime or '').strip() else None
            if expire_dt is None:
                expire_dt = start_dt + timedelta(days=int(plan_row['duration_days'] or 30))

            start_time = start_dt.strftime('%Y-%m-%d %H:%M:%S')
            expire_time = expire_dt.strftime('%Y-%m-%d %H:%M:%S')
            now = _now_str()

            conn.execute(
                '''
                INSERT INTO subscriptions (
                    id, account_id, plan_code, status, start_time, expire_time, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, plan_code) DO UPDATE SET
                    status = excluded.status,
                    start_time = excluded.start_time,
                    expire_time = excluded.expire_time,
                    updated_at = excluded.updated_at
                ''',
                (uuid.uuid4().hex, account_id, plan_code, status, start_time, expire_time, now, now),
            )

            _sync_member_for_account(
                conn,
                account_row=account_row,
                level=str(plan_row['level'] or 'basic'),
                status=status,
                start_time=start_time,
                expire_time=expire_time,
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'subscription.upsert',
                'subscription',
                account_id,
                {'planCode': plan_code, 'status': status, 'expireTime': expire_time},
            )
            conn.commit()

    return _ok(True)


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
