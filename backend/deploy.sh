#!/bin/bash
# AiceMind Admin Backend — 服务器一键部署脚本
# 用法：chmod +x deploy.sh && ./deploy.sh

set -e

echo "🚀 AiceMind Admin Backend 部署脚本"
echo "===================================="

# 1. 检查 Docker
echo "📦 检查 Docker 环境..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，请先安装 Docker"
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose 未安装，请先安装"
    exit 1
fi

# 2. 检查 .env
echo "🔧 检查环境配置..."
if [ ! -f ".env" ]; then
    echo "⚠️  .env 文件不存在，复制模板..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "✅ 已复制 .env.example -> .env"
        echo "⚠️  请务必编辑 .env 填入真实的 MEMBERSHIP_* 配置！"
    else
        echo "❌ .env.example 也不存在，请手动创建 .env"
        exit 1
    fi
fi

# 3. 创建数据目录
echo "📁 创建数据目录..."
mkdir -p data

# 4. 构建并启动
echo "🏗️  构建 Docker 镜像..."
if docker compose version &> /dev/null; then
    docker compose build
    echo "🚀 启动服务..."
    docker compose up -d
else
    docker-compose build
    echo "🚀 启动服务..."
    docker-compose up -d
fi

# 5. 等待启动
sleep 3

# 6. 健康检查
echo "🏥 健康检查..."
if curl -s http://127.0.0.1:5011/admin-api/health | grep -q "healthy"; then
    echo "✅ 服务启动成功！"
    echo ""
    echo "📋 服务信息："
    echo "   本地地址：http://127.0.0.1:5011"
    echo "   健康检查：http://127.0.0.1:5011/admin-api/health"
    echo "   API 文档：http://127.0.0.1:5011/admin-api/docs"
    echo ""
    echo "🔧 常用命令："
    echo "   查看日志：docker logs -f aicemind-admin"
    echo "   停止服务：docker compose down"
    echo "   重启服务：docker compose restart"
    echo "   更新部署：docker compose down && docker compose up -d --build"
else
    echo "⚠️  健康检查未通过，查看日志："
    docker logs aicemind-admin --tail 50
fi
