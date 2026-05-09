# AiceMindServer

AiceMind 管理端独立部署项目（Admin 前后端）。

## 项目定位

本仓库只承载 **服务器端能力**：

- Admin Backend（会员、订阅、支付、订单、安全、审计）
- Admin Frontend（管理控制台）

不包含回测引擎与用户本地桌面端逻辑。

---

## 目录结构

```text
AiceMindServer/
├── backend/   # FastAPI 管理后端（独立服务）
└── frontend/  # Vben Admin 管理前端
```

---

## 本地启动

### 1) 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

后端地址：`http://127.0.0.1:5010`
文档地址：`http://127.0.0.1:5010/admin-api/docs`

### 2) 启动前端

```bash
cd frontend
pnpm install
pnpm dev:antd
```

默认前端端口（自动选择可用端口，例如 5668）。

前端通过 `/admin-api/*` 代理到：
`http://localhost:5010/admin-api/*`

---

## 说明

- 本次拆分只做 **架构分离与代码迁移**，不改业务逻辑。
- 原有主项目可继续承载本地回测前后端；本仓库专注服务器 Admin 能力。
