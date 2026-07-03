"""
DBCheck 插件市场后端 —— 多镜像 fallback + 内置兜底

registry.json 支持多镜像地址（GitHub 主地址 + AtomGit 国内镜像），
用户通过 Web UI 浏览、搜索、一键安装。
安装插件时，下载地址也支持多镜像 fallback。

用法：
    from plugin_market import PluginMarket
    market = PluginMarket()
    plugins = market.fetch_registry()       # 拉取插件列表（自动 fallback）
    ok = market.install("oracle-asm-health")  # 安装插件
"""

import os, json, logging, tempfile, shutil, zipfile, time
from typing import List, Dict, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger('plugin_market')

# ── 默认市场配置 ──────────────────────────────────────────────

# 多镜像地址，按优先级排列，fetch_registry 会自动 fallback
# 注意：AtomGit (atomgit.com) 需要登录才能访问 raw 文件，暂不支持作为公开镜像
# 用户可通过 Web UI 添加其他可访问的镜像地址
DEFAULT_REGISTRY_URLS = [
    # GitHub 主地址（国外/科学上网用户）
    "https://raw.githubusercontent.com/fiyo/dbcheck-plugins/main/registry.json",
    # 可扩展：国内可访问的镜像（如 Gitee、GitCode 等，需要仓库设置为公开）
]

# 内置 registry.json 的文件名（放在 DBCheck 项目根目录）
BUILTIN_REGISTRY_FILE = "builtin_registry.json"

CACHE_TTL = 300  # 缓存 5 分钟


