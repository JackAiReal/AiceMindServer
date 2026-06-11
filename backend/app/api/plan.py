from __future__ import annotations

from fastapi import APIRouter
from app.api.deps import *  # noqa: F401,F403

router = APIRouter()

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
                       backtest_daily_limit, max_backtest_days, updated_at
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
                'backtestDailyLimit': int(r['backtest_daily_limit'] or 0),
                'maxBacktestDays': int(r['max_backtest_days'] or 0),
                'updatedAt': r['updated_at'],
            }
            for r in rows
        ]
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
                       backtest_daily_limit, max_backtest_days, updated_at
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
                'backtestDailyLimit': int(r['backtest_daily_limit'] or 0),
                'maxBacktestDays': int(r['max_backtest_days'] or 0),
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

    backtest_daily_limit = max(0, int(body.backtestDailyLimit or 0))
    max_backtest_days = int(body.maxBacktestDays or 0)
    if max_backtest_days == 0:
        return _fail('最大回测天数不能为 0')

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
                    backtest_daily_limit, max_backtest_days,
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
                    backtest_daily_limit,
                    max_backtest_days,
                    now,
                    now,
                ),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'plan.create',
                'plan',
                row_id,
                {
                    'code': code,
                    'backtestDailyLimit': backtest_daily_limit,
                    'maxBacktestDays': max_backtest_days,
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

    backtest_daily_limit = max(0, int(body.backtestDailyLimit or 0))
    max_backtest_days = int(body.maxBacktestDays or 0)
    if max_backtest_days == 0:
        return _fail('最大回测天数不能为 0')

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
                    backtest_daily_limit = ?, max_backtest_days = ?, updated_at = ?
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
                    backtest_daily_limit,
                    max_backtest_days,
                    _now_str(),
                    row_id,
                ),
            )

            _audit_log(
                conn,
                str(user.get('id') or ''),
                'plan.update',
                'plan',
                row_id,
                {
                    'code': code,
                    'backtestDailyLimit': backtest_daily_limit,
                    'maxBacktestDays': max_backtest_days,
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
