# -*- coding: utf-8 -*-
"""
routes/reports_view.py - 週報檢視（整合「依員工檢視」+「依專案/客戶檢視」）
這裡刻意不去動 routes/view_engineer.py、routes/view_project.py 這兩個原本的
獨立路由——它們保留原樣、繼續能單獨運作（避免任何既有的深層連結失效），
這裡是另外做一個「兩個分頁都在同一頁」的合併版本，核心邏輯是直接沿用
兩邊原本的做法，只是把會撞名的參數（year_start/week_start/year_end/week_end）
分別加上 emp_ / proj_ 前綴，讓兩個分頁的年度週次區間可以各自獨立設定、
不會互相干擾。
"""
from collections import defaultdict
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload, joinedload, contains_eager
from extensions import db
from models import Engineer, Group, Project, WeeklyReport, WeeklyReportItem
from utils.report_utils import (
    current_year_week, week_range_list, REPORT_WRITING_ROLES,
    get_department_project_ids, get_group_project_ids,
    get_fill_status_summary, has_reached_tracking_start, tracking_start_datetime,
)

reports_view_bp = Blueprint('reports_view', __name__)


@reports_view_bp.route('/reports/view')
@login_required
def index():
    if current_user.role in ('engineer', 'hr_manager'):
        abort(403)

    active_tab = request.args.get('tab', 'employee')
    if active_tab not in ('employee', 'project', 'export'):
        active_tab = 'employee'

    is_dept_manager = current_user.role == 'dept_manager'
    is_group_manager = current_user.role == 'group_manager'

    # ----- 依員工檢視分頁 -----
    groups_query = Group.query
    if is_dept_manager:
        groups_query = groups_query.filter_by(department=current_user.department or '__none__')
    groups = groups_query.order_by(Group.sort_order, Group.id).all()
    departments = sorted({
        d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
    })
    department = request.args.get('department', '').strip()
    if is_dept_manager or is_group_manager:
        department = current_user.department or ''
    group_id = request.args.get('group_id', type=int)
    if is_group_manager:
        group_id = current_user.group_id or -1
    engineer_id = request.args.get('engineer_id', type=int)

    default_year, default_week = current_year_week()
    emp_year_start = request.args.get('emp_year_start', type=int) or default_year
    emp_week_start = request.args.get('emp_week_start', type=int) or default_week
    emp_year_end = request.args.get('emp_year_end', type=int) or default_year
    emp_week_end = request.args.get('emp_week_end', type=int) or default_week
    emp_week_keys = week_range_list(emp_year_start, emp_week_start, emp_year_end, emp_week_end)
    emp_is_single_week = len(emp_week_keys) == 1

    # 未填寫追蹤：原本是「週報管理」頁面的獨立分頁，現在整合顯示在「依員工
    # 檢視」分頁的「全部預覽」區塊最下方——只有檢視單一週次時才需要算
    # （全部預覽本身也只有單一週次才會顯示），沿用跟全部預覽同一週次的
    # 篩選範圍，畫面上兩個區塊看到的才會是同一週的狀況，不會不一致
    tracking_summary = None
    tracking_started = True
    tracking_start_at = None
    if emp_is_single_week:
        tracking_department = current_user.department if is_dept_manager else None
        tracking_group_id = current_user.group_id if is_group_manager else None
        tracking_summary = get_fill_status_summary(
            emp_year_start, emp_week_start, department=tracking_department, group_id=tracking_group_id
        )
        tracking_started = has_reached_tracking_start(emp_year_start, emp_week_start)
        tracking_start_at = tracking_start_datetime(emp_year_start, emp_week_start)

    engineers_query = Engineer.query
    if department:
        engineers_query = engineers_query.filter_by(department=department)
    if group_id:
        engineers_query = engineers_query.filter_by(group_id=group_id)
    engineers = engineers_query.order_by(Engineer.sort_order, Engineer.id).all()

    selected_engineer = None
    range_reports = []
    history_reports = []
    emp_preview_rows = []

    if engineer_id:
        selected_engineer = Engineer.query.get_or_404(engineer_id)
        if is_dept_manager and (selected_engineer.department or '') != (current_user.department or ''):
            abort(403)
        if is_group_manager and selected_engineer.group_id != current_user.group_id:
            abort(403)

        years_in_range = {y for y, w in emp_week_keys}
        candidate_reports = (
            WeeklyReport.query
            .options(selectinload(WeeklyReport.items).joinedload(WeeklyReportItem.project),
                     selectinload(WeeklyReport.notes))
            .filter(WeeklyReport.engineer_id == engineer_id,
                    WeeklyReport.year.in_(years_in_range))
            .all()
        )
        emp_week_key_set = set(emp_week_keys)
        range_reports = [r for r in candidate_reports if (r.year, r.week_number) in emp_week_key_set]
        range_reports.sort(key=lambda r: (r.year, r.week_number), reverse=True)

        history_reports = (WeeklyReport.query.filter_by(engineer_id=engineer_id)
                            .order_by(WeeklyReport.year.desc(), WeeklyReport.week_number.desc())
                            .limit(20).all())
    else:
        active_engineers = [e for e in engineers if e.status == 'active' and e.role in REPORT_WRITING_ROLES]
        active_ids = [e.id for e in active_engineers]
        engineers_by_id = {e.id: e for e in active_engineers}

        reports = []
        if active_ids:
            years_in_range = {y for y, w in emp_week_keys}
            candidate_reports = (
                WeeklyReport.query
                .options(selectinload(WeeklyReport.items).joinedload(WeeklyReportItem.project),
                         selectinload(WeeklyReport.notes))
                .filter(WeeklyReport.engineer_id.in_(active_ids),
                        WeeklyReport.year.in_(years_in_range))
                .all()
            )
            emp_week_key_set = set(emp_week_keys)
            reports = [r for r in candidate_reports if (r.year, r.week_number) in emp_week_key_set]

        if emp_is_single_week:
            reports_by_engineer = {r.engineer_id: r for r in reports}
            for eng in active_engineers:
                r = reports_by_engineer.get(eng.id)
                last_week_items = [i for i in r.items if i.item_type == 'last_week'] if r else []
                this_week_items = [i for i in r.items if i.item_type == 'this_week'] if r else []
                emp_preview_rows.append({
                    'engineer': eng, 'report': r, 'year': emp_year_start, 'week': emp_week_start,
                    'last_week_items': last_week_items, 'this_week_items': this_week_items,
                    'has_notes': bool(r.notes) if r else False,
                })
        else:
            reports.sort(key=lambda r: (-r.year, -r.week_number,
                                         engineers_by_id[r.engineer_id].sort_order if r.engineer_id in engineers_by_id else 0))
            for r in reports:
                eng = engineers_by_id.get(r.engineer_id)
                if not eng:
                    continue
                last_week_items = [i for i in r.items if i.item_type == 'last_week']
                this_week_items = [i for i in r.items if i.item_type == 'this_week']
                emp_preview_rows.append({
                    'engineer': eng, 'report': r, 'year': r.year, 'week': r.week_number,
                    'last_week_items': last_week_items, 'this_week_items': this_week_items,
                    'has_notes': bool(r.notes),
                })

    # ----- 依專案/客戶檢視分頁 -----
    projects = Project.query.order_by(Project.code).all()
    if is_dept_manager:
        dept = current_user.department or ''
        allowed_project_ids = get_department_project_ids(dept)
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
    proj_week_key_set = set(proj_week_keys)
    proj_years_in_range = {y for y, w in proj_week_keys}
    proj_is_single_week = len(proj_week_keys) == 1

    selected_project = None
    entries = []
    total_weeks_involved = 0
    proj_preview_rows = []

    if project_id:
        selected_project = Project.query.get_or_404(project_id)
        if (is_dept_manager or is_group_manager) and project_id not in {p.id for p in projects}:
            abort(403)

        candidate_items = (
            WeeklyReportItem.query
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .options(contains_eager(WeeklyReportItem.report).joinedload(WeeklyReport.engineer))
            .filter(WeeklyReportItem.project_id == project_id,
                    WeeklyReport.year.in_(proj_years_in_range))
            .all()
        )
        items = [i for i in candidate_items if (i.report.year, i.report.week_number) in proj_week_key_set]

        by_engineer_week = {}
        for item in items:
            eng = item.report.engineer
            key = (eng.id, item.report.year, item.report.week_number)
            by_engineer_week.setdefault(key, {
                'engineer': eng, 'year': item.report.year, 'week': item.report.week_number,
                'last_week': [], 'this_week': [],
            })
            if item.item_type == 'last_week':
                by_engineer_week[key]['last_week'].append(item)
            else:
                by_engineer_week[key]['this_week'].append(item)
        entries = sorted(by_engineer_week.values(), key=lambda e: (-e['year'], -e['week'], e['engineer'].sort_order))

        total_weeks_involved = (
            db.session.query(func.count(func.distinct(
                WeeklyReport.year * 100 + WeeklyReport.week_number
            )))
            .join(WeeklyReportItem, WeeklyReportItem.report_id == WeeklyReport.id)
            .filter(WeeklyReportItem.project_id == project_id)
            .scalar()
        ) or 0
    else:
        candidate_items = (
            WeeklyReportItem.query
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .options(contains_eager(WeeklyReportItem.report).joinedload(WeeklyReport.engineer))
            .filter(WeeklyReport.year.in_(proj_years_in_range), WeeklyReportItem.project_id.isnot(None))
            .all()
        )
        all_items = [i for i in candidate_items if (i.report.year, i.report.week_number) in proj_week_key_set]

        items_by_project = defaultdict(list)
        for item in all_items:
            items_by_project[item.project_id].append(item)

        for project in projects:
            items = items_by_project.get(project.id)
            if not items:
                continue
            by_engineer = {}
            for item in items:
                eng = item.report.engineer
                by_engineer.setdefault(eng.id, {'engineer': eng, 'last_week': [], 'this_week': []})
                if item.item_type == 'last_week':
                    by_engineer[eng.id]['last_week'].append(item)
                else:
                    by_engineer[eng.id]['this_week'].append(item)
            proj_preview_rows.append({
                'project': project,
                'engineer_count': len(by_engineer),
                'entries': list(by_engineer.values()),
            })
        proj_preview_rows.sort(key=lambda x: x['engineer_count'], reverse=True)

    # ----- 匯出報表分頁（原本是「週報管理」頁面的獨立分頁，現在整合成第三個
    # 分頁；組別主管本來就不能匯出，這裡沿用原本頁面的限制）-----
    export_year = request.args.get('export_year', type=int) or default_year
    export_week = request.args.get('export_week', type=int) or default_week
    export_groups = []
    export_projects = []
    export_departments = []
    if current_user.role != 'group_manager':
        export_groups = Group.query.order_by(Group.sort_order, Group.id).all()
        if is_dept_manager:
            allowed_project_ids = get_department_project_ids(current_user.department or '')
            export_projects = Project.query.filter(Project.id.in_(allowed_project_ids)).order_by(
                Project.sort_order, Project.id
            ).all()
        else:
            export_projects = Project.query.order_by(Project.sort_order, Project.id).all()
            # 部門下拉選單同時涵蓋 Engineer.department 跟 Project.department 兩個
            # 來源，避免漏掉「專案已經設定部門、但那個部門還沒有任何人員帳號」的情況
            export_departments = sorted({
                d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
            } | {
                d[0] for d in Project.query.with_entities(Project.department).distinct().all() if d[0]
            })

    return render_template(
        'reports_view.html',
        active_tab=active_tab,
        # 員工分頁
        groups=groups, engineers=engineers, group_id=group_id,
        departments=departments, department=department,
        selected_engineer=selected_engineer, range_reports=range_reports,
        history_reports=history_reports,
        emp_year_start=emp_year_start, emp_week_start=emp_week_start,
        emp_year_end=emp_year_end, emp_week_end=emp_week_end,
        emp_is_single_week=emp_is_single_week,
        emp_preview_rows=emp_preview_rows,
        tracking_summary=tracking_summary, tracking_started=tracking_started,
        tracking_start_at=tracking_start_at,
        # 專案分頁
        projects=projects, selected_project=selected_project, entries=entries,
        proj_year_start=proj_year_start, proj_week_start=proj_week_start,
        proj_year_end=proj_year_end, proj_week_end=proj_week_end,
        proj_is_single_week=proj_is_single_week,
        total_weeks_involved=total_weeks_involved,
        proj_preview_rows=proj_preview_rows,
        # 匯出報表分頁
        export_year=export_year, export_week=export_week,
        export_groups=export_groups, export_projects=export_projects,
        export_departments=export_departments,
    )
