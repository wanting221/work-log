# -*- coding: utf-8 -*-
"""
routes/diary.py - 填寫日誌（每日客戶聯絡紀錄）
只開放給員工/部門主管/組別主管使用（跟填寫週報的角色範圍一致），另外還要
看這個人自己的「日誌」功能開關（Engineer.diary_enabled）是否開啟——管理者
可以在人員帳號管理頁面針對每個人個別關閉這個功能。

「客戶」欄位沿用「填寫週報」頁面的專案選單邏輯（進行中的專案，部門主管/
組別主管只看得到自己部門的專案），確保這裡選的專案跟公司正式的專案清單、
跟填寫週報頁面看到的選項一致。
"""
from datetime import date as date_cls, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from extensions import db
from models import DailyLog, Project
from utils.report_utils import REPORT_WRITING_ROLES, get_department_project_ids
from utils.timezone_utils import taipei_today
from utils.decorators import log_action

diary_bp = Blueprint('diary', __name__)


def _check_diary_access():
    """
    填寫日誌的權限檢查：角色要在填寫週報的角色範圍內，而且這個人自己的
    日誌功能開關要是開啟的。兩個條件都要滿足，缺一都要擋下來——不能只靠
    側邊欄不顯示連結，直接改網址也要能正確擋住。
    """
    if current_user.role not in REPORT_WRITING_ROLES:
        abort(403)
    if not current_user.diary_enabled:
        abort(403)


def _get_selectable_projects():
    """
    這個人可以選擇的專案清單：跟「填寫週報」頁面（routes/report.py 的
    fill()）用同一套邏輯——只列出「進行中」的專案，一般人員/部門主管/
    組別主管都只看得到自己部門的專案。
    """
    projects_query = Project.query.filter_by(status='進行中')
    if current_user.role in ('engineer', 'dept_manager', 'group_manager'):
        allowed_project_ids = get_department_project_ids(current_user.department or '')
        projects_query = projects_query.filter(Project.id.in_(allowed_project_ids))
    return projects_query.order_by(Project.code).all()


@diary_bp.route('/diary')
@login_required
def index():
    _check_diary_access()
    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if year and week:
        monday = date_cls.fromisocalendar(year, week, 1)
    else:
        today = taipei_today()
        monday = today - timedelta(days=today.weekday())
    week_dates = [monday + timedelta(days=i) for i in range(5)]  # 週一到週五
    iso_year, iso_week, _ = monday.isocalendar()

    logs = (
        DailyLog.query
        .filter(DailyLog.engineer_id == current_user.id, DailyLog.log_date.in_(week_dates))
        .order_by(DailyLog.log_date, DailyLog.sort_order, DailyLog.id)
        .all()
    )
    logs_by_date = {d: [] for d in week_dates}
    for log in logs:
        logs_by_date[log.log_date].append(log)

    selectable_projects = _get_selectable_projects()

    # 「我的客戶/專案」整合顯示在這一頁最下方。這個功能原本有自己獨立的
    # 開關（client_project_enabled），但人員帳號管理頁面已經把「功能」欄
    # 改成關閉/週報/日誌三選一的下拉選單，不再有獨立的客戶/專案勾選框了
    # ——只要這個人能看到「填寫日誌」頁面（日誌功能開關開啟），就會顯示這
    # 個區塊，不再另外判斷 client_project_enabled（避免舊資料裡這個欄位
    # 剛好是 False、但畫面上已經沒有地方可以重新打開它，導致這個區塊
    # 永遠顯示不出來）。
    from routes.personal_project import get_personal_project_rows, WEEKS_LOOKBACK
    personal_project_rows = get_personal_project_rows(current_user.id)
    weeks_lookback = WEEKS_LOOKBACK

    return render_template(
        'diary.html', week_dates=week_dates, logs_by_date=logs_by_date,
        year=iso_year, week=iso_week, selectable_projects=selectable_projects,
        personal_project_rows=personal_project_rows, weeks_lookback=weeks_lookback,
    )


@diary_bp.route('/diary/add', methods=['POST'])
@login_required
def add_entry():
    """
    新增一筆日誌紀錄。支援 AJAX（帶 X-Requested-With 標頭）：回傳 JSON，
    包含這筆新紀錄的完整資料，讓前端可以直接把這筆紀錄插入畫面上的清單，
    不需要整頁重新整理；一般表單提交則走 flash + 導回列表頁。
    """
    _check_diary_access()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    log_date_str = request.form.get('log_date', '').strip()
    if not log_date_str:
        if is_ajax:
            return jsonify({'success': False, 'message': '缺少日期資訊'})
        flash('缺少日期資訊', 'danger')
        return redirect(url_for('diary.index'))
    log_date = date_cls.fromisoformat(log_date_str)

    project_id = request.form.get('project_id', type=int)
    if project_id:
        # 安全檢查：送出的 project_id 一定要是這個人「當下看得到」的選項之一
        allowed_ids = {p.id for p in _get_selectable_projects()}
        if project_id not in allowed_ids:
            project_id = None

    max_order = db.session.query(db.func.max(DailyLog.sort_order)).filter_by(
        engineer_id=current_user.id, log_date=log_date
    ).scalar() or 0

    entry = DailyLog(
        engineer_id=current_user.id, log_date=log_date,
        project_id=project_id,
        country=request.form.get('country', '').strip(),
        contact_method=request.form.get('contact_method', '').strip(),
        content_summary=request.form.get('content_summary', '').strip(),
        sort_order=max_order + 1,
    )
    db.session.add(entry)
    db.session.commit()
    log_action('create', 'DailyLog', entry.id, f'新增 {log_date} 的日誌紀錄')

    if is_ajax:
        return jsonify({
            'success': True, 'message': '已新增日誌紀錄',
            'entry': {
                'id': entry.id,
                'project_id': entry.project_id or '',
                'project_label': f'{entry.project.code} - {entry.project.full_name}' if entry.project else '',
                'country': entry.country or '',
                'contact_method': entry.contact_method or '',
                'content_summary': entry.content_summary or '',
            }
        })
    flash('已新增日誌紀錄', 'success')
    return redirect(url_for('diary.index'))


