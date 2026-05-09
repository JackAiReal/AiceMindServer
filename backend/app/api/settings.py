from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

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
