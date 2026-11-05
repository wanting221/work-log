# -*- coding: utf-8 -*-
"""
routes/view_project.py - 管理者視角二：依客戶／專案檢視
自動彙整區間內有處理此專案的工程師清單，並顯示涉入人數/累積週數作為熱度指標。
支援「年度/週次區間」查詢：起訖只選同一週時，行為等同原本的單週查詢。
"""
from collections import defaultdict
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import contains_eager, joinedload
from extensions import db
from models import Project, WeeklyReportItem, WeeklyReport, Engineer
from utils.report_utils import current_year_week, get_department_project_ids, get_group_project_ids, week_range_list

view_project_bp = Blueprint('view_project', __name__)


@view_project_bp.route('/view/project')
@login_required
def index():
    if current_user.role == 'engineer':
        abort(403)

    is_dept_manager = current_user.role == 'dept_manager'
    is_group_manager = current_user.role == 'group_manager'

    projects = Project.query.order_by(Project.code).all()

    if is_dept_manager:
        dept = current_user.department or ''
        allowed_project_ids = get_department_project_ids(dept)
        projects = [p for p in projects if p.id in allowed_project_ids]
    elif is_group_manager:
        allowed_project_ids = get_group_project_ids(current_user.group_id)
        projects = [p for p in projects if p.id in allowed_project_ids]

    project_id = request.args.get('project_id', type=int)

    default_year, default_week = current_year_week()
    year_start = request.args.get('year_start', type=int) or default_year
    week_start = request.args.get('week_start', type=int) or default_week
    year_end = request.args.get('year_end', type=int) or default_year
    week_end = request.args.get('week_end', type=int) or default_week
    week_keys = week_range_list(year_start, week_start, year_end, week_end)
    week_key_set = set(week_keys)
    years_in_range = {y for y, w in week_keys}
    is_single_week = len(week_keys) == 1

    selected_project = None
    entries = []
    total_weeks_involved = 0
    preview_rows = []

    if project_id:
        selected_project = Project.query.get_or_404(project_id)
        # 安全檢查：部門主管/組別主管直接在網址上指定其他部門或組別的 project_id
        # 也要擋下來，不能只靠下拉選單只列出自己範圍的專案來擋（下拉選單只是
        # UI 上的方便，不能當作真正的權限控管）
        if (is_dept_manager or is_group_manager) and project_id not in {p.id for p in projects}:
            abort(403)

        # 用 contains_eager + joinedload 一次把 report 跟 report.engineer 都撈出來，
        # 避免下面迴圈裡每存取一次 item.report.engineer 就觸發一次額外查詢
        candidate_items = (
            WeeklyReportItem.query
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .options(contains_eager(WeeklyReportItem.report).joinedload(WeeklyReport.engineer))
            .filter(WeeklyReportItem.project_id == project_id,
                    WeeklyReport.year.in_(years_in_range))
            .all()
        )
        items = [i for i in candidate_items if (i.report.year, i.report.week_number) in week_key_set]

        # 依「工程師 + 週次」分組——區間橫跨多週時，同一人不同週的內容分開列出，
        # 避免不同週的工作內容混在一起、失去週次脈絡；單週查詢時自然只有一組
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
        # 排序：越新的一週排在越上面；同一週內再依員工的顯示順序 (sort_order) 排列
        entries = sorted(by_engineer_week.values(), key=lambda e: (-e['year'], -e['week'], e['engineer'].sort_order))

        # 累積週數指標：此專案有史以來曾出現在多少個不同週次的週報中（不限這次查詢的區間）
        total_weeks_involved = (
            db.session.query(func.count(func.distinct(
                WeeklyReport.year * 100 + WeeklyReport.week_number
            )))
            .join(WeeklyReportItem, WeeklyReportItem.report_id == WeeklyReport.id)
            .filter(WeeklyReportItem.project_id == project_id)
            .scalar()
        ) or 0
    else:
        # 未指定特定專案：顯示「區間全部預覽」——列出區間內所有有人處理的專案，
        # 各自涉入的工程師與精要內容彙整成一份，不需要一個一個點進去看。
        candidate_items = (
            WeeklyReportItem.query
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .options(contains_eager(WeeklyReportItem.report).joinedload(WeeklyReport.engineer))
            .filter(WeeklyReport.year.in_(years_in_range), WeeklyReportItem.project_id.isnot(None))
            .all()
        )
        all_items = [i for i in candidate_items if (i.report.year, i.report.week_number) in week_key_set]

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
            preview_rows.append({
                'project': project,
                'engineer_count': len(by_engineer),
                'entries': list(by_engineer.values()),
            })
        preview_rows.sort(key=lambda x: x['engineer_count'], reverse=True)

    return render_template(
        'view_project.html',
        projects=projects, selected_project=selected_project,
        entries=entries,
        year_start=year_start, week_start=week_start, year_end=year_end, week_end=week_end,
        is_single_week=is_single_week,
        total_weeks_involved=total_weeks_involved,
        preview_rows=preview_rows,
    )
