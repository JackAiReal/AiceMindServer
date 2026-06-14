from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.core.db_runtime import connect_sqlite, resolve_sqlite_path

_DB_PATH = resolve_sqlite_path(Path(__file__).resolve().parents[2] / 'data' / 'admin_console.db')


def _db_connect():
    return connect_sqlite(_DB_PATH)
_NO_EXPIRE_TIME = '2099-12-31 23:59:59'

_DEFAULT_POLICY_MAP: dict[str, dict[str, Any]] = {
    'none': {
        'chat_enabled': False,
        'chat_monthly_limit': 0,
        'chat_daily_limit': 0,
        'backtest_enabled': False,
        'backtest_monthly_limit': 0,
        'backtest_daily_limit': 0,
        'backtest_point_multiplier': 1,
        'daily_points_refresh': 0,
        'max_backtest_stocks': 0,
        'max_backtest_days': 0,
        'report_download_enabled': False,
    },
    'basic': {
        'chat_enabled': True,
        'chat_monthly_limit': 2000,
        'chat_daily_limit': 100,
        'backtest_enabled': True,
        'backtest_monthly_limit': 300,
        'backtest_daily_limit': 10,
        'backtest_point_multiplier': 1,
        'daily_points_refresh': 50,
        'max_backtest_stocks': 50,
        'max_backtest_days': 365,
        'report_download_enabled': True,
    },
    'pro': {
        'chat_enabled': True,
        'chat_monthly_limit': 8000,
        'chat_daily_limit': 400,
        'backtest_enabled': True,
        'backtest_monthly_limit': 900,
        'backtest_daily_limit': 30,
        'backtest_point_multiplier': 1,
        'daily_points_refresh': 120,
        'max_backtest_stocks': 300,
        'max_backtest_days': 365 * 5,
        'report_download_enabled': True,
    },
    'vip': {
        'chat_enabled': True,
        'chat_monthly_limit': 30000,
        'chat_daily_limit': 1500,
        'backtest_enabled': True,
        'backtest_monthly_limit': 900,
        'backtest_daily_limit': 30,
        'backtest_point_multiplier': 1,
        'daily_points_refresh': 180,
        'max_backtest_stocks': 2000,
        'max_backtest_days': 365 * 5,
        'report_download_enabled': True,
    },
    'svip': {
        'chat_enabled': True,
        'chat_monthly_limit': -1,
        'chat_daily_limit': -1,
        'backtest_enabled': True,
        'backtest_monthly_limit': 3000,
        'backtest_daily_limit': 100,
        'backtest_point_multiplier': 1,
        'daily_points_refresh': 300,
        'max_backtest_stocks': -1,
        'max_backtest_days': -1,
        'report_download_enabled': True,
    },
}

_FEATURE_LIMIT_KEY_MAP = {
    'chat.message': ('chat_enabled', 'chat_monthly_limit'),
    'backtest.run': ('backtest_enabled', 'backtest_monthly_limit'),
}

_FEATURE_DAILY_LIMIT_KEY_MAP = {
    'chat.message': 'chat_daily_limit',
    'backtest.run': 'backtest_daily_limit',
}


def _parse_dt(value: str | None) -> Optional[datetime]:
    raw = str(value or '').strip()
    if not raw:
        return None

    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _normalize_roles(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or '[]')
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
    except Exception:
        pass
    return ['user']


def _normalize_level(level: str | None) -> str:
    raw = str(level or '').strip().lower()
    if raw in _DEFAULT_POLICY_MAP:
        return raw
    return 'basic'


