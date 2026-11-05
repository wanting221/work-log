# -*- coding: utf-8 -*-
"""
routes/report.py - 工程師填寫週報（本週已執行 / 下週計劃）
支援：新增/刪除/拖曳排序項目、草稿儲存、送出鎖定、送出後自動延續下週計劃、
      附件上傳、附註/需要討論留言
"""
import json
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import selectinload, joinedload
from extensions import db
from models import WeeklyReport, WeeklyReportItem, Project, Attachment, ReportNote
from utils.decorators import log_action, not_viewer_required
from utils.report_utils import (
    current_year_week, get_or_create_report, carry_over_to_next_week,
    check_and_notify_all_groups_complete,
)
from utils.timezone_utils import taipei_now

report_bp = Blueprint('report', __name__)


@report_bp.route('/report/fill')
@login_required
def fill():
    if current_user.role in ('viewer', 'hr_manager'):
        abort(403)
    if not current_user.weekly_report_enabled:
        abort(403)

    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if not year or not week:
        year, week = current_year_week()

    report = get_or_create_report(current_user.id, year, week)
    last_week_items = [i for i in report.items if i.item_type == 'last_week']
    this_week_items = [i for i in report.items if i.item_type == 'this_week']
    prev_carried_count = sum(1 for i in last_week_items if i.is_carried_over)

    projects_query = Project.query.filter_by(status='進行中')
    if current_user.role in ('engineer', 'dept_manager', 'group_manager'):
        # 一般人員、部門主管、組別主管填寫自己的週報時，專案選單只顯示自己
        # 部門的專案（專案本身只有部門層級的歸屬欄位，組別主管一樣是用自己
        # 所屬的部門來篩選，不需要另外用組別再篩一次）——一般人員應該看到
        # 跟自己部門主管、組別主管相同範圍的專案清單，不該看到全公司的專案
        from utils.report_utils import get_department_project_ids
        allowed_project_ids = get_department_project_ids(current_user.department or '')
        projects_query = projects_query.filter(Project.id.in_(allowed_project_ids))
    projects = projects_query.order_by(Project.code).all()

    # 歷史週報資訊欄：原本是獨立頁面「我的歷史週報」，現在整合顯示在這個頁面
    # 最下方——預設只顯示最近 5 週，超過的部分用分頁瀏覽（不用整批載入拖慢頁面）
    HISTORY_PER_PAGE = 5
    history_page = request.args.get('history_page', 1, type=int)
    if history_page < 1:
        history_page = 1
    history_pagination = (
        WeeklyReport.query.filter_by(engineer_id=current_user.id)
        .order_by(WeeklyReport.year.desc(), WeeklyReport.week_number.desc())
        .paginate(page=history_page, per_page=HISTORY_PER_PAGE, error_out=False)
    )
    history_reports = history_pagination.items
    # 這一頁涉及的專案清單（selectinload 太重了，這裡只有 5 筆，直接用
    # 既有的 relationship 存取即可，不用另外查）
    history_report_projects = {}
    for r in history_reports:
        seen = {}
        for item in r.items:
            if item.project:
                seen[item.project.id] = item.project
        history_report_projects[r.id] = list(seen.values())

    return render_template(
        'report_fill.html',
        report=report, year=year, week=week,
        last_week_items=last_week_items, this_week_items=this_week_items,
        prev_carried_count=prev_carried_count,
        projects=projects,
        history_pagination=history_pagination, history_reports=history_reports,
        history_report_projects=history_report_projects,
    )


@report_bp.route('/report/history')
@login_required
def history():
    if current_user.role == 'hr_manager':
        abort(403)
    if not current_user.weekly_report_enabled:
        abort(403)
    # selectinload 一次把每份週報的項目跟項目對應的專案都撈出來，
    # 避免下面組「這週涉及哪些專案」時逐筆觸發額外查詢
    reports = (
        WeeklyReport.query.filter_by(engineer_id=current_user.id)
        .options(selectinload(WeeklyReport.items).joinedload(WeeklyReportItem.project))
        .order_by(WeeklyReport.year.desc(), WeeklyReport.week_number.desc())
        .all()
    )

    # 整理出每份週報「當週涉及的專案」清單（本週已執行+下週計劃都算，去重）
    report_projects = {}
    for r in reports:
        seen = {}
        for item in r.items:
            if item.project:
                seen[item.project.id] = item.project
        report_projects[r.id] = list(seen.values())

    return render_template('report_history.html', reports=reports, report_projects=report_projects)


