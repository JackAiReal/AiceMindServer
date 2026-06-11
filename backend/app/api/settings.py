from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from app.api.deps import *  # noqa: F401,F403
from app.secret_sync_shared import encrypt_payload

router = APIRouter()


# ===== 客户端版本管控 =====

class VersionPolicySaveBody(BaseModel):
    appCode: str = 'AiceMind'
    target: str = 'backtest-desktop'
    platform: str = 'all'
    channel: str = 'stable'
    latestVersion: str = ''
    minSupportedVersion: str = ''
    enforceExactMatch: bool = True
    forceUpgrade: bool = True
    autoUpgradeWithoutConfirm: bool = False
    title: str = '发现新版本，请升级后继续使用'
    details: str = ''
    downloadUrl: str = ''
    releaseNotes: str = ''
    publishedAt: str = ''


class VersionCheckBody(BaseModel):
    appCode: str = 'AiceMind'
    target: str = 'backtest-desktop'
    platform: str = 'all'
    channel: str = 'stable'
    currentVersion: str = ''


_SECRET_STREAM_INFO = b'AiceMindSensitiveSecretStream'
_SECRET_MAC_INFO = b'AiceMindSensitiveSecretMac'
_SECRET_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$')


def _decrypt_activation_response(encrypted_data: str) -> str:
    start_index = int(len(encrypted_data) * 0.02)
    end_index = int(len(encrypted_data) * 0.98)
    reversed_part = encrypted_data[start_index:end_index][::-1]
    decrypted_data = encrypted_data[:start_index] + reversed_part + encrypted_data[end_index:]
    mid_index = len(decrypted_data) // 2
    if len(decrypted_data) % 2 != 0:
        decrypted_data = decrypted_data[:mid_index] + decrypted_data[mid_index + 1:]
    return base64.b64decode(decrypted_data.encode('utf-8')).decode('utf-8')


def _decode_secret_master_key_text(text: str) -> bytes:
    raw = str(text or '').strip()
    if not raw:
        return b''
    compact = ''.join(raw.split())
    if compact:
        try:
            padding = '=' * (-len(compact) % 4)
            data = base64.urlsafe_b64decode((compact + padding).encode('utf-8'))
            if data:
                return data
        except Exception:
            pass
    return raw.encode('utf-8')


def _load_secret_master_key() -> bytes:
    for env_name in _SECRET_MASTER_KEY_ENV_NAMES:
        env_value = os.environ.get(env_name, '')
        data = _decode_secret_master_key_text(env_value)
        if data:
            return hashlib.sha256(data).digest()

    try:
        if _SECRET_MASTER_KEY_FILE.exists():
            file_value = _SECRET_MASTER_KEY_FILE.read_text(encoding='utf-8')
            data = _decode_secret_master_key_text(file_value)
            if data:
                return hashlib.sha256(data).digest()
    except Exception:
        pass

    seed = os.urandom(32)
    try:
        _SECRET_MASTER_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_MASTER_KEY_FILE.write_text(base64.urlsafe_b64encode(seed).decode('utf-8'), encoding='utf-8')
    except Exception:
        pass
    return hashlib.sha256(seed).digest()


def _secret_stream_bytes(key: bytes, nonce: bytes, size: int) -> bytes:
    if size <= 0:
        return b''
    out = bytearray()
    counter = 0
    while len(out) < size:
        block = hmac.new(key, nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:size])


def _secret_encrypt_value(value: str) -> str:
    text = str(value or '')
    if not text:
        return ''
    if text.startswith(_SECRET_CIPHER_PREFIX):
        try:
            _secret_decrypt_value(text)
            return text
        except Exception:
            pass

    master = _load_secret_master_key()
    enc_key = hmac.new(master, _SECRET_STREAM_INFO, hashlib.sha256).digest()
    mac_key = hmac.new(master, _SECRET_MAC_INFO, hashlib.sha256).digest()
    nonce = os.urandom(16)
    plain = text.encode('utf-8')
    stream = _secret_stream_bytes(enc_key, nonce, len(plain))
    cipher = bytes([a ^ b for a, b in zip(plain, stream)])
    digest = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + cipher + digest).decode('utf-8').rstrip('=')
    return _SECRET_CIPHER_PREFIX + payload


