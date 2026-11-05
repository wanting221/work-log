# -*- coding: utf-8 -*-
"""
routes/admin_engineer.py - 工程師帳號與組別管理（Admin）
"""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Engineer, Group, Project, AuditLog, WeeklyReport, WeeklyReportItem, Attachment, ReportNote
from utils.decorators import log_action, admin_or_hr_manager_required, admin_hr_or_dept_manager_required
from utils.csv_utils import export_engineers_csv, import_engineers_csv

admin_engineer_bp = Blueprint('admin_engineer', __name__)

# 若管理者未指定密碼時使用的預設密碼
from utils.branding import DEFAULT_NEW_ACCOUNT_PASSWORD


@admin_engineer_bp.route('/admin/engineers')
@login_required
@admin_hr_or_dept_manager_required
def index():
    query = Engineer.query
    if current_user.role == 'dept_manager':
        # 部門主管只看得到自己部門的人
        query = query.filter_by(department=current_user.department or '__none__')
    elif current_user.role == 'group_manager':
        # 組別主管只看得到自己組別的人
        query = query.filter_by(group_id=current_user.group_id or -1)
    engineers = query.order_by(Engineer.sort_order, Engineer.id).all()

    groups_query = Group.query
    if current_user.role == 'dept_manager':
        # 部門主管只看得到、只能選擇自己部門底下的組別
        groups_query = groups_query.filter_by(department=current_user.department or '__none__')
    elif current_user.role == 'group_manager':
        # 組別主管只看得到自己這一個組別
        groups_query = groups_query.filter_by(id=current_user.group_id or -1)
    groups = groups_query.order_by(Group.sort_order, Group.id).all()

    departments = sorted({
        d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
    })

    # 「最高直屬主管」的候選名單刻意不受目前登入者的部門/組別範圍限制——
    # 最高直屬主管常常是跨部門的高階主管（例如某個部門的員工，最高直屬主管
    # 是另一個部門的部門主管），如果照一般的部門範圍篩選，不只沒辦法「指定」
    # 跨部門的人，連「目前已經被指定的跨部門主管是誰」都會因為那個人不在
    # 篩選後的清單裡而顯示不出來（即使欄位本身是唯讀，也應該要能正確顯示
    # 目前的值，不能因為篩選範圍而顯示錯誤或空白）。
    # 管理者角色不會出現在這份候選名單裡——管理者的姓名不應該被當成
    # 「最高直屬主管」的可選項目顯示出來。
    top_supervisor_options = Engineer.query.filter(
        Engineer.role.in_(('group_manager', 'dept_manager'))
    ).order_by(Engineer.sort_order, Engineer.id).all()

    return render_template(
        'admin_engineers.html', engineers=engineers, groups=groups,
        departments=departments, top_supervisor_options=top_supervisor_options,
    )