def _save_items(report, items_data, item_type):
    """依前端傳來的 JSON 資料重建該類型的所有項目"""
    # 先刪除舊的該類型項目
    WeeklyReportItem.query.filter_by(report_id=report.id, item_type=item_type).delete()

    for idx, data in enumerate(items_data):
        project_id = data.get('project_id') or None
        content = (data.get('content') or '').strip()
        if not content:
            continue
        chain_id = data.get('chain_id') or None
        item = WeeklyReportItem(
            report_id=report.id,
            item_type=item_type,
            project_id=project_id,
            content=content,
            sort_order=idx,
            is_carried_over=bool(data.get('is_carried_over')),
            chain_id=chain_id,
        )
        db.session.add(item)


@report_bp.route('/report/save', methods=['POST'])
@login_required
@not_viewer_required
def save():
    """儲存草稿（AJAX）"""
    payload = request.get_json(force=True)
    year, week = payload.get('year'), payload.get('week')
    report = get_or_create_report(current_user.id, year, week)

    if report.locked:
        return jsonify({'success': False, 'message': '此週報已鎖定，如需修改請聯繫管理者解鎖'}), 403

    _save_items(report, payload.get('last_week_items', []), 'last_week')
    _save_items(report, payload.get('this_week_items', []), 'this_week')
    report.status = '草稿'
    db.session.commit()
    log_action('update', 'WeeklyReport', report.id, '儲存草稿')
    return jsonify({'success': True, 'message': '草稿已儲存'})


@report_bp.route('/report/submit', methods=['POST'])
@login_required
@not_viewer_required
def submit():
    """送出週報並鎖定編輯；送出後自動把「下週計劃工作重點」帶入下一週的「本週已執行工作精要」"""
    payload = request.get_json(force=True)
    year, week = payload.get('year'), payload.get('week')
    report = get_or_create_report(current_user.id, year, week)

    if report.locked:
        return jsonify({'success': False, 'message': '此週報已鎖定'}), 403

    _save_items(report, payload.get('last_week_items', []), 'last_week')
    _save_items(report, payload.get('this_week_items', []), 'this_week')

    report.status = '已送出'
    report.locked = True
    report.submitted_at = taipei_now()
    db.session.commit()

    # 自動延續：把本週的「下週計劃」帶入下一週週報的「本週已執行」，不需要工程師手動勾選
    carry_over_to_next_week(report)

    # 檢查這一週是否所有在職工程師都已送出（各組別填寫完成率皆達 100%），
    # 如果是，自動寄送彙整報表通知信給管理者（內含依專案分類的當週 Excel）。
    # 這裡刻意放在 log_action / 回傳成功訊息「之前」執行，但內部已用 try/except
    # 包起來，就算寄信失敗也不會影響週報送出本身這個主要操作。
    check_and_notify_all_groups_complete(year, week)

    log_action('submit', 'WeeklyReport', report.id, '送出並鎖定週報（已自動延續下週計劃至下週）')
    return jsonify({'success': True, 'message': '週報已送出並鎖定，下週計劃已自動帶入下一週的已執行事項'})


@report_bp.route('/report/unlock/<int:report_id>', methods=['POST'])
@login_required
def unlock(report_id):
    """
    解鎖週報供本人修改。管理者可以解鎖任何人的週報；部門主管也開放這個功能，
    但只能解鎖自己部門內的人（含一般員工、組別主管、部門主管自己）的週報，
    不能解鎖其他部門的人。
    """
    report = WeeklyReport.query.get_or_404(report_id)
    if current_user.role == 'admin':
        pass
    elif current_user.role == 'dept_manager':
        if (report.engineer.department or '') != (current_user.department or ''):
            # 安全檢查：部門主管直接改網址想解鎖別部門的人也要擋下來，不能只靠
            # 頁面只顯示自己部門的人來擋（畫面只是 UI 上的方便，不能當作真正的權限控管）
            abort(403)
    else:
        abort(403)
    report.locked = False
    report.status = '草稿'
    db.session.commit()
    log_action('unlock', 'WeeklyReport', report.id,
               f'{current_user.name} 解鎖 {report.engineer.name} 的週報')
    flash(f'已解鎖 {report.engineer.name} 的 {report.year}年第{report.week_number}週週報', 'success')
    return redirect(request.referrer or url_for('dashboard.index'))


