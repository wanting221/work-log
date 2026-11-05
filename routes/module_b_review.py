# -*- coding: utf-8 -*-
"""
routes/module_b_review.py - 考核系統模組B的完整流程
（目標設定 → 人資審核 → 自評 → 主管評分 → 覆評）

跟模組A（routes/review_self.py, routes/review_scoring.py）的角色範圍、
權限判斷邏輯完全對應，只是資料表換成 ModuleBForm/ModuleBHardIndicator/
ModuleBSoftIndicator，多了「目標設定」「人資審核」這兩個模組A沒有的步驟。
"""
from datetime import datetime, date as date_cls
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from extensions import db
from models import (
    ModuleBForm, ModuleBHardIndicator, ModuleBSoftIndicator,
    ReviewPeriod, Engineer, DeptIndicatorLibrary, DeptGoalTemplateItem, ModuleBRankingResult,
)
from utils.report_utils import (
    REVIEW_SELF_ROLES, get_soft_dimensions, get_dept_weight_config, ensure_dept_goal_template,
)
from utils.decorators import log_action, admin_or_hr_manager_required

module_b_review_bp = Blueprint('module_b_review', __name__)


def _needs_executive_review(engineer):
    """跟模組A同一套判斷：直屬主管、最高直屬主管都有指定，才需要兩階段流程"""
    return engineer.supervisor_id is not None and engineer.top_supervisor_id is not None


def _is_sole_scorer(engineer, user_id):
    """這個人是不是唯一需要為此員工打分數的人（只指定了其中一位主管的情況）"""
    if engineer.supervisor_id == user_id and engineer.top_supervisor_id is None:
        return True
    if engineer.top_supervisor_id == user_id and engineer.supervisor_id is None:
        return True
    return False


def _ensure_soft_indicators(form):
    """確保這張表底下四個固定構面都有一筆 ModuleBSoftIndicator（第一次進來時建立空白列）"""
    existing = {si.dimension: si for si in form.soft_indicators}
    created_any = False
    for dim in get_soft_dimensions():
        if dim not in existing:
            si = ModuleBSoftIndicator(form_id=form.id, dimension=dim)
            db.session.add(si)
            existing[dim] = si
            created_any = True
    if created_any:
        db.session.commit()
    return existing


# ============================================================
# 我的考核表（模組B）—— 員工自己的表單列表 + 自評填寫
# ============================================================

@module_b_review_bp.route('/review/module-b/my-forms')
@login_required
def my_forms():
    if current_user.role not in REVIEW_SELF_ROLES:
        abort(403)
    forms = (
        ModuleBForm.query.filter_by(engineer_id=current_user.id)
        .join(ReviewPeriod)
        .order_by(ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc())
        .all()
    )
    return render_template('module_b_my_forms.html', forms=forms)


@module_b_review_bp.route('/review/module-b/my-forms/<int:form_id>', methods=['GET', 'POST'])
@login_required
def self_edit(form_id):
    if current_user.role not in REVIEW_SELF_ROLES:
        abort(403)
    form = ModuleBForm.query.options(joinedload(ModuleBForm.period)).get_or_404(form_id)
    if form.engineer_id != current_user.id:
        abort(403)

    if form.status in ('goal_setting', 'pending_hr_approval'):
        flash('目標設定尚未核准完成，還不能填寫自評', 'warning')
        return redirect(url_for('module_b_review.my_forms'))

    period = form.period
    deadline_passed = bool(period.self_report_deadline and date_cls.today() > period.self_report_deadline)
    can_edit = form.status == 'draft' and period.is_open and not deadline_passed

    hard_indicators = (
        ModuleBHardIndicator.query.filter_by(form_id=form.id)
        .options(joinedload(ModuleBHardIndicator.library_indicator))
        .order_by(ModuleBHardIndicator.id)
        .all()
    )
    soft_indicators = _ensure_soft_indicators(form)
    soft_dims = get_soft_dimensions()

    if request.method == 'POST':
        if not can_edit:
            flash('此考核表目前無法編輯（可能已送出、期間已關閉或已過填寫期限）', 'warning')
            return redirect(url_for('module_b_review.self_edit', form_id=form.id))

        action = request.form.get('action')

        for hi in hard_indicators:
            hi.self_comment = request.form.get(f'hard_comment_{hi.id}', '').strip()

        missing_behavior = []
        for dim, meta in soft_dims.items():
            si = soft_indicators[dim]
            desc = request.form.get(f'soft_desc_{dim}', '').strip()
            si.self_behavior_desc = desc
            if action == 'submit' and not desc:
                missing_behavior.append(meta['label'])

        if action == 'submit' and missing_behavior:
            db.session.rollback()
            flash(f'送出前請填寫「具體行為描述」：{"、".join(missing_behavior)}', 'danger')
            return redirect(url_for('module_b_review.self_edit', form_id=form.id))

        if action == 'submit':
            form.status = 'submitted'
            form.submitted_at = datetime.utcnow()
            flash('已送出自評，主管將開始進行評分', 'success')
        else:
            flash('已儲存草稿', 'success')
        db.session.commit()
        return redirect(url_for('module_b_review.my_forms'))

    return render_template(
        'module_b_self_form.html', form=form, period=period, can_edit=can_edit,
        hard_indicators=hard_indicators, soft_indicators=soft_indicators,
        soft_dimensions=soft_dims,
    )


