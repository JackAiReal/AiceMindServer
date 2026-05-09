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
    password: str
    confirmPassword: str
    code: str = ''
    nickname: str = ''
    acceptTerms: bool = False


class RevokeSessionBody(BaseModel):
    sessionId: str


class RevokeAccountSessionsBody(BaseModel):
    accountId: str


class UnlockLoginAttemptBody(BaseModel):
    login: str


class PlanBody(BaseModel):
    planCode: str
    planName: str
    planDesc: str = ''
    planLevel: str = 'standard'
    planPrice: float = 0.0
    planCurrency: str = 'CNY'
    trialDays: int = 0
    billingPeriod: str = 'monthly'
    billingInterval: int = 1
    planStatus: str = 'active'
    planRights: dict[str, Any] = {}
    planRightsTips: list[str] = []
    planLimits: dict[str, Any] = {}
    planWeight: int = 0


class SubscriptionUpsertBody(BaseModel):
    accountId: str
    planCode: str
    startTime: str = ''
    expireTime: str = ''
    status: str = 'active'
    autoRenew: bool = True
    trialEnds: str = ''
    comment: str = ''


class OrderCreateBody(BaseModel):
    accountId: str
    planCode: str
    amount: float
    currency: str = 'CNY'
    paymentProvider: str = 'alipay'
    paymentMethod: str = 'native'
    clientIp: str = ''
    clientExtra: dict[str, Any] = {}


class OrderMarkPaidBody(BaseModel):
    id: str
    paidAmount: float
    paidCurrency: str = 'CNY'
    paidTime: str = ''
    paymentProvider: str = ''
    paymentMethod: str = ''
    transactionId: str = ''


class PlanToggleStatusBody(BaseModel):
    planCode: str
    status: str


class PaymentSettingsBody(BaseModel):
    alipayAppId: str = ''
    alipayAppPrivateKey: str = ''
    alipayAppPublicKey: str = ''
    alipayPublicKey: str = ''
    alipayNotifyUrl: str = ''
    alipayReturnUrl: str = ''
    wechatpayMchId: str = ''
    wechatpayApiKey: str = ''
    wechatpayCertSerialNo: str = ''
    wechatpayCertPrivateKey: str = ''
    wechatpayNotifyUrl: str = ''
    wechatpayReturnUrl: str = ''
    paymentAlertEmails: str = ''
    paymentAlertWebhook: str = ''
    paymentAlertWebhookSecret: str = ''
    paymentReconcileEnabled: bool = False
    paymentReconcileCron: str = '0 2 * * *'
    paymentReconcileDays: int = 7
    paymentReconcileProvider: str = 'alipay'
    paymentReconcileLastRun: str = ''


class PaymentTestPayBody(BaseModel):
    provider: str = 'alipay'
    method: str = 'native'
    amount: float = 0.01
    currency: str = 'CNY'
    subject: str = '测试支付'
    body: str = '测试支付描述'


class PaymentInitiateBody(BaseModel):
    provider: str = 'alipay'
    method: str = 'native'
    amount: float
    currency: str = 'CNY'
    subject: str = ''
    body: str = ''
    clientIp: str = ''
    clientExtra: dict[str, Any] = {}


class BillingPolicyBody(BaseModel):
    policyId: str
    policyName: str
    policyDesc: str = ''
    policyRights: dict[str, Any] = {}
    policyLimits: dict[str, Any] = {}
    policyPrice: float = 0.0
    policyCurrency: str = 'CNY'
    policyStatus: str = 'active'
    policyWeight: int = 0


class PaymentReconcileRunBody(BaseModel):
    provider: str = 'alipay'
    startDate: str = ''
    endDate: str = ''


class PaymentRepairBody(BaseModel):
    orderId: str
    action: str = 'sync'
    data: dict[str, Any] = {}


class PaymentAlertTestBody(BaseModel):
    email: str = ''
    webhook: str = ''
    webhookSecret: str = ''
    alertType: str = 'payment_failed'


class ObservabilitySettingsBody(BaseModel):
    alertWebhook: str = ''
    alertWebhookSecret: str = ''
    alertEmails: str = ''
    alertLevel: str = 'error'
    requestMetricsEnabled: bool = False
    errorEventsEnabled: bool = False
    userActionsEnabled: bool = False
    backtestRecordsEnabled: bool = False
    retentionDays: int = 30


class LegalDocSaveBody(BaseModel):
    title: str
    content: str
    isActive: bool = True


class AccountDeleteRequestBody(BaseModel):
    reason: str = ''
    password: str = ''


class AccountDeleteProcessBody(BaseModel):
    requestId: str
    action: str
    comment: str = ''


class RenewalReminderRunBody(BaseModel):
    days: int = 7


class CommerceCreatePayBody(BaseModel):
    planCode: str
    paymentProvider: str = 'alipay'
    paymentMethod: str = 'native'
    clientIp: str = ''
    clientExtra: dict[str, Any] = {}


class OrderCancelBody(BaseModel):
    id: str
    reason: str = ''


class OrderMarkExceptionBody(BaseModel):
    id: str
    reason: str = ''


class OrderRecoverBody(BaseModel):
    id: str
    reason: str = ''


class OrderRefundBody(BaseModel):
    id: str
    refundAmount: float
    refundReason: str = ''
    refundCurrency: str = 'CNY'
    refundNotifyUrl: str = ''


class ChangePasswordBody(BaseModel):
    oldPassword: str
    newPassword: str
    confirmPassword: str


class ResetPasswordBody(BaseModel):
    accountId: str
    newPassword: str
    confirmPassword: str


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