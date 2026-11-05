# -*- coding: utf-8 -*-
"""
routes/diary_view.py - 日誌檢視（部門主管／組別主管檢視轄下人員的日誌紀錄）
排版跟功能設計參考 routes/reports_view.py（週報檢視），但依「填寫日誌」的
內容特性優化呈現——日誌本身就是「客戶／國家／聯絡方式／內容摘要」這種結構化
的欄位，不像週報是自由文字，所以這裡用平鋪的表格列出每一筆紀錄，而不是
比照週報檢視那樣用卡片把每個人的內容包起來。

只開放給部門主管、組別主管使用：管理者要看整體狀況已經有「人員帳號管理」
「考核」等其他頁面；一般員工只能看自己的（填寫日誌頁面本身就看得到）；
人資主管的權限範圍不包含這個。
"""
from collections import defaultdict
from flask import Blueprint, render_template, request, abort, send_file
from flask_login import login_required, current_user
from models import Engineer, Group, Project, DailyLog
from utils.report_utils import (
    current_year_week, week_range_list, week_date_range,
    REPORT_WRITING_ROLES, get_department_project_ids, get_group_project_ids,
    get_diary_fill_status_summary,
)
from utils.excel_utils import export_diary_logs
from utils.decorators import log_action

diary_view_bp = Blueprint('diary_view', __name__)


