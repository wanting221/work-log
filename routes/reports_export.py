# -*- coding: utf-8 -*-
"""
routes/reports_export.py - 週報匯出（整合「未填寫追蹤」+「匯出報表」）
routes/tracking.py、routes/export.py 兩個原本的獨立路由維持不變、繼續能
單獨運作；這裡的合併頁面共用同一組 year/week（兩個分頁本來就都是在看
「同一週」的狀況，共用剛好符合直覺，不需要像週報檢視那樣分開處理）。
「一鍵提醒」「匯出 Excel」「匯入 Excel」這些動作，都還是呼叫原本
tracking.py / export.py 裡對應的既有路由，這裡不重複實作。
"""
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from models import Group, Project, Engineer
from utils.report_utils import (
    current_year_week, get_fill_status_summary, get_department_project_ids,
    has_reached_tracking_start, tracking_start_datetime,
)

reports_export_bp = Blueprint('reports_export', __name__)


@reports_export_bp.route('/reports/export')
@login_required
def index():
    if current_user.role in ('engineer', 'hr_manager'):
        abort(403)

    active_tab = request.args.get('tab', 'tracking')
    if active_tab not in ('tracking', 'export'):
        active_tab = 'tracking'

    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if not year or not week:
        year, week = current_year_week()

    # ----- 未填寫追蹤分頁 -----
    department = current_user.department if current_user.role == 'dept_manager' else None
    group_id = current_user.group_id if current_user.role == 'group_manager' else None
    summary = get_fill_status_summary(year, week, department=department, group_id=group_id)
    tracking_started = has_reached_tracking_start(year, week)
    tracking_start_at = tracking_start_datetime(year, week)

    # ----- 匯出報表分頁（組別主管本來就不能匯出，這裡沿用原本頁面的限制）-----
    groups = []
    projects = []
    departments = []
    if current_user.role != 'group_manager':
        groups = Group.query.order_by(Group.sort_order, Group.id).all()
        if current_user.role == 'dept_manager':
            allowed_project_ids = get_department_project_ids(current_user.department or '')
            projects = Project.query.filter(Project.id.in_(allowed_project_ids)).order_by(
                Project.sort_order, Project.id
            ).all()
        else:
            projects = Project.query.order_by(Project.sort_order, Project.id).all()
            # 「依專案/客戶匯出」的部門下拉選單選項：只有非部門主管的角色（管理者、
            # 唯讀）才需要，因為部門主管本來就固定鎖在自己的部門，不需要選單。
            # 這裡同時涵蓋 Engineer.department 跟 Project.department 兩個來源——
            # get_department_project_ids() 判斷「這個專案算哪個部門」主要是看
            # Project.department，如果只列 Engineer 那邊出現過的部門，會漏掉
            # 「專案已經設定部門、但那個部門還沒有任何人員帳號」的情況。
            departments = sorted({
                d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
            } | {
                d[0] for d in Project.query.with_entities(Project.department).distinct().all() if d[0]
            })

    return render_template(
        'reports_export.html',
        active_tab=active_tab, year=year, week=week,
        summary=summary, groups=groups, projects=projects, departments=departments,
        tracking_started=tracking_started, tracking_start_at=tracking_start_at,
    )
