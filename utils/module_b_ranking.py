# -*- coding: utf-8 -*-
"""
utils/module_b_ranking.py - 模組B的分數計算與排名邏輯

跟模組A（utils/review_ranking.py）的整體設計概念一致，只是評分結構完全
不同，計算方式因此分開實作：

  - 硬指標小計（0~100）= 每個硬指標的 (供評分平均 x weight_pct/100) 加總
    （硬指標的 weight_pct 加總本來就應為 100，由目標設定畫面把關）
  - 軟指標小計（0~100）= 四個固定構面的供評分平均（1~5分）取平均後，
    換算成 0~100 的等比例分數
  - 供評分平均 = 最高直屬主管覆評分數與直屬主管評分的平均（若這位員工
    沒有指定最高直屬主管，則供評分平均 = 直屬主管評分本身，不取平均）
  - 實得分數合計 = 硬指標小計 x 部門硬指標權重% + 軟指標小計 x 部門軟指標權重%
  - 部門內排名／公司內排名：邏輯跟模組A完全一樣，只是分開一張表存
"""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import joinedload, selectinload
from extensions import db
from models import ModuleBForm, ModuleBRankingResult
from utils.report_utils import get_dept_weight_config


def _needs_executive_review(engineer):
    """跟模組A、模組B評分頁面用同一套判斷：直屬主管、最高直屬主管都有指定，才需要兩階段流程"""
    return engineer.supervisor_id is not None and engineer.top_supervisor_id is not None


def _avg_of(supervisor_score, executive_score, needs_executive):
    if supervisor_score is None:
        return None
    if needs_executive:
        if executive_score is None:
            return None
        return (Decimal(str(supervisor_score)) + Decimal(str(executive_score))) / 2
    return Decimal(str(supervisor_score))


def compute_module_b_total_score(form):
    """
    計算模組B單張考核表的實得分數合計（0~100）。任一必要分數還沒填、或
    這張表根本沒有硬指標/軟指標資料，都回傳 None（代表還不能排名）。

    刻意直接迭代 form.hard_indicators / form.soft_indicators 這張表「自己
    擁有」的評分明細，不去額外篩選部門指標庫目前是否還「啟用中」——這是
    吸取模組A那個 bug 的教訓：「評分完整性判斷」跟「總分計算」如果用不
    一樣的篩選範圍，會出現表單明明已經被判定評分完整、狀態變成已定案，
    總分卻因為範圍不一致而算不出來的不一致情況。這裡兩邊都只看表單自己
    的評分明細，不會有這個問題。
    """
    if not form.hard_indicators:
        return None

    needs_executive = _needs_executive_review(form.engineer)

    hard_total = Decimal('0')
    matched_hard = 0
    for hi in form.hard_indicators:
        avg = _avg_of(hi.supervisor_score, hi.executive_score, needs_executive)
        if avg is None:
            return None
        weight = Decimal(str(hi.weight_pct)) if hi.weight_pct is not None else Decimal('0')
        hard_total += avg * (weight / Decimal('100'))
        matched_hard += 1
    if matched_hard == 0:
        return None

    soft_scores = []
    for si in form.soft_indicators:
        avg = _avg_of(si.supervisor_score, si.executive_score, needs_executive)
        if avg is None:
            return None
        soft_scores.append(avg)
    if not soft_scores:
        return None
    soft_avg_1to5 = sum(soft_scores) / len(soft_scores)
    soft_total = soft_avg_1to5 / Decimal('5') * Decimal('100')

    weight_config = get_dept_weight_config(form.engineer.department or '')
    # get_dept_weight_config() 找不到部門自訂設定時，會退回 MODULE_B_DEFAULT_WEIGHT
    # 這份預設值，裡面存的是 float（50.0）不是 Decimal——Decimal 沒辦法直接跟
    # float 做四則運算會拋 TypeError，這裡統一先轉成字串再轉 Decimal，兩種
    # 情況（資料庫來的 Decimal、預設值的 float）都能安全處理
    hard_weight = Decimal(str(weight_config['hard']))
    soft_weight = Decimal(str(weight_config['soft']))

    total = hard_total * (hard_weight / Decimal('100')) + soft_total * (soft_weight / Decimal('100'))
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def recalculate_module_b_period_rankings(period_id):
    """
    重新計算模組B某個考核期間的所有排名。設計比照模組A的
    recalculate_period_rankings()：只有「已評分完整（能算出總分）」的表單
    才會進入排名；還沒評分完的人不會出現在排名清單，也不會佔用名次。

    每一張表單的分數計算都個別包一層防護，避免其中一張表單因為未預期的
    資料問題拋出例外，拖累同一個期間裡其他所有人的排名都連帶算不出來、
    存不進去（這是從模組A那次修正學到的教訓，這裡從一開始就先做好）。
    """
    forms = (
        ModuleBForm.query.filter_by(period_id=period_id)
        .options(
            joinedload(ModuleBForm.engineer),
            selectinload(ModuleBForm.hard_indicators),
            selectinload(ModuleBForm.soft_indicators),
        )
        .all()
    )
    forms = [f for f in forms if f.engineer is not None]

    scored_entries = []  # (form, department, total_score)
    for form in forms:
        try:
            total = compute_module_b_total_score(form)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                f'計算模組B考核表總分時發生未預期的錯誤（module_b_form_id={form.id}），已跳過此表單'
            )
            continue
        if total is None:
            continue
        scored_entries.append((form, form.engineer.department, total))

    # 公司內排名：全部一起由高到低排序
    scored_entries.sort(key=lambda e: e[2], reverse=True)
    company_rank_map = {}
    for idx, (form, dept, score) in enumerate(scored_entries, start=1):
        company_rank_map[form.id] = idx

    # 部門內排名：同部門的人再各自排一次
    by_department = {}
    for form, dept, score in scored_entries:
        by_department.setdefault(dept, []).append((form, score))
    department_rank_map = {}
    for dept, entries in by_department.items():
        entries.sort(key=lambda e: e[1], reverse=True)
        for idx, (form, score) in enumerate(entries, start=1):
            department_rank_map[form.id] = idx

    for form, dept, score in scored_entries:
        result = form.ranking_result
        if result is None:
            result = ModuleBRankingResult(module_b_form_id=form.id)
            db.session.add(result)
        result.period_id = period_id
        result.department = dept
        result.total_score = score
        result.department_rank = department_rank_map.get(form.id)
        result.company_rank = company_rank_map.get(form.id)

    db.session.commit()
    return len(scored_entries)