def _secret_decrypt_value(value: str) -> str:
    text = str(value or '')
    if not text:
        return ''
    if not text.startswith(_SECRET_CIPHER_PREFIX):
        return text

    encoded = text[len(_SECRET_CIPHER_PREFIX):]
    padding = '=' * (-len(encoded) % 4)
    payload = base64.urlsafe_b64decode((encoded + padding).encode('utf-8'))
    if len(payload) < 48:
        raise ValueError('invalid secret payload')

    nonce = payload[:16]
    digest = payload[-32:]
    cipher = payload[16:-32]

    master = _load_secret_master_key()
    enc_key = hmac.new(master, _SECRET_STREAM_INFO, hashlib.sha256).digest()
    mac_key = hmac.new(master, _SECRET_MAC_INFO, hashlib.sha256).digest()
    expected = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(digest, expected):
        raise ValueError('secret integrity check failed')

    stream = _secret_stream_bytes(enc_key, nonce, len(cipher))
    plain = bytes([a ^ b for a, b in zip(cipher, stream)])
    return plain.decode('utf-8')


def _normalize_sensitive_secret_key(value: str) -> str:
    key = str(value or '').strip()
    if not _SECRET_KEY_RE.fullmatch(key):
        raise ValueError('key 仅支持字母、数字、点、下划线、短横线、冒号，长度 2-128')
    return key


def _normalize_secret_access_level(value: str) -> str:
    level = str(value or 'admin').strip().lower() or 'admin'
    if level not in _SECRET_ACCESS_LEVELS:
        return 'admin'
    return level


def _serialize_sensitive_secret(row: Any) -> dict[str, Any]:
    secret_value = str(row['secret_value'] or '')
    return {
        'id': str(row['id'] or ''),
        'key': str(row['secret_key'] or ''),
        'name': str(row['name'] or ''),
        'category': str(row['category'] or ''),
        'description': str(row['description'] or ''),
        'enabled': bool(int(row['enabled'] or 0)),
        'clientAccessLevel': _normalize_secret_access_level(row['client_access_level'] or 'admin'),
        'updatedBy': str(row['updated_by'] or ''),
        'lastAccessedAt': str(row['last_accessed_at'] or ''),
        'updatedAt': str(row['updated_at'] or ''),
        'createdAt': str(row['created_at'] or ''),
        'hasValue': bool(secret_value),
        'maskedValue': _mask_secret(_secret_decrypt_value(secret_value)) if secret_value else '',
    }


def _touch_sensitive_secret_access(conn: sqlite3.Connection, row_id: str):
    conn.execute(
        'UPDATE sensitive_secrets SET last_accessed_at = ?, updated_at = updated_at WHERE id = ?',
        (_now_str(), row_id),
    )


def _require_sensitive_secret_client_user(access_level: str, authorization: Optional[str]):
    level = _normalize_secret_access_level(access_level)
    if level == 'admin':
        user = _require_user(authorization)
        _require_permission(user, 'system.secret.read')
        return user
    if level == 'authenticated':
        return _require_user(authorization)
    user, _ = _require_entitled_user(authorization)
    return user


def _normalize_version(v: str) -> str:
    return str(v or '').strip().lstrip('vV')


def _version_parts(v: str) -> list[int]:
    n = _normalize_version(v)
    if not n:
        return []
    nums = re.findall(r'\d+', n)
    return [int(x) for x in nums] if nums else []


def _cmp_version(a: str, b: str) -> int:
    pa = _version_parts(a)
    pb = _version_parts(b)
    m = max(len(pa), len(pb))
    pa += [0] * (m - len(pa))
    pb += [0] * (m - len(pb))
    for i in range(m):
        if pa[i] < pb[i]:
            return -1
        if pa[i] > pb[i]:
            return 1
    return 0