def _ensure_billing_tables() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with _db_connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS entitlement_policies (
                level TEXT PRIMARY KEY,
                policy_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS billing_usage_ledger (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                feature_code TEXT NOT NULL,
                amount INTEGER NOT NULL,
                period_key TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                ref_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_billing_usage_account_feature_period ON billing_usage_ledger(account_id, feature_code, period_key)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_billing_usage_ref ON billing_usage_ledger(ref_id, feature_code)'
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS member_points_refresh_state (
                account_id TEXT PRIMARY KEY,
                last_refresh_date TEXT NOT NULL DEFAULT '',
                last_refresh_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            '''
        )

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

        now = _now_str()
        for level, policy in _DEFAULT_POLICY_MAP.items():
            exists = conn.execute(
                'SELECT 1 FROM entitlement_policies WHERE level = ? LIMIT 1',
                (level,),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                'INSERT INTO entitlement_policies(level, policy_json, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (level, json.dumps(policy, ensure_ascii=False), now, now),
            )

        conn.commit()


def is_entitlement_active(entitlement: dict[str, Any] | None) -> bool:
    if not entitlement:
        return False

    status = str(entitlement.get('status') or '').strip().lower()
    if status != 'active':
        return False

    expire_raw = str(entitlement.get('expire_at') or '').strip()
    if not expire_raw:
        return True

    if expire_raw == _NO_EXPIRE_TIME:
        return True

    expire_at = _parse_dt(expire_raw)
    if expire_at is None:
        return False

    return expire_at > datetime.now()


def _admin_entitlement(level: str = 'svip') -> dict[str, Any]:
    return {
        'level': level,
        'status': 'active',
        'start_at': _now_str(),
        'expire_at': _NO_EXPIRE_TIME,
        'is_active': True,
        'source': 'role',
    }


def _inactive_entitlement(reason: str) -> dict[str, Any]:
    return {
        'level': 'none',
        'status': 'inactive',
        'start_at': '',
        'expire_at': '',
        'is_active': False,
        'reason': reason,
        'source': 'member_users',
    }


def get_account_identity(account_id: str) -> Optional[dict[str, Any]]:
    account_id = str(account_id or '').strip()
    if not account_id:
        return None

    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT id, username, email, roles, real_name, home_path
            FROM user_accounts
            WHERE id = ?
            LIMIT 1
            ''',
            (account_id,),
        ).fetchone()

    if not row:
        return None

    return {
        'id': str(row['id'] or ''),
        'username': str(row['username'] or ''),
        'email': str(row['email'] or ''),
        'roles': _normalize_roles(row['roles']),
        'realName': str(row['real_name'] or ''),
        'homePath': str(row['home_path'] or '/workspace'),
    }


def get_entitlement_for_account(account_id: str) -> dict[str, Any]:
    identity = get_account_identity(account_id)
    if not identity:
        return _inactive_entitlement('账号不存在')

    roles = set(identity.get('roles') or [])
    if 'super' in roles:
        return _admin_entitlement('svip')
    if 'admin' in roles:
        return _admin_entitlement('vip')

    username = str(identity.get('username') or '').strip().lower()
    email = str(identity.get('email') or '').strip().lower()

    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT member_level, member_status, start_time, expire_time, updated_at
            FROM member_users
            WHERE lower(user_id) = ? OR lower(email) = ?
            ORDER BY datetime(updated_at) DESC
            LIMIT 1
            ''',
            (username, email),
        ).fetchone()

    if not row:
        return _inactive_entitlement('未开通会员，请先购买套餐')

    entitlement = {
        'level': _normalize_level(row['member_level']),
        'status': str(row['member_status'] or 'inactive').lower(),
        'start_at': str(row['start_time'] or ''),
        'expire_at': str(row['expire_time'] or ''),
        'source': 'member_users',
    }

    if not is_entitlement_active(entitlement):
        expire_at = str(entitlement.get('expire_at') or '').strip()
        if entitlement['status'] != 'active':
            reason = '会员已被禁用，请联系管理员'
        elif expire_at:
            reason = f'会员已到期（{expire_at}），请续费后继续使用'
        else:
            reason = '会员不可用，请联系管理员'

        entitlement['is_active'] = False
        entitlement['reason'] = reason
        return entitlement

    entitlement['is_active'] = True
    return entitlement


def get_entitlement_policy(level: str | None, conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    _ensure_billing_tables()

    normalized = _normalize_level(level)
    base = dict(_DEFAULT_POLICY_MAP.get(normalized) or _DEFAULT_POLICY_MAP['basic'])

    owns_conn = conn is None
    if owns_conn:
        conn = _db_connect()

    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT policy_json FROM entitlement_policies WHERE level = ? LIMIT 1',
            (normalized,),
        ).fetchone()
    finally:
        if owns_conn and conn is not None:
            conn.close()

    if row:
        try:
            loaded = json.loads(row['policy_json'] or '{}')
            if isinstance(loaded, dict):
                base.update(loaded)
        except Exception:
            pass

    base['level'] = normalized
    return base


def upsert_entitlement_policy(
    level: str,
    policy: dict[str, Any],
    conn: Optional[sqlite3.Connection] = None,
    auto_commit: bool = True,
) -> dict[str, Any]:
    _ensure_billing_tables()
    normalized = _normalize_level(level)

    owns_conn = conn is None
    if owns_conn:
        conn = _db_connect()

    current = get_entitlement_policy(normalized, conn=conn)
    current.update(policy or {})
    current['level'] = normalized

    now = _now_str()
    try:
        conn.execute(
            '''
            INSERT INTO entitlement_policies(level, policy_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(level) DO UPDATE SET
                policy_json = excluded.policy_json,
                updated_at = excluded.updated_at
            ''',
            (normalized, json.dumps(current, ensure_ascii=False), now, now),
        )
        if auto_commit:
            conn.commit()
    finally:
        if owns_conn and conn is not None:
            conn.close()

    return current


def _period_key_for_feature(feature_code: str, now: Optional[datetime] = None, scope: str = 'month') -> str:
    dt = now or datetime.now()
    if str(scope or '').strip().lower() == 'day':
        return dt.strftime('%Y-%m-%d')
    return dt.strftime('%Y-%m')


def get_feature_usage(account_id: str, feature_code: str, period_key: Optional[str] = None) -> int:
    _ensure_billing_tables()

    account = str(account_id or '').strip()
    feature = str(feature_code or '').strip()
    period = (period_key or _period_key_for_feature(feature)).strip()
    if not account or not feature or not period:
        return 0

    with _db_connect() as conn:
        if len(period) == 7:
            row = conn.execute(
                '''
                SELECT COALESCE(SUM(amount), 0)
                FROM billing_usage_ledger
                WHERE account_id = ? AND feature_code = ? AND period_key LIKE ?
                ''',
                (account, feature, f'{period}%'),
            ).fetchone()
        else:
            row = conn.execute(
                '''
                SELECT COALESCE(SUM(amount), 0)
                FROM billing_usage_ledger
                WHERE account_id = ? AND feature_code = ? AND period_key = ?
                ''',
                (account, feature, period),
            ).fetchone()

    try:
        return int(row[0] or 0)
    except Exception:
        return 0


def _refresh_daily_points_if_needed(account_id: str, now: Optional[datetime] = None) -> None:
    _ensure_billing_tables()

    account = str(account_id or '').strip()
    if not account:
        return

    entitlement = get_entitlement_for_account(account)
    if not is_entitlement_active(entitlement):
        return

    level = _normalize_level(entitlement.get('level'))
    policy = get_entitlement_policy(level)
    try:
        refresh_points = int(policy.get('daily_points_refresh', 0))
    except Exception:
        refresh_points = 0
    if refresh_points <= 0:
        return

    dt = now or datetime.now()
    today = dt.strftime('%Y-%m-%d')

    identity = get_account_identity(account)
    username = str((identity or {}).get('username') or '').strip()
    email = str((identity or {}).get('email') or '').strip().lower()
    if not username and not email:
        return

    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row

        # 兼容 admin 模块未初始化时的情况
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

        state = conn.execute(
            'SELECT last_refresh_date FROM member_points_refresh_state WHERE account_id = ? LIMIT 1',
            (account,),
        ).fetchone()
        if state and str(state['last_refresh_date'] or '') == today:
            return

        member_row = conn.execute(
            '''
            SELECT id, points
            FROM member_users
            WHERE lower(user_id) = ? OR lower(email) = ?
            ORDER BY datetime(updated_at) DESC
            LIMIT 1
            ''',
            (username.lower(), email.lower()),
        ).fetchone()
        if not member_row:
            return

        before = int(member_row['points'] or 0)
        after = int(refresh_points)
        delta = after - before
        now_str = _now_str()

        conn.execute(
            'UPDATE member_users SET points = ?, updated_at = ? WHERE id = ?',
            (after, now_str, str(member_row['id'] or '')),
        )
        conn.execute(
            '''
            INSERT INTO points_ledger (
                id, account_id, username, delta, points_before, points_after,
                reason, source, ref_id, actor_account_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                uuid.uuid4().hex,
                account,
                username,
                delta,
                before,
                after,
                'daily points refresh',
                'system.daily_points_refresh',
                today,
                'system-cron',
                now_str,
            ),
        )
        conn.execute(
            '''
            INSERT INTO member_points_refresh_state(account_id, last_refresh_date, last_refresh_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                last_refresh_date = excluded.last_refresh_date,
                last_refresh_at = excluded.last_refresh_at,
                updated_at = excluded.updated_at
            ''',
            (account, today, now_str, now_str),
        )
        conn.commit()


def _get_member_points(account_id: str) -> int:
    account = str(account_id or '').strip()
    if not account:
        return 0

    identity = get_account_identity(account)
    username = str((identity or {}).get('username') or '').strip().lower()
    email = str((identity or {}).get('email') or '').strip().lower()
    if not username and not email:
        return 0

    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT points
            FROM member_users
            WHERE lower(user_id) = ? OR lower(email) = ?
            ORDER BY datetime(updated_at) DESC
            LIMIT 1
            ''',
            (username, email),
        ).fetchone()

    return int((row['points'] if row else 0) or 0)


def _resolve_consume_amount(feature: str, amount: int, policy: dict[str, Any]) -> tuple[int, int]:
    consume = max(0, int(amount or 0))
    multiplier = 1
    if str(feature or '').strip() == 'backtest.run':
        try:
            multiplier = int((policy or {}).get('backtest_point_multiplier', 1))
        except Exception:
            multiplier = 1
        if multiplier <= 0:
            multiplier = 1
        consume *= multiplier
    return consume, multiplier


def _get_active_plan_override(account_id: str) -> dict[str, Any]:
    account = str(account_id or '').strip()
    if not account:
        return {}

    now_str = _now_str()
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            '''
            SELECT p.code, p.name, p.level, p.backtest_daily_limit, p.max_backtest_days,
                   s.start_time, s.expire_time, s.updated_at
            FROM subscriptions s
            JOIN plans p ON p.code = s.plan_code
            WHERE s.account_id = ?
              AND s.status = 'active'
              AND p.status = 'active'
              AND COALESCE(s.expire_time, '') > ?
            ORDER BY s.expire_time DESC, s.updated_at DESC
            LIMIT 1
            ''',
            (account, now_str),
        ).fetchone()

    if not row:
        return {}

    return {
        'planCode': str(row['code'] or ''),
        'planName': str(row['name'] or ''),
        'planLevel': _normalize_level(row['level']),
        'backtest_daily_limit': int(row['backtest_daily_limit'] or 0),
        'max_backtest_days': int(row['max_backtest_days'] or 0),
        'start_at': str(row['start_time'] or ''),
        'expire_at': str(row['expire_time'] or ''),
    }


def get_effective_entitlement_policy(account_id: str, entitlement: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    entitlement = entitlement or get_entitlement_for_account(account_id)
    level = _normalize_level((entitlement or {}).get('level'))
    policy = get_entitlement_policy(level)
    plan_override = _get_active_plan_override(account_id)
    if plan_override:
        policy = dict(policy)
        policy['backtest_daily_limit'] = int(plan_override.get('backtest_daily_limit', policy.get('backtest_daily_limit', 0)) or 0)
        policy['max_backtest_days'] = int(plan_override.get('max_backtest_days', policy.get('max_backtest_days', 0)) or 0)
        policy['planCode'] = plan_override.get('planCode') or ''
        policy['planName'] = plan_override.get('planName') or ''
        policy['planLevel'] = plan_override.get('planLevel') or level
    return policy


def check_feature_access(account_id: str, feature_code: str, consume_amount: int = 0) -> dict[str, Any]:
    """精简版权益校验：仅对 backtest.run 做限制（每日次数 + 时间范围上限）。"""
    feature = str(feature_code or '').strip()
    raw_amount = max(0, int(consume_amount or 0))
    amount = max(1, raw_amount) if raw_amount > 0 else 1

    if feature != 'backtest.run':
        return {
            'allowed': True,
            'reason': 'ok',
            'featureCode': feature,
            'consumeAmount': raw_amount,
            'dailyQuota': -1,
            'dailyUsed': 0,
            'dailyRemaining': -1,
            'quota': -1,
            'used': 0,
            'remaining': -1,
            'policy': {},
            'entitlement': {'level': 'basic', 'status': 'active', 'is_active': True, 'source': 'simplified'},
        }

    account = str(account_id or '').strip()
    if not account:
        return {
            'allowed': False,
            'reason': '缺少有效账号标识',
            'featureCode': feature,
            'consumeAmount': raw_amount,
        }

    entitlement = get_entitlement_for_account(account)
    if not is_entitlement_active(entitlement):
        return {
            'allowed': False,
            'reason': str(entitlement.get('reason') or '会员不可用，请续费后继续使用'),
            'featureCode': feature,
            'consumeAmount': raw_amount,
            'entitlement': entitlement,
        }

    policy = get_effective_entitlement_policy(account, entitlement)

    try:
        day_limit = int(policy.get('backtest_daily_limit', 0))
    except Exception:
        day_limit = 0

    day_period_key = _period_key_for_feature(feature, scope='day')
    day_used = get_feature_usage(account, feature, day_period_key)

    if day_limit >= 0:
        day_remaining_before = max(0, day_limit - day_used)
        if day_remaining_before < amount:
            return {
                'allowed': False,
                'reason': f'今日回测次数不足（已用 {day_used}/{day_limit}）',
                'featureCode': feature,
                'dayPeriodKey': day_period_key,
                'dailyQuota': day_limit,
                'dailyUsed': day_used,
                'dailyRemaining': day_remaining_before,
                'consumeAmount': amount,
                'policy': policy,
                'entitlement': entitlement,
            }

    day_remaining_after = max(0, day_limit - day_used - amount) if day_limit >= 0 else -1
    return {
        'allowed': True,
        'reason': 'ok',
        'featureCode': feature,
        'dayPeriodKey': day_period_key,
        'dailyQuota': day_limit,
        'dailyUsed': day_used,
        'dailyRemaining': day_remaining_after,
        'consumeAmount': amount,
        'quota': -1,
        'used': 0,
        'remaining': -1,
        'policy': policy,
        'entitlement': entitlement,
    }


def consume_feature_quota(
    account_id: str,
    feature_code: str,
    amount: int = 1,
    source: str = '',
    ref_id: str = '',
    detail: Any = None,
) -> dict[str, Any]:
    account = str(account_id or '').strip()
    feature = str(feature_code or '').strip()
    consume = max(0, int(amount or 0))

    if not account or not feature:
        return {
            'allowed': False,
            'reason': '参数错误：缺少 account_id 或 feature_code',
        }

    check_result = check_feature_access(account, feature, consume_amount=consume)
    if not check_result.get('allowed'):
        return check_result

    actual_consume = max(0, int(check_result.get('consumeAmount') or consume))
    if actual_consume <= 0:
        return check_result

    detail_text = ''
    if detail is not None:
        try:
            detail_text = json.dumps(detail, ensure_ascii=False)
        except Exception:
            detail_text = str(detail)

    period_key = str(check_result.get('dayPeriodKey') or _period_key_for_feature(feature, scope='day'))
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            '''
            INSERT INTO billing_usage_ledger (
                id, account_id, feature_code, amount, period_key,
                source, ref_id, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                uuid.uuid4().hex,
                account,
                feature,
                actual_consume,
                period_key,
                str(source or ''),
                str(ref_id or ''),
                detail_text,
                _now_str(),
            ),
        )
        conn.commit()

    return check_feature_access(account, feature, consume_amount=0)


def get_billing_context(account_id: str) -> dict[str, Any]:
    """精简版权益上下文：暴露“每日回测次数 + 回测时间跨度上限”。"""
    day_period = datetime.now().strftime('%Y-%m-%d')
    month_period = datetime.now().strftime('%Y-%m')

    entitlement = get_entitlement_for_account(account_id)
    policy = get_effective_entitlement_policy(account_id, entitlement)

    try:
        backtest_daily_limit = int(policy.get('backtest_daily_limit', 0))
    except Exception:
        backtest_daily_limit = 0

    try:
        max_backtest_days = int(policy.get('max_backtest_days', 365))
    except Exception:
        max_backtest_days = 365

    usage_daily_backtest = get_feature_usage(account_id, 'backtest.run', day_period)

    return {
        'accountId': str(account_id or ''),
        'period': month_period,
        'dayPeriod': day_period,
        'entitlement': entitlement,
        'policy': {
            'backtest_daily_limit': backtest_daily_limit,
            'max_backtest_days': max_backtest_days,
            'planCode': policy.get('planCode', ''),
            'planName': policy.get('planName', ''),
            'planLevel': policy.get('planLevel', ''),
        },
        'usage': {
            'backtest.run': get_feature_usage(account_id, 'backtest.run', month_period),
        },
        'usageDaily': {
            'backtest.run': usage_daily_backtest,
        },
        'limits': {
            'backtestDailyLimit': backtest_daily_limit,
            'maxBacktestDays': max_backtest_days,
            'chatMonthlyLimit': -1,
            'backtestMonthlyLimit': -1,
            'chatDailyLimit': -1,
            'backtestPointMultiplier': 1,
            'dailyPointsRefresh': 0,
            'maxBacktestStocks': -1,
        },
        'pointsBalance': 0,
    }


def resolve_entitled_identity(account_id: str) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    identity = get_account_identity(account_id)
    entitlement = get_entitlement_for_account(account_id)
    return identity, entitlement
