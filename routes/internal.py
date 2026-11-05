# -*- coding: utf-8 -*-
"""
routes/internal.py - 供外部排程服務（例如 Google Cloud Scheduler）或維護人員
手動觸發的內部端點，適用於 Cloud Run 這類無法直接下指令進容器操作的部署環境。
每個端點都需要在網址帶入正確的密鑰 (?token=xxx，或 Header X-Internal-Token) 才能呼叫。
"""
from flask import Blueprint, request, jsonify, current_app
from extensions import db
from jobs import send_deadline_reminders, run_monthly_full_backup

internal_bp = Blueprint('internal', __name__)


def _check_token():
    # 同時支援 Header (X-Internal-Token，給 Cloud Scheduler 等服務對服務呼叫使用)
    # 與網址參數 (?token=xxx，方便用瀏覽器直接開啟網址操作) 兩種方式
    token = request.headers.get('X-Internal-Token', '') or request.args.get('token', '')
    expected = current_app.config['INTERNAL_TRIGGER_TOKEN']
    return token and expected and token == expected


@internal_bp.route('/internal/trigger/reminder', methods=['POST'])
def trigger_reminder():
    """供 Cloud Scheduler 定時呼叫：觸發截止日提醒信"""
    if not _check_token():
        return jsonify({'success': False, 'message': '未授權'}), 403
    send_deadline_reminders(current_app._get_current_object())
    return jsonify({'success': True, 'message': '提醒信已觸發寄送'})


@internal_bp.route('/internal/trigger/backup', methods=['POST'])
def trigger_backup():
    """供 Cloud Scheduler 定時呼叫：觸發系統資料完整備份（資料庫無關，SQLite/Cloud SQL 都適用）"""
    if not _check_token():
        return jsonify({'success': False, 'message': '未授權'}), 403
    run_monthly_full_backup(current_app._get_current_object())
    return jsonify({'success': True, 'message': '備份已觸發執行'})


@internal_bp.route('/internal/fix-admin-account', methods=['GET'])
def fix_admin_account_route():
    """
    網頁版一鍵修復管理者帳號：在瀏覽器網址列直接開啟以下網址即可執行
        https://你的服務網址/internal/fix-admin-account?token=你在.env設定的INTERNAL_TRIGGER_TOKEN

    會自動處理三種情況：
    1. 資料庫已經是 A00007 -> 只重設密碼為 admin123
    2. 資料庫還是舊的 A171 / A001 -> 改成 A00007，密碼重設為 admin123
    3. 資料庫完全空白 -> 建立管理者帳號（不含任何假資料）
    """
    if not _check_token():
        return '<h3>未授權：token 錯誤或未設定</h3>', 403

    from seed_data import fix_admin_account
    db.create_all()
    message = fix_admin_account()

    return f"""
    <html><head><meta charset="UTF-8"></head>
    <body style="font-family: sans-serif; padding: 40px;">
      <h2>✅ 執行完成</h2>
      <p>{message}</p>
      <p>現在可以使用 <b>工號 A00007 / 密碼 admin123</b> 登入。</p>
      <a href="/weekly_report_login">前往登入頁面</a>
    </body></html>
    """
