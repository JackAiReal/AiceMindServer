from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import *  # noqa: F401,F403

router = APIRouter()


class RbacPermissionSaveBody(BaseModel):
    code: str
    name: str
    resource: str = ''
    action: str = ''
    description: str = ''
    status: str = 'active'


class RbacRoleSaveBody(BaseModel):
    code: str
    name: str
    description: str = ''
    status: str = 'active'
    permissionCodes: list[str] = []


class RbacAccountRolesSaveBody(BaseModel):
    accountId: str
    roleCodes: list[str] = []


@router.get('/system/rbac/permissions')
def list_rbac_permissions(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)
            rows = conn.execute(
                '''
                SELECT code, name, resource, action, description, status, updated_at, created_at
                FROM rbac_permissions
                ORDER BY code ASC
                '''
            ).fetchall()

    data = [
        {
            'code': str(r['code'] or ''),
            'name': str(r['name'] or ''),
            'resource': str(r['resource'] or ''),
            'action': str(r['action'] or ''),
            'description': str(r['description'] or ''),
            'status': str(r['status'] or ''),
            'updatedAt': str(r['updated_at'] or ''),
            'createdAt': str(r['created_at'] or ''),
        }
        for r in rows
    ]
    return _ok(data)


@router.post('/system/rbac/permissions/save')
def save_rbac_permission(body: RbacPermissionSaveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    code = str(body.code or '').strip()
    name = str(body.name or '').strip()
    if not code or not name:
        return _fail('code 和 name 不能为空')

    now = _now_str()
    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)

            exists = conn.execute('SELECT code FROM rbac_permissions WHERE code = ? LIMIT 1', (code,)).fetchone()
            if exists:
                conn.execute(
                    '''
                    UPDATE rbac_permissions
                    SET name = ?, resource = ?, action = ?, description = ?, status = ?, updated_at = ?
                    WHERE code = ?
                    ''',
                    (name, str(body.resource or '').strip(), str(body.action or '').strip(), str(body.description or '').strip(), str(body.status or 'active').strip() or 'active', now, code),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO rbac_permissions (code, name, resource, action, description, status, updated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (code, name, str(body.resource or '').strip(), str(body.action or '').strip(), str(body.description or '').strip(), str(body.status or 'active').strip() or 'active', now, now),
                )
            conn.commit()

    return _ok(True, message='权限已保存')


@router.get('/system/rbac/roles')
def list_rbac_roles(authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)

            roles = conn.execute(
                '''
                SELECT id, code, name, description, is_system, status, updated_at, created_at
                FROM rbac_roles
                ORDER BY is_system DESC, code ASC
                '''
            ).fetchall()

            data = []
            for r in roles:
                p_rows = conn.execute(
                    'SELECT permission_code FROM rbac_role_permissions WHERE role_code = ? ORDER BY permission_code ASC',
                    (str(r['code'] or ''),),
                ).fetchall()
                data.append(
                    {
                        'id': str(r['id'] or ''),
                        'code': str(r['code'] or ''),
                        'name': str(r['name'] or ''),
                        'description': str(r['description'] or ''),
                        'isSystem': bool(int(r['is_system'] or 0)),
                        'status': str(r['status'] or ''),
                        'permissionCodes': [str(x['permission_code'] or '') for x in p_rows],
                        'updatedAt': str(r['updated_at'] or ''),
                        'createdAt': str(r['created_at'] or ''),
                    }
                )

    return _ok(data)


@router.post('/system/rbac/roles/save')
def save_rbac_role(body: RbacRoleSaveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    code = str(body.code or '').strip()
    name = str(body.name or '').strip()
    if not code or not name:
        return _fail('code 和 name 不能为空')

    if code == 'super' and '*' not in set(body.permissionCodes or []):
        return _fail('super 角色必须包含 * 权限')

    now = _now_str()
    perms = sorted(set(str(p or '').strip() for p in (body.permissionCodes or []) if str(p or '').strip()))

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)

            role_row = conn.execute('SELECT id, is_system FROM rbac_roles WHERE code = ? LIMIT 1', (code,)).fetchone()
            role_id = str(role_row['id'] if role_row else uuid.uuid4().hex)
            is_system = bool(int(role_row['is_system'] or 0)) if role_row else False

            if role_row:
                if is_system and code == 'super':
                    # 允许更新描述与权限，但保留系统角色状态
                    pass
                conn.execute(
                    'UPDATE rbac_roles SET name = ?, description = ?, status = ?, updated_at = ? WHERE id = ?',
                    (name, str(body.description or '').strip(), str(body.status or 'active').strip() or 'active', now, role_id),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO rbac_roles (id, code, name, description, is_system, status, updated_at, created_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    ''',
                    (role_id, code, name, str(body.description or '').strip(), str(body.status or 'active').strip() or 'active', now, now),
                )

            conn.execute('DELETE FROM rbac_role_permissions WHERE role_code = ?', (code,))
            for p in perms:
                exists_perm = conn.execute('SELECT code FROM rbac_permissions WHERE code = ? LIMIT 1', (p,)).fetchone()
                if not exists_perm:
                    continue
                conn.execute(
                    'INSERT INTO rbac_role_permissions (id, role_code, permission_code, created_at) VALUES (?, ?, ?, ?)',
                    (uuid.uuid4().hex, code, p, now),
                )

            conn.commit()

    return _ok(True, message='角色已保存')


@router.get('/system/rbac/account/roles')
def get_account_roles(
    accountId: str = Query('', alias='accountId'),
    authorization: Optional[str] = Header(default=None),
):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    account_id = str(accountId or '').strip()
    if not account_id:
        return _fail('accountId 不能为空')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)
            rows = conn.execute('SELECT role_code FROM rbac_account_roles WHERE account_id = ? ORDER BY role_code ASC', (account_id,)).fetchall()

    return _ok([str(r['role_code'] or '') for r in rows])


@router.post('/system/rbac/account/roles/save')
def save_account_roles(body: RbacAccountRolesSaveBody, authorization: Optional[str] = Header(default=None)):
    user = _require_user(authorization)
    _require_permission(user, 'system.rbac.manage')

    account_id = str(body.accountId or '').strip()
    role_codes = sorted(set(str(x or '').strip() for x in (body.roleCodes or []) if str(x or '').strip()))
    if not account_id:
        return _fail('accountId 不能为空')

    with _DB_LOCK:
        _ensure_db()
        with _db_connect() as conn:
            conn.row_factory = sqlite3.Row
            _init_rbac_defaults(conn)

            exists_account = conn.execute('SELECT id FROM user_accounts WHERE id = ? LIMIT 1', (account_id,)).fetchone()
            if not exists_account:
                return _fail('账号不存在')

            conn.execute('DELETE FROM rbac_account_roles WHERE account_id = ?', (account_id,))
            now = _now_str()
            for role_code in role_codes:
                role_row = conn.execute('SELECT code FROM rbac_roles WHERE code = ? LIMIT 1', (role_code,)).fetchone()
                if not role_row:
                    continue
                conn.execute(
                    'INSERT INTO rbac_account_roles (id, account_id, role_code, created_at) VALUES (?, ?, ?, ?)',
                    (uuid.uuid4().hex, account_id, role_code, now),
                )

            conn.commit()

    return _ok(True, message='账号角色已更新')
