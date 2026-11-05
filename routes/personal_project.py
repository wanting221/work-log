# -*- coding: utf-8 -*-
"""
routes/personal_project.py - 我的客戶/專案（自動產生的追蹤表）
只開放給員工/部門主管/組別主管使用（跟填寫週報的角色範圍一致），另外還要
看這個人自己的「客戶/專案」功能開關（Engineer.client_project_enabled）是否
開啟——管理者可以在人員帳號管理頁面針對每個人個別關閉這個功能。

這個表格不是手動新增的清單，而是自動產生：依這位員工「最近 4 週內」的日誌
涉及的專案，自動列出對應的列。「起始日期」「後續更新」是使用者自己輸入、
需要持久保存的欄位（存進 PersonalProject）；「進度更新」不存進資料庫，是每次
開啟頁面時即時算出來的——針對每個專案，由本週開始往前找日誌，找到「有這個
專案紀錄」的最新一週就採用那一週的內容（本週有更新用本週的，本週沒有但
上週有就用上週的，以此類推，最多回溯 4 週），不會把好幾週的內容疊在一起
顯示。
"""
from datetime import timedelta, date as date_cls
from collections import defaultdict
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user
from extensions import db
from models import PersonalProject, Project, DailyLog
from utils.report_utils import REPORT_WRITING_ROLES
from utils.timezone_utils import taipei_today
from utils.decorators import log_action

personal_project_bp = Blueprint('personal_project', __name__)

WEEKS_LOOKBACK = 4
WEEK_POSITION_LABELS = ['本週', '上週', '上上週', '上上上週']


def _check_access():
    if current_user.role not in REPORT_WRITING_ROLES:
        abort(403)
    if not current_user.client_project_enabled:
        abort(403)


def _recent_iso_weeks(weeks_back=WEEKS_LOOKBACK):
    """
    由本週往前推算，回傳 [(本週年度, 本週週次), (上週年度, 上週週次), ...]
    這樣「由近到遠」排序的清單，用來一週一週往回找資料。
    """
    today = taipei_today()
    weeks = []
    for i in range(weeks_back):
        d = today - timedelta(weeks=i)
        y, w, _ = d.isocalendar()
        weeks.append((y, w))
    return weeks


def _gather_active_projects():
    """
    決定「我的客戶/專案」要顯示哪些專案，以及每個專案對應的「進度更新」
    文字內容。

    規則：由本週開始往前找日誌，最多回溯 4 週，只要找到「這一週有這個專案
    的日誌紀錄」就停止採用那一週的內容——例如本週有更新就用本週的，本週
    沒有但上週有，就用上週的，上週也沒有但上上週有，就用上上週的。不會把
    好幾週的內容疊加在一起顯示。
    """
    weeks = _recent_iso_weeks()  # [(本週y,w), (上週y,w), ...] 由近到遠

    today = taipei_today()
    earliest_monday = date_cls.fromisocalendar(weeks[-1][0], weeks[-1][1], 1)
    logs = (
        DailyLog.query.filter(
            DailyLog.engineer_id == current_user.id,
            DailyLog.log_date >= earliest_monday,
            DailyLog.log_date <= today,
        ).all()
    )
    logs_by_week = defaultdict(list)
    for log in logs:
        y, w, _ = log.log_date.isocalendar()
        logs_by_week[(y, w)].append(log)

    # 先找出這 4 週內，所有出現過（且有內容）的專案 id，決定「哪些專案要顯示」
    active_project_ids = set()
    for log in logs:
        if log.project_id and log.content_summary:
            active_project_ids.add(log.project_id)

    result = {}

    for project_id in active_project_ids:
        progress_lines = []

        # 由近到遠找，找到「這一週有這個專案的日誌」就停止，不繼續往更早找
        for idx, (y, w) in enumerate(weeks):
            week_logs = logs_by_week.get((y, w), [])
            matched_logs = [log for log in week_logs if log.project_id == project_id and log.content_summary]
            if matched_logs:
                week_label = WEEK_POSITION_LABELS[idx] if idx < len(WEEK_POSITION_LABELS) else f'{idx}週前'
                for log in sorted(matched_logs, key=lambda l: l.log_date):
                    date_label = log.log_date.strftime('%m/%d')
                    progress_lines.append(f'[{week_label}日誌-{date_label}] {log.content_summary}')
                break

        result[project_id] = {'project': Project.query.get(project_id), 'progress_lines': progress_lines}

    return result


