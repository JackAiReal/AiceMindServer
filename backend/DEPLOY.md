# AiceMind Admin Backend — 服务器部署指南（全新部署）

## 📦 打包内容说明

```
backend/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 编排配置
├── .dockerignore           # Docker 构建忽略规则
├── deploy.sh               # 一键部署脚本
├── requirements.txt        # Python 依赖
├── main.py                 # 启动入口
├── .env.example            # 环境变量模板
├── app/                    # 应用代码
│   ├── __init__.py
│   ├── api/                # API 路由模块
│   │   ├── admin.py        # 路由聚合器
│   │   ├── auth.py
│   │   ├── account.py
│   │   ├── security.py
│   │   ├── member.py
│   │   ├── plan.py
│   │   ├── subscription.py
│   │   ├── order.py
│   │   ├── payment.py
│   │   ├── billing.py
│   │   ├── monitor.py
│   │   ├── settings.py
│   │   ├── audit.py
│   │   └── deps.py         # 公共依赖
│   └── core/               # 核心模块
│       ├── db_runtime.py
│       └── entitlement.py
└── data/                   # SQLite 数据库目录（运行时自动生成，需持久化）
```

---

## 🚀 全新部署步骤（推荐）

### 第一步：上传文件到服务器

```bash
# 1. 将 backend-deploy.tar.gz 上传到服务器
# 方式 A：scp（本地终端执行）
scp backend-deploy.tar.gz root@你的服务器IP:/opt/

# 方式 B：宝塔面板文件管理器直接上传

# 2. 服务器上解压
ssh root@你的服务器IP
cd /opt
tar xzvf backend-deploy.tar.gz
```

### 第二步：进入目录并创建数据目录

```bash
cd /opt/backend

# 创建空数据库目录（程序会自动初始化表结构）
mkdir -p data

# 确认 .env 存在（包里已包含，可选修改）
cat .env
```

当前 `.env` 内容：
```env
# AiceMind Admin Backend 环境配置
# 完全独立的后台系统，不依赖外部 Membership API

# 数据库路径（默认 data/admin_console.db）
# AICEMIND_DB_URL=sqlite:///data/admin_console.db
```

> 一般不需要修改，保持默认即可。

### 第三步：执行一键部署

```bash
chmod +x deploy.sh
./deploy.sh
```

脚本会自动完成：
- ✅ 检查 Docker 环境
- ✅ 创建数据目录
- ✅ 构建 Docker 镜像
- ✅ 启动容器
- ✅ 执行健康检查

### 第四步：验证服务是否启动

```bash
# 健康检查
curl http://127.0.0.1:5010/admin-api/health

# 预期返回：
# {"status":"healthy","service":"aicemind-admin"}

# 查看日志
docker logs -f aicemind-admin
```

### 第五步：配置 Nginx 反代（宝塔面板）

在宝塔中为你的前端站点添加 Nginx 配置：

```nginx
location /admin-api/ {
    proxy_pass http://127.0.0.1:5010/admin-api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

> ⚠️ **生产环境务必使用 HTTPS！**

---

## 🔑 默认登录账号

系统首次启动时会自动创建默认管理员账号：

| 账号类型 | 用户名 | 密码 | 权限 |
|----------|--------|------|------|
| **超级管理员** | `superadmin` | `Qaz12356789` | 全部权限（super） |
| 普通管理员 | `admin` | `123456` | 普通权限（admin） |

**首次登录请使用：**
- 用户名：`superadmin`
- 密码：`Qaz12356789`

> ⚠️ **安全提醒**：登录后请立即前往「系统设置 → 安全策略」修改默认密码！

---

## 🔧 常用命令

| 命令 | 说明 |
|------|------|
| `docker compose up -d` | 后台启动 |
| `docker compose down` | 停止服务 |
| `docker compose restart` | 重启服务 |
| `docker compose up -d --build` | 重新构建并启动 |
| `docker logs -f aicemind-admin` | 查看实时日志 |
| `docker exec -it aicemind-admin sh` | 进入容器 |

---

## ⚠️ 注意事项

1. **数据库初始化**：首次启动时会自动创建 SQLite 数据库和表结构，无需手动执行 SQL
2. **数据持久化**：`data/` 目录挂载到容器外，升级时不会丢失数据
3. **端口安全**：5010 只监听 127.0.0.1，不直接暴露外网，通过 Nginx 反代访问
4. **HTTPS**：生产环境务必配置 HTTPS，尤其涉及支付回调等敏感操作
5. **修改密码**：首次登录后务必修改默认管理员密码

---

## 📁 打包清单

部署包包含以下文件：
- ✅ Dockerfile
- ✅ docker-compose.yml
- ✅ .dockerignore
- ✅ deploy.sh
- ✅ requirements.txt
- ✅ main.py
- ✅ app/ 全部代码
- ✅ .env（已配置为独立运行）
- ❌ data/（运行时自动生成，首次启动自动建表）