@admin_engineer_bp.route('/admin/engineers/create', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def create():
    emp_id = request.form.get('emp_id', '').strip()
    name = request.form.get('name', '').strip()
    nickname = request.form.get('nickname', '').strip()
    email = request.form.get('email', '').strip()
    group_id = request.form.get('group_id', type=int) or None
    role = request.form.get('role', 'engineer')
    # 管理者可自行指定密碼；若留空則使用預設密碼（DEFAULT_NEW_ACCOUNT_PASSWORD）
    password = request.form.get('password', '').strip() or DEFAULT_NEW_ACCOUNT_PASSWORD

    if current_user.role == 'dept_manager':
        # 部門主管新增的帳號一律強制歸在自己的部門，不能指定成其他部門；
        # 角色可以是「部門主管」「組別主管」「員工」，但不能新增管理者帳號
        # （管理者權限的授予只能由既有管理者本人執行）
        department = current_user.department or ''
        if not department:
            flash('你的帳號尚未設定部門，請聯繫管理者設定部門後才能新增人員', 'danger')
            return redirect(url_for('admin_engineer.index'))
        if role not in ('engineer', 'dept_manager', 'group_manager'):
            role = 'engineer'
        if group_id:
            # 安全檢查：指定的組別一定要是自己部門底下的組別，不能透過表單
            # 夾帶別部門的 group_id 把人指派到別部門管理的組別
            group = Group.query.get(group_id)
            if not group or (group.department or '') != department:
                group_id = None
    elif current_user.role == 'group_manager':
        # 組別主管新增的帳號一律強制歸在自己的組別（連同組別所屬的部門），
        # 角色也只能是「員工」或「唯讀」，理由跟部門主管一樣：避免權限提升
        if not current_user.group_id:
            flash('你的帳號尚未設定組別，請聯繫管理者設定組別後才能新增人員', 'danger')
            return redirect(url_for('admin_engineer.index'))
        group_id = current_user.group_id
        department = current_user.department or ''
        if role not in ('engineer', 'viewer'):
            role = 'engineer'
    elif current_user.role == 'hr_manager':
        # 人資主管新增帳號不受部門限制，跟管理者一樣完整（含新增管理者帳號）
        department = request.form.get('department', '').strip()
    else:
        department = request.form.get('department', '').strip()

    if not emp_id or not name or not email:
        flash('工號、姓名、Email 為必填欄位', 'danger')
        return redirect(url_for('admin_engineer.index'))

    if Engineer.query.filter_by(emp_id=emp_id).first():
        flash(f'工號 {emp_id} 已存在', 'danger')
        return redirect(url_for('admin_engineer.index'))
    if Engineer.query.filter_by(email=email).first():
        flash(f'Email {email} 已被使用', 'danger')
        return redirect(url_for('admin_engineer.index'))

    # 新帳號排在清單最後面（沿用目前最大排序值 + 1）
    max_order = db.session.query(db.func.max(Engineer.sort_order)).scalar() or 0

    hire_date_str = request.form.get('hire_date', '').strip()
    hire_date = date.fromisoformat(hire_date_str) if hire_date_str else None
    # 直屬主管／最高直屬主管只有管理者能設定；部門主管、組別主管新增的帳號
    # 一律留空，之後要指定主管關係得請管理者去人員帳號管理頁面補上
    supervisor_id = None
    top_supervisor_id = None
    if current_user.role in ('admin', 'hr_manager'):
        supervisor_id = request.form.get('supervisor_id', type=int) or None
        top_supervisor_id = request.form.get('top_supervisor_id', type=int) or None

    # 「功能」下拉選單：關閉/週報/日誌三選一，互斥，轉換成底層的兩個布林
    # 欄位（weekly_report_enabled、diary_enabled）。新增帳號時沒特別選就
    # 預設是「週報」（沒有 report_mode 欄位時 default 成 'report'）。
    report_mode = request.form.get('report_mode', 'report')
    weekly_report_enabled = (report_mode == 'report')
    diary_enabled = (report_mode == 'diary')
    client_project_enabled = request.form.get('client_project_enabled', 'on') == 'on'

    engineer = Engineer(emp_id=emp_id, name=name, nickname=nickname or None, department=department or None,
                         email=email, group_id=group_id, role=role, sort_order=max_order + 1,
                         hire_date=hire_date, supervisor_id=supervisor_id, top_supervisor_id=top_supervisor_id,
                         weekly_report_enabled=weekly_report_enabled, diary_enabled=diary_enabled,
                         client_project_enabled=client_project_enabled)
    engineer.set_password(password)
    db.session.add(engineer)
    db.session.commit()
    log_action('create', 'Engineer', engineer.id, f'新增工程師帳號 {emp_id} - {name}')
    flash(f'已新增帳號 {name}（密碼：{password}，請提醒使用者盡快登入後自行更改）', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/engineers/<int:eng_id>/update', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def update(eng_id):
    """
    更新單一人員的帳號資訊。支援兩種呼叫方式：
    - 一般表單提交（有 X-Requested-With 標頭以外的情況）：走原本的 flash + 導回列表頁
    - AJAX 自動儲存（前端每次欄位失焦/變更時呼叫，帶 X-Requested-With: XMLHttpRequest
      標頭）：回傳 JSON { success, message }，不導頁，讓前端就地顯示「已儲存」或錯誤訊息，
      不用整頁重新整理
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _respond(success, message, category='success'):
        if is_ajax:
            return jsonify({'success': success, 'message': message})
        flash(message, category)
        return redirect(url_for('admin_engineer.index'))

    engineer = Engineer.query.get_or_404(eng_id)

    if current_user.role == 'hr_manager' and engineer.role == 'admin':
        # 人資主管對既有管理者的帳號完全沒有修改權限——不是只擋角色欄位，
        # 是整張表單（姓名、部門、組別、Email...等所有欄位）都不能碰，
        # 這裡直接整個請求擋下來，不能只靠前端把欄位設成唯讀
        abort(403)
    if current_user.role == 'dept_manager':
        # 安全檢查：部門主管直接改網址想修改別部門的人也要擋下來，不能只靠清單
        # 只列出自己部門的人來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        if (engineer.department or '') != (current_user.department or ''):
            abort(403)
    if current_user.role == 'group_manager':
        if engineer.group_id != current_user.group_id:
            abort(403)

    new_emp_id = request.form.get('emp_id', engineer.emp_id).strip()
    if new_emp_id != engineer.emp_id:
        conflict = Engineer.query.filter(Engineer.emp_id == new_emp_id, Engineer.id != engineer.id).first()
        if conflict:
            return _respond(False, f'工號 {new_emp_id} 已被其他帳號使用，未儲存變更', 'danger')
        engineer.emp_id = new_emp_id

    engineer.name = request.form.get('name', engineer.name).strip()
    engineer.nickname = request.form.get('nickname', '').strip() or None
    engineer.email = request.form.get('email', engineer.email).strip()

    new_group_id = request.form.get('group_id', type=int) or None
    if current_user.role == 'dept_manager' and new_group_id:
        # 安全檢查：指定的組別一定要是自己部門底下的組別，不能透過表單
        # 夾帶別部門的 group_id 把人指派到別部門管理的組別
        group = Group.query.get(new_group_id)
        if not group or (group.department or '') != (current_user.department or ''):
            new_group_id = engineer.group_id  # 驗證不通過就維持原本的組別，不套用這次的改動
    if current_user.role == 'group_manager':
        # 組別主管管理的人一律鎖定在自己的組別，不能改成其他組別
        new_group_id = current_user.group_id
    engineer.group_id = new_group_id

    new_role = request.form.get('role', engineer.role)
    if current_user.role == 'dept_manager':
        # 部門主管一律強制留在自己的部門，不能改成其他部門；
        # 角色可以改成「部門主管」「組別主管」「員工」，但不能把人改成管理者
        # （管理者權限的授予只能由既有管理者本人執行，避免部門主管透過
        # 這個功能把自己或他人升級成系統管理者）
        engineer.department = current_user.department or None
        engineer.role = new_role if new_role in ('engineer', 'dept_manager', 'group_manager') else engineer.role
    elif current_user.role == 'group_manager':
        # 組別主管同樣不能改部門，角色也只能是「員工」或「唯讀」
        engineer.department = current_user.department or None
        engineer.role = new_role if new_role in ('engineer', 'viewer') else engineer.role
    elif current_user.role == 'hr_manager':
        # 人資主管不受部門限制，跟管理者一樣完整（含把人設成/取消管理者）
        engineer.department = request.form.get('department', '').strip() or None
        engineer.role = new_role
    else:
        engineer.department = request.form.get('department', '').strip() or None
        engineer.role = new_role

    hire_date_str = request.form.get('hire_date', '').strip()
    engineer.hire_date = date.fromisoformat(hire_date_str) if hire_date_str else None
    if current_user.role in ('admin', 'hr_manager'):
        # 直屬主管／最高直屬主管只有管理者能修改；部門主管、組別主管一律
        # 維持原值不變——即使表單欄位被停用而沒有送出這兩個欄位，這裡也不會
        # 誤把它們清空成 None，因為根本不會進到這個判斷式裡面
        new_supervisor_id = request.form.get('supervisor_id', type=int) or None
        new_top_supervisor_id = request.form.get('top_supervisor_id', type=int) or None
        # 不能把自己設成自己的主管，避免自我循環參照
        engineer.supervisor_id = new_supervisor_id if new_supervisor_id != engineer.id else None
        engineer.top_supervisor_id = new_top_supervisor_id if new_top_supervisor_id != engineer.id else None

    # 「功能」下拉選單：關閉/週報/日誌三選一，互斥，轉換成底層的兩個布林
    # 欄位。client_project_enabled 沒有對應的欄位了（「我的客戶/專案」已經
    # 整合進「填寫日誌」，跟著日誌功能開關走），這裡刻意不去動這個欄位，
    # 維持資料庫裡原本的值。
    report_mode = request.form.get('report_mode', 'off')
    engineer.weekly_report_enabled = (report_mode == 'report')
    engineer.diary_enabled = (report_mode == 'diary')

    db.session.commit()
    log_action('update', 'Engineer', engineer.id, f'更新工程師帳號 {engineer.emp_id}')
    return _respond(True, f'已更新 {engineer.name} 的帳號資訊', 'success')


@admin_engineer_bp.route('/admin/engineers/<int:eng_id>/set_password', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def set_password(eng_id):
    """
    獨立的密碼更新端點：只更新密碼，不會動到帳號的其他欄位。
    刻意跟一般的帳號資料更新分開，避免密碼小視窗的表單資料不完整時，
    不小心把姓名、小名、組別等其他欄位覆蓋成空白。
    """
    engineer = Engineer.query.get_or_404(eng_id)
    if current_user.role == 'hr_manager':
        # 人資主管的「操作」欄位權限只有停用/啟用帳號，更改密碼跟刪除帳號
        # 都還是不開放——這裡明確擋下，不能只靠畫面上不顯示按鈕，
        # 直接送出請求也要能擋住
        abort(403)
    if current_user.role == 'dept_manager' and (engineer.department or '') != (current_user.department or ''):
        abort(403)
    if current_user.role == 'group_manager' and engineer.group_id != current_user.group_id:
        abort(403)

    new_password = request.form.get('new_password', '').strip()
    if not new_password:
        flash('密碼不可為空白，未變更密碼', 'danger')
        return redirect(url_for('admin_engineer.index'))

    engineer.set_password(new_password)
    db.session.commit()
    log_action('update', 'Engineer', engineer.id, f'重設 {engineer.name}（{engineer.emp_id}）的密碼')
    flash(f'已更新 {engineer.name} 的密碼', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/engineers/<int:eng_id>/toggle_status', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def toggle_status(eng_id):
    """
    切換帳號的在職/離職狀態。取代原本清單裡的「狀態」下拉選單，改成一顆按鈕，
    避免跟其他欄位混在同一個表單裡不小心一起改到。
    """
    engineer = Engineer.query.get_or_404(eng_id)
    if current_user.role == 'dept_manager' and (engineer.department or '') != (current_user.department or ''):
        abort(403)
    if current_user.role == 'group_manager' and engineer.group_id != current_user.group_id:
        abort(403)
    if engineer.id == current_user.id:
        flash('不能停用自己目前登入中的帳號', 'danger')
        return redirect(url_for('admin_engineer.index'))

    engineer.status = 'inactive' if engineer.status == 'active' else 'active'
    db.session.commit()
    status_label = '在職' if engineer.status == 'active' else '離職'
    log_action('update', 'Engineer', engineer.id, f'將 {engineer.name}（{engineer.emp_id}）的狀態改為{status_label}')
    flash(f'已將 {engineer.name} 的狀態改為「{status_label}」', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/engineers/<int:eng_id>/delete', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def delete(eng_id):
    engineer = Engineer.query.get_or_404(eng_id)

    if current_user.role == 'hr_manager':
        abort(403)
    if current_user.role == 'dept_manager' and (engineer.department or '') != (current_user.department or ''):
        # 安全檢查：部門主管直接改網址想刪除別部門的人也要擋下來，不能只靠
        # 清單只列出自己部門的人來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        abort(403)
    if current_user.role == 'group_manager' and engineer.group_id != current_user.group_id:
        abort(403)

    if engineer.id == current_user.id:
        flash('不能刪除自己目前登入中的帳號', 'danger')
        return redirect(url_for('admin_engineer.index'))

    name, emp_id = engineer.name, engineer.emp_id

    # 若此工程師是某些專案的主要負責人，先解除該關聯（保留專案資料）
    Project.query.filter_by(owner_engineer_id=engineer.id).update({'owner_engineer_id': None})
    # 操作紀錄保留 user_name 快照，帳號刪除後仍看得出是誰操作，只需解除外鍵關聯
    AuditLog.query.filter_by(user_id=engineer.id).update({'user_id': None})
    # 刪除此工程師名下所有週報：bulk delete 不會觸發 ORM 層級的 cascade，
    # 所以要先手動依序刪除附件、週報項目明細，再刪除週報主表，避免留下孤兒資料
    report_ids = [r.id for r in WeeklyReport.query.filter_by(engineer_id=engineer.id).with_entities(WeeklyReport.id).all()]
    if report_ids:
        item_ids = [i.id for i in WeeklyReportItem.query.filter(WeeklyReportItem.report_id.in_(report_ids)).with_entities(WeeklyReportItem.id).all()]
        if item_ids:
            Attachment.query.filter(Attachment.item_id.in_(item_ids)).delete(synchronize_session=False)
            WeeklyReportItem.query.filter(WeeklyReportItem.id.in_(item_ids)).delete(synchronize_session=False)
        ReportNote.query.filter(ReportNote.report_id.in_(report_ids)).delete(synchronize_session=False)
        WeeklyReport.query.filter(WeeklyReport.id.in_(report_ids)).delete(synchronize_session=False)
    # 此工程師（可能是管理者）曾在別人週報上留過言的紀錄，保留留言內容但解除作者關聯
    ReportNote.query.filter_by(author_id=engineer.id).update({'author_id': None})

    db.session.delete(engineer)
    db.session.commit()
    log_action('delete', 'Engineer', eng_id, f'刪除工程師帳號 {emp_id} - {name}')
    flash(f'已刪除帳號 {name}（{emp_id}）', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/groups/create', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def create_group():
    name = request.form.get('group_name', '').strip()
    if not name:
        flash('組別名稱不可為空', 'danger')
        return redirect(url_for('admin_engineer.index'))
    if Group.query.filter_by(name=name).first():
        flash(f'組別 {name} 已存在', 'danger')
        return redirect(url_for('admin_engineer.index'))

    if current_user.role == 'dept_manager':
        # 部門主管新增的組別一律強制歸在自己的部門，不能指定成其他部門
        department = current_user.department or ''
        if not department:
            flash('你的帳號尚未設定部門，請聯繫管理者設定部門後才能新增組別', 'danger')
            return redirect(url_for('admin_engineer.index'))
    else:
        department = request.form.get('department', '').strip()
        if not department:
            flash('請選擇組別歸屬的部門', 'danger')
            return redirect(url_for('admin_engineer.index'))

    # 新組別排在清單最後面（沿用目前最大排序值 + 1），跟工程師/專案的新增邏輯一致
    max_order = db.session.query(db.func.max(Group.sort_order)).scalar() or 0

    group = Group(name=name, department=department, sort_order=max_order + 1)
    db.session.add(group)
    db.session.commit()
    log_action('create', 'Group', group.id, f'新增組別 {name}（歸屬部門：{department}）')
    flash(f'已新增組別 {name}（歸屬部門：{department}）', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/groups/<int:group_id>/update', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def update_group(group_id):
    """
    修改組別的歸屬部門。主要用來補上「升級前就已經存在、還沒設定部門」的組別，
    讓管理者可以事後幫這些既有組別分類。部門主管只能修改自己部門的組別，
    而且不能把組別改成別的部門（等於是把整組人送給別的部門管理）。
    """
    group = Group.query.get_or_404(group_id)

    if current_user.role == 'dept_manager':
        if (group.department or '') != (current_user.department or ''):
            abort(403)
        new_department = current_user.department or ''
    else:
        new_department = request.form.get('department', '').strip()
        if not new_department:
            flash('請選擇組別歸屬的部門', 'danger')
            return redirect(url_for('admin_engineer.index'))

    group.department = new_department
    db.session.commit()
    log_action('update', 'Group', group.id, f'將組別 {group.name} 的歸屬部門設為 {new_department}')
    flash(f'已將組別 {group.name} 的歸屬部門設為「{new_department}」', 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/groups/<int:group_id>/delete', methods=['POST'])
@login_required
@admin_hr_or_dept_manager_required
def delete_group(group_id):
    """
    刪除組別。刪除前會先把這個組別底下的人員「解除組別關聯」（group_id 設為
    NULL，人員帳號本身保留，只是暫時沒有組別），再刪除組別本身，避免留下
    指向已刪除組別的孤兒資料。部門主管只能刪除自己部門底下的組別。
    """
    group = Group.query.get_or_404(group_id)

    if current_user.role == 'dept_manager' and (group.department or '') != (current_user.department or ''):
        # 安全檢查：部門主管直接改網址想刪除別部門的組別也要擋下來，不能只靠
        # 清單只列出自己部門的組別來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        abort(403)

    name = group.name
    affected = Engineer.query.filter_by(group_id=group_id).update({'group_id': None})
    db.session.delete(group)
    db.session.commit()
    log_action('delete', 'Group', group_id,
               f'刪除組別 {name}' + (f'，並解除 {affected} 位人員的組別關聯' if affected else ''))
    flash(f'已刪除組別 {name}' + (f'（{affected} 位人員已解除組別關聯）' if affected else ''), 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/engineers/export_csv')
@login_required
@admin_or_hr_manager_required
def export_csv():
    buf = export_engineers_csv()
    log_action('export', 'Engineer', None, '匯出工程師帳號清單 CSV')
    return send_file(buf, as_attachment=True, download_name='公司人員帳號清單.csv', mimetype='text/csv')


@admin_engineer_bp.route('/admin/engineers/import_csv', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def import_csv():
    file = request.files.get('file')
    if not file or not file.filename:
        flash('請選擇要匯入的 CSV 檔案', 'danger')
        return redirect(url_for('admin_engineer.index'))
    if not file.filename.lower().endswith('.csv'):
        flash('檔案格式不正確，請上傳 .csv 檔案', 'danger')
        return redirect(url_for('admin_engineer.index'))

    try:
        result = import_engineers_csv(file.stream)
    except Exception as e:
        flash(f'匯入失敗，請確認檔案格式是否正確：{e}', 'danger')
        return redirect(url_for('admin_engineer.index'))

    log_action('import', 'Engineer', None,
               f'匯入工程師 CSV，新增 {result["created"]} 筆，更新 {result["updated"]} 筆，'
               f'錯誤 {len(result["errors"])} 筆')

    msg = f'匯入完成：新增 {result["created"]} 筆（預設密碼 {DEFAULT_NEW_ACCOUNT_PASSWORD}），更新 {result["updated"]} 筆'
    if result['errors']:
        error_lines = '；'.join(f'第{r}列：{m}' for r, m in result['errors'][:5])
        msg += f'（{len(result["errors"])} 筆有問題：{error_lines}{"..." if len(result["errors"]) > 5 else ""}）'
        flash(msg, 'warning')
    else:
        flash(msg, 'success')
    return redirect(url_for('admin_engineer.index'))


@admin_engineer_bp.route('/admin/engineers/reorder', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def reorder():
    """
    儲存拖曳排序後的新順序。前端會傳來依新順序排列的工程師 ID 清單，
    依清單順序依序寫入 sort_order (0, 1, 2, ...)。
    """
    from flask import jsonify
    payload = request.get_json(force=True)
    ordered_ids = payload.get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': False, 'message': '沒有收到排序資料'}), 400

    for idx, eng_id in enumerate(ordered_ids):
        Engineer.query.filter_by(id=eng_id).update({'sort_order': idx})
    db.session.commit()
    log_action('reorder', 'Engineer', None, '調整工程師清單顯示順序')
    return jsonify({'success': True})


@admin_engineer_bp.route('/admin/groups/reorder', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def reorder_groups():
    """
    儲存組別清單拖曳排序後的新順序。前端會傳來依新順序排列的組別 ID 清單，
    依清單順序依序寫入 sort_order (0, 1, 2, ...)。
    """
    from flask import jsonify
    payload = request.get_json(force=True)
    ordered_ids = payload.get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': False, 'message': '沒有收到排序資料'}), 400

    for idx, group_id in enumerate(ordered_ids):
        Group.query.filter_by(id=group_id).update({'sort_order': idx})
    db.session.commit()
    log_action('reorder', 'Group', None, '調整組別清單顯示順序')
    return jsonify({'success': True})
