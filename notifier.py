# coding: utf-8
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

"""
DBCheck 通知模块
================
支持邮件（SMTP）和 Webhook（企业微信/钉钉/自定义）通知

配置说明：
- 邮件配置读取 dbc_config.json 中的 notification.email 字段
- Webhook 配置读取 dbc_config.json 中的 notification.webhook 字段
- 也支持从 .env 文件读取（优先级更高）

.env 配置示例：
    SMTP_HOST=smtp.qq.com
    SMTP_PORT=587
    SMTP_USER=your_email@qq.com
    SMTP_PASSWORD=your授权码
    SMTP_USE_TLS=true
    SMTP_FROM_NAME=DBCheck巡检报告
"""
import os, smtplib, json, datetime, mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# 加密支持
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'dbc_config.json')
ENCRYPTION_KEY_NAME = 'notification_encryption_key'


def _get_fernet():
    """获取或创建 Fernet 加密密钥（保存在 dbc_config.json 的 notification 节点）"""
    if not HAS_CRYPTOGRAPHY:
        return None
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            pass
    key_b64 = config.get('notification', {}).get(ENCRYPTION_KEY_NAME)
    if key_b64:
        return Fernet(key_b64.encode('utf-8'))
    key = Fernet.generate_key()
    key_b64 = key.decode('utf-8')
    if 'notification' not in config:
        config['notification'] = {}
    config['notification'][ENCRYPTION_KEY_NAME] = key_b64
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('保存加密密钥失败: %s' % e)
    return Fernet(key)


def _encrypt_password(password):
    """加密密码用于存储"""
    if not password:
        return ''
    if not HAS_CRYPTOGRAPHY:
        import base64
        return '_b64_' + base64.b64encode(password.encode('utf-8')).decode('utf-8')
    fernet = _get_fernet()
    if fernet is None:
        import base64
        return '_b64_' + base64.b64encode(password.encode('utf-8')).decode('utf-8')
    return '_enc_' + fernet.encrypt(password.encode('utf-8')).decode('utf-8')


def _decrypt_password(encrypted):
    """解密存储的密码"""
    if not encrypted:
        return ''
    if encrypted.startswith('_enc_'):
        if not HAS_CRYPTOGRAPHY:
            print('cryptography 库未安装，无法解密密码')
            return ''
        fernet = _get_fernet()
        if fernet is None:
            print('获取加密密钥失败，无法解密密码')
            return ''
        try:
            return fernet.decrypt(encrypted[5:].encode('utf-8')).decode('utf-8')
        except Exception as e:
            print('解密邮件密码失败: %s' % e)
            return ''
    elif encrypted.startswith('_b64_'):
        import base64
        try:
            return base64.b64decode(encrypted[5:].encode('utf-8')).decode('utf-8')
        except Exception as e:
            print('Base64 解码密码失败: %s' % e)
            return ''
    else:
        return encrypted


