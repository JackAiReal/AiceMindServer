"""
AiceMind Admin Backend — 独立部署的管理后台 API 服务

职责：会员管理、订阅、支付、订单、安全策略、审计日志等
端口：5010 (默认)
数据库：data/admin_console.db (SQLite，与主后端共享同一数据文件)
"""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / '.env', override=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting AiceMind Admin Backend...")
    # 确保 DB 目录存在
    from app.core.db_runtime import resolve_sqlite_path
    db_path = resolve_sqlite_path(Path(__file__).resolve().parents[1] / 'data' / 'admin_console.db')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"📁 Admin DB path: {db_path}")
    yield
    print("🔄 Admin Backend shutting down...")


def create_app():
    app = FastAPI(
        title="AiceMind Admin API",
        description="AiceMind 管理后台独立 API 服务",
        version="1.0.0",
        docs_url="/admin-api/docs",
        redoc_url="/admin-api/redoc",
        lifespan=lifespan,
    )

    # CORS — 允许 admin-console 前端访问
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=(
            r"^https?://("
            r"localhost(:\d+)?|"
            r"tauri\.localhost(:\d+)?|"
            r"127\.0\.0\.1(:\d+)?|"
            r"192\.168\.\d{1,3}\.\d{1,3}(:\d+)?|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?|"
            r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}(:\d+)?"
            r")$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册 admin API 路由 — 所有路径带 /admin-api 前缀
    from app.api.admin import router as admin_router
    app.include_router(admin_router, prefix="/admin-api", tags=["admin"])

    # Health check
    @app.get("/admin-api/health")
    async def health_check():
        return {"status": "healthy", "service": "aicemind-admin"}

    return app


app = create_app()