@diary_view_bp.route('/diary/view')
@login_required
def index():
    if current_user.role not in ('dept_manager', 'group_manager'):
        abort(403)

    active_tab = request.args.get('tab', 'employee')
    if active_tab not in ('employee', 'project', 'export'):
        active_tab = 'employee'

    is_dept_manager = current_user.role == 'dept_manager'
    is_group_manager = current_user.role == 'group_manager'
    default_year, default_week = current_year_week()

    # ----- 依員工檢視分頁 -----
    groups_query = Group.query
    if is_dept_manager:
        groups_query = groups_query.filter_by(department=current_user.department or '__none__')
    groups = groups_query.order_by(Group.sort_order, Group.id).all()

    department = current_user.department or '' if is_dept_manager else ''
    group_id = current_user.group_id or -1 if is_group_manager else request.args.get('group_id', type=int)
    engineer_id = request.args.get('engineer_id', type=int)

    emp_year_start = request.args.get('emp_year_start', type=int) or default_year
    emp_week_start = request.args.get('emp_week_start', type=int) or default_week
    emp_year_end = request.args.get('emp_year_end', type=int) or default_year
    emp_week_end = request.args.get('emp_week_end', type=int) or default_week
    emp_week_keys = week_range_list(emp_year_start, emp_week_start, emp_year_end, emp_week_end)
    emp_is_single_week = len(emp_week_keys) == 1
    emp_range_start = week_date_range(emp_year_start, emp_week_start)[0]
    emp_range_end = week_date_range(emp_year_end, emp_week_end)[1]
    # 起訖顛倒時 week_range_list 會自動退回只查起始那一週，這裡讓日期範圍
    # 也跟著一致，避免日期區間跟「實際查到的週次清單」對不上
    if emp_range_start > emp_range_end:
        emp_range_end = week_date_range(emp_year_start, emp_week_start)[1]

    engineers_query = Engineer.query
    if department:
        engineers_query = engineers_query.filter_by(department=department)
    if group_id:
        engineers_query = engineers_query.filter_by(group_id=group_id)
    engineers = engineers_query.order_by(Engineer.sort_order, Engineer.id).all()

    selected_engineer = None
    emp_log_rows = []

    if engineer_id:
        selected_engineer = Engineer.query.get_or_404(engineer_id)
        if is_dept_manager and (selected_engineer.department or '') != (current_user.department or ''):
            abort(403)
        if is_group_manager and selected_engineer.group_id != current_user.group_id:
            abort(403)

        logs = (
            DailyLog.query
            .filter(DailyLog.engineer_id == engineer_id,
                    DailyLog.log_date >= emp_range_start, DailyLog.log_date <= emp_range_end)
            .order_by(DailyLog.log_date.desc(), DailyLog.sort_order)
            .all()
        )
        for log in logs:
            emp_log_rows.append({'log': log, 'engineer': selected_engineer})
    else:
        active_engineers = [e for e in engineers if e.status == 'active' and e.role in REPORT_WRITING_ROLES]
        active_ids = [e.id for e in active_engineers]
        engineers_by_id = {e.id: e for e in active_engineers}
        if active_ids:
            logs = (
                DailyLog.query
                .filter(DailyLog.engineer_id.in_(active_ids),
                        DailyLog.log_date >= emp_range_start, DailyLog.log_date <= emp_range_end)
                .order_by(DailyLog.log_date.desc(), DailyLog.sort_order)
                .all()
            )
            for log in logs:
                eng = engineers_by_id.get(log.engineer_id)
                if eng:
                    emp_log_rows.append({'log': log, 'engineer': eng})

    # 未填寫名單：只在單一週次查詢時顯示（比照週報檢視的邏輯），跨週區間
    # 查詢時「這週沒填」的定義會很模糊，沒有意義
    tracking_summary = None
    if emp_is_single_week:
        tracking_summary = get_diary_fill_status_summary(
            emp_year_start, emp_week_start, department=department or None, group_id=group_id
        )

    # ----- 依專案/客戶檢視分頁 -----
    projects = Project.query.order_by(Project.code).all()
    if is_dept_manager:
        allowed_project_ids = get_department_project_ids(current_user.department or '')
        projects = [p for p in projects if p.id in allowed_project_ids]
    elif is_group_manager:
        allowed_project_ids = get_group_project_ids(current_user.group_id)
        projects = [p for p in projects if p.id in allowed_project_ids]

    project_id = request.args.get('project_id', type=int)

    proj_year_start = request.args.get('proj_year_start', type=int) or default_year
    proj_week_start = request.args.get('proj_week_start', type=int) or default_week
    proj_year_end = request.args.get('proj_year_end', type=int) or default_year
    proj_week_end = request.args.get('proj_week_end', type=int) or default_week
    proj_week_keys = week_range_list(proj_year_start, proj_week_start, proj_year_end, proj_week_end)
    proj_is_single_week = len(proj_week_keys) == 1
    proj_range_start = week_date_range(proj_year_start, proj_week_start)[0]
    proj_range_end = week_date_range(proj_year_end, proj_week_end)[1]
    if proj_range_start > proj_range_end:
        proj_range_end = week_date_range(proj_year_start, proj_week_start)[1]

    # 這個範圍內、部門主管/組別主管管轄範圍內的員工 id（依專案檢視要排除
    # 其他部門/組別的人，即使他們剛好也去拜訪過同一個客戶）
    scope_query = Engineer.query
    if is_dept_manager:
        scope_query = scope_query.filter_by(department=current_user.department or '__none__')
    elif is_group_manager:
        scope_query = scope_query.filter_by(group_id=current_user.group_id or -1)
    scope_engineer_ids = {e.id for e in scope_query.all()}

    selected_project = None
    proj_log_rows = []
    proj_preview_groups = []

    if project_id:
        selected_project = Project.query.get_or_404(project_id)
        if (is_dept_manager or is_group_manager) and project_id not in {p.id for p in projects}:
            abort(403)

        logs = (
            DailyLog.query
            .filter(DailyLog.project_id == project_id,
                    DailyLog.engineer_id.in_(scope_engineer_ids) if scope_engineer_ids else False,
                    DailyLog.log_date >= proj_range_start, DailyLog.log_date <= proj_range_end)
            .order_by(DailyLog.log_date.desc(), DailyLog.sort_order)
            .all()
        )
        engineers_by_id = {e.id: e for e in Engineer.query.filter(Engineer.id.in_(scope_engineer_ids)).all()}
        for log in logs:
            eng = engineers_by_id.get(log.engineer_id)
            if eng:
                proj_log_rows.append({'log': log, 'engineer': eng})
    else:
        logs = (
            DailyLog.query
            .filter(DailyLog.project_id.isnot(None),
                    DailyLog.engineer_id.in_(scope_engineer_ids) if scope_engineer_ids else False,
                    DailyLog.log_date >= proj_range_start, DailyLog.log_date <= proj_range_end)
            .all()
        )
        logs_by_project = defaultdict(list)
        for log in logs:
            logs_by_project[log.project_id].append(log)
        engineers_by_id = {e.id: e for e in Engineer.query.filter(Engineer.id.in_(scope_engineer_ids)).all()}

        for project in projects:
            plogs = logs_by_project.get(project.id)
            if not plogs:
                continue
            involved_engineer_ids = {log.engineer_id for log in plogs}
            proj_preview_groups.append({
                'project': project,
                'engineer_count': len(involved_engineer_ids),
                'log_count': len(plogs),
                'logs': sorted(
                    [{'log': log, 'engineer': engineers_by_id.get(log.engineer_id)} for log in plogs
                     if engineers_by_id.get(log.engineer_id)],
                    key=lambda r: r['log'].log_date, reverse=True
                ),
            })
        proj_preview_groups.sort(key=lambda x: x['log_count'], reverse=True)

    # ----- 匯出報表分頁 -----
    export_year_start = request.args.get('export_year_start', type=int) or default_year
    export_week_start = request.args.get('export_week_start', type=int) or default_week
    export_year_end = request.args.get('export_year_end', type=int) or default_year
    export_week_end = request.args.get('export_week_end', type=int) or default_week

    return render_template(
        'diary_view.html',
        active_tab=active_tab,
        # 依員工檢視分頁
        groups=groups, engineers=engineers, group_id=group_id,
        department=department, selected_engineer=selected_engineer,
        emp_year_start=emp_year_start, emp_week_start=emp_week_start,
        emp_year_end=emp_year_end, emp_week_end=emp_week_end,
        emp_is_single_week=emp_is_single_week, emp_log_rows=emp_log_rows, tracking_summary=tracking_summary,
        # 依專案/客戶檢視分頁
        projects=projects, selected_project=selected_project,
        proj_year_start=proj_year_start, proj_week_start=proj_week_start,
        proj_year_end=proj_year_end, proj_week_end=proj_week_end,
        proj_is_single_week=proj_is_single_week,
        proj_log_rows=proj_log_rows, proj_preview_groups=proj_preview_groups,
        # 匯出報表分頁
        export_year_start=export_year_start, export_week_start=export_week_start,
        export_year_end=export_year_end, export_week_end=export_week_end,
    )


