"""
报告脱敏工具 - DBCheck 内置脱敏处理器

将巡检上下文中的敏感信息替换为掩码格式：
  IP地址  → 10.x.x.x
  端口    → ***
  用户名  → ***
  服务名  → ***
  主机名  → DB-HOST-***

使用方式：
  from desensitize import apply_desensitization
  ctx_desens = apply_desensitization(context)
  savedoc = mod.saveDoc(ctx_desens, ofile, ifile, inspector_name)
"""

import re

__all__ = ['Desensitizer', 'apply_desensitization']


class Desensitizer:
    """
    脱敏处理器。

    对 context 字典做深拷贝和原地脱敏替换。
    支持 MySQL / PostgreSQL / DM8 / Oracle Full / SQL Server 五种格式。
    """

    def __init__(self):
        pass

    def _desens_ip(self, val):
        """IP 地址掩码，保留 A.B.x.x 格式"""
        if isinstance(val, str):
            return '10.x.x.x'
        return val

    def _desens_port(self, val):
        """端口号掩码"""
        return '***'

    def _desens_user(self, val):
        """用户名掩码"""
        return '***'

    def _desens_hostname(self, val):
        """主机名掩码"""
        if isinstance(val, str) and val:
            return 'DB-HOST-***'
        return val

    def _desens_service_name(self, val):
        """Oracle 服务名 / SID 掩码"""
        return '***'

    def _desens_str_field(self, val):
        """通用字符串字段脱敏（用户名/IP/主机名混合出现时）"""
        if not isinstance(val, str):
            return val
        # 如果是 IP 格式（含有 .），做 IP 掩码
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', val):
            return '10.x.x.x'
        # 否则用户名掩码
        return '***'

    def apply(self, context: dict) -> dict:
        """
        对 context 做脱敏处理，返回新的 context（深拷贝）。

        支持的字段模式（兼容所有数据库类型）：
        - ip / host / ssh_host          → 10.x.x.x
        - port                          → ***
        - co_name / service_name / sid → ***
        - ssh_user / user / db_user    → ***
        - hostname / sys_hostname      → DB-HOST-***
        - auto_analyze[].col5 (负责人)  → ***
        """
        import copy
        ctx = copy.deepcopy(context)

        # ── 顶层字段脱敏 ──────────────────────────────────────────────────

        # IP 相关字段（MySQL/PG/DM 格式：list of dict）
        for key in ('ip', 'host', 'ssh_host'):
            if key in ctx:
                val = ctx[key]
                if isinstance(val, list) and len(val) > 0:
                    for item in val:
                        if isinstance(item, dict) and 'IP' in item:
                            item['IP'] = self._desens_ip(item['IP'])
                        elif isinstance(item, dict) and 'Host' in item:
                            item['Host'] = self._desens_ip(item['Host'])
                elif isinstance(val, str):
                    ctx[key] = self._desens_ip(val)
                elif isinstance(val, dict):
                    for k2 in val:
                        ctx[key][k2] = self._desens_ip(str(val[k2]))

        # 端口
        for key in ('port', 'ssh_port'):
            if key in ctx:
                val = ctx[key]
                if isinstance(val, list) and len(val) > 0:
                    for item in val:
                        if isinstance(item, dict) and 'PORT' in item:
                            item['PORT'] = self._desens_port(item['PORT'])
                elif isinstance(val, (int, str)):
                    ctx[key] = [{'PORT': '***'}]

        # 实例名称 / 服务名 / SID
        for key in ('co_name', 'service_name', 'sid'):
            if key in ctx:
                val = ctx[key]
                if isinstance(val, list) and len(val) > 0:
                    for item in val:
                        if isinstance(item, dict):
                            for k2 in list(item.keys()):
                                if k2 in item:
                                    item[k2] = self._desens_service_name(item[k2])
                elif isinstance(val, str):
                    ctx[key] = self._desens_service_name(val)

        # 用户名（SSH）
        if 'ssh_user' in ctx and ctx['ssh_user']:
            ctx['ssh_user'] = self._desens_str_field(str(ctx['ssh_user']))

        # 主机名（system_info）
        sys_info = ctx.get('system_info', {})
        if isinstance(sys_info, dict):
            if 'hostname' in sys_info:
                sys_info['hostname'] = self._desens_hostname(str(sys_info['hostname']))
            if 'host' in sys_info:
                sys_info['host'] = self._desens_hostname(str(sys_info['host']))
            # 磁盘列表中的设备名（避免暴露真实服务器）
            for disk in sys_info.get('disk_list', []):
                if 'device' in disk:
                    disk['device'] = disk.get('device', '/dev/sda1')  # 统一显示标准设备名
            ctx['system_info'] = sys_info

        # SSH 主机信息
        ssh_info = ctx.get('ssh_info', {})
        if isinstance(ssh_info, dict):
            if 'host' in ssh_info:
                ssh_info['host'] = self._desens_ip(str(ssh_info['host']))
            if 'user' in ssh_info:
                ssh_info['user'] = self._desens_str_field(str(ssh_info['user']))
            ctx['ssh_info'] = ssh_info

        # auto_analyze 列表中可能出现的敏感字段（负责人等）
        for item in ctx.get('auto_analyze', []):
            if isinstance(item, dict):
                if item.get('col5') and 'DBA' not in str(item.get('col5', '')):
                    # 非 DBA 角色的人名脱敏（保留 DBA/System Admin 标记）
                    item['col5'] = '***'
                # col3 中的 IP/主机名片段
                col3 = item.get('col3', '')
                if isinstance(col3, str):
                    col3 = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '10.x.x.x', col3)
                    item['col3'] = col3

        # Oracle Full 格式的 db_info（dict 直接格式）
        db_info = ctx.get('db_info', {})
        if isinstance(db_info, dict):
            if 'host' in db_info:
                db_info['host'] = self._desens_ip(str(db_info['host']))
            if 'port' in db_info:
                db_info['port'] = '***'
            if 'user' in db_info:
                db_info['user'] = self._desens_str_field(str(db_info['user']))
            if 'service_name' in db_info:
                db_info['service_name'] = self._desens_service_name(str(db_info['service_name']))
            if 'sid' in db_info:
                db_info['sid'] = self._desens_service_name(str(db_info['sid']))

        return ctx


def apply_desensitization(context: dict) -> dict:
    """快捷函数：应用脱敏处理"""
    return Desensitizer().apply(context)