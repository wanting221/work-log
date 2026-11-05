# -*- coding: utf-8 -*-
"""
routes/admin_project.py - 客戶／專案清單管理（Admin）
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort, send_file
from flask_login import login_required, current_user
from extensions import db
from models import Project, Engineer
from utils.decorators import log_action, admin_required, admin_or_dept_manager_required
from utils.csv_utils import export_projects_csv, import_projects_csv
from utils.report_utils import get_department_project_ids, get_group_project_ids

admin_project_bp = Blueprint('admin_project', __name__)


@admin_project_bp.route('/admin/projects')
@login_required
@admin_or_dept_manager_required
def index():
    if current_user.role == 'dept_manager':
        allowed_ids = get_department_project_ids(current_user.department or '')
        projects = Project.query.filter(Project.id.in_(allowed_ids)).order_by(
            Project.sort_order, Project.id
        ).all()
        engineers = Engineer.query.filter_by(
            status='active', department=current_user.department or '__none__'
        ).order_by(Engineer.name).all()
    elif current_user.role == 'group_manager':
        allowed_ids = get_group_project_ids(current_user.group_id)
        projects = Project.query.filter(Project.id.in_(allowed_ids)).order_by(
            Project.sort_order, Project.id
        ).all()
        engineers = Engineer.query.filter_by(
            status='active', group_id=current_user.group_id or -1
        ).order_by(Engineer.name).all()
    else:
        projects = Project.query.order_by(Project.sort_order, Project.id).all()
        engineers = Engineer.query.filter_by(status='active').order_by(Engineer.name).all()
    departments = sorted({
        d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
    })
    return render_template('admin_projects.html', projects=projects, engineers=engineers, departments=departments)


@admin_project_bp.route('/admin/projects/create', methods=['POST'])
@login_required
@admin_or_dept_manager_required
def create():
    code = request.form.get('code', '').strip()
    full_name = request.form.get('full_name', '').strip()
    status = request.form.get('status', '進行中')
    note = request.form.get('note', '').strip()
    color = request.form.get('color', 'primary')
    owner_id = request.form.get('owner_engineer_id', type=int) or None

    if current_user.role == 'dept_manager':
        # 部門主管新增的專案一律強制歸在自己的部門，不能指定成其他部門
        department = current_user.department or ''
        if not department:
            flash('你的帳號尚未設定部門，請聯繫管理者設定部門後才能新增專案', 'danger')
            return redirect(url_for('admin_project.index'))
    elif current_user.role == 'group_manager':
        # 組別主管新增的專案，部門欄位一樣強制設為自己所屬的部門（專案本身沒有
        # 組別層級的歸屬欄位，只能到部門這一層），確保這個新專案會落在
        # 自己看得到的範圍內（get_group_project_ids 會靠負責人/週報項目推斷）
        department = current_user.department or ''
        if not department:
            flash('你的帳號尚未設定部門，請聯繫管理者設定部門後才能新增專案', 'danger')
            return redirect(url_for('admin_project.index'))
        if not owner_id:
            # 沒有指定負責人的話，預設把自己設為負責人，這樣新專案才會被
            # get_group_project_ids() 的推斷規則判斷成「屬於自己組別」
            owner_id = current_user.id
    else:
        department = request.form.get('department', '').strip()

    if not code or not full_name:
        flash('專案代號與全名為必填欄位', 'danger')
        return redirect(url_for('admin_project.index'))

    if Project.query.filter_by(code=code).first():
        flash(f'專案代號 {code} 已存在', 'danger')
        return redirect(url_for('admin_project.index'))

    # 新專案排在清單最後面（沿用目前最大排序值 + 1）
    max_order = db.session.query(db.func.max(Project.sort_order)).scalar() or 0

    project = Project(code=code, full_name=full_name, status=status, note=note,
                       color=color, department=department or None,
                       owner_engineer_id=owner_id, sort_order=max_order + 1)
    db.session.add(project)
    db.session.commit()
    log_action('create', 'Project', project.id, f'新增專案 {code} - {full_name}')
    flash(f'已新增專案 {code}', 'success')
    return redirect(url_for('admin_project.index'))


@admin_project_bp.route('/admin/projects/<int:project_id>/update', methods=['POST'])
@login_required
@admin_or_dept_manager_required
def update(project_id):
    """
    更新單一專案資訊。支援兩種呼叫方式：
    - 一般表單提交：走原本的 flash + 導回列表頁
    - AJAX 自動儲存（前端每次欄位失焦/變更時呼叫，帶
      X-Requested-With: XMLHttpRequest 標頭）：回傳 JSON { success, message }，
      不導頁，讓前端就地顯示「已儲存」，不用整頁重新整理
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    project = Project.query.get_or_404(project_id)

    if current_user.role == 'dept_manager':
        # 安全檢查：部門主管直接改網址想修改別部門的專案也要擋下來，不能只靠
        # 清單只列出自己部門的專案來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        allowed_ids = get_department_project_ids(current_user.department or '')
        if project.id not in allowed_ids:
            abort(403)
    if current_user.role == 'group_manager':
        allowed_ids = get_group_project_ids(current_user.group_id)
        if project.id not in allowed_ids:
            abort(403)

    project.full_name = request.form.get('full_name', project.full_name).strip()
    project.status = request.form.get('status', project.status)
    project.note = request.form.get('note', project.note)
    project.color = request.form.get('color', project.color)

    if current_user.role in ('dept_manager', 'group_manager'):
        # 部門主管/組別主管一律強制鎖定在自己的部門，不能改成其他部門（等於是
        # 把專案送給別的部門管理），確保這個專案還是會留在自己看得到的範圍內
        project.department = current_user.department or None
    else:
        project.department = request.form.get('department', '').strip() or None

    owner_id = request.form.get('owner_engineer_id', type=int)
    if 'owner_engineer_id' in request.form:
        # 只有表單真的有帶這個欄位時才更新——「負責人」欄位已經從清單的
        # 逐列編輯表單移除（只在新增專案時設定一次），如果每次儲存都無條件
        # 覆寫這個欄位，會因為清單表單根本沒有這個欄位，導致每次存檔都把
        # 負責人意外清空成空白
        project.owner_engineer_id = owner_id or None
    db.session.commit()
    log_action('update', 'Project', project.id, f'更新專案 {project.code}')

    if is_ajax:
        return jsonify({'success': True, 'message': f'已更新專案 {project.code}'})
    flash(f'已更新專案 {project.code}', 'success')
    return redirect(url_for('admin_project.index'))