# ============================================================
# 目標設定（項目2：改由管理者/人資直接設定硬指標、目標值與權重，
# 設定後直接完成，不再像原本那樣讓部門主管/組別主管設定後，還要多一道
# 送給人資/CMO審核的關卡——這裡的角色範圍因此從部門主管/組別主管，
# 改成管理者/人資主管，跟「考核項目管理」「部門硬指標庫」同一個權限範圍）
# ============================================================

@module_b_review_bp.route('/review/module-b/goal-setting')
@login_required
@admin_or_hr_manager_required
def goal_setting_dashboard():
    """
    項目3：目標設定改成「部門」層級的範本，不分考核期間、不分人員，
    同一個部門共用同一份設定，不用每個人、每一季重新設定一次。這裡
    列出目前系統裡有建立硬指標庫的部門，管理者/人資直接點進去編輯
    該部門的範本即可。
    """
    departments = sorted({
        d[0] for d in DeptIndicatorLibrary.query.with_entities(DeptIndicatorLibrary.department).distinct().all()
    })
    return render_template('module_b_goal_setting_dashboard.html', departments=departments)


@module_b_review_bp.route('/review/module-b/goal-setting/<department>', methods=['GET', 'POST'])
@login_required
@admin_or_hr_manager_required
def goal_setting_edit(department):
    library_options = (
        DeptIndicatorLibrary.query.filter_by(department=department, is_active=True)
        .order_by(DeptIndicatorLibrary.sort_order).all()
    )
    weight_config = get_dept_weight_config(department)

    if request.method == 'POST':
        # 先清掉這個部門原本的範本項目，再依這次送出的內容重建（比逐筆
        # 比對新增/刪除簡單很多）
        DeptGoalTemplateItem.query.filter_by(department=department).delete()

        selected_ids = request.form.getlist('library_id')
        if not selected_ids:
            flash('請至少挑選一項硬指標', 'danger')
            return redirect(url_for('module_b_review.goal_setting_edit', department=department))

        total_weight = Decimal('0')
        new_rows = []
        try:
            for lib_id in selected_ids:
                target_value = request.form.get(f'target_{lib_id}', '').strip()
                weight_raw = request.form.get(f'weight_{lib_id}', '0').strip()
                weight = Decimal(weight_raw) if weight_raw else Decimal('0')
                total_weight += weight
                new_rows.append(DeptGoalTemplateItem(
                    department=department, library_id=int(lib_id),
                    target_value=target_value, weight_pct=weight,
                ))
        except InvalidOperation:
            db.session.rollback()
            flash('權重格式有誤，請輸入數字', 'danger')
            return redirect(url_for('module_b_review.goal_setting_edit', department=department))

        if abs(total_weight - 100) > Decimal('0.01'):
            db.session.rollback()
            flash(f'所有硬指標的權重加總必須等於 100（目前是 {total_weight}）', 'danger')
            return redirect(url_for('module_b_review.goal_setting_edit', department=department))

        db.session.add_all(new_rows)
        db.session.commit()
        flash(f'已儲存「{department}」的目標設定範本', 'success')
        return redirect(url_for('module_b_review.goal_setting_dashboard'))

    existing_items = ensure_dept_goal_template(department)

    return render_template(
        'module_b_goal_setting_form.html', department=department, library_options=library_options,
        weight_config=weight_config, hard_indicators=existing_items,
    )


