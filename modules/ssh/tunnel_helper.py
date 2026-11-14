# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
# Author: fiyo (Jack Ge) - https://github.com/fiyo/DBCheck
"""
数据库连接 SSH 隧道辅助

为数据库巡检 / 连接测试提供「经 SSH 跳板机本地端口转发」能力：

- 启用时：在本地开 ``127.0.0.1:local_port``，通过 SSH ``direct-tcpip`` 通道转发到
  数据库真实的 ``(remote_host, remote_port)``；数据库驱动只需连接
  ``127.0.0.1:local_port``，对 JDBC / psycopg2 / pymysql 等完全透明。
- 不启用时：返回原始 ``(host, port)``，保持直连。

启用条件（:func:`should_use_db_tunnel`）：须**同时**满足
``ssh_tunnel`` 开关为真且配置了 ``ssh_host``。这样与「仅用 SSH 采集服务器信息」
的语义解耦——填了 SSH 字段并不意味着数据库也走隧道，必须显式开启隧道开关。
"""

from . import SSHTunnel


def should_use_db_tunnel(ssh_info):
    """是否对数据库连接启用 SSH 隧道。

    须同时满足：
        1. ``ssh_info`` 非空；
        2. 显式开关 ``ssh_info['ssh_tunnel']`` 为真；
        3. 配置了 ``ssh_host``。

    返回 ``bool``。
    """
    if not ssh_info:
        return False
    if not ssh_info.get('ssh_tunnel'):
        return False
    if not ssh_info.get('ssh_host'):
        return False
    return True


def build_db_ssh_tunnel(ssh_info, db_host, db_port):
    """为数据库连接建立 SSH 本地端口转发隧道。

    参数:
        ssh_info: 字典，键与实例模型一致：``ssh_host`` / ``ssh_port`` /
            ``ssh_user`` / ``ssh_password`` / ``ssh_key_file`` /
            ``ssh_key_password`` / ``ssh_tunnel`` 等。
        db_host:  数据库真实主机（跳板机视角可达的地址）。
        db_port:  数据库真实端口。

    返回:
        ``(tunnel, connect_host, connect_port)``
        - 启用隧道：``tunnel`` 为已进入 ``__enter__`` 的 :class:`SSHTunnel`；
          ``connect_host='127.0.0.1'``；``connect_port=tunnel.local_port``。
          调用方负责在结束时调用 ``tunnel.close()``。
        - 不启用：``tunnel=None``；``connect_host`` / ``connect_port`` 为原始值。

    注意: 启用隧道时若 SSH 连接失败会直接抛出（paramiko 异常），由调用方捕获并
    转成清晰报错，**不会静默回退直连**——用户显式开启隧道即期望走隧道。
    """
    if not should_use_db_tunnel(ssh_info):
        return None, db_host, int(db_port)

    tunnel = SSHTunnel(
        ssh_host=ssh_info['ssh_host'],
        ssh_port=int(ssh_info.get('ssh_port') or 22),
        ssh_user=ssh_info.get('ssh_user') or 'root',
        ssh_password=ssh_info.get('ssh_password') or '',
        ssh_key=ssh_info.get('ssh_key_file') or '',
        ssh_key_password=ssh_info.get('ssh_key_password') or '',
        remote_host=db_host,
        remote_port=int(db_port),
    )
    tunnel.__enter__()
    return tunnel, '127.0.0.1', tunnel.local_port
