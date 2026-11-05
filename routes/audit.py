# -*- coding: utf-8 -*-
"""
routes/audit.py - 操作紀錄 (Audit Log) 查詢 + 系統資料備份/還原管理
備份機制是資料庫無關的完整資料匯出（見 utils/full_backup_utils.py），
SQLite、Cloud SQL（MySQL/PostgreSQL）都適用。
"""
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, current_app, send_file, flash, redirect, url_for
from flask_login import login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from extensions import db, scheduler
from models import AuditLog, SystemSetting
from utils.decorators import admin_required, log_action
from utils.timezone_utils import taipei_now
from utils.backup_utils import list_backups, cleanup_old_backups
from utils.full_backup_utils import export_all_data_encrypted, decrypt_and_parse, import_all_data, reset_all_data

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('/audit')
@login_required
@admin_required
def index():
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action')
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    if action_filter:
        query = query.filter_by(action=action_filter)
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    return render_template('audit_log.html', pagination=pagination, action_filter=action_filter)


@audit_bp.route('/audit/clear_range', methods=['POST'])
@login_required
@admin_required
def clear_range():
    """清除指定日期區間（含頭尾兩天）內的操作紀錄"""
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')

    if not start_date_str or not end_date_str:
        flash('請選擇開始與結束日期', 'danger')
        return redirect(url_for('audit.index'))

    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
    except ValueError:
        flash('日期格式不正確', 'danger')
        return redirect(url_for('audit.index'))

    if start_dt >= end_dt:
        flash('開始日期不能晚於結束日期', 'danger')
        return redirect(url_for('audit.index'))

    deleted_count = AuditLog.query.filter(
        AuditLog.timestamp >= start_dt, AuditLog.timestamp < end_dt
    ).delete(synchronize_session=False)
    db.session.commit()

    log_action('clear_range', 'AuditLog', None,
               f'清除 {start_date_str} ~ {end_date_str} 區間內的操作紀錄，共 {deleted_count} 筆')
    flash(f'已清除 {start_date_str} ~ {end_date_str} 區間內的操作紀錄，共 {deleted_count} 筆', 'success')
    return redirect(url_for('audit.index'))


def _get_setting(key, default=None):
    setting = SystemSetting.query.get(key)
    return setting.value if setting and setting.value is not None else default


def _set_setting(key, value):
    setting = SystemSetting.query.get(key)
    if setting:
        setting.value = value
    else:
        db.session.add(SystemSetting(key=key, value=value))
    db.session.commit()


def _check_this_month_backup_missing(auto_enabled, backup_day):
    """
    判斷「本月自動備份是否已經逾期未成功」：只有已經過了設定的備份日，
    且找不到本月的成功備份紀錄時，才提醒（還沒到日期就不用緊張）。
    """
    if not auto_enabled:
        return False
    today = taipei_now()
    if today.day < backup_day:
        return False  # 這個月還沒到排定的備份日，還不用擔心

    last_success_at = _get_setting('full_backup_last_success_at')
    if not last_success_at:
        return True
    try:
        last_success_dt = datetime.fromisoformat(last_success_at)
    except ValueError:
        return True
    return not (last_success_dt.year == today.year and last_success_dt.month == today.month)


@audit_bp.route('/audit/backup')
@login_required
@admin_required
def backup_index():
    backups = list_backups(current_app.config['BACKUP_FOLDER'])

    auto_enabled = _get_setting('full_backup_auto_enabled', 'False') == 'True'
    backup_day = int(_get_setting('full_backup_day', str(current_app.config['FULL_BACKUP_DEFAULT_DAY'])))
    last_success_at = _get_setting('full_backup_last_success_at')
    last_status = _get_setting('full_backup_last_status')
    backup_missing = _check_this_month_backup_missing(auto_enabled, backup_day)

    return render_template(
        'backup.html',
        backups=backups,
        auto_enabled=auto_enabled,
        backup_day=backup_day,
        day_options=list(range(1, 29)),
        last_success_at=last_success_at,
        last_status=last_status,
        backup_missing=backup_missing,
        reset_confirmation_phrase=RESET_CONFIRMATION_PHRASE,
    )