# 人資審核（核准/駁回目標設定）
# ============================================================

@module_b_review_bp.route('/review/module-b/hr-approval')
@login_required
@admin_or_hr_manager_required
def hr_approval_dashboard():
    forms = (
        ModuleBForm.query
        .options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period))
        .filter(ModuleBForm.status == 'pending_hr_approval')
        .join(ReviewPeriod)
        .order_by(ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc())
        .all()
    )
    return render_template('module_b_hr_approval.html', forms=forms)


@module_b_review_bp.route('/review/module-b/hr-approval/<int:form_id>/detail')
@login_required
@admin_or_hr_manager_required
def hr_approval_detail(form_id):
    form = ModuleBForm.query.options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period)).get_or_404(form_id)
    hard_indicators = (
        ModuleBHardIndicator.query.filter_by(form_id=form.id)
        .options(joinedload(ModuleBHardIndicator.library_indicator))
        .order_by(ModuleBHardIndicator.id).all()
    )
    weight_config = get_dept_weight_config(form.engineer.department or '')
    return render_template(
        'module_b_hr_approval_detail.html', form=form, hard_indicators=hard_indicators,
        weight_config=weight_config,
    )


@module_b_review_bp.route('/review/module-b/hr-approval/<int:form_id>/approve', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def hr_approve(form_id):
    form = ModuleBForm.query.get_or_404(form_id)
    if form.status != 'pending_hr_approval':
        flash('此考核表目前不是「待審核」狀態', 'warning')
        return redirect(url_for('module_b_review.hr_approval_dashboard'))
    form.status = 'draft'
    form.goal_approved_at = datetime.utcnow()
    form.goal_approved_by_id = current_user.id
    _ensure_soft_indicators(form)
    db.session.commit()
    log_action('update', 'ModuleBForm', form.id, '核准目標設定')
    flash('已核准，員工可以開始填寫自評', 'success')
    return redirect(url_for('module_b_review.hr_approval_dashboard'))


@module_b_review_bp.route('/review/module-b/hr-approval/<int:form_id>/reject', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def hr_reject(form_id):
    form = ModuleBForm.query.get_or_404(form_id)
    if form.status != 'pending_hr_approval':
        flash('此考核表目前不是「待審核」狀態', 'warning')
        return redirect(url_for('module_b_review.hr_approval_dashboard'))
    form.status = 'goal_setting'
    db.session.commit()
    log_action('update', 'ModuleBForm', form.id, '駁回目標設定')
    flash('已駁回，部門主管需要重新設定目標', 'success')
    return redirect(url_for('module_b_review.hr_approval_dashboard'))


# ============================================================
# 待評分（直屬主管評分 / 最高直屬主管覆評）
# ============================================================

@module_b_review_bp.route('/review/module-b/pending')
@login_required
def scoring_dashboard():
    if current_user.role == 'admin':
        abort(403)
    forms = (
        ModuleBForm.query
        .join(Engineer, ModuleBForm.engineer_id == Engineer.id)
        .options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period))
        .filter(
            db.or_(
                Engineer.supervisor_id == current_user.id,
                db.and_(Engineer.top_supervisor_id == current_user.id, Engineer.supervisor_id.is_(None)),
            )
        )
        .filter(ModuleBForm.status.in_(['submitted', 'scored', 'finalized']))
        .join(ReviewPeriod)
        .order_by(ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc())
        .all()
    )
    executive_forms = (
        ModuleBForm.query
        .join(Engineer, ModuleBForm.engineer_id == Engineer.id)
        .options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period))
        .filter(Engineer.top_supervisor_id == current_user.id)
        .filter(Engineer.supervisor_id.isnot(None))
        .filter(ModuleBForm.status.in_(['scored', 'finalized']))
        .join(ReviewPeriod)
        .order_by(ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc())
        .all()
    )
    return render_template('module_b_pending.html', forms=forms, executive_forms=executive_forms)


