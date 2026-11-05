# -*- coding: utf-8 -*-
"""
routes/email_settings.py - Email 提醒設定與範本編輯（Admin）
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from extensions import db
from models import EmailTemplate, SystemSetting
from utils.decorators import log_action, admin_required

email_settings_bp = Blueprint('email_settings', __name__)


@email_settings_bp.route('/settings/email')
@login_required
@admin_required
def index():
    templates = EmailTemplate.query.all()
    settings = {s.key: s.value for s in SystemSetting.query.all()}
    smtp_password_set = bool(settings.get('smtp_password'))
    return render_template('email_settings.html', templates=templates, settings=settings,
                            smtp_password_set=smtp_password_set)


@email_settings_bp.route('/settings/email/template/<int:template_id>/update', methods=['POST'])
@login_required
@admin_required
def update_template(template_id):
    template = EmailTemplate.query.get_or_404(template_id)
    template.subject = request.form.get('subject', template.subject)
    template.body = request.form.get('body', template.body)
    db.session.commit()
    log_action('update', 'EmailTemplate', template.id, f'更新Email範本 {template.name}')
    flash(f'已更新範本「{template.name}」', 'success')
    return redirect(url_for('email_settings.index'))


@email_settings_bp.route('/settings/general/update', methods=['POST'])
@login_required
@admin_required
def update_general_settings():
    """更新填寫截止日、提醒時間等系統設定，並立即套用新的提醒排程（不需要重新啟動系統）"""
    keys = ['deadline_weekday', 'deadline_time', 'reminder_weekday', 'reminder_time']
    for key in keys:
        value = request.form.get(key)
        if value is not None:
            setting = SystemSetting.query.get(key)
            if setting:
                setting.value = value
            else:
                setting = SystemSetting(key=key, value=value)
                db.session.add(setting)

    reminder_enabled = 'True' if request.form.get('reminder_enabled') else 'False'
    enabled_setting = SystemSetting.query.get('reminder_enabled')
    if enabled_setting:
        enabled_setting.value = reminder_enabled
    else:
        db.session.add(SystemSetting(key='reminder_enabled', value=reminder_enabled))
    db.session.commit()

    # 立即套用新的提醒排程，不用重新啟動系統就生效
    # （沿用「資料庫備份」頁面同樣的 add_job + replace_existing 模式）
    from extensions import scheduler
    if reminder_enabled == 'False':
        # 關閉自動提醒：直接把排程移除，不會在背景繼續等著觸發
        if scheduler.get_job('weekly_reminder'):
            scheduler.remove_job('weekly_reminder')
    else:
        try:
            from jobs import send_deadline_reminders
            from flask import current_app
            weekday = int(request.form.get('reminder_weekday', 4))
            weekday = weekday if 0 <= weekday <= 6 else 4
            hour, minute = map(int, request.form.get('reminder_time', '17:00').split(':'))
            day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
            app = current_app._get_current_object()
            scheduler.add_job(
                func=lambda: send_deadline_reminders(app),
                trigger='cron', day_of_week=day_map[weekday], hour=hour, minute=minute,
                id='weekly_reminder', replace_existing=True
            )
        except (ValueError, AttributeError):
            # 表單帶的星期/時間格式不合法時，就不更動排程，維持原本已經在跑的排程設定，
            # 避免因為輸入格式問題導致排程被移除或整個系統啟動失敗
            pass

    log_action('update', 'SystemSetting', None, f'更新填寫截止日/提醒時間設定（自動提醒：{reminder_enabled}）')
    flash('系統設定已更新，新的提醒排程已立即生效', 'success')
    return redirect(url_for('email_settings.index'))


@email_settings_bp.route('/settings/smtp/update', methods=['POST'])
@login_required
@admin_required
def update_smtp_settings():
    """更新 SMTP 寄件設定（帳號、密碼、伺服器、埠號、協定等），儲存於資料庫中，
    設定後即立即生效，優先於 .env 裡的預設值，不需要重新部署系統。"""
    fields = {
        'smtp_server': request.form.get('smtp_server', '').strip(),
        'smtp_port': request.form.get('smtp_port', '').strip(),
        'smtp_username': request.form.get('smtp_username', '').strip(),
        'smtp_use_tls': 'True' if request.form.get('smtp_use_tls') else 'False',
        'mail_sender_name': request.form.get('mail_sender_name', '').strip(),
    }
    # 每分鐘寄送上限：0 或留空代表不限制
    rate_limit_raw = request.form.get('smtp_rate_limit', '0').strip()
    try:
        rate_limit = max(0, int(rate_limit_raw))
    except ValueError:
        rate_limit = 0
    fields['smtp_rate_limit'] = str(rate_limit)

    # 密碼欄位留空代表不變更（避免每次儲存都得重新輸入密碼、或密碼被清空顯示在表單上）
    new_password = request.form.get('smtp_password', '').strip()
    if new_password:
        fields['smtp_password'] = new_password

    for key, value in fields.items():
        setting = SystemSetting.query.get(key)
        if setting:
            setting.value = value
        else:
            setting = SystemSetting(key=key, value=value)
            db.session.add(setting)
    db.session.commit()
    log_action('update', 'SystemSetting', None, '更新 SMTP 寄件設定')
    flash('SMTP 寄件設定已更新', 'success')
    return redirect(url_for('email_settings.index'))


@email_settings_bp.route('/settings/smtp/test', methods=['POST'])
@login_required
@admin_required
def test_smtp_settings():
    """寄送一封測試信到管理者自己的信箱，確認 SMTP 設定是否正確"""
    from flask_login import current_user
    from utils.email_utils import send_email

    if not current_user.email:
        flash('您的帳號沒有設定 Email，無法測試寄信', 'danger')
        return redirect(url_for('email_settings.index'))

    from flask import current_app as _app
    ok, msg = send_email(current_user.email, f"【測試信】{_app.config['COMPANY_NAME']}週報系統 SMTP 設定測試",
                          '這是一封測試信，如果您收到這封信，代表 SMTP 寄件設定正確無誤。')
    if ok:
        flash(f'測試信已寄出至 {current_user.email}，請確認是否收到', 'success')
    else:
        flash(f'測試信寄送失敗：{msg}', 'danger')
    return redirect(url_for('email_settings.index'))
