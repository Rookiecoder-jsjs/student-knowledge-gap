# sc 后端镜像：python:3.11-slim，单进程 uvicorn。
# 单进程是架构不变量（见仓库根 DEPLOY.md「单进程原理」）：批量录卷用进程内线程池、
# 启动期全局 reconcile_stale()、熔断器进程内单例、SQLite 单写者调优——多 worker 会破坏。
# 并发靠线程（SC_BATCH_WORKERS），不靠多进程。崩溃自愈交给宿主 restart 策略。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 镜像默认落 /data（卷）；compose 的 environment 可覆盖
    SC_DATABASE_URL=sqlite:////data/sc.db \
    # 批量上传临时文件挂卷：重启后可重试 failed item（gc_orphan_tempfiles 同扫此目录）
    TMPDIR=/data/tmp

WORKDIR /app

# 非 root 运行
RUN groupadd --system app && useradd --system --gid app --uid 1001 app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 运行时源码逐项 COPY（绝不 COPY .，.env / 测试 / 真实库不入镜像，.dockerignore 双保险）
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts
COPY kb ./kb   # 知识库导入源（yaml_path 相对 WORKDIR 解析，导入后 DB 即真源）

RUN mkdir -p /data/tmp /backups && chown -R app:app /data /backups

USER app

EXPOSE 8000

# liveness：进程 + HTTP 存活（stdlib urllib，免装 curl；依赖可用性见 /ready）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# 单进程不变量钉死在容器边界：--workers 1 明确写死，且不装 gunicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
