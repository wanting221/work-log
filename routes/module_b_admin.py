# -*- coding: utf-8 -*-
"""
routes/module_b_admin.py - 考核系統模組B的後台管理
（部門硬指標庫維護、部門硬/軟指標權重比例設定）

只開放給管理者、人資主管使用，跟「考核項目管理」（模組A的 ScoreCriteria）
同一個權限範圍，但是完全獨立的功能，管理的是模組B專用的資料表。
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from decimal import Decimal, InvalidOperation
from extensions import db
from models import DeptIndicatorLibrary, DeptWeightConfig, DeptGoalTemplateItem, ModuleBSoftDimensionConfig, Engineer, Project
from utils.decorators import admin_or_hr_manager_required, log_action
from utils.report_utils import MODULE_B_DEFAULT_WEIGHT, ensure_dept_goal_template, get_soft_dimensions

module_b_admin_bp = Blueprint('module_b_admin', __name__)


def _known_departments():
    """
    列出目前系統裡出現過的部門名稱（來自人員帳號跟專案兩個來源，取聯集），
    供「新增指標」「設定權重」的部門選單使用——不用另外維護一份獨立的
    部門主檔。
    """
    from_engineers = {d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]}
    from_projects = {d[0] for d in Project.query.with_entities(Project.department).distinct().all() if d[0]}
    return sorted(from_engineers | from_projects)


@module_b_admin_bp.route('/review/module-b/indicators')
@login_required
@admin_or_hr_manager_required
def indicators():
    departments = _known_departments()
    all_indicators = (
        DeptIndicatorLibrary.query
        .order_by(DeptIndicatorLibrary.department, DeptIndicatorLibrary.sort_order, DeptIndicatorLibrary.id)
        .all()
    )
    indicators_by_dept = {}
    for ind in all_indicators:
        indicators_by_dept.setdefault(ind.department, []).append(ind)
    # 指標庫裡如果有部門名稱不在「目前系統已知部門」清單裡（例如部門後來
    # 改名/併掉了），還是要讓管理者看得到、能繼續管理，不能悄悄消失
    for dept in indicators_by_dept:
        if dept not in departments:
            departments.append(dept)
    departments.sort()

    # 項目1：目標設定（目標值/權重）整合顯示在這頁，每個部門的目標範本
    # 沒有的話先自動產生一份預設內容（平均分配權重），library_id 對應到
    # 各自的目標範本項目，方便樣板直接查表
    template_by_lib = {}
    for dept in departments:
        for item in ensure_dept_goal_template(dept):
            template_by_lib[item.library_id] = item

    # 權重比例設定整合在每個部門自己的表格最上面一列（跟指標明細同一張表），
    # 儲存動作沿用 update_weights() 這個既有的路由
    weight_configs = {c.department: c for c in DeptWeightConfig.query.all()}
    weight_by_dept = {}
    for dept in departments:
        config = weight_configs.get(dept)
        weight_by_dept[dept] = {
            'hard_pct': config.hard_weight_pct if config else MODULE_B_DEFAULT_WEIGHT['hard'],
            'soft_pct': config.soft_weight_pct if config else MODULE_B_DEFAULT_WEIGHT['soft'],
            'is_custom': config is not None,
        }

    return render_template(
        'module_b_indicators.html', departments=departments, indicators_by_dept=indicators_by_dept,
        weight_by_dept=weight_by_dept, template_by_lib=template_by_lib,
        soft_dimensions=get_soft_dimensions(),
    )


@module_b_admin_bp.route('/review/module-b/indicators/create', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def create_indicator():
    department = request.form.get('department', '').strip()
    name = request.form.get('name', '').strip()
    measurement_standard = request.form.get('measurement_standard', '').strip()
    if not department or not name:
        flash('部門與指標名稱不可為空', 'danger')
        return redirect(url_for('module_b_admin.indicators'))

    max_order = db.session.query(db.func.max(DeptIndicatorLibrary.sort_order)).filter_by(
        department=department
    ).scalar() or 0

    # 未來新增的指標一律直接視為啟用中，不再有獨立的「啟用」開關可以關閉
    indicator = DeptIndicatorLibrary(
        department=department, name=name, measurement_standard=measurement_standard or None,
        sort_order=max_order + 1, is_active=True,
    )
    db.session.add(indicator)
    db.session.commit()
    # 新增的指標先給一筆目標範本項目（權重先預設0，由管理者自行填寫調整），
    # 這樣「目標值/權重」欄位一開始就有地方可以直接編輯，不用另外一個步驟
    db.session.add(DeptGoalTemplateItem(department=department, library_id=indicator.id, weight_pct=0))
    db.session.commit()
    log_action('create', 'DeptIndicatorLibrary', indicator.id, f'新增 {department} 的硬指標「{name}」')
    flash('已新增指標', 'success')
    return redirect(url_for('module_b_admin.indicators'))


@module_b_admin_bp.route('/review/module-b/indicators/<int:indicator_id>/update', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def update_indicator(indicator_id):
    indicator = DeptIndicatorLibrary.query.get_or_404(indicator_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    indicator.name = request.form.get('name', indicator.name).strip()
    indicator.measurement_standard = request.form.get('measurement_standard', '').strip() or None

    # 項目1：目標值／權重（DeptGoalTemplateItem）在同一次自動儲存裡一併更新，
    # 沒有這筆範本項目的話（理論上不會發生，ensure_dept_goal_template 已經
    # 保證每個指標都有對應項目，這裡多一層防呆）就順手建立一筆
    template_item = DeptGoalTemplateItem.query.filter_by(
        department=indicator.department, library_id=indicator.id
    ).first()
    if not template_item:
        template_item = DeptGoalTemplateItem(department=indicator.department, library_id=indicator.id, weight_pct=0)
        db.session.add(template_item)
    template_item.target_value = request.form.get('target_value', '').strip()
    weight_raw = request.form.get('weight_pct', '').strip()
    try:
        template_item.weight_pct = Decimal(weight_raw) if weight_raw else Decimal('0')
    except InvalidOperation:
        if is_ajax:
            from flask import jsonify
            return jsonify({'success': False, 'message': '權重格式有誤'})
        flash('權重格式有誤', 'danger')
        return redirect(url_for('module_b_admin.indicators'))

    db.session.commit()
    log_action('update', 'DeptIndicatorLibrary', indicator.id, f'更新指標「{indicator.name}」')

    if is_ajax:
        from flask import jsonify
        return jsonify({'success': True, 'message': '已儲存'})
    flash('已儲存', 'success')
    return redirect(url_for('module_b_admin.indicators'))


@module_b_admin_bp.route('/review/module-b/indicators/<int:indicator_id>/delete', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def delete_indicator(indicator_id):
    indicator = DeptIndicatorLibrary.query.get_or_404(indicator_id)
    name = indicator.name
    # 先清掉這個指標對應的目標範本項目（DeptGoalTemplateItem），避免刪除
    # 指標本身之後留下指向不存在指標的孤兒資料
    DeptGoalTemplateItem.query.filter_by(library_id=indicator_id).delete()
    db.session.delete(indicator)
    db.session.commit()
    log_action('delete', 'DeptIndicatorLibrary', indicator_id, f'刪除指標「{name}」')
    flash('已刪除', 'success')
    return redirect(url_for('module_b_admin.indicators'))


@module_b_admin_bp.route('/review/module-b/weights')
@login_required
@admin_or_hr_manager_required
def weights():
    departments = _known_departments()
    configs = {c.department: c for c in DeptWeightConfig.query.all()}
    for dept in configs:
        if dept not in departments:
            departments.append(dept)
    departments.sort()

    rows = []
    for dept in departments:
        config = configs.get(dept)
        rows.append({
            'department': dept,
            'hard_pct': config.hard_weight_pct if config else MODULE_B_DEFAULT_WEIGHT['hard'],
            'soft_pct': config.soft_weight_pct if config else MODULE_B_DEFAULT_WEIGHT['soft'],
            'is_custom': config is not None,
        })
    return render_template('module_b_weights.html', rows=rows)


@module_b_admin_bp.route('/review/module-b/weights/update', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def update_weights():
    department = request.form.get('department', '').strip()
    try:
        hard_pct = float(request.form.get('hard_pct'))
        soft_pct = float(request.form.get('soft_pct'))
    except (TypeError, ValueError):
        flash('權重比例格式錯誤', 'danger')
        return redirect(url_for('module_b_admin.weights'))

    if not department:
        flash('部門不可為空', 'danger')
        return redirect(url_for('module_b_admin.weights'))
    if abs((hard_pct + soft_pct) - 100) > 0.01:
        flash(f'硬指標＋軟指標權重必須等於 100（目前是 {hard_pct + soft_pct}）', 'danger')
        return redirect(url_for('module_b_admin.weights'))

    config = DeptWeightConfig.query.filter_by(department=department).first()
    if config:
        config.hard_weight_pct = hard_pct
        config.soft_weight_pct = soft_pct
    else:
        config = DeptWeightConfig(department=department, hard_weight_pct=hard_pct, soft_weight_pct=soft_pct)
        db.session.add(config)
    db.session.commit()
    log_action('update', 'DeptWeightConfig', config.id, f'設定 {department} 權重為 {hard_pct}/{soft_pct}')
    flash(f'已儲存 {department} 的權重比例', 'success')
    return redirect(url_for('module_b_admin.weights'))


@module_b_admin_bp.route('/review/module-b/soft-dimensions/<dimension>/update', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def update_soft_dimension(dimension):
    """
    儲存模組B軟指標（BARS）某個構面的錨點文字編輯。全公司統一適用，
    不分部門，跟「部門硬指標庫」頁面整合在同一頁最下方維護。
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    config = ModuleBSoftDimensionConfig.query.filter_by(dimension=dimension).first_or_404()

    config.label = request.form.get('label', config.label).strip()
    config.anchor_5 = request.form.get('anchor_5', '').strip()
    config.anchor_3 = request.form.get('anchor_3', '').strip()
    config.anchor_1 = request.form.get('anchor_1', '').strip()
    db.session.commit()
    log_action('update', 'ModuleBSoftDimensionConfig', None, f'更新軟指標構面「{config.label}」的BARS內容')

    if is_ajax:
        from flask import jsonify
        return jsonify({'success': True, 'message': '已儲存'})
    flash('已儲存', 'success')
    return redirect(url_for('module_b_admin.indicators'))