@audit_bp.route('/audit/backup/export', methods=['POST'])
@login_required
@admin_required
def export_backup():
    """系統資料備份：立即把目前所有資料（帳號、專案、週報、SMTP 設定等）加密匯出成一份 .enc 檔案"""
    try:
        encrypted = export_all_data_encrypted(current_app.config['BACKUP_ENCRYPTION_KEY'])
        timestamp = taipei_now().strftime('%Y%m%d_%H%M%S')
        filename = f'full_backup_{timestamp}.enc'
        backup_folder = current_app.config['BACKUP_FOLDER']
        os.makedirs(backup_folder, exist_ok=True)
        with open(os.path.join(backup_folder, filename), 'wb') as f:
            f.write(encrypted)
        cleanup_old_backups(backup_folder, current_app.config['BACKUP_RETENTION_DAYS'])

        # 手動備份也算數：只要「本月確實有備份」就滿足安全需求，不用等到自動排程才算，
        # 這樣管理者發現本月自動備份失敗、手動補做一次之後，上方的提醒就會正確消失
        _set_setting('full_backup_last_success_at', taipei_now().isoformat())
        _set_setting('full_backup_last_status', 'success')

        log_action('backup', 'Database', None, f'手動執行系統資料備份: {filename}')
        flash(f'備份成功：{filename}，可在下方清單下載', 'success')
    except Exception as e:
        flash(f'備份失敗：{e}', 'danger')
    return redirect(url_for('audit.backup_index'))


@audit_bp.route('/audit/backup/schedule', methods=['POST'])
@login_required
@admin_required
def update_schedule():
    """
    儲存「啟用自動備份」開關與備份日期（1~28 號），並立即套用排程（不需要重新啟動系統）。
    固定在台灣時間當天凌晨 03:00 執行。
    """
    auto_enabled = request.form.get('auto_enabled') == '1'
    backup_day = request.form.get('backup_day', type=int)

    if backup_day is None or not (1 <= backup_day <= 28):
        flash('備份日期必須是 1~28 之間的數字', 'danger')
        return redirect(url_for('audit.backup_index'))

    _set_setting('full_backup_auto_enabled', 'True' if auto_enabled else 'False')
    _set_setting('full_backup_day', str(backup_day))

    from jobs import run_monthly_full_backup
    app = current_app._get_current_object()

    if auto_enabled:
        # add_job 搭配 replace_existing=True：不管之前有沒有這個 job，都能正確套用新設定，
        # 比 reschedule_job 更保險（reschedule_job 在 job 還不存在時會丟例外）
        scheduler.add_job(
            func=lambda: run_monthly_full_backup(app),
            trigger='cron', day=backup_day, hour=3, minute=0,
            id='monthly_full_backup', replace_existing=True
        )
        flash(f'已啟用每月自動備份，將於每月 {backup_day} 號凌晨 03:00（台灣時間）執行，立即生效', 'success')
    else:
        if scheduler.get_job('monthly_full_backup'):
            scheduler.remove_job('monthly_full_backup')
        flash('已停用每月自動備份', 'success')

    log_action('update', 'SystemSetting', None,
               f'更新每月自動備份設定：啟用={auto_enabled}，日期={backup_day}號')
    return redirect(url_for('audit.backup_index'))


@audit_bp.route('/audit/backup/download/<filename>')
@login_required
@admin_required
def download_backup(filename):
    filename = secure_filename(filename)
    path = os.path.join(current_app.config['BACKUP_FOLDER'], filename)
    if not os.path.exists(path):
        flash('備份檔不存在', 'danger')
        return redirect(url_for('audit.backup_index'))
    return send_file(path, as_attachment=True, download_name=filename)