def _load_config():
    """加载通知配置（从 dbc_config.json notification 节点，支持 .env 覆盖）"""
    cfg = {}

    # 从 dbc_config.json 的 notification 节点读取
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                cfg = config.get('notification', {})
        except Exception:
            pass

    # 从旧 notifier_config.json 迁移（存在则合并后删除）
    old_path = os.path.join(SCRIPT_DIR, 'notifier_config.json')
    if os.path.exists(old_path):
        try:
            with open(old_path, 'r', encoding='utf-8') as f:
                old_cfg = json.load(f)
            if 'email' in old_cfg:
                cfg.setdefault('email', {}).update(old_cfg['email'])
            if 'webhook' in old_cfg:
                cfg.setdefault('webhook', {}).update(old_cfg['webhook'])
            _save_config(cfg)
            os.remove(old_path)
            print('已迁移 notifier_config.json 到 dbc_config.json')
        except Exception as e:
            print('迁移 notifier_config.json 失败: %s' % e)

    # .env 覆盖（最高优先级）
    env_file = os.path.join(SCRIPT_DIR, '.env')
    if os.path.exists(env_file):
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        k, v = line.split('=', 1)
                        os.environ[k.strip()] = v.strip()
        except Exception:
            pass

    if 'SMTP_HOST' in os.environ:
        cfg.setdefault('email', {})['host'] = os.environ['SMTP_HOST']
    if 'SMTP_PORT' in os.environ:
        cfg.setdefault('email', {})['port'] = int(os.environ['SMTP_PORT'])
    if 'SMTP_USER' in os.environ:
        cfg.setdefault('email', {})['user'] = os.environ['SMTP_USER']
    if 'SMTP_PASSWORD' in os.environ:
        cfg.setdefault('email', {})['password'] = os.environ['SMTP_PASSWORD']
    if 'SMTP_USE_TLS' in os.environ:
        cfg.setdefault('email', {})['use_tls'] = os.environ['SMTP_USE_TLS'].lower() in ('true', '1', 'yes')
    if 'SMTP_FROM_NAME' in os.environ:
        cfg.setdefault('email', {})['from_name'] = os.environ['SMTP_FROM_NAME']
    if 'WEBHOOK_URL' in os.environ:
        cfg.setdefault('webhook', {})['url'] = os.environ['WEBHOOK_URL']
    if 'WEBHOOK_TYPE' in os.environ:
        cfg.setdefault('webhook', {})['type'] = os.environ['WEBHOOK_TYPE']

    # 解密密码
    if 'email' in cfg and 'password' in cfg['email']:
        try:
            cfg['email']['password'] = _decrypt_password(cfg['email']['password'])
        except Exception as e:
            print('解密邮件密码失败: %s' % e)
            cfg['email']['password'] = ''

    return cfg


def _save_config(cfg):
    """保存通知配置到 dbc_config.json（只更新 notification 节点，保留其他配置）"""
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)

        save_cfg = json.loads(json.dumps(cfg))

        if 'email' in save_cfg and 'password' in save_cfg['email']:
            save_cfg['email']['password'] = _encrypt_password(save_cfg['email']['password'])

        config['notification'] = save_cfg

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print('保存通知配置失败: %s' % e)
        return False


# ── 邮件通知 ────────────────────────────────────────────────

