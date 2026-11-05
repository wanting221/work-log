# -*- coding: utf-8 -*-
"""
routes/review_executive.py - 待覆評（最高直屬主管覆評）
設計參考「原績效考核系統」的高管覆評流程：如果某人的「最高直屬主管」
欄位指定了你，這張考核表在直屬主管完成第一階段評分後，會出現在你的
待覆評清單，需要你也填一次分數，兩階段分數會取平均當作最終實得分數。
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from extensions import db
from models import ReviewForm, ReviewPeriod, CriteriaScore, ScoreCriteria, Engineer
from routes.review_scoring import _is_form_fully_scored
from utils.review_ranking import recalculate_period_rankings

review_executive_bp = Blueprint('review_executive', __name__)


@review_executive_bp.route('/review/executive-pending')
@login_required
def dashboard():
    if current_user.role == 'admin':
        # 管理者角色不具備待覆評功能——這是刻意的設計，不是漏掉權限檢查，
        # 管理者要看整體狀況請用「考核期間管理」裡的「管理考核表」頁面
        abort(403)
    forms = (
        ReviewForm.query
        .join(Engineer, ReviewForm.engineer_id == Engineer.id)
        .options(joinedload(ReviewForm.engineer), joinedload(ReviewForm.period))
        .filter(Engineer.top_supervisor_id == current_user.id)
        # 只有同時指定了直屬主管的人，才是真正的兩階段流程；如果這位員工沒有
        # 指定直屬主管（只指定了最高直屬主管），那麼最高直屬主管在「待評分」
        # 那一關就已經是唯一評分者、一次評完直接定案了，不會、也不需要
        # 再出現在這個「待覆評」清單裡。
        .filter(Engineer.supervisor_id.isnot(None))
        # 直屬主管還沒完成第一階段評分（狀態還是 submitted）之前，覆評無從評起，
        # 只有直屬主管已經評過（scored/finalized）才會出現在覆評清單
        .filter(ReviewForm.status.in_(['scored', 'finalized']))
        .join(ReviewPeriod)
        .order_by(ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc())
        .all()
    )
    return render_template('review_executive_pending.html', forms=forms)


@review_executive_bp.route('/review/executive-pending/<int:form_id>', methods=['GET', 'POST'])
@login_required
def score_form(form_id):
    if current_user.role == 'admin':
        # 管理者角色不具備待覆評功能——這是刻意的設計，不是漏掉權限檢查
        abort(403)
    form = ReviewForm.query.options(joinedload(ReviewForm.engineer), joinedload(ReviewForm.period)).get_or_404(form_id)
    is_two_stage_executive = (
        form.engineer.top_supervisor_id == current_user.id and form.engineer.supervisor_id is not None
    )
    if not is_two_stage_executive:
        # 安全檢查：直接改網址想覆評不是自己負責的人也要擋下來，不能只靠清單
        # 只列出自己範圍的表單來擋（清單只是 UI 上的方便，不能當作真正的權限控管）；
        # 這裡也要擋下「這位員工其實沒有直屬主管、單一評分者情境」的表單，
        # 那種情境的評分已經在「待評分」階段直接定案，這裡沒有覆評可做
        abort(403)
    if form.status not in ('scored', 'finalized'):
        flash('直屬主管尚未完成第一階段評分，無法覆評', 'warning')
        return redirect(url_for('review_executive.dashboard'))

    criteria_list = ScoreCriteria.query.filter_by(is_active=True).order_by(ScoreCriteria.sort_order).all()
    existing = {cs.criteria_id: cs for cs in form.criteria_scores}

    if request.method == 'POST':
        try:
            for crit in criteria_list:
                raw = request.form.get(f'score_{crit.id}', '').strip()
                cs = existing.get(crit.id)
                if cs is None:
                    cs = CriteriaScore(review_form_id=form.id, criteria_id=crit.id)
                    db.session.add(cs)
                    existing[crit.id] = cs
                if raw == '':
                    cs.executive_score = None
                    continue
                value = Decimal(raw)
                if value < 0 or value > 100:
                    raise ValueError('分數必須介於 0 到 100 之間')
                cs.executive_score = value
        except (InvalidOperation, ValueError) as exc:
            flash(f'輸入的分數格式有誤：{exc}', 'danger')
            return redirect(url_for('review_executive.score_form', form_id=form.id))

        form.executive_signed_at = datetime.utcnow()
        if _is_form_fully_scored(form):
            form.status = 'finalized'
            flash('覆評已完成並定案', 'success')
        else:
            flash('已儲存覆評', 'success')

        db.session.commit()
        if _is_form_fully_scored(form):
            recalculate_period_rankings(form.period_id)
        return redirect(url_for('review_executive.dashboard'))

    return render_template(
        'review_executive_score_form.html', form=form, criteria_list=criteria_list,
        scores={cid: cs for cid, cs in existing.items()},
    )
