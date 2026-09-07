# RaccoonX (DBCheck) - Full Edition Dockerfile (with RBAC User Management)
# Supports: MySQL, TiDB, MariaDB, PostgreSQL, IvorySQL, Oracle, SQL Server,
#          DM8, HGDB, Kingbase, UXDB, YashanDB, MongoDB, Redis, ClickHouse,
#          DB2, TDSQL-C
#
# Build (single-arch, native host):
#   docker build -t jackge12345/dbcheck:latest .
#
# Build & push MULTI-ARCH (linux/amd64 + linux/arm64) — recommended for mixed
# x86 / ARM64 fleets (e.g. ARM64 信创服务器: 鲲鹏/Kunpeng, 飞腾). Requires Docker
# Buildx + QEMU:
#   ./scripts/build-multiarch.sh --push
#   (or CI: .github/workflows/docker-multiarch.yml on tag push)
#
# WHY numpy is pinned to <2 (see deploy/requirements-docker.txt): NumPy 2.x PyPI
# wheels require the AVX2 (x86-64-v2) instruction set. On hosts/VMs whose CPU does
# NOT expose AVX2 (common under virtualization / older x86_64 silicon),
# `import numpy` aborts with "NumPy was built with baseline optimizations:
# (X86_V2) but your machine doesn't support" — which crashes web_ui.py at import
# time (main_ivorysql -> openpyxl -> numpy). Pinning numpy<2 (1.26.x) uses the
# baseline x86_64 wheel (SSE2 only) and imports everywhere. This is independent of
# the multi-arch story below (ARM64 信创 fleets still want a native arm64 image).
# NOTE: Oracle client libs in drivers/ are x86_64; Oracle support is
#       unavailable on arm64 (non-fatal — the image still builds & serves 5003).
#
# Run:
#   docker run -d -p 5003:5003 \
#     --memory=2g --memory-swap=2g \
#     -v dbcheck_data:/app/data \
#     -v dbcheck_pro_data:/app/data/pro_data \
#     -v dbcheck_reports:/app/data/reports \
#     jackge12345/dbcheck:latest

# ─────────────────────────────────────────────────────────────────────────────
# Builder Stage: install Python deps & copy app code
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

# System build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    default-jdk \
    unixodbc-dev \
    curl \
    wget \
    gnupg \
    gpg \
    libaio1 \
    libaio-dev \
    unzip \
    tzdata \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Python dependencies (venv)
# pyodbc is installed via pip; the actual ODBC driver (msodbcsql18)
# must be installed at runtime if SQL Server support is needed.
# See docs/enable-sqlserver.md for instructions.
COPY deploy/requirements-docker.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements-docker.txt \
    && find /opt/venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/venv -name "*.pyc" -delete 2>/dev/null || true

# dmpython: optional FALLBACK for DM8. DM8 test connection now prefers the
# pure-Java JDBC driver (drivers/dm8/DmJdbcDriver*.jar, no native client needed);
# dmpython is only used when that jar is absent AND a DM8 client is installed.
# Non-fatal: if it fails to build (no DM8 client headers), DM8 just uses JDBC.
RUN /opt/venv/bin/pip install --no-cache-dir dmpython>=1.0.0 \
    || echo "WARNING: dmpython installation failed (DM8 will use JDBC driver if present)."

# Copy application code（drivers/ 全部子目录及文件随构建上下文打包进镜像，
# 无需再从 GitHub Releases 拉取 drivers.zip——构建期不再要求联网下载驱动包）
COPY . .

# Docker 镜像仅运行于 Linux：剔除 Oracle instant client 的 windows_x64 平台文件
# （drivers/ 随本地 COPY 带入，.dockerignore 已排除该子目录，此处再清一次双保险）
RUN rm -rf /build/drivers/oracle_client/windows_x64

# Install YashanDB wheel（drivers/yashandb/ 已随 COPY 进入镜像）
RUN if [ -f /build/drivers/yashandb/yasdb-1.2.0-py3-none-any.whl ]; then \
        /opt/venv/bin/pip install --no-cache-dir /build/drivers/yashandb/yasdb-1.2.0-py3-none-any.whl; \
    else \
        echo "WARNING: yasdb wheel not found, YashanDB support disabled"; \
    fi

# Pre-compile .pyc for faster startup
RUN /opt/venv/bin/python -m compileall /build 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────
# Final Stage (clean runtime image)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm

# Runtime system dependencies (no MS ODBC driver — install manually if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    curl \
    gnupg \
    libaio1 \
    tzdata \
    ca-certificates \
    unzip \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Copy Python venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code from builder
COPY --from=builder /build /app

# Copy entrypoint script and make it executable
COPY deploy/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app

# Make Python venv active by default
ENV PATH="/opt/venv/bin:$PATH"
ENV TZ=Asia/Shanghai
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONMEMORYLIMIT=2g

# Create VERSION.txt (without v prefix, matches Docker tag format)
# DBCHECK_VERSION 可在构建时覆盖: --build-arg DBCHECK_VERSION=v26.9.8.0
# 注意：CI / build-multiarch.sh 传入的值带 v 前缀（如 v26.9.8.0），
# 这里用 ${DBCHECK_VERSION#v} 去掉前缀，保证镜像内 VERSION.txt 与标签一致。
ARG DBCHECK_VERSION=26.9.8.0
RUN echo "${DBCHECK_VERSION#v}" > /app/VERSION.txt

# Ensure data and pro_data directories exist for volume mounts
RUN mkdir -p /app/data /app/data/pro_data /app/data/reports

EXPOSE 5003

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:5003/api/v1/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
