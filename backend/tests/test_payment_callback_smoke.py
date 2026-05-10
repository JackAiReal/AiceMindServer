from fastapi.testclient import TestClient

from app import app as fastapi_app
from app.api import payment as payment_api


def test_callback_handler_is_imported():
    """防回归：payment.py 必须正确导入回调处理函数，避免 NameError。"""
    assert callable(payment_api._handle_payment_callback)


def test_alipay_callback_returns_success_text(monkeypatch):
    async def fake_handler(provider: str, request):
        return {"code": 0, "data": {"ok": True}, "message": "ok"}

    monkeypatch.setattr(payment_api, "_handle_payment_callback", fake_handler)

    client = TestClient(fastapi_app)
    resp = client.post(
        "/admin-api/payment/callback/alipay",
        data={"out_trade_no": "TPAY_TEST_001", "trade_status": "TRADE_SUCCESS", "total_amount": "0.01"},
    )
    assert resp.status_code == 200
    assert resp.text.strip().lower() == "success"


def test_alipay_callback_exception_still_ack(monkeypatch):
    async def boom_handler(provider: str, request):
        raise RuntimeError("boom")

    monkeypatch.setattr(payment_api, "_handle_payment_callback", boom_handler)

    client = TestClient(fastapi_app)
    resp = client.post(
        "/admin-api/payment/callback/alipay",
        data={"out_trade_no": "TPAY_TEST_002", "trade_status": "TRADE_SUCCESS", "total_amount": "0.01"},
    )
    assert resp.status_code == 200
    assert resp.text.strip().lower() == "success"
