# -*- coding: utf-8 -*-
"""
routes/view_engineer.py - 管理者視角一：依工程師檢視
支援「年度/週次區間」查詢：起訖只選同一週時，行為等同原本的單週查詢；
選了多週區間時，會列出區間內每一筆實際存在的週報（而不是列出每個組合，
避免區間一拉大、大量「未填寫」列把畫面塞爆）。
"""
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import selectinload, joinedload
from models import Engineer, Group, WeeklyReport, WeeklyReportItem
from utils.report_utils import current_year_week, week_range_list, REPORT_WRITING_ROLES

view_engineer_bp = Blueprint('view_engineer', __name__)


@view_engineer_bp.route('/view/engineer')
@login_required
def index():
    if current_user.role == 'engineer':
        abort(403)

    is_dept_manager = current_user.role == 'dept_manager'
    is_group_manager = current_user.role == 'group_manager'

    groups_query = Group.query
    # 部門主管的「組別」下拉選單只顯示自己部門底下的組別，不應該看到其他部門的組別
    if is_dept_manager:
        groups_query = groups_query.filter_by(department=current_user.department or '__none__')
    groups = groups_query.order_by(Group.sort_order, Group.id).all()
    # 部門是純文字屬性（不像組別有獨立資料表），這裡查詢目前實際用過的部門名稱，
    # 供篩選下拉選單使用，並依字母/筆畫排序
    departments = sorted({
        d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
    })
    department = request.args.get('department', '').strip()
    # 部門主管一律只能看自己部門的資料，忽略／覆蓋網址上任何想指定其他部門的參數，
    # 避免直接改網址參數就能繞過部門限制看到別部門的資料；組別主管同樣固定
    # 顯示自己所屬的部門（組別主管的部門欄位是唯讀顯示，不是可篩選的下拉選單）
    if is_dept_manager or is_group_manager:
        department = current_user.department or ''
    group_id = request.args.get('group_id', type=int)
    # 組別主管一律只能看自己組別的資料，同樣忽略／覆蓋網址上任何想指定其他組別的參數
    if is_group_manager:
        group_id = current_user.group_id or -1  # -1 確保沒設定組別時篩不到任何資料，而不是顯示全部
    engineer_id = request.args.get('engineer_id', type=int)

    default_year, default_week = current_year_week()
    year_start = request.args.get('year_start', type=int) or default_year
    week_start = request.args.get('week_start', type=int) or default_week
    year_end = request.args.get('year_end', type=int) or default_year
    week_end = request.args.get('week_end', type=int) or default_week
    week_keys = week_range_list(year_start, week_start, year_end, week_end)
    is_single_week = len(week_keys) == 1

    engineers_query = Engineer.query
    if department:
        engineers_query = engineers_query.filter_by(department=department)
    if group_id:
        engineers_query = engineers_query.filter_by(group_id=group_id)
    # 排序比照「人員帳號管理」頁面可拖曳調整的順序 (sort_order)
    engineers = engineers_query.order_by(Engineer.sort_order, Engineer.id).all()

    selected_engineer = None
    range_reports = []
    history_reports = []
    preview_rows = []

    if engineer_id:
        selected_engineer = Engineer.query.get_or_404(engineer_id)
        # 安全檢查：部門主管/組別主管直接在網址上指定其他部門或組別員工的 engineer_id
        # 也要擋下來，不能只靠篩選下拉選單擋（下拉選單只是 UI 上的方便，不能當作
        # 真正的權限控管）
        if is_dept_manager and (selected_engineer.department or '') != (current_user.department or ''):
            abort(403)
        if is_group_manager and selected_engineer.group_id != current_user.group_id:
            abort(403)

        # 區間內（含只選一週的情況）此員工實際存在的週報，依週次由新到舊排列
        years_in_range = {y for y, w in week_keys}
        candidate_reports = (
            WeeklyReport.query
            .options(selectinload(WeeklyReport.items).joinedload(WeeklyReportItem.project),
                     selectinload(WeeklyReport.notes))
            .filter(WeeklyReport.engineer_id == engineer_id,
                    WeeklyReport.year.in_(years_in_range))
            .all()
        )
        week_key_set = set(week_keys)
        range_reports = [r for r in candidate_reports if (r.year, r.week_number) in week_key_set]
        range_reports.sort(key=lambda r: (r.year, r.week_number), reverse=True)

        history_reports = (WeeklyReport.query.filter_by(engineer_id=engineer_id)
                            .order_by(WeeklyReport.year.desc(), WeeklyReport.week_number.desc())
                            .limit(20).all())
    else:
        # 未指定特定工程師：顯示「區間全部預覽」
        active_engineers = [e for e in engineers if e.status == 'active' and e.role in REPORT_WRITING_ROLES]
        active_ids = [e.id for e in active_engineers]
        engineers_by_id = {e.id: e for e in active_engineers}

        reports = []
        if active_ids:
            years_in_range = {y for y, w in week_keys}
            candidate_reports = (
                WeeklyReport.query
                .options(selectinload(WeeklyReport.items).joinedload(WeeklyReportItem.project),
                         selectinload(WeeklyReport.notes))
                .filter(WeeklyReport.engineer_id.in_(active_ids),
                        WeeklyReport.year.in_(years_in_range))
                .all()
            )
            week_key_set = set(week_keys)
            reports = [r for r in candidate_reports if (r.year, r.week_number) in week_key_set]

        if is_single_week:
            # 單週查詢：維持原本行為——列出所有在職員工，沒填寫的人也要顯示
            # 「未填寫」，方便一眼看出誰還沒交
            reports_by_engineer = {r.engineer_id: r for r in reports}
            for eng in active_engineers:
                r = reports_by_engineer.get(eng.id)
                last_week_items = [i for i in r.items if i.item_type == 'last_week'] if r else []
                this_week_items = [i for i in r.items if i.item_type == 'this_week'] if r else []
                preview_rows.append({
                    'engineer': eng, 'report': r, 'year': year_start, 'week': week_start,
                    'last_week_items': last_week_items, 'this_week_items': this_week_items,
                    'has_notes': bool(r.notes) if r else False,
                })
        else:
            # 多週區間：只列出區間內「實際有送出/儲存週報」的紀錄，一份週報一列，
            # 不會把每個人在每一週的「未填寫」都攤出來塞滿畫面。
            # 排序：越新的一週排在越上面；同一週內再依員工的顯示順序 (sort_order) 排列
            reports.sort(key=lambda r: (-r.year, -r.week_number,
                                         engineers_by_id[r.engineer_id].sort_order if r.engineer_id in engineers_by_id else 0))
            for r in reports:
                eng = engineers_by_id.get(r.engineer_id)
                if not eng:
                    continue
                last_week_items = [i for i in r.items if i.item_type == 'last_week']
                this_week_items = [i for i in r.items if i.item_type == 'this_week']
                preview_rows.append({
                    'engineer': eng, 'report': r, 'year': r.year, 'week': r.week_number,
                    'last_week_items': last_week_items, 'this_week_items': this_week_items,
                    'has_notes': bool(r.notes),
                })

    return render_template(
        'view_engineer.html',
        groups=groups, engineers=engineers, group_id=group_id,
        departments=departments, department=department,
        selected_engineer=selected_engineer, range_reports=range_reports,
        history_reports=history_reports,
        year_start=year_start, week_start=week_start, year_end=year_end, week_end=week_end,
        is_single_week=is_single_week,
        preview_rows=preview_rows,
    )
