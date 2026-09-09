#!/bin/bash
# docker-entrypoint.sh
# RaccoonX Docker 容器启动脚本

set -e

echo "==> RaccoonX v$(cat /app/VERSION.txt 2>/dev/null || echo 'unknown')"

# DM8 (达梦) native driver needs its shared libs resolvable via LD_LIBRARY_PATH.
# dmPython's SSL libs (libdmssl.so) live in site-packages/dmssl; the main dmPython
# package dir is also included so libdmoci.so resolves. Without this, DM8
# inspection / test-connection via dmPython fails with
# "libdmssl.so: cannot open shared object file" (or libdmoci.so).
export LD_LIBRARY_PATH="/opt/venv/lib/python3.12/site-packages/dmPython:/opt/venv/lib/python3.12/site-packages/dmssl:${LD_LIBRARY_PATH}"

# Check available memory (warn if < 2GB)
if [ -f /proc/meminfo ]; then
    AVAIL_MEM=$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null || echo "unknown")
    if [ "$AVAIL_MEM" != "unknown" ] && [ "$AVAIL_MEM" -lt 2097152 ]; then
        echo "==> WARNING: Available memory is less than 2GB (${AVAIL_MEM}KB)"
        echo "    Report generation may fail due to insufficient memory."
        echo "    Consider increasing Docker memory limit (--memory=2g)"
    fi
fi

# Ensure data/ and drivers/ directories exist and are writable
mkdir -p /app/data
chmod 755 /app/data
mkdir -p /app/drivers
chmod 755 /app/drivers
mkdir -p /app/data/pro_data
chmod 755 /app/data/pro_data

# Initialize database tables (create if not exist)
# This ensures inspection_template and other tables exist even on first run
echo "==> Initializing database tables..."
python -c "
import sys
sys.path.insert(0, '/app')
from modules.inspection.dal import init_database
init_database()
print('inspection.db tables ready.')
" 2>&1 || echo "WARNING: inspection.db init failed"

# Initialize default inspection templates (skip if already exist).
# NOTE: 巡检模板由 modules/inspection/init_db.py 提供；旧路径 /app/inspection/init_db.py
#       已在 root restructure 后废弃，改用模块式调用以兼容路径变动。
#       首次运行（data 卷为空）时，web_ui 启动时 dal.py 也会自动 init_default_templates(force=False) 播种新 SQL。
echo "==> Initializing default inspection templates..."
python -m modules.inspection.init_db 2>&1 || echo "WARNING: inspection templates init failed"

# Check drivers status
DRIVER_COUNT=$(find /app/drivers -type f 2>/dev/null | wc -l)
if [ "$DRIVER_COUNT" -eq 0 ]; then
    echo "==> WARNING: /app/drivers/ is empty."
    echo "    Oracle client libs and YashanDB wheel are not included."
    echo "    To enable these databases, place driver files in /app/drivers/"
    echo "    or use '-v /path/to/drivers:/app/drivers' when running the container."
else
    echo "==> Drivers found: $DRIVER_COUNT file(s) in /app/drivers/"
fi

# Initialize RBAC user management seed data
echo "==> Initializing RBAC user management..."
# NOTE: user_management 不是顶层包，而是 modules.user_management（命名空间包），
#       必须用 modules.user_management.seed，否则模块找不到、RBAC 静默不播种。
python -m modules.user_management.seed 2>&1 || echo "WARNING: RBAC seed init failed"

# Auto-enable bundled plugins: plugins/available -> plugins/enabled
# NOTE: uses the local loader API (discover_plugins / enable_plugin), NOT the
# online PluginMarket registry. The old code called PluginMarket.list_available()
# which does not exist -> "PluginMarket has no attribute 'list_available'".
echo "==> Enabling bundled plugins (available -> enabled)..."
timeout 120 python -c "
import sys
sys.path.insert(0, '/app')
try:
    from modules.pluginkit.loader import discover_plugins, enable_plugin
    plugins = discover_plugins()
    pending = sorted({p['name'] for p in plugins if not p.get('enabled')})
    if not pending:
        print('All bundled plugins already enabled.')
    for name in pending:
        try:
            res = enable_plugin(name)
            if isinstance(res, tuple):
                ok, msg = res
            else:
                ok, msg = bool(res), 'done'
            status = '✓' if ok else '✗'
            print(f'  {status} {name}: {msg}')
        except Exception as e:
            print(f'  ✗ {name} enable failed: {e}')
    print('Plugin auto-installation completed.')
except Exception as e:
    print(f'WARNING: Plugin auto-installation failed: {e}')
" 2>&1 || echo "WARNING: Plugin auto-installation timeout or failed"

echo ""
exec python /app/web_ui.py