class EmailNotifier:
    """邮件通知器"""

    def __init__(self, cfg=None):
        if cfg is None:
            cfg = _load_config().get('email', {})
        self.host = cfg.get('host', '')
        self.port = int(cfg.get('port', 587))
        self.user = cfg.get('user', '')
        self.password = cfg.get('password', '')
        self.use_tls = cfg.get('use_tls', True)
        self.from_name = cfg.get('from_name', 'DBCheck 巡检报告')
        self.recipients = cfg.get('recipients', [])

    def send_report(self, label, db_type, report_file, recipients=None, custom_msg=None):
        if not recipients:
            recipients = self.recipients or []
        if not recipients:
            raise ValueError('没有指定收件人')

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        body = custom_msg or (
            '<h2>DBCheck 定时巡检报告</h2>'
            '<table style="border-collapse:collapse; font-family:Arial,sans-serif;">'
            '<tr><td style="padding:8px;border:1px solid #ddd;"><b>数据库</b></td>'
            '<td style="padding:8px;border:1px solid #ddd;">%s</td></tr>'
            '<tr><td style="padding:8px;border:1px solid #ddd;"><b>类型</b></td>'
            '<td style="padding:8px;border:1px solid #ddd;">%s</td></tr>'
            '<tr><td style="padding:8px;border:1px solid #ddd;"><b>生成时间</b></td>'
            '<td style="padding:8px;border:1px solid #ddd;">%s</td></tr>'
            '</table>'
            '<p style="margin-top:20px;">详见附件报告。</p>'
        ) % (label, db_type, now)

        msg = MIMEMultipart('mixed')
        msg['From'] = self.user
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = '[DBCheck] %s - %s 巡检报告 %s' % (
            label, db_type, now[:10])

        html_part = MIMEText(body, 'html', 'utf-8')
        msg.attach(html_part)

        if report_file and os.path.exists(report_file):
            with open(report_file, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = os.path.basename(report_file)
                from email.header import Header
                filename_encoded = str(Header(filename, 'utf-8'))
                part.add_header('Content-Disposition', 'attachment',
                               filename=filename_encoded)
                msg.attach(part)

        return self._send_smtp(msg, recipients)

    def send_test(self, recipients=None):
        """发送测试邮件（无附件，纯通知），返回 (ok, error_msg)"""
        if not recipients:
            recipients = self.recipients or []
        if not recipients:
            return False, '没有指定收件人'

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        body = (
            '<h2>DBCheck 邮件通知测试</h2>'
            '<p>这是一封测试邮件，收到说明您的邮件通知配置正确。</p>'
            '<hr>'
            '<p style="color:#888;font-size:12px">发送时间：%s</p>'
            '<p style="color:#888;font-size:12px">发件人：%s</p>'
        ) % (now, self.from_name or self.user)

        msg = MIMEMultipart('mixed')
        msg['From'] = self.user
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = '[DBCheck] 邮件通知测试 %s' % now[:10]

        html_part = MIMEText(body, 'html', 'utf-8')
        msg.attach(html_part)

        return self._send_smtp(msg, recipients)

    def _send_smtp(self, msg, recipients):
        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=30)
                if self.use_tls:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
            server.login(self.user, self.password)
            server.sendmail(self.user, recipients, msg.as_string())
            server.quit()
            print('邮件发送成功: %s' % ', '.join(recipients))
            return True, None
        except smtplib.SMTPAuthenticationError as e:
            msg = 'SMTP 认证失败，请检查用户名和密码/授权码: %s' % e
            print('邮件发送失败: %s' % msg)
            return False, msg
        except smtplib.SMTPException as e:
            msg = 'SMTP 错误: %s' % e
            print('邮件发送失败: %s' % msg)
            return False, msg
        except Exception as e:
            msg = '邮件发送异常: %s' % e
            print(msg)
            return False, msg

    def test_connection(self):
        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=10)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=10)
                if self.use_tls:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
            server.login(self.user, self.password)
            server.quit()
            return True, 'SMTP 连接成功'
        except Exception as e:
            return False, str(e)


# ── Webhook 通知 ─────────────────────────────────────────────