def _serialize_version_policy(row: Any) -> dict[str, Any]:
    return {
        'id': str(row['id'] or ''),
        'appCode': str(row['app_code'] or ''),
        'target': str(row['target'] or ''),
        'platform': str(row['platform'] or ''),
        'channel': str(row['channel'] or ''),
        'latestVersion': str(row['latest_version'] or ''),
        'minSupportedVersion': str(row['min_supported_version'] or ''),
        'enforceExactMatch': bool(int(row['enforce_exact_match'] or 0)),
        'forceUpgrade': bool(int(row['force_upgrade'] or 0)),
        'autoUpgradeWithoutConfirm': bool(int(row['auto_upgrade_without_confirm'] or 0)),
        'title': str(row['title'] or ''),
        'details': str(row['details'] or ''),
        'downloadUrl': str(row['download_url'] or ''),
        'releaseNotes': str(row['release_notes'] or ''),
        'publishedAt': str(row['published_at'] or ''),
        'updatedBy': str(row['updated_by'] or ''),
        'updatedAt': str(row['updated_at'] or ''),
        'createdAt': str(row['created_at'] or ''),
    }


def _find_best_version_policy(
    conn: sqlite3.Connection,
    app_code: str,
    target: str,
    platform: str,
    channel: str,
):
    candidates = [
        (app_code, target, platform, channel),
        (app_code, target, 'all', channel),
        (app_code, target, platform, 'stable'),
        (app_code, target, 'all', 'stable'),
    ]

    for a, t, p, c in candidates:
        row = conn.execute(
            '''
            SELECT *
            FROM client_version_policies
            WHERE app_code = ? AND target = ? AND platform = ? AND channel = ?
            LIMIT 1
            ''',
            (a, t, p, c),
        ).fetchone()
        if row:
            return row
    return None


@router.get('/system/version-policy/list')
def list_version_policies(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.version_policy.read')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT *
                FROM client_version_policies
                ORDER BY app_code ASC, target ASC, platform ASC, channel ASC, updated_at DESC
                '''
            ).fetchall()

    return _ok([_serialize_version_policy(r) for r in rows])


@router.post('/system/version-policy/save')
def save_version_policy(body: VersionPolicySaveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.version_policy.manage')

    app_code = str(body.appCode or 'AiceMind').strip() or 'AiceMind'
    target = str(body.target or 'backtest-desktop').strip() or 'backtest-desktop'
    platform = str(body.platform or 'all').strip() or 'all'
    channel = str(body.channel or 'stable').strip() or 'stable'

    latest_version = _normalize_version(body.latestVersion)
    min_supported = _normalize_version(body.minSupportedVersion)

    if not latest_version:
        return _fail('latestVersion 不能为空')

    if min_supported and _cmp_version(min_supported, latest_version) > 0:
        return _fail('minSupportedVersion 不能高于 latestVersion')

    now = _now_str()
    actor = str(user.get('username') or user.get('id') or '').strip()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            exists = conn.execute(
                '''
                SELECT id FROM client_version_policies
                WHERE app_code = ? AND target = ? AND platform = ? AND channel = ?
                LIMIT 1
                ''',
                (app_code, target, platform, channel),
            ).fetchone()

            row_id = str(exists['id'] if exists else uuid.uuid4().hex)

            if exists:
                conn.execute(
                    '''
                    UPDATE client_version_policies
                    SET latest_version = ?,
                        min_supported_version = ?,
                        enforce_exact_match = ?,
                        force_upgrade = ?,
                        auto_upgrade_without_confirm = ?,
                        title = ?,
                        details = ?,
                        download_url = ?,
                        release_notes = ?,
                        published_at = ?,
                        updated_by = ?,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        latest_version,
                        min_supported,
                        1 if body.enforceExactMatch else 0,
                        1 if body.forceUpgrade else 0,
                        1 if body.autoUpgradeWithoutConfirm else 0,
                        str(body.title or '').strip() or '发现新版本，请升级后继续使用',
                        str(body.details or '').strip(),
                        str(body.downloadUrl or '').strip(),
                        str(body.releaseNotes or '').strip(),
                        str(body.publishedAt or '').strip(),
                        actor,
                        now,
                        row_id,
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO client_version_policies (
                        id, app_code, target, platform, channel,
                        latest_version, min_supported_version,
                        enforce_exact_match, force_upgrade, auto_upgrade_without_confirm,
                        title, details, download_url, release_notes,
                        published_at, updated_by, updated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        row_id,
                        app_code,
                        target,
                        platform,
                        channel,
                        latest_version,
                        min_supported,
                        1 if body.enforceExactMatch else 0,
                        1 if body.forceUpgrade else 0,
                        1 if body.autoUpgradeWithoutConfirm else 0,
                        str(body.title or '').strip() or '发现新版本，请升级后继续使用',
                        str(body.details or '').strip(),
                        str(body.downloadUrl or '').strip(),
                        str(body.releaseNotes or '').strip(),
                        str(body.publishedAt or '').strip(),
                        actor,
                        now,
                        now,
                    ),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'system.version_policy.save',
                'client_version_policies',
                row_id,
                {
                    'appCode': app_code,
                    'target': target,
                    'platform': platform,
                    'channel': channel,
                    'latestVersion': latest_version,
                    'minSupportedVersion': min_supported,
                    'forceUpgrade': bool(body.forceUpgrade),
                    'autoUpgradeWithoutConfirm': bool(body.autoUpgradeWithoutConfirm),
                },
            )
            conn.commit()

    return _ok(True, message='版本策略已保存')


