"""
DBCheck 插件市场后端 —— GitHub 驱动

registry.json 托管在 GitHub 仓库 fiyo/dbcheck-plugins，
用户通过 Web UI 浏览、搜索、一键安装。

用法：
    from plugin_market import PluginMarket
    market = PluginMarket()
    plugins = market.fetch_registry()       # 拉取插件列表
    ok = market.install("oracle-asm-health")  # 安装插件
"""

import os, json, logging, tempfile, shutil, zipfile, time
from typing import List, Dict, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger('plugin_market')

# ── 默认市场配置 ──────────────────────────────────────────────

DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/fiyo/dbcheck-plugins/main/registry.json"
)
CACHE_TTL = 300  # 缓存 5 分钟


class PluginMarket:
    """插件市场"""

    def __init__(self, registry_url: str = None, cache_ttl: int = CACHE_TTL):
        self.registry_url = registry_url or DEFAULT_REGISTRY_URL
        self.cache_ttl = cache_ttl
        self._cache = None
        self._cache_time = 0
        self._plugins_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'plugins'
        )

    # ── 拉取市场列表 ──────────────────────────────────────────

    def fetch_registry(self, force: bool = False) -> Dict:
        """拉取插件市场 registry，带缓存"""
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self.cache_ttl:
            return self._cache
        try:
            req = Request(self.registry_url, headers={'User-Agent': 'DBCheck-PluginMarket/1.0'})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            self._cache = data
            self._cache_time = now
            logger.info(f"市场数据拉取成功: {len(data.get('plugins', []))} 个插件")
            return data
        except Exception as e:
            logger.warning(f"拉取市场数据失败: {e}")
            if self._cache:
                return self._cache  # 使用过期缓存
            return {'version': '1', 'plugins': [], 'error': str(e)}

    def list_plugins(self, category: str = None, keyword: str = None) -> List[Dict]:
        """列出市场插件，支持筛选"""
        data = self.fetch_registry()
        plugins = data.get('plugins', [])
        if category:
            plugins = [p for p in plugins if p.get('category') == category]
        if keyword:
            kw = keyword.lower()
            plugins = [p for p in plugins if
                       kw in p.get('name', '').lower() or
                       kw in p.get('description', '').lower() or
                       kw in ' '.join(p.get('keywords', [])).lower()]
        return plugins

    def get_plugin(self, plugin_id: str) -> Optional[Dict]:
        """获取单个插件详情"""
        for p in self.list_plugins():
            if p.get('id') == plugin_id:
                return p
        return None

    def search(self, keyword: str) -> List[Dict]:
        """搜索插件（alias）"""
        return self.list_plugins(keyword=keyword)

    # ── 安装 ──────────────────────────────────────────────────

    def install(self, plugin_id: str) -> Dict:
        """
        安装插件：下载 zip → 解压到 plugins/ → 动态加载
        返回 {'ok': bool, 'message': str}
        """
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return {'ok': False, 'message': f'插件 {plugin_id} 不在市场中'}

        download_url = plugin.get('download', '')
        if not download_url:
            return {'ok': False, 'message': f'插件 {plugin_id} 没有下载地址'}

        # 检查是否已安装
        target_dir = os.path.join(self._plugins_dir, plugin_id)
        if os.path.isdir(target_dir):
            return {'ok': False, 'message': f'插件 {plugin_id} 已安装，请先卸载'}

        # 下载 zip
        tmp_zip = None
        try:
            req = Request(download_url, headers={'User-Agent': 'DBCheck-PluginMarket/1.0'})
            with urlopen(req, timeout=120) as resp:
                tmp_zip = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
                tmp_zip.write(resp.read())
                tmp_zip.close()
        except URLError as e:
            return {'ok': False, 'message': f'下载失败: {e.reason}'}
        except Exception as e:
            return {'ok': False, 'message': f'下载失败: {e}'}

        # 解压
        try:
            extract_dir = tempfile.mkdtemp(prefix='dbcheck_plugin_')
            with zipfile.ZipFile(tmp_zip.name, 'r') as zf:
                zf.extractall(extract_dir)

            # 检测解压后的结构：如果只有一个子文件夹，直接用它
            entries = os.listdir(extract_dir)
            if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                src = os.path.join(extract_dir, entries[0])
            else:
                src = extract_dir

            # 验证 plugin.json 存在
            if not os.path.isfile(os.path.join(src, 'plugin.json')):
                return {'ok': False, 'message': '插件包缺少 plugin.json'}

            # 移到 plugins/ 目录
            os.makedirs(target_dir, exist_ok=True)
            for item in os.listdir(src):
                s = os.path.join(src, item)
                d = os.path.join(target_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

            # 动态加载
            from plugin_core import load_plugin
            manifest = load_plugin(target_dir)
            if manifest:
                logger.info(f"插件安装成功: {plugin_id}")
                return {'ok': True, 'message': f'插件 {plugin.get("name", plugin_id)} 安装成功'}
            else:
                # 回滚
                shutil.rmtree(target_dir, ignore_errors=True)
                return {'ok': False, 'message': '插件加载失败，已回滚'}
        except Exception as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return {'ok': False, 'message': f'安装失败: {e}'}
        finally:
            if tmp_zip and os.path.exists(tmp_zip.name):
                os.unlink(tmp_zip.name)

    def uninstall(self, plugin_id: str) -> Dict:
        """卸载插件：删除目录 + 注销"""
        from plugin_core import PluginRegistry
        PluginRegistry.unregister(plugin_id)
        target_dir = os.path.join(self._plugins_dir, plugin_id)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        return {'ok': True, 'message': f'插件 {plugin_id} 已卸载'}

    def reload(self) -> Dict:
        """重新加载所有插件"""
        from plugin_core import PluginRegistry, load_plugins
        PluginRegistry.clear()
        n = load_plugins(self._plugins_dir)
        return {'ok': True, 'message': f'已重新加载 {n} 个插件'}

    def categories(self) -> List[Dict]:
        """返回分类列表（含数量）"""
        data = self.fetch_registry()
        plugins = data.get('plugins', [])
        cat_count = {}
        for p in plugins:
            cat = p.get('category', 'other')
            cat_count[cat] = cat_count.get(cat, 0) + 1
        return [
            {'id': c, 'name': c, 'count': cat_count[c]}
            for c in sorted(cat_count.keys())
        ]

    def get_installed_ids(self) -> set:
        """返回已安装插件 ID 集合"""
        from plugin_core import PluginRegistry
        return set(PluginRegistry._all_plugins.keys())


# ── 单例 ──────────────────────────────────────────────────────

_market_instance = None

def get_market() -> PluginMarket:
    global _market_instance
    if _market_instance is None:
        _market_instance = PluginMarket()
    return _market_instance
