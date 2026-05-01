# coding: utf-8
#
# Copyright (c) 2024 DBCheck Contributors
# sdfiyon@gmail.com
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License.
# See LICENSE or visit https://opensource.org/licenses/MIT for full license text.
#
"""
DBCheck 通知模块
================
支持邮件（SMTP）和 Webhook（企业微信/钉钉/自定义）通知

配置说明：
- 邮件配置读取 notifier_config.json 中的 email 字段
- Webhook 配置读取 notifier_config.json 中的 webhook 字段
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'notifier_config.json')


def _load_config():
    """加载通知配置（支持 .env 覆盖）"""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            pass
    
    # .env 覆盖（优先级更高）
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
    
    # 邮件配置：从环境变量覆盖
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
    
    # Webhook 配置：从环境变量覆盖
    if 'WEBHOOK_URL' in os.environ:
        cfg.setdefault('webhook', {})['url'] = os.environ['WEBHOOK_URL']
    if 'WEBHOOK_TYPE' in os.environ:
        cfg.setdefault('webhook', {})['type'] = os.environ['WEBHOOK_TYPE']
    
    return cfg


def _save_config(cfg):
    """保存通知配置到 JSON 文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print('保存通知配置失败: %s' % e)
        return False


# ── 邮件通知 ─────────────────────────────────────────────────

