# DBCheck v2.5.3 - Full Edition Dockerfile
# Supports: MySQL, TiDB, PostgreSQL, IvorySQL, Oracle, SQL Server, DM8, YashanDB
#
# Build:
#   docker build -t jackge12345/dbcheck:v2.5.3 .
#   docker build -t jackge12345/dbcheck:latest .
#
# Run:
#   docker run -d -p 5003:5003 \
#     -v dbcheck_data:/app/data \
#     -v dbcheck_reports:/app/reports \
#     jackge12345/dbcheck:v2.5.3

# ─────────────────────────────────────────────────────────────────────────────
# Builder Stage: install Python deps & copy app code
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

# System build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
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
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir \
        pymysql>=1.0.0 \
        psycopg2-binary>=2.9.0 \
        oracledb>=1.4.0 \
        pyodbc>=4.0.0 \
        flask>=2.0.0 \
    && /opt/venv/bin/pip install --no-cache-dir dmpython>=1.0.0 \
        || echo "WARNING: dmpython installation failed (DM8 support disabled). Install manually if needed."
        flask-socketio>=5.0.0 \
        gevent>=24.0.0 \
        psutil>=5.9.0 \
        pyyaml>=6.0.0 \
        cryptography>=41.0.0 \
        paramiko>=2.10.0 \
        openpyxl>=3.0.0 \
        reportlab>=4.0.0 \
        apscheduler>=3.10.0 \
        PyPDF2>=3.0.1 \
        python-docx>=0.8.10 \
        docxtpl>=0.16.0 \
    && find /opt/venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/venv -name "*.pyc" -delete 2>/dev/null || true

# YashanDB wheel: downloaded at runtime via the "Database Client Settings" page.
# If pre-install is desired, uncomment and ensure .dockerignore does not exclude drivers/
# COPY drivers/yashandb/yasdb-1.2.0-py3-none-any.whl ./
# RUN /opt/venv/bin/pip install --no-cache-dir yasdb-1.2.0-py3-none-any.whl || true

# Copy application code
COPY . .

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
    && rm -rf /var/lib/apt/lists/*

# Copy Python venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code from builder
COPY --from=builder /build /app

WORKDIR /app

# Make Python venv active by default
ENV PATH="/opt/venv/bin:$PATH"
ENV TZ=Asia/Shanghai
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 5003

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:5003/api/v1/health || exit 1

CMD ["python", "web_ui.py"]
