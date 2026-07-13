"""PyInstaller runtime hook: ensure gevent is fully initialized before app code runs."""
import gevent.monkey
gevent.monkey.patch_all()

# Force import of gevent WSGI components so PyInstaller bundles them
# NOTE: gevent.wsgi / gevent.http were removed in gevent 1.4+; only gevent.pywsgi remains.
import gevent.pywsgi
import gevent.local
import gevent.hub
import gevent.server