class PluginMarket:
    """插件市场（支持多镜像 fallback + 内置兜底）"""

    def __init__(self, registry_urls: list = None, cache_ttl: int = CACHE_TTL):
        """
        registry_urls: 镜像地址列表，按优先级排列。
                      传入字符串则当作单地址；传入列表则依次尝试。
                      为 None 时使用 DEFAULT_REGISTRY_URLS。
                      也支持从 DBCheck 配置文件读取用户自定义地址。
        """
        if registry_urls is None:
            # 尝试从配置文件读取用户自定义地址
            registry_urls = self._load_custom_urls()
        if not registry_urls:
            registry_urls = DEFAULT_REGISTRY_URLS
        elif isinstance(registry_urls, str):
            registry_urls = [registry_urls]
        self.registry_urls = registry_urls
        self.cache_ttl = cache_ttl
        self._cache = None
        self._cache_time = 0
        self._plugins_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'plugins'
        )

    def _load_custom_urls(self):
        """从 dbc_config.json 读取用户自定义的 registry URLs"""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dbc_config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                urls = cfg.get('plugin_market', {}).get('registry_urls')
                if urls and isinstance(urls, list):
                    return urls
        except Exception:
            pass
        return None

    def _save_custom_urls(self, urls: list):
        """保存用户自定义的 registry URLs 到 dbc_config.json"""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dbc_config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            if 'plugin_market' not in cfg:
                cfg['plugin_market'] = {}
            cfg['plugin_market']['registry_urls'] = urls
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.warning(f"保存自定义 registry URLs 失败: {e}")
            return False

    # ── 拉取市场列表 ──────────────────────────────────────────

    def _load_builtin_registry(self) -> Optional[Dict]:
        """加载内置 registry.json（所有镜像都失败时的兜底）"""
        try:
            builtin_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), BUILTIN_REGISTRY_FILE
            )
            if os.path.isfile(builtin_path):
                with open(builtin_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f"使用内置 registry.json: {len(data.get('plugins', []))} 个插件")
                return data
        except Exception as e:
            logger.warning(f"加载内置 registry.json 失败: {e}")
        return None

    def fetch_registry(self, force: bool = False) -> Dict:
        """
        拉取插件市场 registry，带缓存 + 多镜像 fallback + 内置兜底。
        依次尝试 self.registry_urls 中的每个地址，任一个成功即返回。
        所有镜像都失败时，返回内置 builtin_registry.json（如果存在）。
        
        增强：如果网络数据不完整（插件数量少于内置数据），则合并内置数据。
        """
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self.cache_ttl:
            return self._cache

        last_error = None
        network_data = None
        for url in self.registry_urls:
            try:
                req = Request(url, headers={'User-Agent': 'DBCheck-PluginMarket/1.0'})
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                network_data = data
                logger.info(f"市场数据拉取成功（{url}）: {len(data.get('plugins', []))} 个插件")
                break  # 成功拉取，跳出循环
            except Exception as e:
                logger.warning(f"拉取市场数据失败（{url}）: {e}")
                last_error = e
                continue  # 尝试下一个镜像

        # 如果网络拉取成功，则合并内置数据
        # 策略：内置数据作为补充；对于两边都有的插件，优先使用内置的 download 等字段
        # （因为内置 registry 是本地维护的，更准确）
        if network_data:
            builtin = self._load_builtin_registry()
            if builtin:
                network_plugins = network_data.get('plugins', [])
                builtin_plugins = builtin.get('plugins', [])

                # 构建内置插件的索引（按 id）
                builtin_by_id = {bp.get('id'): bp for bp in builtin_plugins}

                # 对于网络数据中已存在的插件，用内置数据覆盖关键字段（如下载地址）
                for np in network_plugins:
                    nid = np.get('id', '')
                    if nid in builtin_by_id:
                        bp = builtin_by_id[nid]
                        # 覆盖 download 字段（内置的更准确）
                        if bp.get('download'):
                            np['download'] = bp['download']
                        # 覆盖其他关键字段
                        for key in ('description', 'author', 'min_version', 'official', 'featured'):
                            if bp.get(key) is not None:
                                np[key] = bp[key]

                # 合并插件（内置数据作为补充，避免重复）
                existing_ids = {p.get('id') for p in network_plugins}
                for bp in builtin_plugins:
                    if bp.get('id') not in existing_ids:
                        network_plugins.append(bp)
                        existing_ids.add(bp.get('id'))

                # 合并分类定义
                if 'categories' not in network_data and 'categories' in builtin:
                    network_data['categories'] = builtin['categories']

                logger.info(f"合并内置数据后: {len(network_plugins)} 个插件")

            self._cache = network_data
            self._cache_time = now
            return network_data

        # 所有镜像都失败了，尝试内置兜底
        logger.warning("所有镜像均拉取失败，尝试使用内置 registry.json")
        builtin = self._load_builtin_registry()
        if builtin:
            self._cache = builtin
            self._cache_time = now
            return builtin

        # 内置也没有，返回空列表
        logger.error(f"所有镜像均拉取失败，且无内置兜底，最后错误: {last_error}")
        if self._cache:
            logger.warning("使用过期缓存")
            return self._cache
        return {'version': '1', 'plugins': [], 'error': str(last_error), 'offline': True}

    def set_registry_urls(self, urls: list) -> bool:
        """设置自定义 registry URLs（用户配置）"""
        self.registry_urls = urls
        self._cache = None  # 清除缓存，强制下次重新拉取
        return self._save_custom_urls(urls)

    def get_registry_urls(self) -> list:
        """返回当前使用的 registry URLs"""
        return self.registry_urls

    def save_builtin_registry(self) -> Dict:
        """
        将当前 registry_urls 中第一个成功拉取的内容保存为内置兜底文件。
        保存路径：DBCheck 项目根目录 / builtin_registry.json
        返回 {'ok': bool, 'message': str}
        """
        data = self.fetch_registry(force=True)
        if not data or not data.get('plugins'):
            return {'ok': False, 'message': '无法拉取市场数据，内置 registry 未更新'}
        try:
            builtin_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), BUILTIN_REGISTRY_FILE
            )
            with open(builtin_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {
                'ok': True,
                'message': f'内置 registry 已保存（{len(data.get("plugins", []))} 个插件）',
                'path': builtin_path
            }
        except Exception as e:
            return {'ok': False, 'message': f'保存内置 registry 失败: {e}'}

    def list_plugins(self, category: str = None, keyword: str = None) -> List[Dict]:
        """列出市场插件，支持筛选"""
        data = self.fetch_registry()
        plugins = data.get('plugins', [])
        
        # 为每个插件设置 source 字段（根据 download 字段判断）
        for p in plugins:
            dl = p.get('download', '')
            if dl and (dl.startswith('http://') or dl.startswith('https://')):
                p['source'] = 'online'
            else:
                p['source'] = 'local'
        
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

    def _get_mirror_download_url(self, original_url: str) -> List[str]:
        """
        根据 registry_urls 生成插件下载地址的镜像列表。
        将 GitHub releases/download 地址替换为对应的镜像地址。
        """
        urls = [original_url]
        for registry_url in self.registry_urls:
            # GitHub: https://raw.githubusercontent.com/fiyo/dbcheck-plugins/main/registry.json
            #   → 下载地址: https://github.com/fiyo/dbcheck-plugins/releases/download/...
            # AtomGit: https://atomgit.com/api/v5/repos/wfgyj/dbcheck-plugins/raw/registry.json?ref=main
            #   → 下载地址: https://atomgit.com/wfgyj/dbcheck-plugins/releases/download/...
            if 'atomgit.com' in registry_url and 'github.com' in original_url:
                # AtomGit 镜像：把 github.com 替换成 atomgit.com
                mirror_download = original_url.replace(
                    'https://github.com/', 'https://atomgit.com/'
                )
                if mirror_download != original_url:
                    urls.append(mirror_download)
            # 如果原始 URL 就是 atomgit.com 的，直接尝试下载（不需要替换）
        return urls

    def install(self, plugin_id: str) -> Dict:
        """
        安装插件：
        1. 如果 enabled/<plugin_id>/ 存在 → 已安装，提示先卸载
        2. 如果 available/<plugin_id>/ 存在但 enabled/ 不存在 → 直接启用（从 available/ 复制到 enabled/）
        3. 否则 → 从市场下载安装
        返回 {'ok': bool, 'message': str}
        """
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return {'ok': False, 'message': f'插件 {plugin_id} 不在市场中'}

        download_url = plugin.get('download', '')
        if not download_url:
            return {'ok': False, 'message': f'插件 {plugin_id} 没有下载地址'}

        available_dir = os.path.join(self._plugins_dir, 'available', plugin_id)
        enabled_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)

        # 1. 检查是否已启用（enabled/ 存在 = 真正已安装）
        if os.path.isdir(enabled_dir):
            return {'ok': False, 'message': f'插件 {plugin_id} 已安装，请先卸载'}

        # 2. 如果 available/ 有但 enabled/ 没有 → 直接启用（从 available/ 复制到 enabled/）
        if os.path.isdir(available_dir) and not os.path.isdir(enabled_dir):
            try:
                os.makedirs(enabled_dir, exist_ok=True)
                for item in os.listdir(available_dir):
                    s = os.path.join(available_dir, item)
                    d = os.path.join(enabled_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
                # 动态加载
                from plugin_core import load_plugin, PluginRegistry
                manifest = load_plugin(enabled_dir)
                if manifest:
                    logger.info(f"插件已启用（从 available/ 复制）: {plugin_id}")
                    
                    # 调用插件的 on_install() 方法（初始化数据）
                    try:
                        plugin = PluginRegistry.get_plugin_instance(plugin_id)
                        if plugin and hasattr(plugin, 'on_install'):
                            # 获取数据库文件路径
                            from inspection_dal import DEFAULT_DB_PATH
                            plugin.on_install(db_path=DEFAULT_DB_PATH)
                            logger.info(f"插件 {plugin_id} 的 on_install() 已调用")
                    except Exception as e:
                        logger.warning(f"调用插件 {plugin_id} 的 on_install() 失败: {e}")
                    
                    return {'ok': True, 'message': f'插件 {plugin_id} 安装成功'}
                else:
                    shutil.rmtree(enabled_dir, ignore_errors=True)
                    return {'ok': False, 'message': '插件加载失败，已回滚'}
            except Exception as e:
                shutil.rmtree(enabled_dir, ignore_errors=True)
                return {'ok': False, 'message': f'启用插件失败: {e}'}

        # 安装目标目录：plugins/available/<plugin_id>
        target_dir = available_dir

        # 支持本地路径（用于测试）
        if download_url.startswith('file://') or os.path.isfile(download_url):
            if download_url.startswith('file://'):
                download_url = download_url[7:]  # 去掉 file://
            return self._install_from_local(plugin_id, download_url, target_dir)

        # 生成镜像下载地址列表
        download_urls = self._get_mirror_download_url(download_url)

        # 依次尝试下载
        tmp_zip_path = None
        last_error = None
        for url in download_urls:
            try:
                req = Request(url, headers={'User-Agent': 'DBCheck-PluginMarket/1.0'})
                with urlopen(req, timeout=120) as resp:
                    tmp_f = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
                    tmp_f.write(resp.read())
                    tmp_f.close()
                    tmp_zip_path = tmp_f.name
                logger.info(f"插件 {plugin_id} 下载成功: {url}")
                break  # 下载成功，跳出循环
            except Exception as e:
                logger.warning(f"插件 {plugin_id} 下载失败（{url}）: {e}")
                last_error = e
                continue

        if not tmp_zip_path:
            # 下载全部失败：尝试本地回退安装
            # 场景：插件在 builtin_registry.json 中注册，download 为 https:// 但实际 zip 包还未上传
            #       此时如果本地 plugins/ 目录已有该插件的源码目录，可以直接复制安装
            local_fallback = self._try_local_fallback(plugin_id, target_dir)
            if local_fallback['ok']:
                return local_fallback
            return {'ok': False, 'message': f'所有镜像均下载失败: {last_error}'}

        # 解压并安装
        try:
            extract_dir = tempfile.mkdtemp(prefix='dbcheck_plugin_')
            with zipfile.ZipFile(tmp_zip_path, 'r') as zf:
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

            # 动态加载（先加载到 available/）
            from plugin_core import load_plugin, PluginRegistry
            manifest = load_plugin(target_dir)
            if manifest:
                logger.info(f"插件安装成功（available/）: {plugin_id}")
                # 自动启用：复制到 enabled/
                try:
                    os.makedirs(enabled_dir, exist_ok=True)
                    for item in os.listdir(target_dir):
                        s = os.path.join(target_dir, item)
                        d = os.path.join(enabled_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)
                    # 加载 enabled/ 中的插件
                    manifest2 = load_plugin(enabled_dir)
                    if manifest2:
                        logger.info(f"插件已启用: {plugin_id}")
                        
                        # 调用插件的 on_install() 方法（初始化数据）
                        try:
                            plugin = PluginRegistry.get_plugin_instance(plugin_id)
                            if plugin and hasattr(plugin, 'on_install'):
                                # 获取数据库文件路径
                                from inspection_dal import DEFAULT_DB_PATH
                                plugin.on_install(db_path=DEFAULT_DB_PATH)
                                logger.info(f"插件 {plugin_id} 的 on_install() 已调用")
                        except Exception as e:
                            logger.warning(f"调用插件 {plugin_id} 的 on_install() 失败: {e}")
                        
                        return {'ok': True, 'message': f'插件 {plugin.get("name", plugin_id)} 安装成功'}
                    else:
                        shutil.rmtree(enabled_dir, ignore_errors=True)
                        return {'ok': True, 'message': f'插件 {plugin.get("name", plugin_id)} 安装成功（已安装但未启用）'}
                except Exception as e:
                    logger.warning(f"插件启用失败: {e}")
                    return {'ok': True, 'message': f'插件 {plugin.get("name", plugin_id)} 安装成功（启用失败：{e}）'}
            else:
                # 回滚
                shutil.rmtree(target_dir, ignore_errors=True)
                return {'ok': False, 'message': '插件加载失败，已回滚'}
        except Exception as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return {'ok': False, 'message': f'安装失败: {e}'}
        finally:
            if tmp_zip_path and os.path.exists(tmp_zip_path):
                os.unlink(tmp_zip_path)



    def _install_from_local(self, plugin_id: str, source_path: str, target_dir: str) -> Dict:
        """
        从本地安装插件（支持目录或 zip 包）
        source_path: 本地路径（目录或 zip 文件）
        target_dir: 安装目标目录
        """
        try:
            # 判断是目录还是 zip 文件
            if os.path.isdir(source_path):
                # 直接复制目录
                if not os.path.isfile(os.path.join(source_path, 'plugin.json')):
                    return {'ok': False, 'message': '插件目录缺少 plugin.json'}
                
                os.makedirs(target_dir, exist_ok=True)
                for item in os.listdir(source_path):
                    s = os.path.join(source_path, item)
                    d = os.path.join(target_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            elif os.path.isfile(source_path) and source_path.endswith('.zip'):
                # 解压 zip 文件
                import tempfile
                extract_dir = tempfile.mkdtemp(prefix='dbcheck_plugin_')
                with zipfile.ZipFile(source_path, 'r') as zf:
                    zf.extractall(extract_dir)
                
                # 检测解压后的结构
                entries = os.listdir(extract_dir)
                if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
                    src = os.path.join(extract_dir, entries[0])
                else:
                    src = extract_dir
                
                # 验证 plugin.json 存在
                if not os.path.isfile(os.path.join(src, 'plugin.json')):
                    return {'ok': False, 'message': '插件包缺少 plugin.json'}
                
                # 复制到目标目录
                os.makedirs(target_dir, exist_ok=True)
                for item in os.listdir(src):
                    s = os.path.join(src, item)
                    d = os.path.join(target_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            else:
                return {'ok': False, 'message': f'无效的本地路径: {source_path}'}
            
            # 动态加载（先加载到 available/）
            from plugin_core import load_plugin
            manifest = load_plugin(target_dir)
            if manifest:
                logger.info(f"插件安装成功（available/）: {plugin_id}")
                # 自动启用：复制到 enabled/
                enabled_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)
                try:
                    os.makedirs(enabled_dir, exist_ok=True)
                    for item in os.listdir(target_dir):
                        s = os.path.join(target_dir, item)
                        d = os.path.join(enabled_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)
                    manifest2 = load_plugin(enabled_dir)
                    if manifest2:
                        logger.info(f"插件已启用: {plugin_id}")
                        
                        # 调用插件的 on_install() 方法（初始化数据）
                        try:
                            from plugin_core import PluginRegistry
                            plugin = PluginRegistry.get_plugin_instance(plugin_id)
                            if plugin and hasattr(plugin, 'on_install'):
                                # 获取数据库文件路径
                                from inspection_dal import DEFAULT_DB_PATH
                                plugin.on_install(db_path=DEFAULT_DB_PATH)
                                logger.info(f"插件 {plugin_id} 的 on_install() 已调用")
                        except Exception as e:
                            logger.warning(f"调用插件 {plugin_id} 的 on_install() 失败: {e}")
                        
                        return {'ok': True, 'message': f'插件 {plugin_id} 安装成功'}
                    else:
                        shutil.rmtree(enabled_dir, ignore_errors=True)
                        return {'ok': True, 'message': f'插件 {plugin_id} 安装成功（已安装但未启用）'}
                except Exception as e:
                    logger.warning(f"插件启用失败: {e}")
                    return {'ok': True, 'message': f'插件 {plugin_id} 安装成功（启用失败：{e}）'}
            else:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {'ok': False, 'message': '插件加载失败，已回滚'}
        except Exception as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return {'ok': False, 'message': f'本地安装失败: {e}'}

    def uninstall(self, plugin_id: str) -> Dict:
        """
        卸载插件（禁用插件）：
        - 读取 plugin.json 中的 cleanup 配置（清理数据）
        - 调用插件的 on_uninstall() 方法（如果存在且可加载）
        - 注销内存中的注册
        - 删除 plugins/enabled/<plugin_id>/ 目录（禁用插件）
        - 不删除 plugins/available/<plugin_id>/ 目录（保留原始文件）
        """
        from plugin_core import PluginRegistry
        
        # 1. 清理数据（优先使用 plugin.json 中的 cleanup 配置）
        try:
            import json
            from inspection_dal import DEFAULT_DB_PATH
            
            # 读取 plugin.json
            available_dir = os.path.join(self._plugins_dir, 'available', plugin_id)
            plugin_json_path = os.path.join(available_dir, 'plugin.json')
            
            if os.path.isfile(plugin_json_path):
                with open(plugin_json_path, 'r', encoding='utf-8') as f:
                    plugin_config = json.load(f)
                
                # 检查是否有 cleanup 配置
                cleanup_config = plugin_config.get('cleanup', {})
                
                if cleanup_config:
                    # 使用 cleanup 配置清理数据
                    db_types = cleanup_config.get('db_types', [plugin_id])
                    data_types = cleanup_config.get('data_types', ['template', 'baseline'])
                    
                    logger.info(f"插件 {plugin_id} 开始清理数据: db_types={db_types}, data_types={data_types}")
                    
                    from inspection_dal import (
                        get_templates_by_db_type,
                        get_baselines_by_db_type,
                        delete_template,
                        delete_baseline
                    )
                    
                    # 清理模板数据
                    if 'template' in data_types:
                        for db_type in db_types:
                            templates = get_templates_by_db_type(db_type, db_path=DEFAULT_DB_PATH)
                            if templates:
                                logger.info(f"清理 {db_type} 的 {len(templates)} 个模板...")
                                for t in templates:
                                    try:
                                        # 强制删除（包括预置模板）
                                        delete_template(t['id'], db_path=DEFAULT_DB_PATH, force=True)
                                        logger.info(f"删除模板: {t.get('template_name', t['id'])} (ID: {t['id']})")
                                    except Exception as e:
                                        logger.warning(f"删除模板 {t['id']} 失败: {e}")
                    
                    # 清理基线数据
                    if 'baseline' in data_types:
                        for db_type in db_types:
                            baselines = get_baselines_by_db_type(db_type, db_path=DEFAULT_DB_PATH)
                            if baselines:
                                logger.info(f"清理 {db_type} 的 {len(baselines)} 条基线...")
                                for b in baselines:
                                    try:
                                        delete_baseline(b['id'], db_path=DEFAULT_DB_PATH)
                                        logger.info(f"删除基线: {b.get('param_name', b['id'])} (ID: {b['id']})")
                                    except Exception as e:
                                        logger.warning(f"删除基线 {b['id']} 失败: {e}")
                    
                    logger.info(f"插件 {plugin_id} 的数据清理完成")
                else:
                    # 没有 cleanup 配置，尝试调用插件的 on_uninstall() 方法
                    plugin = PluginRegistry.get_plugin_instance(plugin_id)
                    if plugin and hasattr(plugin, 'on_uninstall'):
                        plugin.on_uninstall(db_path=DEFAULT_DB_PATH)
                        logger.info(f"插件 {plugin_id} 的 on_uninstall() 已调用")
            else:
                logger.warning(f"插件 {plugin_id} 的 plugin.json 不存在，跳过数据清理")
        except Exception as e:
            logger.warning(f"清理插件 {plugin_id} 的数据失败: {e}")
            import traceback
            logger.warning(traceback.format_exc())
        
        # 2. 注销内存中的注册
        PluginRegistry.unregister(plugin_id)

        # 3. 删除 enabled/ 目录中的插件（禁用插件）
        target_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
            logger.info(f"插件已禁用，文件已保留: {target_dir}")
            return {'ok': True, 'message': f'插件 {plugin_id} 已禁用（原始文件已保留）'}
        else:
            return {'ok': True, 'message': f'插件 {plugin_id} 未启用，无需卸载'}

    def reload(self) -> Dict:
        """重新加载所有插件"""
        from plugin_core import PluginRegistry, load_plugins
        PluginRegistry.clear()
        n = load_plugins(self._plugins_dir)
        return {'ok': True, 'message': f'已重新加载 {n} 个插件'}

    def _try_local_fallback(self, plugin_id: str, target_dir: str) -> Dict:
        """
        下载全部失败时，尝试本地回退安装。
        在以下位置查找同名插件目录并直接复制：
          1. plugins/available/<plugin_id>/
          2. plugins/enabled/<plugin_id>/
          3. 内置 registry 中 download 字段为 file:// 开头的本地路径
        返回 {'ok': bool, 'message': str}
        """
        import shutil

        # 1. 在本地 plugins 目录中查找同名目录
        #    优先使用 enabled/（已启用的），其次 available/（未启用但已下载的）
        for subdir in ('enabled', 'available'):
            local_src = os.path.join(self._plugins_dir, subdir, plugin_id)
            if os.path.isdir(local_src) and os.path.isfile(os.path.join(local_src, 'plugin.json')):
                logger.info(f"下载失败，回退到本地目录安装: {local_src}")
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    for item in os.listdir(local_src):
                        s = os.path.join(local_src, item)
                        d = os.path.join(target_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)

                    # 如果是从 available/ 复制的，还需要启用（复制到 enabled/）
                    if subdir == 'available':
                        enabled_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)
                        os.makedirs(enabled_dir, exist_ok=True)
                        for item in os.listdir(target_dir):
                            s = os.path.join(target_dir, item)
                            d = os.path.join(enabled_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)

                    # 动态加载
                    from plugin_core import load_plugin
                    manifest = load_plugin(target_dir)
                    if manifest:
                        return {'ok': True, 'message': f'插件 {plugin_id} 安装成功（本地回退）'}
                    else:
                        shutil.rmtree(target_dir, ignore_errors=True)
                        enabled_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)
                        shutil.rmtree(enabled_dir, ignore_errors=True)
                        return {'ok': False, 'message': '本地回退安装失败：插件加载失败'}
                except Exception as e:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    enabled_dir = os.path.join(self._plugins_dir, 'enabled', plugin_id)
                    shutil.rmtree(enabled_dir, ignore_errors=True)
                    return {'ok': False, 'message': f'本地回退安装失败: {e}'}

        # 2. 检查内置 registry 中是否有 file:// 的本地路径
        plugin = self.get_plugin(plugin_id)
        if plugin:
            dl = plugin.get('download', '')
            if dl.startswith('file://'):
                local_path = dl[7:]
                if os.path.isdir(local_path) or (os.path.isfile(local_path) and local_path.endswith('.zip')):
                    result = self._install_from_local(plugin_id, local_path, target_dir)
                    if result['ok']:
                        result['message'] += '（本地回退）'
                    return result

        return {'ok': False, 'message': '本地回退失败：未找到本地插件目录'}

    def categories(self) -> List[Dict]:
        """
        返回分类列表（含数量）
        优先从 registry.json 的 categories 字段读取分类定义，
        如果没有则自动从插件列表中统计。
        """
        data = self.fetch_registry()
        plugins = data.get('plugins', [])
        
        # 优先使用 registry.json 中定义的 categories
        defined_categories = data.get('categories', [])
        if defined_categories:
            # 统计每个分类的插件数量
            cat_count = {}
            for p in plugins:
                cat = p.get('category', 'other')
                cat_count[cat] = cat_count.get(cat, 0) + 1
            
            # 返回分类列表（只包含有插件的分类）
            result = []
            for cat_def in defined_categories:
                cat_id = cat_def['id']
                if cat_id in cat_count:
                    result.append({
                        'id': cat_id,
                        'name': cat_def.get('name', cat_id),
                        'description': cat_def.get('description', ''),
                        'count': cat_count[cat_id]
                    })
            return result
        
        # 如果没有定义 categories，则自动统计
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