@admin_project_bp.route('/admin/projects/<int:project_id>/delete', methods=['POST'])
@login_required
@admin_or_dept_manager_required
def delete(project_id):
    """
    刪除專案。刪除前會先把所有引用這個專案的週報項目「解除關聯」（project_id 設為
    NULL，保留項目本身的文字內容，只是不再歸類到這個專案底下），再刪除專案本身。
    這一步是必要的：Cloud SQL（MySQL）預設會強制檢查外鍵約束，如果專案還有週報項目
    參照著它，直接刪除專案會被資料庫拒絕、噴出 500 錯誤（SQLite 預設不會檢查這個
    約束，所以本機測試時不容易發現，接上 Cloud SQL 後才會顯現）。
    """
    from models import WeeklyReportItem
    project = Project.query.get_or_404(project_id)

    if current_user.role == 'dept_manager':
        # 安全檢查：部門主管直接改網址想刪除別部門的專案也要擋下來，不能只靠
        # 清單只列出自己部門的專案來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        allowed_ids = get_department_project_ids(current_user.department or '')
        if project.id not in allowed_ids:
            abort(403)
    if current_user.role == 'group_manager':
        allowed_ids = get_group_project_ids(current_user.group_id)
        if project.id not in allowed_ids:
            abort(403)

    code = project.code

    affected_items = WeeklyReportItem.query.filter_by(project_id=project_id).update({'project_id': None})
    db.session.delete(project)
    db.session.commit()
    log_action('delete', 'Project', project_id,
               f'刪除專案 {code}' + (f'，並解除 {affected_items} 筆週報項目的專案關聯' if affected_items else ''))
    flash(f'已刪除專案 {code}' + (f'（{affected_items} 筆週報項目已解除專案關聯，內容保留）' if affected_items else ''), 'success')
    return redirect(url_for('admin_project.index'))