class WebhookNotifier:
    """Webhook 通知器（支持企业微信、钉钉、自定义 Webhook）"""

    def __init__(self, cfg=None):
        if cfg is None:
            cfg = _load_config().get('webhook', {})
        self.url = cfg.get('url', '')
        self.wtype = cfg.get('type', 'custom')
        self.secret = cfg.get('secret', '')
        self.at_mobiles = cfg.get('at_mobiles', [])
        self.is_at_all = cfg.get('is_at_all', False)

    def send_alert(self, label, db_type, status, error=None, report_file=None):
        if not self.url:
            raise ValueError('Webhook URL 未配置')
        if self.wtype == 'wecom':
            payload = self._build_wecom_payload(label, db_type, status, error)
        elif self.wtype == 'dingtalk':
            payload = self._build_dingtalk_payload(label, db_type, status, error)
        else:
            payload = self._build_custom_payload(label, db_type, status, error)
        return self._send_webhook(payload)

    def _build_wecom_payload(self, label, db_type, status, error):
        color = '34' if status == '完成' else 'FF0000'
        content = [
            'DBCheck 定时巡检通知',
            '━━━━━━━━━━━━━━━━━',
            '数据库: %s' % label,
            '类型: %s' % db_type,
            '状态: %s' % status,
            '时间: %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        ]
        if error:
            content.append('错误: %s' % error[:200])
        return {
            'msgtype': 'markdown',
            'markdown': {
                'content': '\n'.join(content)
            }
        }

    def _build_dingtalk_payload(self, label, db_type, status, error):
        content = [
            '### DBCheck 定时巡检通知',
            '---',
            '**数据库**: %s' % label,
            '**类型**: %s' % db_type,
            '**状态**: %s' % status,
            '**时间**: %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        ]
        if error:
            content.append('**错误**: %s' % error[:200])
        payload = {
            'msgtype': 'markdown',
            'markdown': {
                'title': 'DBCheck 巡检通知',
                'text': '\n'.join(content)
            },
            'at': {
                'atMobiles': self.at_mobiles,
                'isAtAll': self.is_at_all
            }
        }
        return payload

    def _build_custom_payload(self, label, db_type, status, error):
        return {
            'label': label,
            'db_type': db_type,
            'status': status,
            'error': error,
            'timestamp': datetime.datetime.now().isoformat(),
            'message': 'DBCheck 定时巡检 %s: %s (%s)' % (status, label, db_type)
        }

    def _send_webhook(self, payload):
        try:
            data = json.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            req = Request(self.url, data=data, headers=headers)
            with urlopen(req, timeout=30) as resp:
                result = resp.read().decode('utf-8')
            try:
                result_json = json.loads(result)
                errcode = result_json.get('errcode', 0)
                if errcode != 0:
                    errmsg = result_json.get('errmsg', '未知错误')
                    print('Webhook 发送失败: [%d] %s' % (errcode, errmsg))
                    return False
            except json.JSONDecodeError:
                pass
            print('Webhook 发送成功')
            return True
        except HTTPError as e:
            print('Webhook HTTP 错误: %d %s' % (e.code, e.reason))
            return False
        except URLError as e:
            print('Webhook URL 错误: %s' % e.reason)
            return False
        except Exception as e:
            print('Webhook 发送异常: %s' % e)
            return False

    def test_connection(self):
        try:
            payload = {
                'msgtype': 'text',
                'text': {'content': 'DBCheck Webhook 测试消息 - %s' %
                         datetime.datetime.now().strftime('%H:%M:%S')}
            }
            return self._send_webhook(payload), 'Webhook 测试完成'
        except Exception as e:
            return False, str(e)


# ── API 路由支持 ───────────────────────────────────────────

def get_notifier_config():
    """获取通知配置（隐藏敏感信息）"""
    cfg = _load_config()
    if 'email' in cfg:
        cfg = dict(cfg)
        cfg['email'] = dict(cfg['email'])
        if 'password' in cfg['email']:
            cfg['email']['password'] = '***' if cfg['email']['password'] else ''
    return cfg


def save_notifier_config(email_cfg=None, webhook_cfg=None):
    """保存通知配置"""
    cfg = _load_config()

    if email_cfg is not None:
        old_pwd = cfg.get('email', {}).get('password', '')
        cfg['email'] = dict(email_cfg)
        if not cfg['email'].get('password'):
            cfg['email']['password'] = old_pwd
        elif cfg['email']['password'] == '***':
            cfg['email']['password'] = old_pwd

    if webhook_cfg is not None:
        cfg['webhook'] = dict(webhook_cfg)

    return _save_config(cfg)


# ── 命令行测试 ───────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DBCheck 通知测试')
    parser.add_argument('--test-email', action='store_true', help='测试邮件发送')
    parser.add_argument('--test-webhook', action='store_true', help='测试 Webhook')
    parser.add_argument('--recipient', default='', help='测试收件人邮箱')
    args = parser.parse_args()

    cfg = _load_config()

    if args.test_email:
        notifier = EmailNotifier(cfg.get('email', {}))
        recipient = args.recipient or (notifier.recipients[0] if notifier.recipients else '')
        if not recipient:
            print('请指定 --recipient 参数')
        else:
            ok, msg = notifier.test_connection()
            print('SMTP 测试:', msg)
            if ok:
                ret, msg = notifier.send_report('测试数据库', 'MySQL', None,
                                    recipients=[recipient],
                                    custom_msg='<p>这是一封来自 DBCheck 的测试邮件。</p>')
                if not ret:
                    print('发送失败:', msg)

    if args.test_webhook:
        notifier = WebhookNotifier(cfg.get('webhook', {}))
        ok, msg = notifier.test_connection()
        print('Webhook 测试:', msg)
