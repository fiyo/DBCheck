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
DBCheck 定时调度模块
===================
基于 apscheduler 的后台定时任务调度，支持：
- Cron 表达式配置（秒/分/时/日/月/周）
- 持久化任务配置到 JSON 文件
- 巡检完成后触发邮件/钉钉/企业微信通知
"""
import os, sys, json, datetime, threading, logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, 'scheduler_jobs.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'scheduler.log')

# ── 日志配置 ────────────────────────────────────────────────
logger = logging.getLogger('scheduler')
logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_FILE, encoding='utf-8', mode='a')
_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(_handler)


def _load_jobs():
    """从 JSON 文件加载任务配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning('加载任务配置失败: %s', e)
    return []


def _save_jobs(jobs):
    """保存任务配置到 JSON 文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error('保存任务配置失败: %s', e)


def _run_inspection(job_id, db_info, inspector_name, notify_on_done):
    """
    执行巡检并发送通知（在独立线程中运行）

    参数:
        job_id:      任务ID（用于日志）
        db_info:     数据库连接信息字典
        inspector_name: 巡检人员
        notify_on_done: 是否在完成后发送通知
    """
    from run_inspection import (
        run_mysql, run_pg, run_oracle_full,
        run_dm, run_sqlserver, run_tidb
    )
    from notifier import EmailNotifier, WebhookNotifier

    # 如果指定了 datasource_id，从 Pro 模块获取完整连接信息（解密密码）
    if db_info.get('datasource_id'):
        try:
            from pro import get_instance_manager
            im = get_instance_manager()
            ds = im.get_instance_decrypted(db_info['datasource_id'])
            if ds:
                db_info = ds.copy()
                db_info['label'] = db_info.get('name', db_info.get('host', ''))
            else:
                raise ValueError('数据源不存在: ' + db_info['datasource_id'])
        except ImportError:
            raise ValueError('Pro 模块未安装，无法使用数据源')
        except Exception as e:
            raise ValueError('获取数据源失败: ' + str(e))

    db_type = db_info.get('db_type', 'mysql')
    logger.info('[%s] 定时巡检开始: %s %s:%s', job_id, db_type,
                db_info.get('host'), db_info.get('port'))

    report_file = None
    error_msg = None

    try:
        # SSH 信息（如果有）
        ssh_info = None
        if db_info.get('ssh_host'):
            ssh_info = {
                'ssh_host':     db_info.get('ssh_host'),
                'ssh_port':     int(db_info.get('ssh_port', 22)),
                'ssh_user':     db_info.get('ssh_user', 'root'),
                'ssh_password': db_info.get('ssh_password', ''),
                'ssh_key_file': db_info.get('ssh_key_file', ''),
            }

        # 执行巡检
        if db_type == 'mysql':
            report_file, _ = run_mysql(db_info, inspector_name, ssh_info)
        elif db_type == 'pg':
            report_file, _ = run_pg(db_info, inspector_name, ssh_info)
        elif db_type == 'oracle_full':
            report_file, _ = run_oracle_full(db_info, inspector_name, ssh_info)
        elif db_type == 'dm':
            report_file, _ = run_dm(db_info, inspector_name, ssh_info)
        elif db_type == 'sqlserver':
            report_file, _ = run_sqlserver(db_info, inspector_name, ssh_info)
        elif db_type == 'tidb':
            report_file, _ = run_tidb(db_info, inspector_name, ssh_info)
        else:
            raise ValueError('不支持的数据库类型: %s' % db_type)

        logger.info('[%s] 巡检完成: %s', job_id, report_file)

        # 发送通知
        if notify_on_done:
            _send_notifications(job_id, db_info, report_file, error=None)

    except Exception as e:
        error_msg = str(e)
        logger.error('[%s] 巡检失败: %s', job_id, error_msg)
        # 即使失败也发送通知（告警）
        if notify_on_done:
            _send_notifications(job_id, db_info, report_file, error=error_msg)


def _send_notifications(job_id, db_info, report_file, error=None):
    """发送邮件和 Webhook 通知"""
    from notifier import EmailNotifier, WebhookNotifier
    
    label = db_info.get('label', db_info.get('host', '未知'))
    db_type = db_info.get('db_type', 'unknown')
    status = '失败' if error else '完成'
    
    # 加载通知配置
    notifier_cfg_path = os.path.join(SCRIPT_DIR, 'notifier_config.json')
    cfg = {}
    if os.path.exists(notifier_cfg_path):
        try:
            with open(notifier_cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            pass
    
    # 发送邮件通知（只要配置了收件人就发送，不强制要求 enabled 字段）
    email_cfg = cfg.get('email', {})
    if email_cfg.get('recipients') and not error:
        try:
            notifier = EmailNotifier(cfg['email'])
            notifier.send_report(label, db_type, report_file)
            logger.info('[%s] 邮件通知已发送', job_id)
        except Exception as e:
            logger.error('[%s] 邮件发送失败: %s', job_id, e)
    
    # 发送 Webhook 告警
    webhook_cfg = cfg.get('webhook', {})
    if webhook_cfg.get('enabled'):
        try:
            notifier = WebhookNotifier(webhook_cfg)
            notifier.send_alert(
                label=label,
                db_type=db_type,
                status=status,
                error=error,
                report_file=report_file
            )
            logger.info('[%s] Webhook 通知已发送', job_id)
        except Exception as e:
            logger.error('[%s] Webhook 发送失败: %s', job_id, e)


class SchedulerManager:
    """定时调度管理器（单例）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # APScheduler 配置
        jobstores = {'default': MemoryJobStore()}
        job_defaults = {
            'coalesce': True,       # 合并错过的执行
            'max_instances': 1,      # 同一任务最多一个实例
            'misfire_grace_time': 300  # 5分钟内可补执行
        }
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
            timezone=datetime.timezone(datetime.timedelta(hours=8))  # 北京时间
        )
        self.scheduler.start()
        logger.info('调度器启动成功')
        
        # 恢复持久化的任务
        self._restore_jobs()
    
    def _restore_jobs(self):
        """从配置文件恢复任务"""
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg.get('enabled', True):
                self.add_job(job_cfg, restore=True)
    
    def _job_func(self, job_id, db_info, inspector_name, notify_on_done):
        """任务执行函数（包装器）"""
        # 在独立线程中运行，避免阻塞调度器
        t = threading.Thread(
            target=_run_inspection,
            args=(job_id, db_info, inspector_name, notify_on_done),
            daemon=True
        )
        t.start()
    
    def add_job(self, config, restore=False):
        """
        添加定时任务
        
        参数:
            config: 任务配置字典，包含:
                - id: str, 任务ID
                - name: str, 任务名称
                - db_type: str, 数据库类型
                - db_info: dict, 数据库连接信息
                - cron: dict, Cron 配置 {second, minute, hour, day, month, day_of_week}
                - enabled: bool, 是否启用
                - inspector_name: str, 巡检人员
                - notify_on_done: bool, 完成后是否发送通知
        
        返回:
            bool: 是否添加成功
        """
        job_id = config.get('id')
        if not job_id:
            return False
        
        # 如果任务已存在，先移除
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        
        # 构建 CronTrigger
        cron = config.get('cron', {})
        trigger_kwargs = {}
        for unit in ('second', 'minute', 'hour', 'day', 'month', 'day_of_week'):
            if unit in cron and cron[unit] not in (None, '*'):
                trigger_kwargs[unit] = cron[unit]
        
        if not trigger_kwargs:
            logger.warning('任务 %s 没有有效的 cron 配置', job_id)
            return False
        
        try:
            trigger = CronTrigger(**trigger_kwargs)
            self.scheduler.add_job(
                func=self._job_func,
                trigger=trigger,
                job_id=job_id,
                args=[job_id, config['db_info'], config.get('inspector_name', 'DBCheck'),
                      config.get('notify_on_done', True)],
                name=config.get('name', job_id),
                replace_existing=True
            )
            logger.info('添加定时任务: %s (%s)', job_id, config.get('name', ''))
            
            # 持久化（非恢复模式才保存）
            if not restore:
                jobs = _load_jobs()
                # 替换或追加
                existing_idx = next((i for i, j in enumerate(jobs) if j['id'] == job_id), -1)
                if existing_idx >= 0:
                    jobs[existing_idx] = config
                else:
                    jobs.append(config)
                _save_jobs(jobs)
            
            return True
        except Exception as e:
            logger.error('添加任务失败 %s: %s', job_id, e)
            return False
    
    def remove_job(self, job_id):
        """
        移除定时任务
        
        返回:
            bool: 是否移除成功
        """
        try:
            # 先尝试从调度器移除（任务可能已过期或被禁用）
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                logger.info('从调度器移除任务: %s', job_id)
            
            # 无论调度器里有没有，都从 JSON 里删除（避免遗留）
            jobs = _load_jobs()
            original_count = len(jobs)
            jobs = [j for j in jobs if j['id'] != job_id]
            if len(jobs) < original_count:
                _save_jobs(jobs)
                logger.info('从 JSON 删除任务: %s', job_id)
                return True
            return False
        except Exception as e:
            logger.error('移除任务失败 %s: %s', job_id, e)
            return False
    
    def list_jobs(self):
        """
        列出所有定时任务

        返回:
            list: 任务配置列表（包含调度器中的运行状态）
        """
        jobs = _load_jobs()
        scheduled_ids = {job.id for job in self.scheduler.get_jobs()}

        result = []
        for job_cfg in jobs:
            job_cfg = dict(job_cfg)  # 拷贝
            job_cfg['running'] = job_cfg['id'] in scheduled_ids
            # 隐藏敏感信息
            if 'password' in job_cfg.get('db_info', {}):
                job_cfg['db_info'] = dict(job_cfg['db_info'])
                job_cfg['db_info']['password'] = '***'
            # 如果使用了数据源，获取数据源名称用于显示
            if job_cfg.get('db_info', {}).get('datasource_id'):
                ds_id = job_cfg['db_info']['datasource_id']
                try:
                    from pro import get_instance_manager
                    im = get_instance_manager()
                    ds = im.get_instance(ds_id, mask_password=False)
                    if ds:
                        job_cfg['db_info']['host'] = ds.get('name') or ds.get('host', ds_id)
                        job_cfg['db_info']['db_type'] = ds.get('db_type', '')
                        job_cfg['db_info']['port'] = ds.get('port', '')
                        job_cfg['db_info']['user'] = ds.get('user', '')
                        job_cfg['db_info']['password'] = ''
                except Exception:
                    pass
            result.append(job_cfg)
        return result
    
    def toggle_job(self, job_id, enabled):
        """
        启用/禁用任务
        
        参数:
            job_id: 任务ID
            enabled: True=启用, False=禁用
        """
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg['id'] == job_id:
                job_cfg['enabled'] = enabled
                _save_jobs(jobs)
                if enabled:
                    self.add_job(job_cfg)
                else:
                    if self.scheduler.get_job(job_id):
                        self.scheduler.remove_job(job_id)
                return True
        return False
    
    def run_job_now(self, job_id):
        """
        立即执行一次任务（手动触发）
        
        返回:
            bool: 是否触发成功
        """
        jobs = _load_jobs()
        for job_cfg in jobs:
            if job_cfg['id'] == job_id:
                self._job_func(
                    job_id,
                    job_cfg['db_info'],
                    job_cfg.get('inspector_name', 'DBCheck'),
                    job_cfg.get('notify_on_done', True)
                )
                return True
        return False
    
    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info('调度器已关闭')


# ── 全局调度器实例（延迟初始化）──────────────────────────────
_scheduler = None

def get_scheduler():
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerManager()
    return _scheduler


if __name__ == '__main__':
    # 测试：列出所有任务
    sm = get_scheduler()
    for job in sm.list_jobs():
        print(job)