@report_bp.route('/report/attachment/upload', methods=['POST'])
@login_required
@not_viewer_required
def upload_attachment():
    """上傳附件截圖，以 Base64 存入 DB，回傳附件 ID 供前端關聯"""
    item_id = request.form.get('item_id', type=int)
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'message': '未選擇檔案'}), 400

    import base64
    data = file.read()
    if len(data) > 8 * 1024 * 1024:
        return jsonify({'success': False, 'message': '檔案過大（上限 8MB）'}), 400

    b64 = base64.b64encode(data).decode('utf-8')
    attachment = Attachment(
        item_id=item_id, filename=file.filename,
        mime_type=file.mimetype or 'application/octet-stream',
        data_base64=b64
    )
    db.session.add(attachment)
    db.session.commit()
    return jsonify({'success': True, 'attachment_id': attachment.id, 'filename': attachment.filename})


@report_bp.route('/report/project/quick_add', methods=['POST'])
@login_required
@not_viewer_required
def quick_add_project():
    """工程師填寫時，若下拉選單找不到專案，可即時新增（Admin後續可整併重複項目）"""
    payload = request.get_json(force=True)
    code = (payload.get('code') or '').strip()
    full_name = (payload.get('full_name') or code).strip()
    if not code:
        return jsonify({'success': False, 'message': '專案代號不可為空'}), 400

    existing = Project.query.filter_by(code=code).first()
    if existing:
        return jsonify({'success': True, 'project_id': existing.id, 'code': existing.code})

    project = Project(code=code, full_name=full_name, status='進行中',
                       note='由工程師於填寫週報時即時新增')
    db.session.add(project)
    db.session.commit()
    log_action('create', 'Project', project.id, f'{current_user.name} 即時新增專案 {code}')
    return jsonify({'success': True, 'project_id': project.id, 'code': project.code})


NOTE_STATUS_OPTIONS = ('待討論', '討論中', '已處理')


@report_bp.route('/report/note/add', methods=['POST'])
@login_required
def add_note():
    """
    新增「附註／需要討論」留言。
    工程師只能在自己的週報留言；管理者可以在任何工程師的週報留言（回覆）；
    唯讀角色不可留言。留言可以指定狀態（待討論／討論中／已處理），預設為「待討論」。
    """
    if current_user.role in ('viewer', 'hr_manager'):
        abort(403)

    payload = request.get_json(force=True)
    report_id = payload.get('report_id')
    content = (payload.get('content') or '').strip()
    status = payload.get('status') or '待討論'
    if status not in NOTE_STATUS_OPTIONS:
        status = '待討論'
    if not content:
        return jsonify({'success': False, 'message': '留言內容不可為空'}), 400

    report = WeeklyReport.query.get_or_404(report_id)
    if current_user.role == 'engineer' and report.engineer_id != current_user.id:
        abort(403)

    note = ReportNote(
        report_id=report.id, author_id=current_user.id,
        author_name=current_user.name, author_role=current_user.role,
        content=content, status=status,
    )
    db.session.add(note)
    db.session.commit()
    log_action('add_note', 'WeeklyReport', report.id, f'{current_user.name} 新增留言（狀態：{status}）')

    return jsonify({
        'success': True,
        'note': {
            'id': note.id,
            'author_name': note.author_name,
            'author_role': note.author_role,
            'content': note.content,
            'status': note.status,
            'created_at': note.created_at.strftime('%Y-%m-%d %H:%M'),
        }
    })


@report_bp.route('/report/note/<int:note_id>/status', methods=['POST'])
@login_required
def update_note_status(note_id):
    """更新既有留言的狀態（待討論／討論中／已處理），權限規則與新增留言相同"""
    if current_user.role in ('viewer', 'hr_manager'):
        abort(403)

    payload = request.get_json(force=True)
    status = payload.get('status')
    if status not in NOTE_STATUS_OPTIONS:
        return jsonify({'success': False, 'message': '無效的狀態值'}), 400

    note = ReportNote.query.get_or_404(note_id)
    report = note.report
    if current_user.role == 'engineer' and report.engineer_id != current_user.id:
        abort(403)

    note.status = status
    db.session.commit()
    log_action('update_note_status', 'WeeklyReport', report.id,
               f'{current_user.name} 將留言 #{note.id} 狀態改為「{status}」')

    return jsonify({'success': True, 'status': status})
