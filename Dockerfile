# 多阶段构建：builder 装依赖，runtime 只带运行所需
FROM python:3.13-slim AS builder

WORKDIR /build
COPY requirements.txt .
# 装进独立前缀，方便整块拷进 runtime
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim AS runtime

# 不以 root 运行 —— 容器逃逸时少一层可利用面
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /install /usr/local

WORKDIR /app
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser supabase/migrations/ ./supabase/migrations/

USER appuser

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# API / relay / worker / migrate 共用这一个镜像，靠 command 区分（compose 和
# ECS task definition 里各自覆盖）。默认起 API。
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