def get_personal_project_rows(engineer_id):
    """
    取得某個人「我的客戶/專案」要顯示的列（自動補齊尚未建立追蹤紀錄的專案，
    並附上即時算出的「進度更新」文字）。抽成獨立函式，讓 diary.py 的
    填寫日誌頁面也能重複使用，把這份資料顯示在同一頁最下方。
    """
    active = _gather_active_projects()

    # 自動補齊 PersonalProject 記錄：目前有活動、但還沒有追蹤紀錄的專案，
    # 建立一筆空白的（起始日期、後續更新都留空，等使用者自己填）
    existing = {
        pp.project_id: pp
        for pp in PersonalProject.query.filter_by(engineer_id=engineer_id).all()
    }
    max_order = max([pp.sort_order for pp in existing.values()], default=-1)
    created_any = False
    for project_id in active:
        if project_id not in existing:
            max_order += 1
            pp = PersonalProject(engineer_id=engineer_id, project_id=project_id, sort_order=max_order)
            db.session.add(pp)
            created_any = True
            existing[project_id] = pp
    if created_any:
        db.session.commit()

    # 只顯示「目前有活動」的專案列（依 sort_order 排序），每列附上即時算出的
    # 進度更新文字
    rows = []
    for project_id, info in active.items():
        pp = existing[project_id]
        rows.append({
            'personal_project': pp,
            'project': info['project'],
            'progress_text': '\n'.join(info['progress_lines']) if info['progress_lines'] else f'（最近{WEEKS_LOOKBACK}週都沒有這個專案的日誌紀錄）',
        })
    rows.sort(key=lambda r: r['personal_project'].sort_order)
    return rows


@personal_project_bp.route('/personal-projects')
@login_required
def index():
    _check_access()
    rows = get_personal_project_rows(current_user.id)
    return render_template('personal_project.html', rows=rows, weeks_lookback=WEEKS_LOOKBACK)


@personal_project_bp.route('/personal-projects/<int:project_id>/update', methods=['POST'])
@login_required
def update(project_id):
    """
    更新單筆追蹤紀錄的「起始日期」「後續更新」（僅這兩個欄位可以手動編輯，
    「進度更新」是即時運算出來的，不能在這裡修改）。支援 AJAX 自動儲存，
    比照這個系統其他頁面的既有做法。
    """
    _check_access()
    pp = PersonalProject.query.get_or_404(project_id)
    if pp.engineer_id != current_user.id:
        abort(403)

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    start_date_str = request.form.get('start_date', '').strip()
    if start_date_str:
        from datetime import date as date_cls
        try:
            pp.start_date = date_cls.fromisoformat(start_date_str)
        except ValueError:
            pass
    else:
        pp.start_date = None
    pp.followup_update = request.form.get('followup_update', '').strip()
    db.session.commit()
    log_action('update', 'PersonalProject', pp.id, '更新個人專案追蹤（起始日期／後續更新）')

    if is_ajax:
        return jsonify({'success': True, 'message': '已儲存'})
    from flask import flash, redirect, url_for
    flash('已儲存', 'success')
    return redirect(url_for('personal_project.index'))


@personal_project_bp.route('/personal-projects/reorder', methods=['POST'])
@login_required
def reorder():
    """拖曳排序：前端用 SortableJS 拖完之後，把新的順序（id 陣列）用 JSON 傳回來"""
    _check_access()
    payload = request.get_json(silent=True) or {}
    ordered_ids = payload.get('ordered_ids', [])
    for idx, pid in enumerate(ordered_ids):
        pp = PersonalProject.query.get(pid)
        if pp and pp.engineer_id == current_user.id:
            pp.sort_order = idx
    db.session.commit()
    return jsonify({'success': True})
