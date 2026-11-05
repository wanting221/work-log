# -*- coding: utf-8 -*-
"""
routes/export.py - 匯出/匯入報表
"""
from datetime import date
from flask import Blueprint, render_template, request, send_file, flash, redirect, url_for, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload, contains_eager, selectinload
from extensions import db
from models import WeeklyReport, Project, Group, Engineer, WeeklyReportItem
from utils.decorators import log_action, admin_required
from utils.excel_utils import export_reports_by_engineer, parse_import_excel
from utils.report_utils import current_year_week, build_project_report_excel, get_department_project_ids
from routes.admin_engineer import DEFAULT_NEW_ACCOUNT_PASSWORD
from utils.branding import PLACEHOLDER_EMAIL_DOMAIN

export_bp = Blueprint('export', __name__)


@export_bp.route('/export')
@login_required
def index():
    if current_user.role in ('engineer', 'group_manager'):
        abort(403)
    groups = Group.query.order_by(Group.sort_order, Group.id).all()
    if current_user.role == 'dept_manager':
        allowed_project_ids = get_department_project_ids(current_user.department or '')
        projects = Project.query.filter(Project.id.in_(allowed_project_ids)).order_by(
            Project.sort_order, Project.id
        ).all()
    else:
        projects = Project.query.order_by(Project.sort_order, Project.id).all()
    year, week = current_year_week()
    return render_template('export.html', groups=groups, projects=projects, year=year, week=week)


