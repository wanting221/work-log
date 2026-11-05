# -*- coding: utf-8 -*-
"""
jobs.py - APScheduler 排程工作函式
1. send_deadline_reminders: 截止日前自動提醒未填寫工程師
2. run_monthly_full_backup: 每月自動完整備份系統資料（帳號/專案/週報/SMTP設定等）
"""
import os
from utils.report_utils import current_year_week, get_fill_status_summary, get_deadline_text
from utils.email_utils import send_email, render_template_vars


def send_deadline_reminders(app):
    """截止日前自動提醒尚未填寫週報的工程師"""
    with app.app_context():
        from models import EmailTemplate, SystemSetting
        from extensions import db

        # 防禦性檢查：即使排程 job 這次剛好還是被觸發了，也要先確認「設定提醒」
        # 頁面上的自動提醒開關是不是已經被關掉，避免管理者關閉當下剛好排程已經
        # 觸發、或系統啟動時的舊排程還沒被移除完成這類邊界情況下誤寄提醒信。
        enabled_setting = SystemSetting.query.get('reminder_enabled')
        if enabled_setting and enabled_setting.value == 'False':
            app.logger.info('[排程] 自動提醒已被管理者關閉，本次略過不寄送')
            return

        year, week = current_year_week()
        summary = get_fill_status_summary(year, week)
        template = EmailTemplate.query.filter_by(key='reminder').first()
        deadline = get_deadline_text()

        sent = 0
        for eng in summary['not_submitted']:
            if template:
                subject = render_template_vars(template.subject, {'name': eng.name, 'year': year, 'week': week, 'deadline': deadline})
                body = render_template_vars(template.body, {'name': eng.name, 'year': year, 'week': week, 'deadline': deadline})
            else:
                subject = f'【自動提醒】第{year}年第{week}週工程師週報尚未填寫'
                body = (f'{eng.name} 您好，<br>系統偵測到您尚未填寫第{year}年第{week}週的工程師週報，'
                        f'請於{deadline}前登入系統完成填寫，謝謝。')
            ok, msg = send_email(eng.email, subject, body)
            if ok:
                sent += 1
        app.logger.info(f'[排程] 截止日提醒已寄出 {sent}/{len(summary["not_submitted"])} 封')


def run_monthly_full_backup(app):
    """
    每月自動完整備份系統資料（帳號、專案、週報內容、SMTP 設定等全部資料表），
    加密後存成 .enc 檔案。這個備份是資料庫無關的（SQLite/Cloud SQL 都適用），
    不管系統實際接的是哪種資料庫都能正常運作。
    """
    with app.app_context():
        from extensions import db
        from models import SystemSetting
        from utils.full_backup_utils import export_all_data_encrypted
        from utils.backup_utils import cleanup_old_backups
        from utils.timezone_utils import taipei_now

        try:
            encrypted = export_all_data_encrypted(app.config['BACKUP_ENCRYPTION_KEY'])
            timestamp = taipei_now().strftime('%Y%m%d_%H%M%S')
            filename = f'monthly_backup_{timestamp}.enc'
            backup_folder = app.config['BACKUP_FOLDER']
            os.makedirs(backup_folder, exist_ok=True)
            with open(os.path.join(backup_folder, filename), 'wb') as f:
                f.write(encrypted)

            cleanup_old_backups(backup_folder, app.config['BACKUP_RETENTION_DAYS'])

            # 記錄這次備份成功的時間，供「本頁面提醒」功能判斷本月是否已經備份過
            _set_setting(db, SystemSetting, 'full_backup_last_success_at', taipei_now().isoformat())
            _set_setting(db, SystemSetting, 'full_backup_last_status', 'success')
            app.logger.info(f'[排程] 每月完整備份完成: {filename}')
        except Exception as e:
            from models import SystemSetting as SS
            _set_setting(db, SS, 'full_backup_last_status', f'failed: {e}')
            app.logger.error(f'[排程] 每月完整備份失敗: {e}')


def _set_setting(db, SystemSetting, key, value):
    setting = SystemSetting.query.get(key)
    if setting:
        setting.value = value
    else:
        db.session.add(SystemSetting(key=key, value=value))
    db.session.commit()
