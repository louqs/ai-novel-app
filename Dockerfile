FROM python:3.12-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 数据目录
RUN mkdir -p /app/novel_output /app/data/chroma

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV NOVEL_ENV=production

# 端口
EXPOSE 8000

# 启动
CMD ["python", "-m", "uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
