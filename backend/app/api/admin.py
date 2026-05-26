from __future__ import annotations

from fastapi import APIRouter

from app.api.account import router as account_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.backup import router as backup_router
from app.api.billing import router as billing_router
from app.api.member import router as member_router
from app.api.monitor import router as monitor_router
from app.api.order import router as order_router
from app.api.payment import router as payment_router
from app.api.plan import router as plan_router
from app.api.rbac import router as rbac_router
from app.api.security import router as security_router
from app.api.settings import router as settings_router
from app.api.subscription import router as subscription_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(account_router)
router.include_router(security_router)
router.include_router(rbac_router)
router.include_router(member_router)
router.include_router(plan_router)
router.include_router(subscription_router)
router.include_router(order_router)
router.include_router(payment_router)
router.include_router(billing_router)
router.include_router(monitor_router)
router.include_router(backup_router)
router.include_router(settings_router)
router.include_router(audit_router)
