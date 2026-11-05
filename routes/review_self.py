# -*- coding: utf-8 -*-
"""
routes/review_self.py - 我的考核表（員工自評）
開放給有填寫週報功能的角色使用（員工、部門主管、組別主管），加上人資主管
（人資主管不用填週報，但需要填自己的績效自評）。

dashboard() 這個列表頁整合顯示模組A、模組B兩邊的考核表（同一個人在不同
考核期間，可能有模組A的表、也可能有模組B的表，取決於那個期間選了哪個
模組），統一用一份清單呈現，不用分開兩個頁面各自查看。
"""
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from extensions import db
from models import ReviewForm, ReviewPeriod, ModuleBForm
from utils.report_utils import REVIEW_SELF_ROLES

review_self_bp = Blueprint('review_self', __name__)

# 模組B比模組A多兩個「目標設定」相關的狀態，這兩個狀態下員工還不能填寫，
# 只能看到「尚未開放填寫」的提示
MODULE_B_PRE_FILL_STATUSES = ('goal_setting', 'pending_hr_approval')


@review_self_bp.route('/review/my-forms')
@login_required
def dashboard():
    if current_user.role not in REVIEW_SELF_ROLES:
        abort(403)

    # 注意：這裡刻意不帶出評分內容給樣板使用，員工只看得到「填寫狀態」，
    # 不會看到分數（跟原本績效考核系統的設計一致）。
    module_a_forms = (
        ReviewForm.query.filter_by(engineer_id=current_user.id)
        .join(ReviewPeriod)
        .all()
    )
    module_b_forms = (
        ModuleBForm.query.filter_by(engineer_id=current_user.id)
        .join(ReviewPeriod)
        .all()
    )

    rows = []
    for f in module_a_forms:
        rows.append({
            'period': f.period, 'module': 'A', 'status': f.status,
            'edit_url': url_for('review_self.edit_review', form_id=f.id),
        })
    for f in module_b_forms:
        can_view = f.status not in MODULE_B_PRE_FILL_STATUSES
        rows.append({
            'period': f.period, 'module': 'B', 'status': f.status,
            'edit_url': url_for('module_b_review.self_edit', form_id=f.id) if can_view else None,
        })
    rows.sort(key=lambda r: (r['period'].roc_year, r['period'].quarter), reverse=True)

    return render_template('review_my_forms.html', rows=rows)


@review_self_bp.route('/review/my-forms/<int:form_id>', methods=['GET', 'POST'])
@login_required
def edit_review(form_id):
    if current_user.role not in REVIEW_SELF_ROLES:
        abort(403)
    form = ReviewForm.query.get_or_404(form_id)
    if form.engineer_id != current_user.id:
        abort(403)

    period = form.period
    deadline_passed = bool(period.self_report_deadline and date.today() > period.self_report_deadline)
    can_edit = form.status == 'draft' and period.is_open and not deadline_passed

    if request.method == 'POST':
        if not can_edit:
            flash('此考核表目前無法編輯（可能已送出、期間已關閉或已過填寫期限）', 'warning')
            return redirect(url_for('review_self.edit_review', form_id=form.id))

        form.revenue_report = request.form.get('revenue_report', '').strip()
        form.cost_report = request.form.get('cost_report', '').strip()
        form.risk_report = request.form.get('risk_report', '').strip()

        action = request.form.get('action')
        if action == 'submit':
            from datetime import datetime
            form.status = 'submitted'
            form.submitted_at = datetime.utcnow()
            flash('已送出自評，主管將開始進行評分', 'success')
        else:
            flash('已儲存草稿', 'success')

        db.session.commit()
        return redirect(url_for('review_self.dashboard'))

    return render_template('review_my_form_edit.html', form=form, period=period, can_edit=can_edit)
