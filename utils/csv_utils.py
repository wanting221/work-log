# -*- coding: utf-8 -*-
"""
utils/csv_utils.py - 專案清單／工程師帳號的 CSV 匯出、匯入工具
匯出的 CSV 使用 UTF-8 with BOM 編碼，讓 Excel 開啟時中文不會變亂碼。
"""
import csv
import io
from extensions import db
from models import Project, Engineer, Group


# ============================================================
# 專案清單 CSV
# ============================================================
PROJECT_CSV_HEADERS = ['代號', '全名', '狀態', '顏色', '負責工程師工號', '備註']


def export_projects_csv():
    """匯出所有專案為 CSV，回傳 BytesIO。排列順序比照專案清單管理頁面可拖曳調整的順序 (sort_order)"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(PROJECT_CSV_HEADERS)
    for p in Project.query.order_by(Project.sort_order, Project.id).all():
        writer.writerow([
            p.code, p.full_name, p.status, p.color or 'primary',
            p.owner.emp_id if p.owner else '', p.note or '',
        ])
    # 加上 UTF-8 BOM，Excel 在 Windows 上開啟才不會把中文顯示成亂碼
    buf = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    buf.seek(0)
    return buf


def import_projects_csv(file_stream):
    """
    匯入專案 CSV：代號已存在則更新其餘欄位，不存在則新增。
    回傳 dict: {created, updated, errors: [(row_number, message), ...]}
    """
    text = file_stream.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))

    created, updated, errors = 0, 0, []
    next_order = (db.session.query(db.func.max(Project.sort_order)).scalar() or 0) + 1
    for row_num, row in enumerate(reader, start=2):  # 第1列是標題，資料從第2列開始
        code = (row.get('代號') or '').strip()
        if not code:
            errors.append((row_num, '代號為空，已略過'))
            continue

        full_name = (row.get('全名') or code).strip()
        status = (row.get('狀態') or '進行中').strip()
        if status not in ('進行中', '已結案'):
            status = '進行中'
        color = (row.get('顏色') or 'primary').strip()
        owner_emp_id = (row.get('負責工程師工號') or '').strip()
        note = (row.get('備註') or '').strip()

        owner = None
        if owner_emp_id:
            owner = Engineer.query.filter_by(emp_id=owner_emp_id).first()
            if not owner:
                errors.append((row_num, f'找不到工號為 {owner_emp_id} 的工程師，負責人欄位已略過'))

        project = Project.query.filter_by(code=code).first()
        if project:
            project.full_name = full_name
            project.status = status
            project.color = color
            project.note = note
            project.owner_engineer_id = owner.id if owner else None
            updated += 1
        else:
            project = Project(
                code=code, full_name=full_name, status=status, color=color,
                note=note, owner_engineer_id=owner.id if owner else None,
                sort_order=next_order
            )
            next_order += 1
            db.session.add(project)
            created += 1

    db.session.commit()
    return {'created': created, 'updated': updated, 'errors': errors}


# ============================================================
# 工程師帳號 CSV
# ============================================================
ENGINEER_CSV_HEADERS = ['工號', '姓名', '小名', '部門', '組別', '直屬主管', '最高直屬主管', '到職日期', 'Email', '角色', '狀態']
from utils.branding import DEFAULT_NEW_ACCOUNT_PASSWORD as DEFAULT_IMPORT_PASSWORD


def export_engineers_csv():
    """
    匯出所有工程師帳號為 CSV，回傳 BytesIO。
    基於安全考量，不匯出密碼欄位（匯出檔案可能經 Email／隨身碟流傳，
    密碼雜湊值外流沒有意義但仍不建議連帳號清單一起大量流通明文對應關係）。
    排列順序比照工程師帳號管理頁面可拖曳調整的順序 (sort_order)。
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(ENGINEER_CSV_HEADERS)
    for e in Engineer.query.order_by(Engineer.sort_order, Engineer.id).all():
        writer.writerow([
            e.emp_id, e.name, e.nickname or '', e.department or '',
            e.group.name if e.group else '',
            e.supervisor.name if e.supervisor else '',
            e.top_supervisor.name if e.top_supervisor else '',
            e.hire_date.isoformat() if e.hire_date else '',
            e.email, e.role, e.status,
        ])
    buf = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    buf.seek(0)
    return buf


def import_engineers_csv(file_stream):
    """
    匯入工程師帳號 CSV：工號已存在則更新姓名/組別/Email/角色/狀態（不動密碼），
    不存在則新增帳號，新帳號密碼統一為預設密碼（見 config.py 的 DEFAULT_NEW_ACCOUNT_PASSWORD）。
    回傳 dict: {created, updated, errors: [(row_number, message), ...]}
    """
    text = file_stream.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))

    created, updated, errors = 0, 0, []
    next_order = (db.session.query(db.func.max(Engineer.sort_order)).scalar() or 0) + 1
    for row_num, row in enumerate(reader, start=2):
        emp_id = (row.get('工號') or '').strip()
        name = (row.get('姓名') or '').strip()
        if not emp_id or not name:
            errors.append((row_num, '工號或姓名為空，已略過'))
            continue

        email = (row.get('Email') or '').strip()
        nickname = (row.get('小名') or '').strip()
        group_name = (row.get('組別') or '').strip()
        role = (row.get('角色') or 'engineer').strip()
        if role not in ('admin', 'engineer', 'viewer'):
            role = 'engineer'
        status = (row.get('狀態') or 'active').strip()
        if status not in ('active', 'inactive'):
            status = 'active'

        group = None
        if group_name:
            group = Group.query.filter_by(name=group_name).first()
            if not group:
                group = Group(name=group_name)
                db.session.add(group)
                db.session.flush()

        engineer = Engineer.query.filter_by(emp_id=emp_id).first()
        if engineer:
            engineer.name = name
            engineer.nickname = nickname or None
            if email:
                # Email 有唯一性限制，先檢查是否會撞到別人
                conflict = Engineer.query.filter(Engineer.email == email, Engineer.id != engineer.id).first()
                if conflict:
                    errors.append((row_num, f'Email {email} 已被其他帳號使用，此欄位未更新'))
                else:
                    engineer.email = email
            engineer.group_id = group.id if group else None
            engineer.role = role
            engineer.status = status
            updated += 1
        else:
            if not email:
                errors.append((row_num, f'新增帳號 {emp_id} 缺少 Email，已略過'))
                continue
            if Engineer.query.filter_by(email=email).first():
                errors.append((row_num, f'Email {email} 已被使用，帳號 {emp_id} 未建立'))
                continue
            engineer = Engineer(
                emp_id=emp_id, name=name, nickname=nickname or None, email=email,
                group_id=group.id if group else None,
                role=role, status=status, sort_order=next_order,
            )
            next_order += 1
            engineer.set_password(DEFAULT_IMPORT_PASSWORD)
            db.session.add(engineer)
            created += 1

    db.session.commit()
    return {'created': created, 'updated': updated, 'errors': errors}
