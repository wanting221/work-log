# -*- coding: utf-8 -*-
"""
routes/review_scoring.py - 待評分（直屬主管為下屬評分）
設計參考「原績效考核系統」：誰能評誰的分，是看 Engineer.supervisor_id
這個欄位——如果某人的「直屬主管」欄位指定了你，這張考核表就會出現在你的
待評分清單，不是看部門/組別範圍。
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from extensions import db
from models import ReviewForm, ReviewPeriod, CriteriaScore, ScoreCriteria, Engineer, ModuleBForm
from utils.review_ranking import recalculate_period_rankings

review_scoring_bp = Blueprint('review_scoring', __name__)


def _needs_executive_review(engineer):
    """
    是否需要「直屬主管評分 → 最高直屬主管覆評」兩階段流程：只有這位員工
    同時指定了直屬主管跟最高直屬主管，才需要兩階段。如果只指定了其中一項
    （例如只設了最高直屬主管、沒設直屬主管），那唯一被指定的那位就是
    唯一需要打分數的人，一次評完直接定案，不需要再多一道覆評關卡。
    """
    return engineer.supervisor_id is not None and engineer.top_supervisor_id is not None


def _is_sole_scorer(engineer, user_id):
    """
    這個人是不是「唯一需要為此員工打分數的人」：可能是唯一被指定的直屬主管
    （沒有最高直屬主管），也可能是唯一被指定的最高直屬主管（沒有直屬主管）。
    """
    if engineer.supervisor_id == user_id and engineer.top_supervisor_id is None:
        return True
    if engineer.top_supervisor_id == user_id and engineer.supervisor_id is None:
        return True
    return False


def _is_form_fully_scored(form):
    """
    是否已經完整評分完成：所有啟用中的考評項目都要有主管評分；
    只有在同時指定了直屬主管與最高直屬主管的情況下，才需要再等最高直屬主管
    的覆評分數也都填了，才算真正完成（呼應原系統 needs_executive_review 的
    概念，這裡改成直接看 Engineer 的主管欄位是否同時指定，不需要每張表單
    各自標記）。
    """
    if not form.criteria_scores:
        return False
    active_ids = {c.id for c in ScoreCriteria.query.filter_by(is_active=True).all()}
    if not active_ids:
        return False
    needs_executive = _needs_executive_review(form.engineer)
    scored_ids = set()
    for cs in form.criteria_scores:
        if cs.criteria_id not in active_ids:
            continue
        if cs.supervisor_score is None:
            return False
        if needs_executive and cs.executive_score is None:
            return False
        scored_ids.add(cs.criteria_id)
    return active_ids.issubset(scored_ids)


@review_scoring_bp.route('/review/pending')
@login_required
def dashboard():
    if current_user.role == 'admin':
        # 管理者角色不具備待評分功能——這是刻意的設計，不是漏掉權限檢查，
        # 管理者要看整體狀況請用「考核期間管理」裡的「管理考核表」頁面
        abort(403)
    module_a_forms = (
        ReviewForm.query
        .join(Engineer, ReviewForm.engineer_id == Engineer.id)
        .options(joinedload(ReviewForm.engineer), joinedload(ReviewForm.period))
        .filter(
            db.or_(
                Engineer.supervisor_id == current_user.id,
                # 只指定了最高直屬主管、沒有直屬主管時，最高直屬主管就是
                # 唯一需要打分數的人，這裡也要讓他在「待評分」看得到，
                # 不然這個人的考核表會卡在沒有人能評分的狀態
                db.and_(Engineer.top_supervisor_id == current_user.id, Engineer.supervisor_id.is_(None)),
            )
        )
        .filter(ReviewForm.status.in_(['submitted', 'scored', 'finalized']))
        .join(ReviewPeriod)
        .all()
    )
    module_b_forms = (
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
        .all()
    )

    forms = []
    for f in module_a_forms:
        forms.append({
            'module': 'A', 'period': f.period, 'engineer': f.engineer, 'status': f.status,
            'score_url': url_for('review_scoring.score_form', form_id=f.id),
        })
    for f in module_b_forms:
        forms.append({
            'module': 'B', 'period': f.period, 'engineer': f.engineer, 'status': f.status,
            'score_url': url_for('module_b_review.score_form', form_id=f.id),
        })
    forms.sort(key=lambda r: (r['period'].roc_year, r['period'].quarter), reverse=True)

    # 待覆評的表格整合在同一頁下方顯示（不再是獨立頁面），這裡沿用
    # routes/review_executive.py 裡 dashboard() 的同一套查詢邏輯
    module_a_exec_forms = (
        ReviewForm.query
        .join(Engineer, ReviewForm.engineer_id == Engineer.id)
        .options(joinedload(ReviewForm.engineer), joinedload(ReviewForm.period))
        .filter(Engineer.top_supervisor_id == current_user.id)
        .filter(Engineer.supervisor_id.isnot(None))
        .filter(ReviewForm.status.in_(['scored', 'finalized']))
        .join(ReviewPeriod)
        .all()
    )
    module_b_exec_forms = (
        ModuleBForm.query
        .join(Engineer, ModuleBForm.engineer_id == Engineer.id)
        .options(joinedload(ModuleBForm.engineer), joinedload(ModuleBForm.period))
        .filter(Engineer.top_supervisor_id == current_user.id)
        .filter(Engineer.supervisor_id.isnot(None))
        .filter(ModuleBForm.status.in_(['scored', 'finalized']))
        .join(ReviewPeriod)
        .all()
    )

    executive_forms = []
    for f in module_a_exec_forms:
        executive_forms.append({
            'module': 'A', 'period': f.period, 'engineer': f.engineer, 'status': f.status,
            'score_url': url_for('review_executive.score_form', form_id=f.id),
        })
    for f in module_b_exec_forms:
        executive_forms.append({
            'module': 'B', 'period': f.period, 'engineer': f.engineer, 'status': f.status,
            'score_url': url_for('module_b_review.executive_score_form', form_id=f.id),
        })
    executive_forms.sort(key=lambda r: (r['period'].roc_year, r['period'].quarter), reverse=True)

    return render_template('review_pending.html', forms=forms, executive_forms=executive_forms)


@review_scoring_bp.route('/review/pending/<int:form_id>', methods=['GET', 'POST'])
@login_required
def score_form(form_id):
    if current_user.role == 'admin':
        # 管理者角色不具備待評分功能——這是刻意的設計，不是漏掉權限檢查
        abort(403)
    form = ReviewForm.query.options(joinedload(ReviewForm.engineer), joinedload(ReviewForm.period)).get_or_404(form_id)
    is_direct_supervisor = form.engineer.supervisor_id == current_user.id
    is_sole_top_supervisor = _is_sole_scorer(form.engineer, current_user.id)
    if not is_direct_supervisor and not is_sole_top_supervisor:
        # 安全檢查：直接改網址想評不是自己下屬的人也要擋下來，不能只靠清單
        # 只列出自己下屬的表單來擋（清單只是 UI 上的方便，不能當作真正的權限控管）
        abort(403)
    if form.status == 'draft':
        flash('此人尚未送出自評，無法評分', 'warning')
        return redirect(url_for('review_scoring.dashboard'))

    criteria_list = ScoreCriteria.query.filter_by(is_active=True).order_by(ScoreCriteria.sort_order).all()

    # 確保每個 criteria 都有一筆 CriteriaScore（第一次進來時建立空白列）
    existing = {cs.criteria_id: cs for cs in form.criteria_scores}
    for crit in criteria_list:
        if crit.id not in existing:
            cs = CriteriaScore(review_form_id=form.id, criteria_id=crit.id)
            db.session.add(cs)
            existing[crit.id] = cs
    if len(existing) > len(form.criteria_scores):
        db.session.commit()

    if request.method == 'POST':
        try:
            for crit in criteria_list:
                raw = request.form.get(f'score_{crit.id}', '').strip()
                cs = existing[crit.id]
                if raw == '':
                    cs.supervisor_score = None
                    continue
                value = Decimal(raw)
                if value < 0 or value > 100:
                    raise ValueError('分數必須介於 0 到 100 之間')
                cs.supervisor_score = value
        except (InvalidOperation, ValueError) as exc:
            flash(f'輸入的分數格式有誤：{exc}', 'danger')
            return redirect(url_for('review_scoring.score_form', form_id=form.id))

        form.scored_at = datetime.utcnow()
        if _is_form_fully_scored(form):
            form.status = 'finalized'
            flash('評分已完成並定案', 'success')
        else:
            form.status = 'scored'
            flash('已儲存評分，此人還需要最高直屬主管完成覆評', 'success')

        db.session.commit()
        if _is_form_fully_scored(form):
            recalculate_period_rankings(form.period_id)
        return redirect(url_for('review_scoring.dashboard'))

    return render_template(
        'review_score_form.html', form=form, criteria_list=criteria_list,
        scores={cid: cs for cid, cs in existing.items()},
    )
