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
COPY requirements-docker.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements-docker.txt \
    && find /opt/venv -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/venv -name "*.pyc" -delete 2>/dev/null || true

# dmpython: install separately (requires DM8 client libs; non-fatal)
RUN /opt/venv/bin/pip install --no-cache-dir dmpython>=1.0.0 \
    || echo "WARNING: dmpython installation failed (DM8 support disabled)."

# Download and extract drivers.zip from GitHub Releases (non-fatal)
# Provides: Oracle client libs, YashanDB wheel, etc.
# If download fails, related DB types will be disabled at runtime.
RUN curl -fSL "https://github.com/fiyo/DBCheck/releases/download/drivers/drivers.zip" \
    -o /tmp/drivers.zip \
    && unzip -o /tmp/drivers.zip -d /build/drivers/ \
    && rm -f /tmp/drivers.zip \
    || echo "WARNING: drivers.zip download failed (Oracle/YashanDB drivers not included)."

# Install YashanDB wheel (if downloaded successfully)
RUN if [ -f /build/drivers/yashandb/yasdb-1.2.0-py3-none-any.whl ]; then \
        /opt/venv/bin/pip install --no-cache-dir /build/drivers/yashandb/yasdb-1.2.0-py3-none-any.whl; \
    else \
        echo "WARNING: yasdb wheel not found, YashanDB support disabled"; \
    fi

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
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy Python venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code from builder
COPY --from=builder /build /app

# Copy entrypoint script and make it executable
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app

# Make Python venv active by default
ENV PATH="/opt/venv/bin:$PATH"
ENV TZ=Asia/Shanghai
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Create VERSION.txt (without v prefix, matches Docker tag format)
RUN echo "2.5.4" > /app/VERSION.txt

EXPOSE 5003

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:5003/api/v1/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
