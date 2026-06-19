"""PyInstaller runtime hook: ensure gevent is fully initialized before app code runs."""
import gevent.monkey
gevent.monkey.patch_all()

# Force import of gevent WSGI components so PyInstaller bundles them
import gevent.pywsgi
import gevent.wsgi
import gevent.http
import gevent.local
import gevent.hub
import gevent.server