@router.post('/public/version/check')
def public_version_check(body: VersionCheckBody):
    app_code = str(body.appCode or 'AiceMind').strip() or 'AiceMind'
    target = str(body.target or 'backtest-desktop').strip() or 'backtest-desktop'
    platform = str(body.platform or 'all').strip() or 'all'
    channel = str(body.channel or 'stable').strip() or 'stable'
    current = _normalize_version(body.currentVersion)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = _find_best_version_policy(conn, app_code, target, platform, channel)

    if not row:
        return _ok(
            {
                'appCode': app_code,
                'target': target,
                'platform': platform,
                'channel': channel,
                'currentVersion': current,
                'allowRun': True,
                'needUpgrade': False,
                'forceUpgrade': False,
                'autoUpgradeWithoutConfirm': False,
                'reason': 'no_policy',
            }
        )

    latest = _normalize_version(str(row['latest_version'] or ''))
    minimum = _normalize_version(str(row['min_supported_version'] or ''))
    enforce_exact = bool(int(row['enforce_exact_match'] or 0))
    force_upgrade = bool(int(row['force_upgrade'] or 0))
    auto_upgrade = bool(int(row['auto_upgrade_without_confirm'] or 0))

    need_upgrade = False
    allow_run = True
    reason = 'ok'

    if enforce_exact:
        need_upgrade = (not current) or _cmp_version(current, latest) != 0
        allow_run = not need_upgrade
        if need_upgrade:
            reason = 'exact_version_required'
    else:
        if latest and current and _cmp_version(current, latest) < 0:
            need_upgrade = True
            reason = 'new_version_available'
        if minimum and (not current or _cmp_version(current, minimum) < 0):
            need_upgrade = True
            allow_run = not force_upgrade
            reason = 'below_min_supported'

    if enforce_exact and need_upgrade:
        allow_run = not force_upgrade

    return _ok(
        {
            'appCode': app_code,
            'target': target,
            'platform': platform,
            'channel': channel,
            'currentVersion': current,
            'latestVersion': latest,
            'minSupportedVersion': minimum,
            'enforceExactMatch': enforce_exact,
            'allowRun': allow_run,
            'needUpgrade': need_upgrade,
            'forceUpgrade': force_upgrade,
            'autoUpgradeWithoutConfirm': auto_upgrade,
            'title': str(row['title'] or ''),
            'details': str(row['details'] or ''),
            'downloadUrl': str(row['download_url'] or ''),
            'releaseNotes': str(row['release_notes'] or ''),
            'publishedAt': str(row['published_at'] or ''),
            'reason': reason,
        }
    )


class MigrationDownBody(BaseModel):
    steps: int = 1


