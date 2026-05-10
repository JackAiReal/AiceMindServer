# 使用官方 Python 3.11 精简镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/
COPY main.py .

# 创建数据目录（SQLite 数据持久化挂载点）
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 5010

# 启动命令
CMD ["python", "main.py"]
