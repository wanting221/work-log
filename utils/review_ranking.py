# -*- coding: utf-8 -*-
"""
utils/review_ranking.py - 分數計算與排名邏輯
設計參考「原績效考核系統」：

  - 每個考評項目：平均分數 = 最高直屬主管覆評分數與直屬主管評分的平均（若這位
    員工沒有指定最高直屬主管，則平均分數 = 直屬主管評分本身，不取平均）
  - 實得分數（單項）= 平均分數 x 權重
  - 實得分數合計 = 所有啟用中考評項目的實得分數加總
  - 部門內排名 = 同一考核期間、同一部門內，依實得分數合計高到低排序
  - 公司內排名 = 同一考核期間，全公司依實得分數合計高到低排序
  - 排名調整後實得分數：人資事後可手動微調（例如跨部門校準），不影響原始
    total_score，只疊加在 adjusted_score 欄位上
"""
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import joinedload, selectinload
from extensions import db
from models import ReviewForm, CriteriaScore, RankingResult, ScoreCriteria


def compute_total_score(review_form):
    """
    計算單張表單的實得分數合計。尚未開始評分、或任一「啟用中」項目尚未評分
    完成，都回傳 None（代表還不能排名），不會把「完全沒評分」誤算成 0 分。

    只加總「啟用中」的考評項目——跟 _is_form_fully_scored() 判斷「是否評分
    完整」用的是同一個範圍。這兩個函式必須用同一套範圍判斷，不然會出現
    「表單被判定已完整評分、狀態變成已定案，但這裡卻因為某個已停用、從沒
    被填過分數的舊項目而算出 None」的不一致，導致已定案的表單卻不會出現
    在排名清單裡（已停用的項目就是被停用了，本來就不該再要求它一定要有
    分數才能計算總分）。
    """
    if not review_form.criteria_scores:
        return None

    active_ids = {c.id for c in ScoreCriteria.query.filter_by(is_active=True).all()}

    total = Decimal('0')
    matched_count = 0
    for cs in review_form.criteria_scores:
        if cs.criteria_id not in active_ids:
            continue
        weighted = cs.weighted_score
        if weighted is None:
            return None
        total += weighted
        matched_count += 1

    if matched_count == 0:
        return None
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def recalculate_period_rankings(period_id):
    """
    重新計算某個考核期間內，所有「已完成評分」表單的總分與排名。
    尚未評分完成的表單會被跳過（不會產生排名），不影響其他人。

    效能優化：這個函式會在「每一次」直屬主管/最高直屬主管送出評分後被呼叫，
    是全系統執行頻率最高的地方之一。用 joinedload/selectinload 一次撈齊，
    不管有幾位員工，固定只需要 2~3 次查詢，不會變成「每張表單各自觸發
    N 次額外查詢」。
    """
    forms = (
        ReviewForm.query.filter_by(period_id=period_id)
        .options(
            joinedload(ReviewForm.engineer),
            selectinload(ReviewForm.criteria_scores).joinedload(CriteriaScore.criteria),
            joinedload(ReviewForm.ranking_result),
        )
        .all()
    )
    forms = [f for f in forms if f.engineer is not None]

    scored_entries = []  # (form, department, total_score)
    for form in forms:
        try:
            total = compute_total_score(form)
        except Exception:
            # 單一張表單的分數計算不該因為未預期的資料問題（例如日後又衍生
            # 出其他邊界情況）而拋出例外，導致整個期間所有其他人的排名都
            # 連帶算不出來、存不進去——這裡刻意用防禦性的方式把這張表單當作
            # 「暫時還不能排名」跳過，不影響其他人正常計算跟寫回結果。
            import logging
            logging.getLogger(__name__).exception(
                f'計算考核表總分時發生未預期的錯誤（review_form_id={form.id}），已跳過此表單'
            )
            continue
        if total is None:
            continue
        scored_entries.append((form, form.engineer.department, total))

    # 公司內排名：依總分由高到低，同分並列同一名次
    company_sorted = sorted(scored_entries, key=lambda e: e[2], reverse=True)
    company_rank_map = {}
    rank = 0
    prev_score = None
    for idx, (form, _dept, score) in enumerate(company_sorted, start=1):
        if score != prev_score:
            rank = idx
        company_rank_map[form.id] = rank
        prev_score = score

    # 部門內排名
    by_department = {}
    for form, dept, score in scored_entries:
        by_department.setdefault(dept, []).append((form, score))

    department_rank_map = {}
    for dept, entries in by_department.items():
        entries_sorted = sorted(entries, key=lambda e: e[1], reverse=True)
        rank = 0
        prev_score = None
        for idx, (form, score) in enumerate(entries_sorted, start=1):
            if score != prev_score:
                rank = idx
            department_rank_map[form.id] = rank
            prev_score = score

    # 寫回 RankingResult（保留既有的 adjusted_score，不因重新計算而消失）
    for form, dept, score in scored_entries:
        result = form.ranking_result
        if result is None:
            result = RankingResult(review_form_id=form.id)
            db.session.add(result)

        result.period_id = period_id
        result.department = dept
        result.total_score = score
        result.department_rank = department_rank_map.get(form.id)
        result.company_rank = company_rank_map.get(form.id)

        if form.status != 'finalized':
            form.status = 'scored'

    db.session.commit()
    return len(scored_entries)
