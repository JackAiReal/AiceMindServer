from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO

from app.core.migrations import migrate_down, migrate_up, migration_status

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from app.api.deps import *  # noqa: F401,F403

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
            'status', 'description', 'daily_points_refresh', 'backtest_point_multiplier',
        ],
    },
    'client_version_policies': {
        'single': False,
        'id_col': 'id',
        'cols': [
            'id', 'app_code', 'target', 'platform', 'channel',
            'latest_version', 'min_supported_version',
            'enforce_exact_match', 'force_upgrade', 'auto_upgrade_without_confirm',
            'title', 'details', 'download_url', 'release_notes',
            'published_at', 'updated_by',
        ],
    },
    'rbac_permissions': {
        'single': False,
        'id_col': 'code',
        'cols': ['code', 'name', 'resource', 'action', 'description', 'status'],
    },
    'rbac_roles': {
        'single': False,
        'id_col': 'code',
        'cols': ['id', 'code', 'name', 'description', 'is_system', 'status'],
    },
    'rbac_role_permissions': {
        'single': False,
        'id_col': 'id',
        'cols': ['id', 'role_code', 'permission_code'],
    },
    'rbac_account_roles': {
        'single': False,
        'id_col': 'id',
        'cols': ['id', 'account_id', 'role_code'],
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
    _require_permission(user, 'system.config.export')

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
    _require_permission(user, 'system.config.import')

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

            # 审计日志必须在连接生命周期内写入，避免“导入成功但接口报500”
            try:
                _audit_log(
                    conn,
                    str(user.get('id') or ''),
                    'system.config.import',
                    'config',
                    'all',
                    {'results': results},
                )
            except Exception:
                # 导入主流程已完成，审计失败不应影响用户结果
                pass

            conn.commit()

    return _ok({'results': results}, message='配置导入完成')


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