@export_bp.route('/export/by_engineer', methods=['GET'])
@login_required
def by_engineer():
    if current_user.role in ('engineer', 'group_manager'):
        abort(403)
    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    group_id = request.args.get('group_id', type=int)

    # joinedload 一次把 engineer 撈出來（避免排序時 N+1）；
    # items 用 selectinload（而非 joinedload）—— 因為 items 是一對多集合，
    # 若跟 engineer 的 joinedload 疊在一起做 SQL JOIN 會產生笛卡兒積重複列，
    # selectinload 會另外發一次「IN」查詢一次撈齊所有報表的項目，效果一樣但更安全
    query = (WeeklyReport.query
             .options(joinedload(WeeklyReport.engineer), selectinload(WeeklyReport.items))
             .filter_by(year=year, week_number=week))

    needs_engineer_join = bool(group_id) or current_user.role == 'dept_manager'
    if needs_engineer_join:
        query = query.join(Engineer, WeeklyReport.engineer_id == Engineer.id)
    if group_id:
        query = query.filter(Engineer.group_id == group_id)
    if current_user.role == 'dept_manager':
        # 部門主管只能匯出自己部門的人，不管有沒有另外選組別，都要疊加這個限制
        query = query.filter(Engineer.department == current_user.department)
    reports = query.all()
    # 排列順序比照「工程師帳號管理」頁面可拖曳調整的順序 (sort_order)
    reports.sort(key=lambda r: (r.engineer.sort_order, r.engineer.id))

    buf = export_reports_by_engineer(reports, year, week)
    log_action('export', 'WeeklyReport', None, f'匯出 {year}年第{week}週 依工程師報表')
    filename = f'RD週報_{year}W{week}_依員工.xlsx'
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@export_bp.route('/export/by_project', methods=['GET'])
@login_required
def by_project():
    if current_user.role in ('engineer', 'group_manager'):
        abort(403)
    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)

    allowed_project_ids = None
    if current_user.role == 'dept_manager':
        # 部門主管一律鎖定在自己的部門，不受表單帶來的 department 參數影響
        # （就算網址帶了別的部門，也不能拿來匯出別部門的資料）
        allowed_project_ids = get_department_project_ids(current_user.department or '')
    else:
        # 其他角色（管理者、唯讀）可以選填「部門」下拉選單，篩選成只匯出
        # 該部門的專案；留空則沿用原本「不限部門」的行為，匯出全部專案
        selected_department = request.args.get('department', '').strip()
        if selected_department:
            allowed_project_ids = get_department_project_ids(selected_department)

    buf = build_project_report_excel(year, week, allowed_project_ids=allowed_project_ids)
    log_action('export', 'WeeklyReport', None, f'匯出 {year}年第{week}週 依專案報表')
    filename = f'RD週報_{year}W{week}_依專案.xlsx'
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@export_bp.route('/export/import', methods=['POST'])
@login_required
@admin_required
def import_excel():
    """
    批次匯入歷史週報 Excel 檔案（供系統上線初期補歷史資料用）。
    若 Excel 裡的工程師姓名在系統裡還沒有帳號，會自動建立一個新帳號
    （預設密碼見系統設定，工號/Email 先用系統產生的暫時值，之後請到
    「人員帳號管理」頁面補上正確的工號與 Email），而不是直接略過這筆資料。
    """
    file = request.files.get('file')
    year = request.form.get('import_year', type=int)
    week = request.form.get('import_week', type=int)
    if not file or not year or not week:
        flash('請選擇檔案並指定年度／週次', 'danger')
        return redirect(url_for('export.index'))

    rows = parse_import_excel(file)
    created_count, skipped_count, new_engineer_count = 0, 0, 0

    # 避免同一份檔案裡重複出現的新工程師被建立好幾次帳號
    newly_created_engineers = {}
    # 用「目前實際存在的最大 TEMP 編號」而不是數量，避免中間有 TEMP 帳號被
    # 改名或刪除時，用數量計算出來的下一個編號可能撞到既有帳號
    existing_temp_ids = [
        e.emp_id for e in Engineer.query.filter(Engineer.emp_id.like('TEMP%')).all()
    ]
    max_temp_seq = 0
    for emp_id in existing_temp_ids:
        suffix = emp_id[4:]
        if suffix.isdigit():
            max_temp_seq = max(max_temp_seq, int(suffix))

    for row in rows:
        engineer_name = (row['engineer_name'] or '').strip()
        if not engineer_name:
            skipped_count += 1
            continue

        engineer = Engineer.query.filter_by(name=engineer_name).first()
        if not engineer:
            engineer = newly_created_engineers.get(engineer_name)
        if not engineer:
            group = None
            group_name = (row['group'] or '').strip()
            if group_name:
                group = Group.query.filter_by(name=group_name).first()
                if not group:
                    group = Group(name=group_name)
                    db.session.add(group)
                    db.session.flush()

            max_temp_seq += 1
            temp_emp_id = f'TEMP{max_temp_seq:03d}'
            department = (row['department'] or '').strip()
            engineer = Engineer(
                emp_id=temp_emp_id,
                name=engineer_name,
                email=f'{temp_emp_id.lower()}@{PLACEHOLDER_EMAIL_DOMAIN}',
                department=department or None,
                group_id=group.id if group else None,
                role='engineer', status='active',
            )
            engineer.set_password(DEFAULT_NEW_ACCOUNT_PASSWORD)
            db.session.add(engineer)
            db.session.flush()
            newly_created_engineers[engineer_name] = engineer
            new_engineer_count += 1

        from datetime import date as _date
        report = WeeklyReport.query.filter_by(
            engineer_id=engineer.id, year=year, week_number=week
        ).first()
        if not report:
            monday = _date.fromisocalendar(year, week, 1)
            report = WeeklyReport(engineer_id=engineer.id, year=year, week_number=week,
                                   report_date=monday, status='已送出', locked=True)
            db.session.add(report)
            db.session.flush()

        project = None
        if row['project_code']:
            project = Project.query.filter_by(code=row['project_code']).first()
            if not project:
                project = Project(code=row['project_code'], full_name=row['project_code'],
                                   status='進行中', note='由歷史資料匯入自動建立')
                db.session.add(project)
                db.session.flush()

        max_order = len(report.items)
        if row['last_week_content']:
            db.session.add(WeeklyReportItem(
                report_id=report.id, item_type='last_week', project_id=project.id if project else None,
                content=row['last_week_content'], sort_order=max_order
            ))
        if row['this_week_content']:
            db.session.add(WeeklyReportItem(
                report_id=report.id, item_type='this_week', project_id=project.id if project else None,
                content=row['this_week_content'], sort_order=max_order
            ))
        created_count += 1

    db.session.commit()
    log_action('import', 'WeeklyReport', None,
               f'匯入歷史資料 {year}年第{week}週，成功{created_count}筆，'
               f'自動建立新工程師帳號{new_engineer_count}位，略過{skipped_count}筆（姓名空白）')

    msg = f'匯入完成，成功處理 {created_count} 筆'
    if new_engineer_count:
        msg += f'，並自動建立 {new_engineer_count} 位新工程師帳號（預設密碼 {DEFAULT_NEW_ACCOUNT_PASSWORD}，' \
               f'工號暫用 TEMP 開頭的暫時編號，請到「人員帳號管理」頁面補上正確的工號與 Email）'
    if skipped_count:
        msg += f'，略過 {skipped_count} 筆（工程師姓名空白）'
    flash(msg, 'success')
    return redirect(url_for('export.index'))