@router.get('/system/migrations/status')
def get_migrations_status(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    data = migration_status()
    return _ok(data)


@router.post('/system/migrations/up')
def run_migrations_up(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    applied = migrate_up()
    return _ok({'applied': applied}, message='迁移执行完成')


@router.post('/system/migrations/down')
def run_migrations_down(body: MigrationDownBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    steps = max(1, int(body.steps or 1))
    rolled = migrate_down(steps)
    return _ok({'rolledBack': rolled}, message='回滚执行完成')


# ===== 配置导出/导入 =====

_CONFIG_TABLES = {
    'security_policy': {
        'single': True,
        'id_col': 'id',
        'cols': [
            'password_min_length', 'password_require_letter', 'password_require_digit',
            'password_require_special', 'login_fail_max', 'login_fail_window_minutes',
            'login_lock_minutes', 'session_ttl_hours', 'force_logout_on_password_reset',
        ],
    },
    'email_settings': {
        'single': True,
        'id_col': 'id',
        'cols': [
            'smtp_host', 'smtp_port', 'smtp_username', 'smtp_password',
            'from_email', 'from_name', 'use_tls', 'use_ssl',
            'verify_subject_template', 'verify_body_template',
        ],
    },
    'payment_settings': {
        'single': True,
        'id_col': 'id',
        'cols': [
            'alipay_enabled', 'alipay_app_id', 'alipay_merchant_id',
            'alipay_app_private_key', 'alipay_public_key',
            'alipay_gateway', 'alipay_notify_url', 'alipay_return_url', 'alipay_sign_type',
            'wechat_enabled', 'wechat_app_id', 'wechat_merchant_id',
            'wechat_api_v3_key', 'wechat_private_key', 'wechat_serial_no',
            'wechat_gateway', 'wechat_notify_url', 'wechat_return_url',
            'payment_alert_enabled', 'payment_alert_emails', 'payment_alert_webhook',
        ],
    },
    'observability_settings': {
        'single': True,
        'id_col': 'id',
        'cols': ['sentry_dsn', 'alert_webhook', 'alert_emails'],
    },
    'sensitive_secrets': {
        'single': False,
        'id_col': 'id',
        'cols': [
            'id', 'secret_key', 'name', 'category', 'secret_value', 'description',
            'enabled', 'client_access_level', 'updated_by', 'last_accessed_at',
        ],
    },
    'legal_docs': {
        'single': False,
        'id_col': 'doc_type',
        'cols': ['doc_type', 'title', 'content', 'version', 'effective_at'],
    },
    'plans': {
        'single': False,
        'id_col': 'code',
        'cols': [
            'id', 'code', 'name', 'price', 'duration_days', 'level',
            'status', 'description', 'backtest_daily_limit', 'max_backtest_days',
        ],
    },
}


def _export_table(conn: sqlite3.Connection, table_name: str, meta: dict) -> list[dict]:
    cols = meta['cols']
    col_str = ', '.join(cols)
    rows = conn.execute(f'SELECT {col_str} FROM {table_name}').fetchall()
    result = []
    for row in rows:
        item = {}
        for col in cols:
            val = row[col]
            # 布尔值转换
            if isinstance(val, int) and col in (
                'password_require_letter', 'password_require_digit', 'password_require_special',
                'force_logout_on_password_reset', 'use_tls', 'use_ssl',
                'alipay_enabled', 'wechat_enabled', 'payment_alert_enabled',
            ):
                item[col] = bool(val)
            else:
                item[col] = val
        result.append(item)
    return result


def _import_table(conn: sqlite3.Connection, table_name: str, meta: dict, rows: list[dict]):
    cols = meta['cols']
    placeholders = ', '.join(['?' for _ in cols])
    col_str = ', '.join(cols)

    if meta['single']:
        # 单条记录表：先确保 id=1 存在
        row = rows[0] if rows else {}
        if not row:
            return
        exists = conn.execute(f'SELECT 1 FROM {table_name} LIMIT 1').fetchone()
        if exists:
            # UPDATE
            set_clause = ', '.join([f'{c} = ?' for c in cols])
            values = [row.get(c) for c in cols]
            conn.execute(f'UPDATE {table_name} SET {set_clause}', values)
        else:
            # INSERT（需要 created_at/updated_at）
            now = _now_str()
            all_cols = cols + ['updated_at', 'created_at']
            all_vals = [row.get(c) for c in cols] + [now, now]
            all_placeholders = ', '.join(['?' for _ in all_cols])
            conn.execute(
                f'INSERT INTO {table_name} ({", ".join(all_cols)}) VALUES ({all_placeholders})',
                all_vals,
            )
    else:
        # 多条记录表：先清空再插入
        conn.execute(f'DELETE FROM {table_name}')
        now = _now_str()
        extra_cols = []
        if table_name == 'legal_docs':
            extra_cols = ['updated_at', 'created_at']
        elif table_name == 'plans':
            extra_cols = ['updated_at', 'created_at']
        elif table_name == 'client_version_policies':
            extra_cols = ['updated_at', 'created_at']
        elif table_name == 'sensitive_secrets':
            extra_cols = ['updated_at', 'created_at']
        elif table_name == 'rbac_permissions':
            extra_cols = ['updated_at', 'created_at']
        elif table_name == 'rbac_roles':
            extra_cols = ['updated_at', 'created_at']
        elif table_name in ('rbac_role_permissions', 'rbac_account_roles'):
            extra_cols = ['created_at']

        for row in rows:
            values = [row.get(c) for c in cols]
            if extra_cols:
                all_cols = cols + extra_cols
                all_vals = values + [now, now]
            else:
                all_cols = cols
                all_vals = values
            all_placeholders = ', '.join(['?' for _ in all_cols])
            conn.execute(
                f'INSERT INTO {table_name} ({", ".join(all_cols)}) VALUES ({all_placeholders})',
                all_vals,
            )


@router.post('/system/config/export')
def export_config(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_admin(user)

    _ensure_db()
    payload = {
        'version': '1.0',
        'exported_at': datetime.now().isoformat(),
        'tables': {},
    }

    with _DB_LOCK:
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            for table_name, meta in _CONFIG_TABLES.items():
                try:
                    payload['tables'][table_name] = _export_table(conn, table_name, meta)
                except Exception:
                    payload['tables'][table_name] = []

    filename = f"aicemind-config-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')

    return StreamingResponse(
        BytesIO(data),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@router.post('/system/config/import')
def import_config(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_admin(user)

    if not file.filename or not file.filename.endswith('.json'):
        return _fail('请上传 .json 文件')

    try:
        content = file.file.read().decode('utf-8')
        payload = json.loads(content)
    except Exception as e:
        return _fail(f'文件解析失败: {e}')

    if not isinstance(payload, dict):
        return _fail('文件格式错误')

    version = str(payload.get('version') or '')
    if version not in ('', '1.0'):
        return _fail(f'不支持的配置版本: {version}')

    tables = payload.get('tables')
    if not isinstance(tables, dict):
        return _fail('配置数据格式错误')

    results = {}
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            for table_name, meta in _CONFIG_TABLES.items():
                rows = tables.get(table_name)
                if not isinstance(rows, list):
                    results[table_name] = 'skipped'
                    continue
                try:
                    _import_table(conn, table_name, meta, rows)
                    results[table_name] = f'ok ({len(rows)} rows)'
                except Exception as e:
                    results[table_name] = f'error: {e}'
            conn.commit()

    _audit_log(
        conn,
        str(user.get('id') or ''),
        'system.config.import',
        'config',
        'all',
        {'results': results},
    )

    return _ok({'results': results}, message='配置导入完成')


# ===== 敏感数据管理 =====

@router.get('/system/sensitive-secrets/list')
def list_sensitive_secrets(
    authorization: Optional[str] = Header(default=None),
    category: str = Query('', description='按分类筛选，可选'),
):
    user = _require_user(authorization)
    _require_permission(user, 'system.secret.manage')

    category_key = str(category or '').strip()
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            if category_key:
                rows = conn.execute(
                    '''
                    SELECT *
                    FROM sensitive_secrets
                    WHERE category = ?
                    ORDER BY category ASC, secret_key ASC, updated_at DESC
                    ''',
                    (category_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    '''
                    SELECT *
                    FROM sensitive_secrets
                    ORDER BY category ASC, secret_key ASC, updated_at DESC
                    '''
                ).fetchall()

    return _ok([_serialize_sensitive_secret(r) for r in rows])


@router.post('/system/sensitive-secrets/save')
def save_sensitive_secret(body: SensitiveSecretItemBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.secret.manage')

    try:
        secret_key = _normalize_sensitive_secret_key(body.key)
    except ValueError as e:
        return _fail(str(e))

    secret_name = str(body.name or '').strip() or secret_key
    category = str(body.category or 'general').strip() or 'general'
    description = str(body.description or '').strip()
    enabled = 1 if body.enabled else 0
    access_level = _normalize_secret_access_level(body.clientAccessLevel)
    clear_value = bool(body.clearValue)
    input_value = str(body.value or '')
    actor = str(user.get('username') or user.get('id') or '').strip()
    now = _now_str()

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                'SELECT * FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                (secret_key,),
            ).fetchone()

            row_id = str(exists['id'] if exists else uuid.uuid4().hex)
            current_secret_value = str((exists['secret_value'] if exists else '') or '')
            if clear_value:
                next_secret_value = ''
            elif input_value == '' and exists is not None:
                next_secret_value = current_secret_value
            else:
                next_secret_value = _secret_encrypt_value(input_value)

            if exists:
                conn.execute(
                    '''
                    UPDATE sensitive_secrets
                    SET name = ?,
                        category = ?,
                        secret_value = ?,
                        description = ?,
                        enabled = ?,
                        client_access_level = ?,
                        updated_by = ?,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        secret_name,
                        category,
                        next_secret_value,
                        description,
                        enabled,
                        access_level,
                        actor,
                        now,
                        row_id,
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO sensitive_secrets (
                        id, secret_key, name, category, secret_value, description,
                        enabled, client_access_level, updated_by, last_accessed_at, updated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                    ''',
                    (
                        row_id,
                        secret_key,
                        secret_name,
                        category,
                        next_secret_value,
                        description,
                        enabled,
                        access_level,
                        actor,
                        now,
                        now,
                    ),
                )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'system.secret.save',
                'sensitive_secrets',
                row_id,
                {
                    'key': secret_key,
                    'category': category,
                    'enabled': bool(enabled),
                    'clientAccessLevel': access_level,
                    'valueChanged': clear_value or input_value != '',
                    'cleared': clear_value,
                },
            )
            conn.commit()

    return _ok(True, message='敏感数据已保存')


@router.post('/system/sensitive-secrets/delete')
def delete_sensitive_secret(body: SensitiveSecretResolveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.secret.manage')

    try:
        secret_key = _normalize_sensitive_secret_key(body.key)
    except ValueError as e:
        return _fail(str(e))

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT id FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                (secret_key,),
            ).fetchone()
            if not row:
                return _fail('敏感数据不存在')

            conn.execute('DELETE FROM sensitive_secrets WHERE id = ?', (str(row['id'] or ''),))
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'system.secret.delete',
                'sensitive_secrets',
                str(row['id'] or ''),
                {'key': secret_key},
            )
            conn.commit()

    return _ok(True, message='敏感数据已删除')


@router.post('/system/sensitive-secrets/resolve')
def resolve_sensitive_secret(body: SensitiveSecretResolveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.secret.read')

    try:
        secret_key = _normalize_sensitive_secret_key(body.key)
    except ValueError as e:
        return _fail(str(e))

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                (secret_key,),
            ).fetchone()
            if not row:
                return _fail('敏感数据不存在')

            try:
                plaintext = _secret_decrypt_value(str(row['secret_value'] or ''))
            except Exception as e:
                return _fail(f'敏感数据解密失败: {e}')

            _touch_sensitive_secret_access(conn, str(row['id'] or ''))
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'system.secret.resolve',
                'sensitive_secrets',
                str(row['id'] or ''),
                {'key': secret_key},
            )
            conn.commit()

    return _ok(
        {
            'key': str(row['secret_key'] or ''),
            'name': str(row['name'] or ''),
            'category': str(row['category'] or ''),
            'description': str(row['description'] or ''),
            'enabled': bool(int(row['enabled'] or 0)),
            'clientAccessLevel': _normalize_secret_access_level(row['client_access_level'] or 'admin'),
            'value': plaintext,
        }
    )


@router.post('/client/sensitive-secrets/resolve')
def client_resolve_sensitive_secret(
    body: SensitiveSecretResolveBody,
    authorization: Optional[str] = Header(default=None),
):
    try:
        secret_key = _normalize_sensitive_secret_key(body.key)
    except ValueError as e:
        return _fail(str(e))

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                (secret_key,),
            ).fetchone()
            if not row:
                return _fail('敏感数据不存在')
            if int(row['enabled'] or 0) != 1:
                return _fail('敏感数据未启用')

    user = _require_sensitive_secret_client_user(str(row['client_access_level'] or 'admin'), authorization)

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                (secret_key,),
            ).fetchone()
            if not row:
                return _fail('敏感数据不存在')
            if int(row['enabled'] or 0) != 1:
                return _fail('敏感数据未启用')

            try:
                plaintext = _secret_decrypt_value(str(row['secret_value'] or ''))
            except Exception as e:
                return _fail(f'敏感数据解密失败: {e}')

            _touch_sensitive_secret_access(conn, str(row['id'] or ''))
            _audit_log(
                conn,
                str(user.get('id') or ''),
                'client.secret.resolve',
                'sensitive_secrets',
                str(row['id'] or ''),
                {
                    'key': secret_key,
                    'clientAccessLevel': _normalize_secret_access_level(row['client_access_level'] or 'admin'),
                },
            )
            conn.commit()

    return _ok(
        {
            'key': str(row['secret_key'] or ''),
            'name': str(row['name'] or ''),
            'category': str(row['category'] or ''),
            'description': str(row['description'] or ''),
            'value': plaintext,
            'updatedAt': str(row['updated_at'] or ''),
        }
    )


@router.post('/client/sensitive-secrets/sync')
def client_sync_sensitive_secrets(body: SensitiveSecretSyncBody):
    machine_id = str(body.machineId or '').strip()
    activation_secret = str(body.activationSecret or '').strip()
    issued_at = int(body.issuedAt or 0)
    requested_keys = []
    for item in body.requestedKeys or []:
        key = str(item or '').strip()
        if not key:
            continue
        try:
            requested_keys.append(_normalize_sensitive_secret_key(key))
        except ValueError:
            return _fail(f'非法敏感数据 key: {key}')

    if not machine_id:
        return _fail('缺少 machineId')
    if not activation_secret:
        return _fail('缺少 activationSecret')
    now_ts = int(time.time())
    if issued_at and abs(now_ts - issued_at) > 300:
        return _fail('请求已过期')
    if not requested_keys:
        return _fail('requestedKeys 不能为空')

    activation_base_url = str(os.getenv('AICEMIND_ACTIVATION_BASE_URL', '')).strip() or 'http://api.8188811.xyz'
    activation_app_code = str(os.getenv('AICEMIND_ACTIVATION_APP_CODE', '')).strip() or 'AiceMind'

    try:
        verify_resp = requests.post(
            f"{activation_base_url.rstrip('/')}/v1/active_code/use_code_encry",
            json={
                'code_type': activation_app_code,
                'bind_id': machine_id,
                'code': activation_secret,
            },
            timeout=12,
        )
        if verify_resp.status_code != 200:
            return _fail('激活校验失败')
        verify_json = json.loads(_decrypt_activation_response(verify_resp.text))
        verify_data = verify_json.get('data') if isinstance(verify_json, dict) else None
        if not isinstance(verify_data, dict) or not bool(verify_data.get('status')):
            return _fail('激活状态无效')
    except Exception as e:
        return _fail(f'激活校验异常: {e}')

    items: dict[str, str] = {}
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            for secret_key in requested_keys:
                row = conn.execute(
                    'SELECT * FROM sensitive_secrets WHERE secret_key = ? LIMIT 1',
                    (secret_key,),
                ).fetchone()
                if not row or int(row['enabled'] or 0) != 1:
                    continue
                try:
                    items[secret_key] = _secret_decrypt_value(str(row['secret_value'] or ''))
                except Exception:
                    continue
                _touch_sensitive_secret_access(conn, str(row['id'] or ''))
                _audit_log(
                    conn,
                    machine_id,
                    'client.secret.sync',
                    'sensitive_secrets',
                    str(row['id'] or ''),
                    {
                        'key': secret_key,
                        'machineId': machine_id,
                    },
                )
            conn.commit()

    payload = encrypt_payload({
        'machineId': machine_id,
        'issuedAt': now_ts,
        'items': items,
    })

    return _ok({'payload': payload, 'count': len(items)})


# ===== 原有接口 =====

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
