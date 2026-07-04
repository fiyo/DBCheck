#!/bin/bash
# docker-entrypoint.sh
# DBCheck Docker 容器启动脚本

set -e

echo "==> DBCheck v$(cat /app/VERSION.txt 2>/dev/null || echo 'unknown')"

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
mkdir -p /app/pro_data
chmod 755 /app/pro_data

# Initialize database tables (create if not exist)
# This ensures inspection_template and other tables exist even on first run
echo "==> Initializing database tables..."
python -c "
from inspection_dal import init_database
init_database()
print('inspection.db tables ready.')
" 2>&1 || echo "WARNING: inspection.db init failed"

# Initialize default inspection templates (skip if already exist)
echo "==> Initializing default inspection templates..."
python /app/inspection_init_db.py 2>&1 || echo "WARNING: inspection_init_db.py failed"

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
python -m user_management.seed 2>&1 || echo "WARNING: RBAC seed init failed"

# Auto-install plugins from plugins/available/
echo "==> Auto-installing plugins..."
timeout 30 python -c "
import sys
import os

# Add /app to Python path
sys.path.insert(0, '/app')

try:
    from plugin_market import PluginMarket
    
    pm = PluginMarket()
    available_plugins = pm.list_available()
    
    print(f'Found {len(available_plugins)} plugin(s) available:')
    for plugin in available_plugins:
        plugin_id = plugin['id']
        if pm.is_installed(plugin_id):
            print(f'  ✓ {plugin_id} (already installed)')
        else:
            print(f'  → Installing {plugin_id}...')
            try:
                pm.install(plugin_id)
                print(f'  ✓ {plugin_id} installed successfully')
            except Exception as e:
                print(f'  ✗ {plugin_id} installation failed: {e}')
    
    print('Plugin auto-installation completed.')
except ImportError as e:
    print(f'WARNING: Plugin system not available: {e}')
except Exception as e:
    print(f'WARNING: Plugin auto-installation failed: {e}')
" 2>&1 || echo "WARNING: Plugin auto-installation timeout or failed"

echo ""
exec python /app/web_ui.py