@diary_view_bp.route('/diary/view/export')
@login_required
def export():
    """
    匯出「日誌檢視」轄下人員在指定年度週次區間內的日誌紀錄。部門主管只能
    匯出自己部門的人，組別主管只能匯出自己組別的人——用同一套權限範圍
    邏輯，不能只靠畫面上的篩選來限制，直接呼叫這個路由也要正確擋住。
    """
    if current_user.role not in ('dept_manager', 'group_manager'):
        abort(403)

    default_year, default_week = current_year_week()
    year_start = request.args.get('export_year_start', type=int) or default_year
    week_start = request.args.get('export_week_start', type=int) or default_week
    year_end = request.args.get('export_year_end', type=int) or default_year
    week_end = request.args.get('export_week_end', type=int) or default_week

    range_start = week_date_range(year_start, week_start)[0]
    range_end = week_date_range(year_end, week_end)[1]
    if range_start > range_end:
        range_end = week_date_range(year_start, week_start)[1]

    scope_query = Engineer.query
    if current_user.role == 'dept_manager':
        scope_query = scope_query.filter_by(department=current_user.department or '__none__')
    else:
        scope_query = scope_query.filter_by(group_id=current_user.group_id or -1)
    scope_engineers = {e.id: e for e in scope_query.all()}

    if not scope_engineers:
        logs = []
    else:
        logs = (
            DailyLog.query
            .filter(DailyLog.engineer_id.in_(scope_engineers.keys()),
                    DailyLog.log_date >= range_start, DailyLog.log_date <= range_end)
            .order_by(DailyLog.log_date, DailyLog.sort_order)
            .all()
        )
    log_rows = [{'log': log, 'engineer': scope_engineers[log.engineer_id]} for log in logs]

    title = f'{year_start}W{week_start}-{year_end}W{week_end}日誌'
    buf = export_diary_logs(log_rows, title)
    log_action('export', 'DailyLog', None, f'匯出日誌檢視 {year_start}W{week_start}~{year_end}W{week_end}')
    filename = f'日誌紀錄_{year_start}W{week_start}-{year_end}W{week_end}.xlsx'
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
