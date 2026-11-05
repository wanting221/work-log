# -*- coding: utf-8 -*-
"""
routes/review_admin.py - 績效考核系統管理（考核期間、考評項目），限管理者使用
從「原績效考核系統」整合進來，人員資料改用週報系統既有的 Engineer，
不再另外維護一份 User/Department 資料表。
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from extensions import db
from models import (
    ReviewPeriod, ScoreCriteria, ReviewForm, Engineer, RankingResult,
    ModuleBForm, ModuleBHardIndicator, ModuleBSoftIndicator,
)
from utils.decorators import log_action, admin_or_hr_manager_required, admin_required
from utils.report_utils import REVIEW_SELF_ROLES, get_soft_dimensions, ensure_dept_goal_template
from utils.review_excel_io import (
    export_periods_workbook, import_periods_from_workbook,
    export_criteria_workbook, import_criteria_from_workbook,
)
from utils.review_ranking import recalculate_period_rankings

review_admin_bp = Blueprint('review_admin', __name__)

ROC_YEAR_OFFSET = 1911  # 民國年轉西元年：西元年 = 民國年 + 1911

# 標準日曆季對照表：Q1 01/01~03/31、Q2 04/01~06/30、Q3 07/01~09/30、Q4 10/01~12/31
QUARTER_MONTH_RANGES = {
    1: ((1, 1), (3, 31)),
    2: ((4, 1), (6, 30)),
    3: ((7, 1), (9, 30)),
    4: ((10, 1), (12, 31)),
}


def compute_quarter_dates(roc_year, quarter):
    """依民國年 + 季別自動算出標準日曆季的起訖日期，回傳 (start_date, end_date)"""
    if quarter not in QUARTER_MONTH_RANGES:
        raise ValueError('季別必須是 1~4')
    ad_year = roc_year + ROC_YEAR_OFFSET
    (start_month, start_day), (end_month, end_day) = QUARTER_MONTH_RANGES[quarter]
    return date(ad_year, start_month, start_day), date(ad_year, end_month, end_day)


# ---------------------------------------------------------------------------
# 考核期間管理
# ---------------------------------------------------------------------------
@review_admin_bp.route('/review/periods', methods=['GET', 'POST'])
@login_required
@admin_or_hr_manager_required
def periods():
    if request.method == 'POST':
        try:
            roc_year = int(request.form.get('roc_year'))
            quarter = int(request.form.get('quarter'))

            start_date_str = request.form.get('start_date', '').strip()
            end_date_str = request.form.get('end_date', '').strip()
            if start_date_str and end_date_str:
                start_date = date.fromisoformat(start_date_str)
                end_date = date.fromisoformat(end_date_str)
            else:
                start_date, end_date = compute_quarter_dates(roc_year, quarter)

            deadline_str = request.form.get('self_report_deadline', '').strip()
            deadline = date.fromisoformat(deadline_str) if deadline_str else None
        except (TypeError, ValueError) as exc:
            flash(f'日期或季別格式錯誤：{exc}', 'danger')
            return redirect(url_for('review_admin.periods'))

        module = request.form.get('module', 'A')
        if module not in ('A', 'B'):
            module = 'A'

        if ReviewPeriod.query.filter_by(roc_year=roc_year, quarter=quarter).first():
            flash('此考核期間已存在', 'danger')
            return redirect(url_for('review_admin.periods'))

        period = ReviewPeriod(
            roc_year=roc_year, quarter=quarter, start_date=start_date, end_date=end_date,
            self_report_deadline=deadline, is_open=True, module=module,
        )
        db.session.add(period)
        db.session.commit()
        log_action('create', 'ReviewPeriod', period.id, f'新增考核期間 {period.label}')
        flash(f'考核期間 {period.label} 已建立（{start_date} ~ {end_date}）', 'success')
        return redirect(url_for('review_admin.periods'))

    all_periods = ReviewPeriod.query.order_by(
        ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc()
    ).all()
    current_roc_year = date.today().year - ROC_YEAR_OFFSET
    return render_template('review_periods.html', periods=all_periods, current_roc_year=current_roc_year)


@review_admin_bp.route('/review/periods/<int:period_id>/update', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def update_period(period_id):
    period = ReviewPeriod.query.get_or_404(period_id)
    try:
        new_roc_year = int(request.form.get('roc_year'))
        new_quarter = int(request.form.get('quarter'))

        start_date_str = request.form.get('start_date', '').strip()
        end_date_str = request.form.get('end_date', '').strip()
        if start_date_str and end_date_str:
            new_start_date = date.fromisoformat(start_date_str)
            new_end_date = date.fromisoformat(end_date_str)
        else:
            new_start_date, new_end_date = compute_quarter_dates(new_roc_year, new_quarter)

        deadline_str = request.form.get('self_report_deadline', '').strip()
        deadline = date.fromisoformat(deadline_str) if deadline_str else None
    except (TypeError, ValueError) as exc:
        flash(f'日期或季別格式錯誤：{exc}', 'danger')
        return redirect(url_for('review_admin.periods'))

    conflict = ReviewPeriod.query.filter(
        ReviewPeriod.roc_year == new_roc_year, ReviewPeriod.quarter == new_quarter,
        ReviewPeriod.id != period.id,
    ).first()
    if conflict is not None:
        flash('已經有另一個考核期間使用相同的民國年+季別', 'danger')
        return redirect(url_for('review_admin.periods'))

    period.roc_year = new_roc_year
    period.quarter = new_quarter
    period.start_date = new_start_date
    period.end_date = new_end_date
    period.self_report_deadline = deadline
    db.session.commit()
    log_action('update', 'ReviewPeriod', period.id, f'更新考核期間 {period.label}')
    flash(f'考核期間 {period.label} 已更新', 'success')
    return redirect(url_for('review_admin.periods'))


@review_admin_bp.route('/review/periods/<int:period_id>/delete', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def delete_period(period_id):
    """
    刪除考核期間，會連同底下所有考核表（含各自的考評項目評分、排名結果）
    一併刪除，這是不可復原的操作，所以要求管理者在表單上輸入這個期間的
    標籤（例如 115Q2）再次確認，不是只靠瀏覽器的 confirm() 彈窗。
    """
    period = ReviewPeriod.query.get_or_404(period_id)
    confirm_label = request.form.get('confirm_label', '').strip()
    if confirm_label != period.label:
        flash(f'確認文字不正確，請輸入完整的期間標籤「{period.label}」才能刪除', 'danger')
        return redirect(url_for('review_admin.periods'))

    label = period.label
    form_count = ReviewForm.query.filter_by(period_id=period_id).count()
    db.session.delete(period)  # cascade='all, delete-orphan' 會一併刪除底下的考核表
    db.session.commit()
    log_action('delete', 'ReviewPeriod', period_id, f'刪除考核期間 {label}（含 {form_count} 張考核表）')
    flash(f'考核期間 {label} 已刪除（含 {form_count} 張考核表）', 'success')
    return redirect(url_for('review_admin.periods'))


@review_admin_bp.route('/review/periods/<int:period_id>/toggle-open', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def toggle_period_open(period_id):
    period = ReviewPeriod.query.get_or_404(period_id)
    period.is_open = not period.is_open
    db.session.commit()
    log_action('update', 'ReviewPeriod', period.id, f'考核期間 {period.label} 狀態切換為 {period.is_open}')
    flash(f"考核期間 {period.label} 已{'開放' if period.is_open else '關閉'}", 'success')
    return redirect(url_for('review_admin.periods'))


@review_admin_bp.route('/review/periods/<int:period_id>/detail')
@login_required
@admin_or_hr_manager_required
def period_detail(period_id):
    """
    考核表管理：列出這個期間所有人的考核表狀態、直屬主管、最高直屬主管。
    設計參考「原績效考核系統」，差異是這裡的直屬主管/最高直屬主管是
    固定在 Engineer 帳號本身的欄位（人員帳號管理頁面設定），不是每張表單
    各自指定，所以沒有原系統那個「設定」按鈕可以個別調整。

    這個期間是模組A還是模組B，查詢的資料表不一樣（ReviewForm vs
    ModuleBForm），統一轉成同一份格式的清單給樣板使用，畫面看起來一致。
    """
    from sqlalchemy.orm import joinedload
    period = ReviewPeriod.query.get_or_404(period_id)

    if period.module == 'B':
        raw_forms = (
            ModuleBForm.query.filter_by(period_id=period_id)
            .options(
                joinedload(ModuleBForm.engineer).joinedload(Engineer.supervisor),
                joinedload(ModuleBForm.engineer).joinedload(Engineer.top_supervisor),
            )
            .join(Engineer, ModuleBForm.engineer_id == Engineer.id)
            .order_by(Engineer.sort_order, Engineer.id)
            .all()
        )
    else:
        raw_forms = (
            ReviewForm.query.filter_by(period_id=period_id)
            .options(
                joinedload(ReviewForm.engineer).joinedload(Engineer.supervisor),
                joinedload(ReviewForm.engineer).joinedload(Engineer.top_supervisor),
            )
            .join(Engineer)
            .order_by(Engineer.sort_order, Engineer.id)
            .all()
        )

    forms = [{'engineer': f.engineer, 'status': f.status} for f in raw_forms]
    return render_template('review_period_detail.html', period=period, forms=forms)


@review_admin_bp.route('/review/periods/<int:period_id>/generate-forms', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def generate_forms(period_id):
    """
    為此考核期間、在職中「有填寫週報功能的角色」（員工/部門主管/組別主管）
    加上人資主管，建立考核表，若已存在則略過。管理者、唯讀角色不需要填
    考核表，不會產生。

    直屬主管、最高直屬主管兩個欄位都沒有指定的人也會略過不產生考核表——
    這種人沒有任何人可以幫他評分，讓他填了自評也只會卡在「已送出」狀態
    永遠評不完，不如一開始就不產生，等人資補上主管欄位後再重新產生即可
    （已存在的考核表不會被這裡的規則影響，只影響「這次要不要新建」的判斷）。
    """
    period = ReviewPeriod.query.get_or_404(period_id)
    engineers = Engineer.query.filter(
        Engineer.status == 'active', Engineer.role.in_(REVIEW_SELF_ROLES)
    ).all()

    created = 0
    skipped_no_supervisor = 0
    for eng in engineers:
        if eng.supervisor_id is None and eng.top_supervisor_id is None:
            skipped_no_supervisor += 1
            continue
        if period.module == 'B':
            existing = ModuleBForm.query.filter_by(engineer_id=eng.id, period_id=period.id).first()
            if existing is not None:
                continue
            # 項目3、4：模組B不再需要「目標設定中」這個等待步驟——同一部門
            # 共用同一份目標設定範本（DeptGoalTemplateItem），產生考核表時
            # 直接把當下的範本內容複製一份過來，狀態直接是 draft（可以
            # 自評），並順手建立四個軟指標構面的空白列
            new_form = ModuleBForm(engineer_id=eng.id, period_id=period.id, status='draft')
            db.session.add(new_form)
            db.session.flush()

            template_items = ensure_dept_goal_template(eng.department or '')
            for item in template_items:
                db.session.add(ModuleBHardIndicator(
                    form_id=new_form.id, library_id=item.library_id,
                    target_value=item.target_value, weight_pct=item.weight_pct,
                ))
            for dim in get_soft_dimensions():
                db.session.add(ModuleBSoftIndicator(form_id=new_form.id, dimension=dim))
        else:
            existing = ReviewForm.query.filter_by(engineer_id=eng.id, period_id=period.id).first()
            if existing is not None:
                continue
            db.session.add(ReviewForm(engineer_id=eng.id, period_id=period.id, status='draft'))
        created += 1

    db.session.commit()
    period.forms_generated_at = datetime.utcnow()
    db.session.commit()
    log_action('create', 'ReviewForm', None, f'為考核期間 {period.label} 產生 {created} 張考核表')
    msg = f'已為 {created} 位在職人員建立考核表'
    if period.module == 'B':
        msg += '（模組B：請部門主管先進行目標設定）'
    if skipped_no_supervisor:
        msg += f'；另有 {skipped_no_supervisor} 位因為沒有指定直屬主管或最高直屬主管，暫不產生考核表（請到人員帳號管理頁面補上後再重新產生）'
    flash(msg, 'success')
    return redirect(url_for('review_admin.periods'))


@review_admin_bp.route('/review/periods/export')
@login_required
@admin_or_hr_manager_required
def periods_export():
    buffer = export_periods_workbook()
    log_action('export', 'ReviewPeriod', None, '匯出考核期間 Excel')
    return send_file(buffer, as_attachment=True, download_name='考核期間.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@review_admin_bp.route('/review/periods/import', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def periods_import():
    uploaded = request.files.get('excel_file')
    if uploaded is None or uploaded.filename == '':
        flash('請選擇要匯入的 Excel 檔案', 'danger')
        return redirect(url_for('review_admin.periods'))

    try:
        result = import_periods_from_workbook(uploaded)
    except Exception as exc:  # noqa: BLE001
        flash(f'匯入失敗，檔案格式可能不正確：{exc}', 'danger')
        return redirect(url_for('review_admin.periods'))

    log_action('import', 'ReviewPeriod', None,
               f'匯入考核期間 Excel，新增{result["created"]}筆，更新{result["updated"]}筆，錯誤{len(result["errors"])}筆')
    msg = f'匯入完成：新增 {result["created"]} 筆，更新 {result["updated"]} 筆'
    if result['errors']:
        error_lines = '；'.join(result['errors'][:5])
        msg += f'（{len(result["errors"])} 筆有問題：{error_lines}{"..." if len(result["errors"]) > 5 else ""}）'
        flash(msg, 'warning')
    else:
        flash(msg, 'success')
    return redirect(url_for('review_admin.periods'))


# ---------------------------------------------------------------------------
# 考評項目管理
# ---------------------------------------------------------------------------
@review_admin_bp.route('/review/criteria')
@login_required
@admin_or_hr_manager_required
def criteria():
    criteria_list = ScoreCriteria.query.order_by(ScoreCriteria.sort_order).all()
    return render_template('review_criteria.html', criteria_list=criteria_list)


@review_admin_bp.route('/review/criteria/<int:criteria_id>/update', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def update_criteria(criteria_id):
    crit = ScoreCriteria.query.get_or_404(criteria_id)
    name = request.form.get('name', '').strip()
    if not name:
        flash('考評項目名稱不可空白', 'danger')
        return redirect(url_for('review_admin.criteria'))

    try:
        weight = Decimal(request.form.get('weight', '').strip())
    except InvalidOperation:
        flash('權重格式錯誤，請輸入 0~1 之間的小數（例如 0.5 代表 50%）', 'danger')
        return redirect(url_for('review_admin.criteria'))
    if weight < 0 or weight > 1:
        flash('權重必須介於 0~1 之間', 'danger')
        return redirect(url_for('review_admin.criteria'))

    crit.name = name
    crit.weight = weight.quantize(Decimal('0.01'))
    crit.is_active = request.form.get('is_active') == 'on'
    db.session.commit()
    log_action('update', 'ScoreCriteria', crit.id, f'{crit.code}: weight={crit.weight}')
    flash('考評項目已更新', 'success')
    return redirect(url_for('review_admin.criteria'))


@review_admin_bp.route('/review/criteria/create', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def create_criteria():
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    if not code or not name:
        flash('代碼與說明文字皆為必填', 'danger')
        return redirect(url_for('review_admin.criteria'))
    if ScoreCriteria.query.filter_by(code=code).first():
        flash(f'代碼「{code}」已經存在，請換一個代碼', 'danger')
        return redirect(url_for('review_admin.criteria'))

    try:
        weight = Decimal(request.form.get('weight', '').strip())
    except InvalidOperation:
        flash('權重格式錯誤，請輸入 0~1 之間的小數（例如 0.5 代表 50%）', 'danger')
        return redirect(url_for('review_admin.criteria'))
    if weight < 0 or weight > 1:
        flash('權重必須介於 0~1 之間', 'danger')
        return redirect(url_for('review_admin.criteria'))

    max_sort_order = db.session.query(db.func.max(ScoreCriteria.sort_order)).scalar() or 0
    crit = ScoreCriteria(
        code=code, name=name, weight=weight.quantize(Decimal('0.01')),
        sort_order=max_sort_order + 1, is_active=request.form.get('is_active') == 'on',
    )
    db.session.add(crit)
    db.session.commit()
    log_action('create', 'ScoreCriteria', crit.id, code)
    flash('考評項目已新增', 'success')
    return redirect(url_for('review_admin.criteria'))


@review_admin_bp.route('/review/criteria/<int:criteria_id>/delete', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def delete_criteria(criteria_id):
    crit = ScoreCriteria.query.get_or_404(criteria_id)
    from models import CriteriaScore
    if CriteriaScore.query.filter_by(criteria_id=criteria_id).first():
        flash('此考評項目已經有評分紀錄，無法刪除；建議改用「停用」以保留歷史評分完整性。', 'danger')
        return redirect(url_for('review_admin.criteria'))
    code = crit.code
    db.session.delete(crit)
    db.session.commit()
    log_action('delete', 'ScoreCriteria', criteria_id, code)
    flash('考評項目已刪除', 'success')
    return redirect(url_for('review_admin.criteria'))


@review_admin_bp.route('/review/criteria/reorder', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def reorder_criteria():
    payload = request.get_json(silent=True) or {}
    ordered_ids = payload.get('ordered_ids', [])
    if not isinstance(ordered_ids, list) or not ordered_ids:
        return jsonify({'error': 'ordered_ids 格式錯誤'}), 400
    for index, criteria_id in enumerate(ordered_ids):
        ScoreCriteria.query.filter_by(id=criteria_id).update({'sort_order': index})
    db.session.commit()
    log_action('reorder', 'ScoreCriteria', None, '調整考評項目顯示順序')
    return jsonify({'status': 'ok'})


@review_admin_bp.route('/review/criteria/export')
@login_required
@admin_or_hr_manager_required
def criteria_export():
    buffer = export_criteria_workbook()
    log_action('export', 'ScoreCriteria', None, '匯出考評項目 Excel')
    return send_file(buffer, as_attachment=True, download_name='考評項目.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@review_admin_bp.route('/review/criteria/import', methods=['POST'])
@login_required
@admin_or_hr_manager_required
def criteria_import():
    uploaded = request.files.get('excel_file')
    if uploaded is None or uploaded.filename == '':
        flash('請選擇要匯入的 Excel 檔案', 'danger')
        return redirect(url_for('review_admin.criteria'))

    try:
        result = import_criteria_from_workbook(uploaded)
    except Exception as exc:  # noqa: BLE001
        flash(f'匯入失敗，檔案格式可能不正確：{exc}', 'danger')
        return redirect(url_for('review_admin.criteria'))

    log_action('import', 'ScoreCriteria', None,
               f'匯入考評項目 Excel，新增{result["created"]}筆，更新{result["updated"]}筆，錯誤{len(result["errors"])}筆')
    msg = f'匯入完成：新增 {result["created"]} 筆，更新 {result["updated"]} 筆'
    if result.get('weight_sum') is not None and abs(result['weight_sum'] - 1.0) > 0.001:
        msg += f'（提醒：目前啟用中項目的權重合計為 {result["weight_sum"] * 100:.0f}%，不是 100%）'
    if result['errors']:
        error_lines = '；'.join(result['errors'][:5])
        msg += f'（{len(result["errors"])} 筆有問題：{error_lines}{"..." if len(result["errors"]) > 5 else ""}）'
        flash(msg, 'warning')
    else:
        flash(msg, 'success')
    return redirect(url_for('review_admin.criteria'))


# ---------------------------------------------------------------------------
# 排名（設計參考「原績效考核系統」）
# ---------------------------------------------------------------------------
@review_admin_bp.route('/review/periods/<int:period_id>/rankings')
@login_required
@admin_required
def rankings(period_id):
    from sqlalchemy.orm import joinedload
    period = ReviewPeriod.query.get_or_404(period_id)
    results = (
        RankingResult.query.filter_by(period_id=period_id)
        .options(joinedload(RankingResult.review_form).joinedload(ReviewForm.engineer))
        .order_by(RankingResult.company_rank)
        .all()
    )
    return render_template('review_rankings.html', period=period, results=results)


@review_admin_bp.route('/review/periods/<int:period_id>/rankings/recalculate', methods=['POST'])
@login_required
@admin_required
def recalculate(period_id):
    """
    手動重新計算一次排名。正常情況下每次評分/覆評送出時都會自動觸發，
    這個按鈕是給「補算」用的（例如剛匯入舊資料、或懷疑排名跟目前分數對不上時）。
    """
    period = ReviewPeriod.query.get_or_404(period_id)
    count = recalculate_period_rankings(period_id)
    log_action('update', 'RankingResult', None, f'手動重新計算考核期間 {period.label} 的排名，共 {count} 筆')
    flash(f'已重新計算 {count} 張已完成評分的考核表排名', 'success')
    return redirect(url_for('review_admin.rankings', period_id=period_id))


@review_admin_bp.route('/review/periods/<int:period_id>/rankings/export')
@login_required
@admin_required
def rankings_export(period_id):
    from sqlalchemy.orm import joinedload
    period = ReviewPeriod.query.get_or_404(period_id)
    results = (
        RankingResult.query.filter_by(period_id=period_id)
        .options(joinedload(RankingResult.review_form).joinedload(ReviewForm.engineer))
        .order_by(RankingResult.company_rank)
        .all()
    )

    import openpyxl
    from openpyxl.styles import Font
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = period.label[:31]  # Excel 工作表名稱上限 31 字元

    headers = ['公司排名', '部門排名', '工號', '姓名', '部門', '實得分數合計', '排名調整後分數', '調整原因']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for r in results:
        ws.append([
            r.company_rank, r.department_rank,
            r.review_form.engineer.emp_id, r.review_form.engineer.name,
            r.department or '-', float(r.total_score),
            float(r.adjusted_score) if r.adjusted_score is not None else None,
            r.adjusted_note or '',
        ])
    for col_cells in ws.columns:
        max_len = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = max(10, min(max_len + 2, 40))

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    log_action('export', 'RankingResult', None, f'匯出考核期間 {period.label} 排名 Excel')
    return send_file(buffer, as_attachment=True, download_name=f'{period.label}_排名.xlsx',
                      mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@review_admin_bp.route('/review/rankings/<int:result_id>/adjust', methods=['POST'])
@login_required
@admin_required
def adjust_ranking(result_id):
    result = RankingResult.query.get_or_404(result_id)
    raw = request.form.get('adjusted_score', '').strip()
    note = request.form.get('adjusted_note', '').strip()

    if raw == '':
        result.adjusted_score = None
    else:
        try:
            value = Decimal(raw)
        except InvalidOperation:
            flash('調整後分數格式錯誤', 'danger')
            return redirect(url_for('review_admin.rankings', period_id=result.period_id))
        result.adjusted_score = value

    result.adjusted_note = note or None
    result.adjusted_by_id = current_user.id
    result.adjusted_at = datetime.utcnow()
    log_action('update', 'RankingResult', result.id, f'調整排名分數：adjusted_score={raw}')
    db.session.commit()
    flash('排名調整後分數已更新', 'success')
    return redirect(url_for('review_admin.rankings', period_id=result.period_id))
