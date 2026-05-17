"""
SSH 隧道工具模块 v2
提供本地端口转发功能，修复 Paramiko request_port_forward() API 使用错误
"""

import socket
import threading
import time
import select


def create_local_port_forward(transport, remote_host: str, remote_port: int,
                             ssh_tunnel_obj=None):
    """
    创建 SSH 本地端口转发
    返回: (local_port, server_socket, server_thread)
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('127.0.0.1', 0))
    server_socket.listen(5)
    actual_local_port = server_socket.getsockname()[1]

    if ssh_tunnel_obj is not None:
        ssh_tunnel_obj._server_socket = server_socket

    def _forward_worker():
        while not ssh_tunnel_obj._stop_event.is_set():
            try:
                readable, _, _ = select.select([server_socket], [], [], 0.5)
                if not readable:
                    continue
                client_socket, addr = server_socket.accept()
            except OSError:
                break

            handler_thread = threading.Thread(
                target=_handle_connection,
                args=(client_socket, transport, remote_host, remote_port),
                daemon=True
            )
            handler_thread.start()

    def _handle_connection(client_socket, transport, remote_host, remote_port):
        channel = None
        try:
            channel = transport.open_channel(
                'direct-tcpip',
                (remote_host, remote_port),
                client_socket.getsockname()
            )
            if channel is None:
                raise Exception("Failed to open direct-tcpip channel")
            _bidirectional_forward(client_socket, channel)
        except Exception as e:
            print(f"SSH隧道连接处理失败: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            try:
                if channel:
                    channel.close()
            except:
                pass

    def _bidirectional_forward(sock1, sock2, chunk_size=4096):
        try:
            while True:
                readable, _, exceptional = select.select(
                    [sock1, sock2], [], [sock1, sock2], 1.0)
                if exceptional:
                    break
                if not readable:
                    if sock1.fileno() == -1 or sock2.fileno() == -1:
                        break
                    continue
                for sock in readable:
                    try:
                        data = sock.recv(chunk_size)
                        if not data:
                            return
                        peer = sock2 if sock == sock1 else sock1
                        peer.sendall(data)
                    except:
                        return
        except:
            pass

    server_thread = threading.Thread(target=_forward_worker, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    return actual_local_port, server_socket, server_thread


class SSHTunnel:
    """
    SSH 隧道上下文管理器
    用法:
        with SSHTunnel(ssh_host, ssh_port, ...) as t:
            local_port = t.local_port
            # 使用 localhost:local_port 连接远程数据库
        # 退出 with 块时自动关闭隧道
    或使用 close() 手动关闭:
        t = SSHTunnel(...)
        t.__enter__()
        local_port = t.local_port
        # ... 使用隧道 ...
        t.close()
    """

    def __init__(self, ssh_host: str, ssh_port: int, ssh_user: str,
                 ssh_password: str = '', ssh_key: str = '',
                 remote_host: str = '', remote_port: int = 0):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_password = ssh_password
        self.ssh_key = ssh_key
        self.remote_host = remote_host
        self.remote_port = remote_port

        self._client = None
        self._transport = None
        self._local_port = None
        self._server_socket = None
        self._server_thread = None
        self._stop_event = threading.Event()

    def __enter__(self):
        import paramiko

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            'hostname': self.ssh_host,
            'port': int(self.ssh_port),
            'username': self.ssh_user,
            'timeout': 15,
            'look_for_keys': False,
            'allow_agent': False,
        }

        if self.ssh_key:
            connect_kwargs['key_filename'] = self.ssh_key
        else:
            connect_kwargs['password'] = self.ssh_password or ''

        self._client.connect(**connect_kwargs)
        self._transport = self._client.get_transport()

        self._local_port, self._server_socket, self._server_thread = \
            create_local_port_forward(
                self._transport,
                self.remote_host,
                self.remote_port,
                self
            )

        print(f"  🔗 SSH 隧道已建立: localhost:{self._local_port}"
              f" → {self.remote_host}:{self.remote_port}")
        return self

    @property
    def local_port(self):
        return self._local_port

    def close(self):
        """关闭 SSH 隧道，释放所有资源"""
        self._stop_event.set()
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass
            self._server_socket = None
        if self._client:
            try:
                self._client.close()
            except:
                pass
        self._client = None
        self._transport = None
        self._local_port = None
        self._server_thread = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == '__main__':
    print("SSH Tunnel Utils v2 - 单元测试")
    print("这个模块需要配合 Paramiko 使用")
    # 简单测试（需要真实的 SSH 服务器）
    # with SSHTunnel('example.com', 22, 'user', ssh_password='pass',
    #              remote_host='localhost', remote_port=1521) as t:
    #     print(f"Local port: {t.local_port}")
    #     time.sleep(5)