@audit_bp.route('/audit/backup/restore', methods=['POST'])
@login_required
@admin_required
def restore_backup():
    """
    選擇加密備份檔（.enc）並匯入還原：上傳後會先解密驗證格式，
    確認無誤才會真正執行還原。這是「完全覆蓋」，會清空目前所有資料，
    換成備份檔裡的內容，操作前頁面上已經有明顯的警告與二次確認。
    """
    file = request.files.get('file')
    if not file or not file.filename:
        flash('請選擇要匯入的備份檔案', 'danger')
        return redirect(url_for('audit.backup_index'))

    filename = secure_filename(file.filename)
    if not filename.endswith('.enc'):
        flash('檔案格式不正確，請上傳本系統匯出的 .enc 加密備份檔', 'danger')
        return redirect(url_for('audit.backup_index'))

    try:
        encrypted_bytes = file.read()
        data = decrypt_and_parse(encrypted_bytes, current_app.config['BACKUP_ENCRYPTION_KEY'])
    except Exception as e:
        flash(f'匯入失敗：無法解密或格式不正確，可能是加密金鑰不符或檔案已損毀（{e}）', 'danger')
        return redirect(url_for('audit.backup_index'))

    try:
        summary = import_all_data(data)
        exported_at = data.get('exported_at', '未知時間')
        detail = '、'.join(f'{name} {count}筆' for name, count in summary.items())
        log_action('restore', 'Database', None,
                   f'從備份檔 {filename}（匯出於 {exported_at}）還原系統資料：{detail}')
        flash(f'還原成功！已套用備份檔（匯出於 {exported_at}）的資料：{detail}。'
              f'部分帳號可能已變動，請重新登入確認。', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'還原過程發生錯誤：{e}，請確認資料庫狀態，必要時聯繫系統管理者', 'danger')

    return redirect(url_for('audit.backup_index'))


RESET_CONFIRMATION_PHRASE = '清空資料庫'


@audit_bp.route('/audit/reset_database', methods=['POST'])
@login_required
@admin_required
def reset_database():
    """
    資料庫 RESET：清空整個系統資料庫回到初始狀態（只留一個預設管理者帳號 A00007 /
    admin123，以及系統運作所需的預設 Email 範本）。這是系統裡風險最高的操作，
    比「匯入還原」更徹底——執行前需要同時符合兩項確認：
    1. 正確輸入指定的確認文字
    2. 正確輸入目前登入管理者自己的密碼（再次確認身份與意圖，避免帳號在
       無人看管的瀏覽器分頁被誤觸發）
    任一項不符合就會直接拒絕，不會執行任何清空動作。
    """
    confirm_phrase = request.form.get('confirm_phrase', '').strip()
    confirm_password = request.form.get('confirm_password', '')

    if confirm_phrase != RESET_CONFIRMATION_PHRASE:
        flash(f'確認文字不正確，請完整輸入「{RESET_CONFIRMATION_PHRASE}」，未執行任何清空動作', 'danger')
        return redirect(url_for('audit.backup_index'))

    if not current_user.check_password(confirm_password):
        flash('密碼不正確，未執行任何清空動作', 'danger')
        return redirect(url_for('audit.backup_index'))

    admin_name = current_user.name
    admin_emp_id = current_user.emp_id

    reset_all_data()

    # 這裡刻意不用 log_action()：清空之後，原本登入的管理者帳號（連同它的 id）
    # 已經不存在於資料庫裡了，如果沿用 current_user.id 當作 user_id 寫入，
    # 在 Cloud SQL 上會因為外鍵約束找不到對應的使用者而失敗（沒有 user_id 對應
    # 資料就無法寫入）。改成明確不寫 user_id（設為 None），只在文字描述裡記錄
    # 是誰執行的，避免這個問題，同時保留「誰在什麼時候重設過系統」這筆重要記錄。
    reset_log = AuditLog(
        user_id=None, user_name=f'{admin_name}（{admin_emp_id}，重設前帳號）',
        action='reset', target_type='Database', target_id=None,
        detail=f'{admin_name}（{admin_emp_id}）執行了系統資料庫 RESET，所有業務資料已清空',
        ip_address=request.remote_addr,
    )
    db.session.add(reset_log)
    db.session.commit()

    logout_user()
    flash('系統資料庫已重設為初始狀態，請使用預設管理者帳號 A00007 / admin123 重新登入。', 'success')
    return redirect(url_for('auth.login'))