class EmailNotifier:
    """邮件通知器"""
    
    def __init__(self, cfg=None):
        """
        初始化邮件通知器
        
        参数:
            cfg: 邮件配置字典，包含:
                - host: SMTP 服务器地址
                - port: SMTP 端口（默认 587）
                - user: 用户名/邮箱
                - password: 密码或授权码
                - use_tls: 是否使用 TLS（默认 True）
                - from_name: 发件人显示名称
                - recipients: 默认收件人列表
        """
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
        """
        发送巡检报告邮件
        
        参数:
            label: 数据库标签
            db_type: 数据库类型
            report_file: 报告文件路径
            recipients: 收件人列表（覆盖默认）
            custom_msg: 自定义消息内容
        
        返回:
            bool: 是否发送成功
        """
        if not recipients:
            recipients = self.recipients or []
        if not recipients:
            raise ValueError('没有指定收件人')
        
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 邮件正文
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
        
        # 构建邮件
        msg = MIMEMultipart('mixed')
        # 126/163 等国内邮箱要求 From 必须等于登录账号，不能用别名格式
        msg['From'] = self.user
        msg['To'] = ', '.join(recipients)
        msg['Subject'] = '[DBCheck] %s - %s 巡检报告 %s' % (
            label, db_type, now[:10])
        
        # HTML 正文
        html_part = MIMEText(body, 'html', 'utf-8')
        msg.attach(html_part)
        
        # 附件
        if report_file and os.path.exists(report_file):
            with open(report_file, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = os.path.basename(report_file)
                # 中文文件名处理
                from email.header import Header
                filename_encoded = str(Header(filename, 'utf-8'))
                part.add_header('Content-Disposition', 'attachment',
                               filename=filename_encoded)
                msg.attach(part)
        
        # 发送邮件
        return self._send_smtp(msg, recipients)
    
    def _send_smtp(self, msg, recipients):
        """通过 SMTP 发送邮件"""
        try:
            # 端口 465 使用隐式 SSL（SMTP_SSL），其他端口用明文+STARTTLS
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
            return True
        except smtplib.SMTPAuthenticationError:
            print('邮件发送失败: 认证失败，请检查用户名和密码/授权码')
            return False
        except smtplib.SMTPException as e:
            print('邮件发送失败: %s' % e)
            return False
        except Exception as e:
            print('邮件发送异常: %s' % e)
            return False
    
    def test_connection(self):
        """测试 SMTP 连接"""
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
        """
        初始化 Webhook 通知器
        
        参数:
            cfg: Webhook 配置字典，包含:
                - url: Webhook 地址
                - type: 类型 ('wecom' 企业微信, 'dingtalk' 钉钉, 'custom' 自定义)
                - secret: 签名密钥（可选，用于加签）
                - at_mobiles: 需要 @ 的手机号列表（钉钉）
                - is_at_all: 是否 @ 所有人（钉钉）
        """
        if cfg is None:
            cfg = _load_config().get('webhook', {})
        
        self.url = cfg.get('url', '')
        self.wtype = cfg.get('type', 'custom')
        self.secret = cfg.get('secret', '')
        self.at_mobiles = cfg.get('at_mobiles', [])
        self.is_at_all = cfg.get('is_at_all', False)
    
    def send_alert(self, label, db_type, status, error=None, report_file=None):
        """
        发送告警通知
        
        参数:
            label: 数据库标签
            db_type: 数据库类型
            status: 状态 ('完成' / '失败')
            error: 错误信息（如果有）
            report_file: 报告文件路径（可选）
        
        返回:
            bool: 是否发送成功
        """
        if not self.url:
            raise ValueError('Webhook URL 未配置')
        
        # 根据类型构建 payload
        if self.wtype == 'wecom':
            payload = self._build_wecom_payload(label, db_type, status, error)
        elif self.wtype == 'dingtalk':
            payload = self._build_dingtalk_payload(label, db_type, status, error)
        else:
            payload = self._build_custom_payload(label, db_type, status, error)
        
        return self._send_webhook(payload)
    
    def _build_wecom_payload(self, label, db_type, status, error):
        """构建企业微信消息格式"""
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
        """构建钉钉消息格式"""
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
        """构建自定义消息格式（JSON）"""
        return {
            'label': label,
            'db_type': db_type,
            'status': status,
            'error': error,
            'timestamp': datetime.datetime.now().isoformat(),
            'message': 'DBCheck 定时巡检 %s: %s (%s)' % (status, label, db_type)
        }
    
    def _send_webhook(self, payload):
        """发送 Webhook 请求"""
        try:
            data = json.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            req = Request(self.url, data=data, headers=headers)
            
            with urlopen(req, timeout=30) as resp:
                result = resp.read().decode('utf-8')
            
            # 企业微信/钉钉返回错误码检查
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
        """测试 Webhook 连接"""
        try:
            payload = {
                'msgtype': 'text',
                'text': {'content': 'DBCheck Webhook 测试消息 - %s' % 
                         datetime.datetime.now().strftime('%H:%M:%S')}
            }
            return self._send_webhook(payload), 'Webhook 测试完成'
        except Exception as e:
            return False, str(e)


# ── API 路由支持 ─────────────────────────────────────────────

def get_notifier_config():
    """获取通知配置（隐藏敏感信息）"""
    cfg = _load_config()
    # 隐藏密码
    if 'email' in cfg:
        cfg['email'] = dict(cfg['email'])
        if 'password' in cfg['email']:
            cfg['email']['password'] = '***' if cfg['email']['password'] else ''
    return cfg


def save_notifier_config(email_cfg=None, webhook_cfg=None):
    """保存通知配置"""
    cfg = get_notifier_config()
    
    if email_cfg is not None:
        # 保留原有密码（如果新配置为空）
        old_pwd = ''
        if 'email' in cfg and 'password' in cfg['email']:
            old_pwd = cfg['email']['password']
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
        recipient = args.recipient or notifier.recipients[0] if notifier.recipients else ''
        if not recipient:
            print('请指定 --recipient 参数')
        else:
            ok, msg = notifier.test_connection()
            print('SMTP 测试:', msg)
            if ok:
                # 发送测试邮件（无附件）
                notifier.send_report('测试数据库', 'MySQL', None,
                                    recipients=[recipient],
                                    custom_msg='<p>这是一封来自 DBCheck 的测试邮件。</p>')
    
    if args.test_webhook:
        notifier = WebhookNotifier(cfg.get('webhook', {}))
        ok, msg = notifier.test_connection()
        print('Webhook 测试:', msg)