def _is_form_fully_scored(form):
    """
    是否已經完整評分完成：直屬主管評分（supervisor_score）一定要全部填齊；
    如果這個人同時指定了直屬主管跟最高直屬主管（_needs_executive_review
    為 True），還要等最高直屬主管的覆評分數（executive_score）也全部填齊，
    才算真正完成——呼應模組A同一套「兩階段流程」的設計，不能只看主管
    評分填完就直接定案，不然覆評這一關會被跳過。
    """
    if not form.hard_indicators:
        return False
    for hi in form.hard_indicators:
        if hi.supervisor_score is None:
            return False
    for si in form.soft_indicators:
        if si.supervisor_score is None:
            return False
    if len(form.soft_indicators) != len(get_soft_dimensions()):
        return False

    if _needs_executive_review(form.engineer):
        for hi in form.hard_indicators:
            if hi.executive_score is None:
                return False
        for si in form.soft_indicators:
            if si.executive_score is None:
                return False

    return True


@module_b_review_bp.route('/review/module-b/pending/<int:form_id>', methods=['GET', 'POST'])
@login_required
def score_form(form_id):
    if current_user.role == 'admin':
        abort(403)
    form = ModuleBForm.query.options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period)).get_or_404(form_id)
    is_direct_supervisor = form.engineer.supervisor_id == current_user.id
    is_sole_top_supervisor = _is_sole_scorer(form.engineer, current_user.id)
    if not is_direct_supervisor and not is_sole_top_supervisor:
        abort(403)
    if form.status in ('goal_setting', 'pending_hr_approval', 'draft'):
        flash('此人尚未送出自評，無法評分', 'warning')
        return redirect(url_for('module_b_review.scoring_dashboard'))

    hard_indicators = (
        ModuleBHardIndicator.query.filter_by(form_id=form.id)
        .options(joinedload(ModuleBHardIndicator.library_indicator))
        .order_by(ModuleBHardIndicator.id).all()
    )
    soft_indicators = _ensure_soft_indicators(form)
    soft_dims = get_soft_dimensions()

    if request.method == 'POST':
        try:
            for hi in hard_indicators:
                raw = request.form.get(f'hard_score_{hi.id}', '').strip()
                hi.supervisor_score = Decimal(raw) if raw else None
                hi.supervisor_comment = request.form.get(f'hard_comment_{hi.id}', '').strip()
            for dim in soft_dims:
                si = soft_indicators[dim]
                score_raw = request.form.get(f'soft_score_{dim}', '').strip()
                si.supervisor_score = int(score_raw) if score_raw else None
        except (InvalidOperation, ValueError) as exc:
            flash(f'輸入的分數格式有誤：{exc}', 'danger')
            return redirect(url_for('module_b_review.score_form', form_id=form.id))

        form.scored_at = datetime.utcnow()
        if _is_form_fully_scored(form):
            form.status = 'finalized'
            flash('評分已完成並定案', 'success')
        else:
            form.status = 'scored'
            flash('已儲存評分，此人還需要最高直屬主管完成覆評', 'success')
        db.session.commit()
        if _is_form_fully_scored(form):
            from utils.module_b_ranking import recalculate_module_b_period_rankings
            recalculate_module_b_period_rankings(form.period_id)
        return redirect(url_for('module_b_review.scoring_dashboard'))

    weight_config = get_dept_weight_config(form.engineer.department or '')
    return render_template(
        'module_b_score_form.html', form=form, hard_indicators=hard_indicators,
        soft_indicators=soft_indicators, soft_dimensions=soft_dims,
        weight_config=weight_config, score_field='supervisor',
    )


