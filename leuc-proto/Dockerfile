# AI-GEN-BEGIN
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEUC_HOST=0.0.0.0 \
    LEUC_PORT=5055 \
    PYTHONNOUSERSITE=1

# 基础编译依赖（cryptography / pypinyin 偶发需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite / 上传数据目录；去掉 Windows CRLF，避免 exec 报 no such file
RUN mkdir -p /app/data \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 5055

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${LEUC_PORT}/" >/dev/null || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]
# AI-GEN-END
