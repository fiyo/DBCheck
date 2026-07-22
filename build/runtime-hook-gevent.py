"""PyInstaller runtime hook: ensure gevent is fully initialized before app code runs."""
import gevent.monkey
gevent.monkey.patch_all()

# gevent 1.4+ 已移除 gevent.wsgi / gevent.http 子模块；仅保留仍在用的组件，
# 避免将来该 hook 被接入 runtime_hooks 后，冻结程序启动即 ModuleNotFoundError
import gevent.pywsgi
import gevent.local
import gevent.hub
import gevent.server
