# -*- coding: utf-8 -*-
"""
utils/email_utils.py - Email 通知工具（支援 Gmail 或 Microsoft 365 SMTP）
SMTP 設定優先順序：資料庫內透過「Email提醒設定」頁面儲存的設定 > .env 裡的預設值
"""
import time
import smtplib
from collections import deque
from threading import Lock
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.header import Header
from flask import current_app

# 「每分鐘寄送上限」的計數狀態：記錄最近寄出信件的時間戳記，用滑動視窗判斷是否超過設定上限。
# 這是行程內共用的記憶體狀態（gunicorn 這裡設定為單一 worker），足以應付本系統的寄信情境；
# 不需要額外的 Redis 之類外部元件。
_send_timestamps = deque()
_send_lock = Lock()


def _enforce_rate_limit():
    """
    寄信前檢查「每分鐘寄送上限」設定，若這一分鐘內已寄出的數量達到上限，
    會在這裡等待到有空額為止才繼續寄下一封，避免短時間內大量寄信被 SMTP
    服務商判定為濫發而暫時封鎖帳號。設定為 0 表示不限制。
    """
    from models import SystemSetting

    setting = SystemSetting.query.get('smtp_rate_limit')
    limit = 0
    if setting and setting.value:
        try:
            limit = int(setting.value)
        except ValueError:
            limit = 0
    if limit <= 0:
        return

    with _send_lock:
        now = time.time()
        while _send_timestamps and now - _send_timestamps[0] >= 60:
            _send_timestamps.popleft()

        if len(_send_timestamps) >= limit:
            wait_seconds = 60 - (now - _send_timestamps[0])
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            now = time.time()
            while _send_timestamps and now - _send_timestamps[0] >= 60:
                _send_timestamps.popleft()

        _send_timestamps.append(time.time())


def get_smtp_config():
    """取得目前生效的 SMTP 設定：資料庫設定優先，缺少的欄位才使用 .env 裡的預設值"""
    from models import SystemSetting

    db_settings = {s.key: s.value for s in SystemSetting.query.filter(
        SystemSetting.key.in_([
            'smtp_server', 'smtp_port', 'smtp_username', 'smtp_password',
            'smtp_use_tls', 'mail_sender_name'
        ])
    ).all()}

    def pick(db_key, config_key, cast=str):
        value = db_settings.get(db_key)
        if value not in (None, ''):
            try:
                return cast(value)
            except (TypeError, ValueError):
                pass
        return current_app.config[config_key]

    return {
        'smtp_server': pick('smtp_server', 'SMTP_SERVER'),
        'smtp_port': pick('smtp_port', 'SMTP_PORT', int),
        'smtp_user': pick('smtp_username', 'SMTP_USERNAME'),
        'smtp_pass': pick('smtp_password', 'SMTP_PASSWORD'),
        'sender_name': pick('mail_sender_name', 'MAIL_SENDER_NAME'),
        'use_tls': (db_settings.get('smtp_use_tls') == 'True') if db_settings.get('smtp_use_tls') else current_app.config['SMTP_USE_TLS'],
    }


def send_email(to_addrs, subject, html_body, attachments=None):
    """
    寄送 Email
    to_addrs: 收件者 email 清單 (list) 或單一字串
    subject: 郵件標題
    html_body: HTML 格式內容
    attachments: [(filename, bytes_data, mime_subtype), ...] 例如 PDF/Excel 附件
    """
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    to_addrs = [a for a in to_addrs if a]
    if not to_addrs:
        return False, '無有效收件人'

    cfg = get_smtp_config()
    smtp_server = cfg['smtp_server']
    smtp_port = cfg['smtp_port']
    smtp_user = cfg['smtp_user']
    smtp_pass = cfg['smtp_pass']
    sender_name = cfg['sender_name']
    use_tls = cfg['use_tls']

    if not smtp_user or not smtp_pass:
        current_app.logger.warning('SMTP 帳號密碼未設定，略過寄信（請於「Email提醒設定」頁面或 .env 設定）')
        return False, 'SMTP 帳號密碼未設定，請至「Email提醒設定」頁面設定'

    # 套用每分鐘寄送上限（若有設定），必要時會在這裡等待
    _enforce_rate_limit()

    msg = MIMEMultipart()
    msg['From'] = f'{Header(sender_name, "utf-8").encode()} <{smtp_user}>'
    msg['To'] = ', '.join(to_addrs)
    msg['Subject'] = Header(subject, 'utf-8')

    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    if attachments:
        for filename, data, subtype in attachments:
            part = MIMEApplication(data, _subtype=subtype)
            part.add_header('Content-Disposition', 'attachment',
                             filename=('utf-8', '', filename))
            msg.attach(part)

    try:
        if smtp_port == 465:
            # 埠號 465 是「隱含式 SSL」(SMTPS)：連線一開始就要求是加密連線，
            # 不能像 587 那樣先用明碼連線再升級成加密（STARTTLS），
            # 用 SMTP() + starttls() 去接 465 會導致連線卡住直到逾時。
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_addrs, msg.as_string())
        else:
            # 其他埠號（例如 587）用一般連線，需要的話再升級成 STARTTLS 加密
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
                if use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_addrs, msg.as_string())
        return True, 'OK'
    except Exception as e:
        current_app.logger.error(f'寄送 Email 失敗: {e}')
        return False, str(e)


def render_template_vars(template_str, variables):
    """將範本中的 {變數} 替換為實際內容"""
    try:
        return template_str.format(**variables)
    except Exception:
        return template_str