@module_b_review_bp.route('/review/module-b/executive/<int:form_id>', methods=['GET', 'POST'])
@login_required
def executive_score_form(form_id):
    if current_user.role == 'admin':
        abort(403)
    form = ModuleBForm.query.options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period)).get_or_404(form_id)
    if form.engineer.top_supervisor_id != current_user.id or form.engineer.supervisor_id is None:
        abort(403)
    if form.status not in ('scored', 'finalized'):
        flash('此人尚未經過直屬主管評分，無法覆評', 'warning')
        return redirect(url_for('module_b_review.scoring_dashboard'))

    hard_indicators = (
        ModuleBHardIndicator.query.filter_by(form_id=form.id)
        .options(joinedload(ModuleBHardIndicator.library_indicator))
        .order_by(ModuleBHardIndicator.id).all()
    )
    soft_indicators = _ensure_soft_indicators(form)
    soft_dims = get_soft_dimensions()

    if request.method == 'POST':
        try:
            for hi in hard_indicators:
                raw = request.form.get(f'hard_score_{hi.id}', '').strip()
                hi.executive_score = Decimal(raw) if raw else None
            for dim in soft_dims:
                si = soft_indicators[dim]
                score_raw = request.form.get(f'soft_score_{dim}', '').strip()
                si.executive_score = int(score_raw) if score_raw else None
        except (InvalidOperation, ValueError) as exc:
            flash(f'輸入的分數格式有誤：{exc}', 'danger')
            return redirect(url_for('module_b_review.executive_score_form', form_id=form.id))

        if _is_form_fully_scored(form):
            form.status = 'finalized'
            form.executive_signed_at = datetime.utcnow()
            flash('覆評已完成並定案', 'success')
        else:
            flash('已儲存覆評分數', 'success')
        db.session.commit()
        if _is_form_fully_scored(form):
            from utils.module_b_ranking import recalculate_module_b_period_rankings
            recalculate_module_b_period_rankings(form.period_id)
        return redirect(url_for('module_b_review.scoring_dashboard'))

    weight_config = get_dept_weight_config(form.engineer.department or '')
    return render_template(
        'module_b_score_form.html', form=form, hard_indicators=hard_indicators,
        soft_indicators=soft_indicators, soft_dimensions=soft_dims,
        weight_config=weight_config, score_field='executive',
    )


# ============================================================
# 分數與排名（模組B）
# 設計比照模組A（routes/review_admin.py 的 rankings/recalculate/adjust_ranking），
# 差別只在改用 ModuleBRankingResult、排名計算邏輯改叫
# utils/module_b_ranking.py 這一套
# ============================================================

@module_b_review_bp.route('/review/module-b/periods/<int:period_id>/rankings')
@login_required
@admin_or_hr_manager_required
def rankings(period_id):
    period = ReviewPeriod.query.get_or_404(period_id)
    results = (
        ModuleBRankingResult.query.filter_by(period_id=period_id)
        .options(joinedload(ModuleBRankingResult.module_b_form).joinedload(ModuleBForm.engineer))
        .order_by(ModuleBRankingResult.company_rank)
        .all()
    )
    return render_template('module_b_rankings.html', period=period, results=results)


@module_b_review_bp.route('/review/module-b/periods/<int:period_id>/rankings/recalculate', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def recalculate_rankings(period_id):
    """
    手動重新計算一次模組B的排名。正常情況下每次評分/覆評送出時都會自動
    觸發，這個按鈕是給「補算」用的（例如剛匯入舊資料、或懷疑排名跟目前
    分數對不上時）。
    """
    from utils.module_b_ranking import recalculate_module_b_period_rankings
    period = ReviewPeriod.query.get_or_404(period_id)
    count = recalculate_module_b_period_rankings(period_id)
    log_action('update', 'ModuleBRankingResult', None,
               f'手動重新計算考核期間 {period.label}（模組B）的排名，共 {count} 筆')
    flash(f'已重新計算 {count} 張已完成評分的考核表排名', 'success')
    return redirect(url_for('module_b_review.rankings', period_id=period_id))


@module_b_review_bp.route('/review/module-b/rankings/<int:result_id>/adjust', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def adjust_ranking(result_id):
    """人資/管理者手動微調某人的排名分數（例如跨部門校準），不影響原始 total_score"""
    result = ModuleBRankingResult.query.get_or_404(result_id)
    adjusted_raw = request.form.get('adjusted_score', '').strip()
    note = request.form.get('adjusted_note', '').strip()

    if adjusted_raw:
        try:
            result.adjusted_score = Decimal(adjusted_raw)
        except InvalidOperation:
            flash('調整後分數格式有誤', 'danger')
            return redirect(url_for('module_b_review.rankings', period_id=result.period_id))
        result.adjusted_note = note or None
        result.adjusted_by_id = current_user.id
        result.adjusted_at = datetime.utcnow()
    else:
        result.adjusted_score = None
        result.adjusted_note = None
        result.adjusted_by_id = None
        result.adjusted_at = None

    db.session.commit()
    log_action('update', 'ModuleBRankingResult', result.id, f'調整排名分數為 {result.adjusted_score}')
    flash('已儲存調整', 'success')
    return redirect(url_for('module_b_review.rankings', period_id=result.period_id))