@diary_bp.route('/diary/<int:entry_id>/update', methods=['POST'])
@login_required
def update_entry(entry_id):
    """
    更新單筆日誌紀錄。支援 AJAX 自動儲存（欄位失焦/變更時前端呼叫，帶
    X-Requested-With 標頭）：回傳 JSON 不導頁；一般表單提交則走
    flash + 導回列表頁。
    """
    _check_diary_access()
    entry = DailyLog.query.get_or_404(entry_id)
    if entry.engineer_id != current_user.id:
        # 安全檢查：不能透過改網址修改別人的日誌紀錄
        abort(403)

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    new_project_id = request.form.get('project_id', type=int)
    if new_project_id:
        allowed_ids = {p.id for p in _get_selectable_projects()}
        # 如果送出的 project_id 不在目前可選範圍內，就維持原本的值不變
        if new_project_id in allowed_ids:
            entry.project_id = new_project_id
    else:
        entry.project_id = None

    entry.country = request.form.get('country', '').strip()
    entry.contact_method = request.form.get('contact_method', '').strip()
    entry.content_summary = request.form.get('content_summary', '').strip()
    db.session.commit()
    log_action('update', 'DailyLog', entry.id, '更新日誌紀錄')

    if is_ajax:
        return jsonify({'success': True, 'message': '已儲存'})
    flash('已儲存', 'success')
    return redirect(url_for('diary.index'))


@diary_bp.route('/diary/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_entry(entry_id):
    _check_diary_access()
    entry = DailyLog.query.get_or_404(entry_id)
    if entry.engineer_id != current_user.id:
        abort(403)
    log_date = entry.log_date
    db.session.delete(entry)
    db.session.commit()
    log_action('delete', 'DailyLog', entry_id, f'刪除 {log_date} 的日誌紀錄')
    flash('已刪除日誌紀錄', 'success')
    return redirect(url_for('diary.index'))


@diary_bp.route('/diary/personal-projects-refresh')
@login_required
def personal_projects_refresh():
    """
    回傳目前這個人「我的客戶/專案」最新算出的內容（JSON），供填寫日誌頁面
    在新增/編輯日誌紀錄成功後，不用整頁重新整理就能即時更新頁面最下方那
    段內容——畢竟日誌內容本身就是「進度更新」欄位的計算依據，剛存檔的
    內容如果沒有跟著更新，使用者會看到最下方顯示的還是存檔前的舊資料。
    """
    _check_diary_access()

    from routes.personal_project import get_personal_project_rows
    rows = get_personal_project_rows(current_user.id)
    return jsonify({
        'success': True,
        'rows': [
            {
                'id': row['personal_project'].id,
                'project_code': row['project'].code,
                'project_full_name': row['project'].full_name,
                'project_color': row['project'].color or 'primary',
                'start_date': row['personal_project'].start_date.isoformat() if row['personal_project'].start_date else '',
                'progress_text': row['progress_text'],
                'followup_update': row['personal_project'].followup_update or '',
            }
            for row in rows
        ]
    })


@diary_bp.route('/diary/project/quick_add', methods=['POST'])
@login_required
def quick_add_project():
    """
    填寫日誌時，若下拉選單找不到專案，可即時新增。設計完全比照「填寫週報」
    頁面的 quick_add_project()（routes/report.py），差別只在權限檢查改用
    日誌功能開關（_check_diary_access），不是週報功能開關。
    """
    _check_diary_access()
    payload = request.get_json(force=True)
    code = (payload.get('code') or '').strip()
    full_name = (payload.get('full_name') or code).strip()
    if not code:
        return jsonify({'success': False, 'message': '專案代號不可為空'}), 400

    existing = Project.query.filter_by(code=code).first()
    if existing:
        return jsonify({'success': True, 'project_id': existing.id, 'code': existing.code})

    project = Project(code=code, full_name=full_name, status='進行中',
                       note='由員工於填寫日誌時即時新增')
    db.session.add(project)
    db.session.commit()
    log_action('create', 'Project', project.id, f'{current_user.name} 即時新增專案 {code}')
    return jsonify({'success': True, 'project_id': project.id, 'code': project.code})


@diary_bp.route('/diary/keep-alive')
@login_required
def keep_alive():
    """
    什麼事都不做，單純讓瀏覽器定期呼叫這個端點，藉此刷新 session 的閒置
    逾時計時（Flask 預設每次請求都會重新整理逾時時間）。填寫日誌頁面的
    「後續更新」這類長文字欄位，使用者可能會停留好一段時間在輸入、思考，
    中間都沒有觸發任何請求，如果剛好卡在 session 閒置逾時的臨界點，
    存檔時就會遇到「登入已逾時」的狀況、剛輸入的內容也就沒能存到——
    定期呼叫這個端點可以在使用者還在頁面上、還在輸入的期間，持續延續
    登入狀態，降低這種情況發生的機率。
    """
    return jsonify({'ok': True})
