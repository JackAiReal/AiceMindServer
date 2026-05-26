# AiceMind Admin Backend

独立部署的管理后台 API 服务

## 职责
- 会员管理 & 订阅
- 支付（支付宝/微信）& 订单
- 安全策略 & 2FA
- 审计日志 & 监控
- 计费 & 积分
- 合规文档

## 技术栈
- FastAPI + Uvicorn
- SQLite (admin_console.db)
- Python 3.10+

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务 (默认端口 5011)
python main.py
```

API 文档：http://localhost:5011/admin-api/docs

## 架构

```
admin-backend/
├── app/
│   ├── __init__.py          # FastAPI app 入口
│   ├── api/
│   │   ├── admin.py         # 路由聚合器
│   │   ├── deps.py          # 共享依赖/辅助函数/模型
│   │   ├── auth.py          # 认证/登录/2FA
│   │   ├── account.py       # 用户账号
│   │   ├── security.py      # 安全策略
│   │   ├── member.py        # 会员管理
│   │   ├── plan.py          # 套餐管理
│   │   ├── subscription.py  # 订阅管理
│   │   ├── order.py         # 订单管理
│   │   ├── payment.py       # 支付/回调
│   │   ├── billing.py       # 计费/积分
│   │   ├── monitor.py       # 监控/可观测
│   │   ├── audit.py         # 审计日志
│   │   └── settings.py      # 邮箱/合规
│   └── core/
│       ├── db_runtime.py    # 数据库运行时
│       └── entitlement.py   # 会员权益/计费
├── main.py                  # 启动入口
├── requirements.txt
└── .env                     # 环境配置
```

## 与主项目的关系

| 服务 | 部署位置 | 说明 |
|------|---------|------|
| AiceMindServer (本项目) | 服务器 | 管理后台API，提供会员/支付等服务 |
| AiceMind 桌面端 | 用户本地 | 回测引擎+前端，通过API鉴权 |
| admin-console | 服务器 | 管理后台前端，对接本API |
