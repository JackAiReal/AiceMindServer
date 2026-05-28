# 使用官方 Python 3.11 精简镜像
FROM python:3.11-slim

# 防止 python 生成 .pyc，日志直出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 可通过 build-arg 覆盖镜像源（默认清华）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 安装依赖（加重试和超时，国内网络更稳）
RUN python -m pip install --upgrade pip setuptools wheel \
      -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST} \
    && pip install --no-cache-dir --retries 5 --timeout 120 \
      -r requirements.txt \
      -i ${PIP_INDEX_URL} --trusted-host ${PIP_TRUSTED_HOST}

# 复制应用代码
COPY app/ ./app/
COPY main.py .

# 创建数据目录（SQLite 数据持久化挂载点）
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 5010

# 启动命令
CMD ["python", "main.py"]