@admin_project_bp.route('/admin/projects/merge', methods=['POST'])
@login_required
@admin_or_dept_manager_required
def merge():
    """整併重複項目：將來源專案的所有週報項目改指向目標專案，並刪除來源專案"""
    from models import WeeklyReportItem
    source_id = request.form.get('source_id', type=int)
    target_id = request.form.get('target_id', type=int)

    if not source_id or not target_id or source_id == target_id:
        flash('請選擇不同的來源與目標專案', 'danger')
        return redirect(url_for('admin_project.index'))

    source = Project.query.get_or_404(source_id)
    target = Project.query.get_or_404(target_id)

    if current_user.role == 'dept_manager':
        # 安全檢查：來源、目標都必須是自己部門看得到的專案，兩邊都要檢查——
        # 否則部門主管可能把自己部門的專案整併進別部門的專案（等於把資料送出去），
        # 或是把別部門的專案整併進自己部門的專案（等於把別部門的資料吃進來）
        allowed_ids = get_department_project_ids(current_user.department or '')
        if source.id not in allowed_ids or target.id not in allowed_ids:
            abort(403)
    if current_user.role == 'group_manager':
        allowed_ids = get_group_project_ids(current_user.group_id)
        if source.id not in allowed_ids or target.id not in allowed_ids:
            abort(403)

    WeeklyReportItem.query.filter_by(project_id=source_id).update({'project_id': target_id})
    source_code = source.code
    db.session.delete(source)
    db.session.commit()
    log_action('merge', 'Project', target_id, f'將 {source_code} 整併至 {target.code}')
    flash(f'已將 {source_code} 整併至 {target.code}', 'success')
    return redirect(url_for('admin_project.index'))


@admin_project_bp.route('/admin/projects/export_csv')
@login_required
@admin_required
def export_csv():
    buf = export_projects_csv()
    log_action('export', 'Project', None, '匯出專案清單 CSV')
    return send_file(buf, as_attachment=True, download_name='專案清單.csv', mimetype='text/csv')


@admin_project_bp.route('/admin/projects/import_csv', methods=['POST'])
@login_required
@admin_required
def import_csv():
    file = request.files.get('file')
    if not file or not file.filename:
        flash('請選擇要匯入的 CSV 檔案', 'danger')
        return redirect(url_for('admin_project.index'))
    if not file.filename.lower().endswith('.csv'):
        flash('檔案格式不正確，請上傳 .csv 檔案', 'danger')
        return redirect(url_for('admin_project.index'))

    try:
        result = import_projects_csv(file.stream)
    except Exception as e:
        flash(f'匯入失敗，請確認檔案格式是否正確：{e}', 'danger')
        return redirect(url_for('admin_project.index'))

    log_action('import', 'Project', None,
               f'匯入專案 CSV，新增 {result["created"]} 筆，更新 {result["updated"]} 筆，'
               f'錯誤 {len(result["errors"])} 筆')

    msg = f'匯入完成：新增 {result["created"]} 筆，更新 {result["updated"]} 筆'
    if result['errors']:
        error_lines = '；'.join(f'第{r}列：{m}' for r, m in result['errors'][:5])
        msg += f'（{len(result["errors"])} 筆有問題：{error_lines}{"..." if len(result["errors"]) > 5 else ""}）'
        flash(msg, 'warning')
    else:
        flash(msg, 'success')
    return redirect(url_for('admin_project.index'))


@admin_project_bp.route('/admin/projects/reorder', methods=['POST'])
@login_required
@admin_required
def reorder():
    """
    儲存拖曳排序後的新順序。前端會傳來依新順序排列的專案 ID 清單，
    依清單順序依序寫入 sort_order (0, 1, 2, ...)。
    """
    payload = request.get_json(force=True)
    ordered_ids = payload.get('ordered_ids', [])
    if not ordered_ids:
        return jsonify({'success': False, 'message': '沒有收到排序資料'}), 400

    for idx, project_id in enumerate(ordered_ids):
        Project.query.filter_by(id=project_id).update({'sort_order': idx})
    db.session.commit()
    log_action('reorder', 'Project', None, '調整專案清單顯示順序')
    return jsonify({'success': True})
