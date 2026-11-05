# -*- coding: utf-8 -*-
"""
utils/review_excel_io.py - 績效考核系統（考核期間、考評項目）的 Excel 匯出/匯入
沿用「原績效考核系統」原本的匯出入慣例：
  匯出：openpyxl 產生 .xlsx，第一列是欄位標題（粗體），欄寬依內容自動調整。
  匯入：依「主鍵欄位」（民國年+季別 / 代碼）比對，存在就更新、不存在就新增；
        每一列各自 try/except，單一列出錯不影響其他列繼續匯入，最後回傳
        「成功幾筆、失敗幾筆＋每筆失敗原因」的摘要。
"""
import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

from extensions import db
from models import ReviewPeriod, ScoreCriteria


# ---------------------------------------------------------------------------
# 共用小工具
# ---------------------------------------------------------------------------
def _new_workbook(sheet_title, headers):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    return wb, ws


def _autofit_columns(ws):
    for col_cells in ws.columns:
        max_len = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = max(10, min(max_len + 2, 40))


def _workbook_to_bytes(wb):
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _read_uploaded_workbook(file_storage):
    """回傳 (header, data_rows)。第一列當標題，之後每列是一筆資料（tuple）。"""
    wb = openpyxl.load_workbook(file_storage, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    header = [str(h).strip() if h is not None else '' for h in rows[0]]
    return header, rows[1:]


def _row_to_dict(header, row):
    d = {}
    for i, key in enumerate(header):
        d[key] = row[i] if i < len(row) else None
    return d


def _parse_bool_zh(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip()
    if s in ('是', 'TRUE', 'True', 'true', '1', 'Y', 'y'):
        return True
    if s in ('否', 'FALSE', 'False', 'false', '0', 'N', 'n', ''):
        return False
    return default


def _parse_date_cell(value):
    """Excel 日期儲存格可能是 datetime/date 物件（Excel 原生日期格式），也可能是
    字串（使用者手動輸入或另存 CSV 再打開的情況），兩種都要能處理。"""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.fromisoformat(s)  # 最後嘗試標準 ISO 格式，格式仍不對就讓例外往外拋


def _parse_decimal_cell(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().rstrip('%'))


def _parse_int_cell(value):
    if value is None or value == '':
        return None
    return int(float(value))


# ---------------------------------------------------------------------------
# 考核期間
# ---------------------------------------------------------------------------
def export_periods_workbook():
    headers = ['民國年', '季別', '起始日期', '結束日期', '自評截止日', '開放狀態']
    wb, ws = _new_workbook('考核期間', headers)
    periods = ReviewPeriod.query.order_by(
        ReviewPeriod.roc_year.desc(), ReviewPeriod.quarter.desc()
    ).all()
    for p in periods:
        ws.append([
            p.roc_year, p.quarter,
            p.start_date.isoformat() if p.start_date else '',
            p.end_date.isoformat() if p.end_date else '',
            p.self_report_deadline.isoformat() if p.self_report_deadline else '',
            '是' if p.is_open else '否',
        ])
    _autofit_columns(ws)
    return _workbook_to_bytes(wb)


def import_periods_from_workbook(file_storage):
    header, rows = _read_uploaded_workbook(file_storage)
    if not header:
        return {'created': 0, 'updated': 0, 'errors': ['檔案是空的或格式無法辨識']}

    existing = {(p.roc_year, p.quarter): p for p in ReviewPeriod.query.all()}
    created = 0
    updated = 0
    errors = []

    for idx, row in enumerate(rows, start=2):
        data = _row_to_dict(header, row)
        roc_year_raw = data.get('民國年')
        quarter_raw = data.get('季別')
        if roc_year_raw is None and quarter_raw is None:
            continue

        try:
            roc_year = _parse_int_cell(roc_year_raw)
            quarter = _parse_int_cell(quarter_raw)
            if roc_year is None or quarter is None:
                raise ValueError('民國年與季別為必填')
            if quarter not in (1, 2, 3, 4):
                raise ValueError('季別必須是 1~4')

            start_date_val = _parse_date_cell(data.get('起始日期'))
            end_date_val = _parse_date_cell(data.get('結束日期'))
            if start_date_val is None or end_date_val is None:
                raise ValueError('起始日期與結束日期為必填')
            deadline_val = _parse_date_cell(data.get('自評截止日'))
            is_open = _parse_bool_zh(data.get('開放狀態'), default=True)

            key = (roc_year, quarter)
            period = existing.get(key)
            if period is None:
                period = ReviewPeriod(roc_year=roc_year, quarter=quarter)
                db.session.add(period)
                existing[key] = period
                created += 1
            else:
                updated += 1

            period.start_date = start_date_val
            period.end_date = end_date_val
            period.self_report_deadline = deadline_val
            period.is_open = is_open
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第 {idx} 列（{roc_year_raw} 年第 {quarter_raw} 季）：{exc}')

    db.session.commit()
    return {'created': created, 'updated': updated, 'errors': errors}


# ---------------------------------------------------------------------------
# 考評項目
# ---------------------------------------------------------------------------
def export_criteria_workbook():
    headers = ['代碼', '名稱', '權重（小數，例如0.5代表50%）', '排序', '啟用中']
    wb, ws = _new_workbook('考評項目', headers)
    items = ScoreCriteria.query.order_by(ScoreCriteria.sort_order).all()
    for c in items:
        ws.append([c.code, c.name, float(c.weight), c.sort_order, '是' if c.is_active else '否'])
    _autofit_columns(ws)
    return _workbook_to_bytes(wb)


def import_criteria_from_workbook(file_storage):
    header, rows = _read_uploaded_workbook(file_storage)
    if not header:
        return {'created': 0, 'updated': 0, 'errors': ['檔案是空的或格式無法辨識'], 'weight_sum': None}

    existing = {c.code: c for c in ScoreCriteria.query.all()}
    created = 0
    updated = 0
    errors = []

    weight_header = next((h for h in header if h.startswith('權重')), '權重')

    for idx, row in enumerate(rows, start=2):
        data = _row_to_dict(header, row)
        code = str(data.get('代碼') or '').strip()
        if not code:
            continue

        try:
            name = str(data.get('名稱') or '').strip()
            if not name:
                raise ValueError('名稱為必填')

            weight = _parse_decimal_cell(data.get(weight_header))
            if weight is None:
                raise ValueError('權重為必填')
            if weight > 1:
                weight = weight / 100  # 容錯：填 50 視為 50%
            if weight < 0 or weight > 1:
                raise ValueError('權重必須介於 0~1（或 0~100 視為百分比）之間')

            sort_order = _parse_int_cell(data.get('排序')) or 0
            is_active = _parse_bool_zh(data.get('啟用中'), default=True)

            crit = existing.get(code)
            if crit is None:
                crit = ScoreCriteria(code=code)
                db.session.add(crit)
                existing[code] = crit
                created += 1
            else:
                updated += 1

            crit.name = name
            crit.weight = Decimal(str(weight)).quantize(Decimal('0.01'))
            crit.sort_order = sort_order
            crit.is_active = is_active
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第 {idx} 列（代碼 {code}）：{exc}')

    db.session.commit()
    weight_sum = sum(float(c.weight) for c in ScoreCriteria.query.filter_by(is_active=True).all())
    return {'created': created, 'updated': updated, 'errors': errors, 'weight_sum': round(weight_sum, 4)}
