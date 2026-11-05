# -*- coding: utf-8 -*-
"""
routes/dashboard.py - 管理者首頁 Dashboard
顯示填寫狀態統計、各組別完成率、專案熱度排行、卡關風險提醒
"""
from collections import defaultdict
from datetime import timedelta
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import contains_eager
from extensions import db
from models import Group, Project, WeeklyReportItem, WeeklyReport, Engineer, ReviewPeriod, DailyLog
from utils.report_utils import (
    current_year_week, get_fill_status_summary, get_diary_fill_status_summary,
    detect_stuck_items, REPORT_WRITING_ROLES,
)
from flask import current_app

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def index():
    if current_user.role == 'engineer':
        # 工程師登入後直接導向填寫頁面
        from flask import redirect, url_for
        return redirect(url_for('report.fill'))

    if current_user.role == 'hr_manager':
        # 人資主管的管理總覽只看得到考核期間資訊，不涉及週報填寫狀況，
        # 這裡直接跳過所有週報相關的統計運算（填寫狀態、各組別完成率、
        # 專案熱度排行、卡關風險），效能上也不用白白算一堆不會顯示的資料
        review_periods = ReviewPeriod.query.order_by(
            ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc()
        ).all()
        year, week = current_year_week()
        return render_template(
            'dashboard.html',
            year=year, week=week, summary=None,
            group_stats=[], project_ranking=[],
            risky_items=[], review_periods=review_periods,
            is_diary_mode=False,
        )

    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if not year or not week:
        year, week = current_year_week()

    is_dept_manager = current_user.role == 'dept_manager'
    is_group_manager = current_user.role == 'group_manager'
    department = current_user.department if is_dept_manager else None
    group_id = current_user.group_id if is_group_manager else None

    # 項目5、6：部門主管／組別主管自己的「功能」設定若是「日誌」，管理總覽
    # 改用日誌相關的統計資料呈現；設為「週報」或「關閉」則維持原本週報相關
    # 的統計（管理者、唯讀、人資主管不受這個切換影響，因為「功能」這個
    # 概念本來就只套用在部門主管／組別主管身上）
    is_diary_mode = (
        (is_dept_manager or is_group_manager)
        and current_user.diary_enabled and not current_user.weekly_report_enabled
    )

    if is_diary_mode:
        summary = get_diary_fill_status_summary(year, week, department=department, group_id=group_id)
    else:
        summary = get_fill_status_summary(year, week, department=department, group_id=group_id)
    submitted_ids = {e.id for e in summary['submitted']}

    # 各組別填寫完成率：一次查出「各組別在職人數」，避免逐組觸發 lazy load 查詢
    groups = Group.query.order_by(Group.sort_order, Group.id).all()
    if is_group_manager:
        # 組別主管只管一個組別，直接篩掉其他組別，不用跑後面整段組別統計迴圈
        groups = [g for g in groups if g.id == group_id]
    active_count_query = db.session.query(Engineer.group_id, func.count(Engineer.id)).filter(
        Engineer.status == 'active',
        Engineer.diary_enabled.is_(True) if is_diary_mode else Engineer.role.in_(REPORT_WRITING_ROLES)
    )
    if is_dept_manager:
        # 部門主管的組別完成率只計算「自己部門」在該組別的人數，不是整個組別
        # （同一個組別底下可能同時有其他部門的人，不應該算進部門主管看到的統計裡）
        active_count_query = active_count_query.filter(Engineer.department == department)
    if is_group_manager:
        active_count_query = active_count_query.filter(Engineer.group_id == group_id)
    active_counts_by_group = dict(active_count_query.group_by(Engineer.group_id).all())

    submitted_counts_by_group = {}
    if submitted_ids:
        submitted_count_query = db.session.query(Engineer.group_id, func.count(Engineer.id)).filter(
            Engineer.status == 'active',
            Engineer.diary_enabled.is_(True) if is_diary_mode else Engineer.role.in_(REPORT_WRITING_ROLES),
            Engineer.id.in_(submitted_ids)
        )
        if is_dept_manager:
            submitted_count_query = submitted_count_query.filter(Engineer.department == department)
        if is_group_manager:
            submitted_count_query = submitted_count_query.filter(Engineer.group_id == group_id)
        submitted_counts_by_group = dict(submitted_count_query.group_by(Engineer.group_id).all())

    group_stats = []
    for g in groups:
        total = active_counts_by_group.get(g.id, 0)
        if not total:
            continue
        submitted_count = submitted_counts_by_group.get(g.id, 0)
        rate = round(submitted_count / total * 100, 1)
        group_stats.append({'group': g, 'total': total, 'submitted': submitted_count, 'rate': rate})

    # 本週涉入專案數量排行
    project_engineer_map = defaultdict(set)
    if is_diary_mode:
        # 日誌模式：改用這週（週一到週五）的 DailyLog 紀錄，看哪些專案有
        # 客戶拜訪紀錄，一樣是「涉入的員工人數」排序
        from datetime import date as date_cls
        monday = date_cls.fromisocalendar(year, week, 1)
        friday = monday + timedelta(days=4)
        logs_query = DailyLog.query.filter(
            DailyLog.log_date >= monday, DailyLog.log_date <= friday, DailyLog.project_id.isnot(None)
        )
        if is_dept_manager:
            logs_query = logs_query.join(Engineer, DailyLog.engineer_id == Engineer.id).filter(
                Engineer.department == department
            )
        if is_group_manager:
            logs_query = logs_query.join(Engineer, DailyLog.engineer_id == Engineer.id).filter(
                Engineer.group_id == group_id
            )
        for log in logs_query.all():
            project_engineer_map[log.project_id].add(log.engineer_id)
    else:
        # 用 contains_eager 讓已經 JOIN 進來的 WeeklyReport 資料直接被 ORM 重複使用，
        # 避免後面存取 item.report 時，每一筆都再觸發一次額外查詢（N+1 問題）
        items_query = (
            WeeklyReportItem.query
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .options(contains_eager(WeeklyReportItem.report))
            .filter(WeeklyReport.year == year, WeeklyReport.week_number == week)
        )
        if is_dept_manager:
            # 部門主管的專案熱度排行只計算自己部門的人參與的項目
            items_query = items_query.join(Engineer, WeeklyReport.engineer_id == Engineer.id).filter(
                Engineer.department == department
            )
        if is_group_manager:
            # 組別主管的專案熱度排行只計算自己組別的人參與的項目
            items_query = items_query.join(Engineer, WeeklyReport.engineer_id == Engineer.id).filter(
                Engineer.group_id == group_id
            )
        items = items_query.all()
        for item in items:
            if item.project_id:
                project_engineer_map[item.project_id].add(item.report.engineer_id)

    # 一次把所有相關專案查出來，取代原本「每個專案各查一次」的寫法
    projects_by_id = {}
    if project_engineer_map:
        projects_by_id = {
            p.id: p for p in Project.query.filter(Project.id.in_(project_engineer_map.keys())).all()
        }

    project_ranking = []
    for pid, eng_ids in project_engineer_map.items():
        project = projects_by_id.get(pid)
        if project:
            project_ranking.append({'project': project, 'engineer_count': len(eng_ids)})
    project_ranking.sort(key=lambda x: x['engineer_count'], reverse=True)

    # 卡關風險清單：這個概念是針對週報內容的關鍵字偵測，日誌模式不適用，
    # 直接跳過不計算
    if is_diary_mode:
        risky_items = []
    else:
        risky_items = detect_stuck_items(
            current_app.config['STUCK_KEYWORDS'],
            current_app.config['STUCK_WEEKS_THRESHOLD']
        )
        if is_dept_manager:
            # 部門主管只看自己部門工程師的卡關項目
            risky_items = [
                r for r in risky_items
                if r['engineer'] and (r['engineer'].department or '') == department
            ]
        if is_group_manager:
            # 組別主管只看自己組別工程師的卡關項目
            risky_items = [
                r for r in risky_items
                if r['engineer'] and r['engineer'].group_id == group_id
            ]
        risky_items = risky_items[:10]

    # 考核期間資訊：現在績效考核系統的資料已經整合進同一個資料庫，
    # 直接查詢 ReviewPeriod 即可，不需要再透過 HTTP 呼叫外部系統。
    # 跟該系統原本「管理總覽」頁面一樣，限管理者查看。
    review_periods = None
    if current_user.role == 'admin':
        review_periods = ReviewPeriod.query.order_by(
            ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc()
        ).all()

    return render_template(
        'dashboard.html',
        year=year, week=week, summary=summary,
        group_stats=group_stats, project_ranking=project_ranking[:10],
        risky_items=risky_items, review_periods=review_periods,
        is_diary_mode=is_diary_mode,
    )
