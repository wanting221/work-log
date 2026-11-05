# -*- coding: utf-8 -*-
"""
utils/excel_utils.py - Excel 匯出/匯入工具
使用 openpyxl 產生比照現行「RD各組週報」Excel版面的報表
"""
import io
import unicodedata
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import FormulaRule

HEADER_FILL = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
HEADER_FONT = Font(bold=True, color='FFFFFF', size=11)
THIN_BORDER = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin')
)
WRAP_ALIGN = Alignment(wrap_text=True, vertical='top', horizontal='left')
CENTER_ALIGN = Alignment(vertical='center', horizontal='center', wrap_text=True)


def _lenb(text):
    """
    模擬 Excel LENB() 函式的位元組計算方式：中文、全形字這類「雙位元組字元」
    算 2 個位元組，英數字、半形符號這類「單位元組字元」算 1 個位元組。
    用 unicodedata.east_asian_width() 判斷：寬字元(W)、全形字元(F) 算 2，
    其餘（窄字元、半形、中性、模稜兩可）算 1，這是 Python 模擬 LENB() 的
    標準做法。
    """
    total = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ('W', 'F'):
            total += 2
        else:
            total += 1
    return total


def calculate_row_height_for_content(content):
    """
    依照指定規則計算某一欄位內容需要的行高：
    行高 = 文字行數 * 20px，再依這欄位「每一行」的 LENB() 是否超過
    50 / 100 / 150 各加 20 / 40 / 60px（三個門檻各自獨立判斷，同時符合
    多個門檻時會累加——例如某一行 LENB() 是 160，三個門檻都符合，
    會一次加上 20+40+60=120px）。
    文字行數以換行符號（\\n）分隔計算，多個項目合併成一格時會用換行符號
    串接，這裡沿用同一套計算方式。
    """
    if not content:
        return 20
    lines = content.split('\n')
    height = len(lines) * 20
    max_lenb = max(_lenb(line) for line in lines)
    if max_lenb > 50:
        height += 20
    if max_lenb > 100:
        height += 40
    if max_lenb > 150:
        height += 60
    return height

# 專案代號顏色對照表：跟網頁上「專案清單管理」頁面可選擇的標籤顏色一致
# (static/css/style.css 裡的 .badge.bg-xxx 顏色，Bootstrap 標準色 + 5 個自訂色)
PROJECT_COLOR_HEX = {
    'primary': 'FF0D6EFD',
    'secondary': 'FF6C757D',
    'success': 'FF198754',
    'danger': 'FFDC3545',
    'warning': 'FFFFC107',
    'info': 'FF0DCAF0',
    'dark': 'FF212529',
    'purple': 'FF6F42C1',
    'pink': 'FFD63384',
    'orange': 'FFFD7E14',
    'teal': 'FF0D9488',
    'indigo': 'FF4338CA',
    'navy': 'FF1E3A5F',
    'maroon': 'FF800000',
    'olive': 'FF808000',
    'brown': 'FF8B4513',
}


def _style_header(ws, row_idx, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER_ALIGN
        cell.border = THIN_BORDER


def export_reports_by_engineer(reports, year, week_number):
    """
    匯出格式：組別｜人員｜已執行工作｜客戶/業務｜計劃工作重點
    比照現行 Excel 版面配置
    reports: WeeklyReport 物件清單（含 relationship: engineer, items）
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f'{year}年第{week_number}週週報'

    headers = ['組別', '人員', '已執行工作', '客戶/業務', '計劃工作重點']
    ws.append(headers)
    _style_header(ws, 1, len(headers))

    row_idx = 2
    for report in reports:
        eng = report.engineer
        group_name = eng.group.name if eng.group else ''
        last_week_items = [i for i in report.items if i.item_type == 'last_week']
        this_week_items = [i for i in report.items if i.item_type == 'this_week']

        max_rows = max(len(last_week_items), len(this_week_items), 1)
        start_row = row_idx
        for idx in range(max_rows):
            lw = last_week_items[idx] if idx < len(last_week_items) else None
            tw = this_week_items[idx] if idx < len(this_week_items) else None
            ws.cell(row=row_idx, column=1, value=group_name if idx == 0 else '')
            ws.cell(row=row_idx, column=2, value=eng.display_name if idx == 0 else '')
            ws.cell(row=row_idx, column=3, value=lw.content if lw else '')
            proj_code = ''
            if lw and lw.project:
                proj_code = lw.project.code
            elif tw and tw.project:
                proj_code = tw.project.code
            ws.cell(row=row_idx, column=4, value=proj_code)
            ws.cell(row=row_idx, column=5, value=tw.content if tw else '')
            for col in range(1, len(headers) + 1):
                c = ws.cell(row=row_idx, column=col)
                c.alignment = WRAP_ALIGN
                c.border = THIN_BORDER

            # 列高：只依 C 欄（已執行工作）的內容計算，不看 E 欄（計劃工作
            # 重點）——公式本身（行數 x 20px，再依每行 LENB() 是否超過
            # 50/100/150 各加 20/40/60px）已經在 calculate_row_height_for_content()
            # 裡實作好了
            ws.row_dimensions[row_idx].height = calculate_row_height_for_content(lw.content if lw else '')

            row_idx += 1

        # 合併 RD組別 與 工程師 欄位（多列時）
        if max_rows > 1:
            ws.merge_cells(start_row=start_row, start_column=1, end_row=row_idx - 1, end_column=1)
            ws.merge_cells(start_row=start_row, start_column=2, end_row=row_idx - 1, end_column=2)

    # 欄寬設定
    widths = [16, 16, 64, 16, 64]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_reports_by_project(project_report_map, year, week_number):
    """
    匯出格式：專案/客戶代號｜人員｜已執行工作｜計劃工作重點
    比照 export_reports_by_engineer() 同一套版面風格，只是分組依據從
    「組別」改成「專案/客戶」。

    project_report_map: dict，key 是 Project 物件，value 是這個專案底下
    每個人的 (姓名, 上週內容, 本週內容) tuple 清單，由
    utils/report_utils.py 的 build_project_report_excel() 先彙整好再傳進來。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f'{year}年第{week_number}週週報(依專案)'

    headers = ['專案/客戶代號', '人員', '已執行工作', '計劃工作重點']
    ws.append(headers)
    _style_header(ws, 1, len(headers))
    # 這裡只覆寫字級：標題用14，其他匯出功能（例如依員工匯出）共用的
    # HEADER_FONT 維持原本的11，不要因為這裡的需求連帶被改到
    for col in range(1, len(headers) + 1):
        ws.cell(row=1, column=col).font = Font(bold=True, color='FFFFFF', size=14)
    ws.row_dimensions[1].height = 30

    body_font = Font(size=12)

    row_idx = 2
    for project, entries in project_report_map.items():
        max_rows = max(len(entries), 1)
        start_row = row_idx

        # A欄「專案/客戶代號」的底色跟畫面上這個專案的標籤顏色一致，文字
        # 改成白色（跟畫面上的彩色標籤呈現方式對應），色碼對照
        # PROJECT_COLOR_HEX，找不到對應顏色時（理論上不會發生，因為
        # Project.color 一定是這份色票裡的其中一個值）退回預設的藍色
        color_hex = PROJECT_COLOR_HEX.get(project.color or 'primary', PROJECT_COLOR_HEX['primary'])
        code_fill = PatternFill(start_color=color_hex, end_color=color_hex, fill_type='solid')
        code_font = Font(color='FFFFFF', size=12)

        for idx in range(max_rows):
            name, last, this = entries[idx] if idx < len(entries) else ('', '', '')
            ws.cell(row=row_idx, column=1, value=project.code if idx == 0 else '')
            ws.cell(row=row_idx, column=2, value=name)
            ws.cell(row=row_idx, column=3, value=last)
            ws.cell(row=row_idx, column=4, value=this)
            for col in range(1, len(headers) + 1):
                c = ws.cell(row=row_idx, column=col)
                c.alignment = WRAP_ALIGN
                c.border = THIN_BORDER
                c.font = body_font

            code_cell = ws.cell(row=row_idx, column=1)
            code_cell.fill = code_fill
            code_cell.font = code_font
            code_cell.alignment = CENTER_ALIGN

            # 列高：C 欄（已執行工作）、D 欄（計劃工作重點）各自依同一套
            # 公式（行數 x 20px，再依每行 LENB() 是否超過 50/100/150 各加
            # 20/40/60px，已經在 calculate_row_height_for_content() 裡實作
            # 好了）計算出所需高度，取兩者中較高的一個
            height_c = calculate_row_height_for_content(last)
            height_d = calculate_row_height_for_content(this)
            ws.row_dimensions[row_idx].height = max(height_c, height_d)

            row_idx += 1

        # 合併「專案/客戶代號」欄位（這個專案底下有多筆人員紀錄時）
        if max_rows > 1:
            ws.merge_cells(start_row=start_row, start_column=1, end_row=row_idx - 1, end_column=1)

    widths = [18, 12, 72, 72]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_diary_logs(log_rows, title):
    """
    匯出「日誌檢視」的日誌紀錄。log_rows 是一份 [{'log': DailyLog, 'engineer': Engineer}, ...]
    的清單（呼叫端已經依權限範圍篩選好），欄位：日期｜星期｜員工｜客戶/專案｜國家｜聯絡方式｜內容摘要。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]  # Excel 工作表名稱上限 31 字元

    headers = ['日期', '星期', '員工', '客戶/專案', '國家', '聯絡方式', '內容摘要']
    ws.append(headers)
    _style_header(ws, 1, len(headers))

    weekday_names = ['一', '二', '三', '四', '五', '六', '日']
    row_idx = 2
    for row in log_rows:
        log = row['log']
        eng = row['engineer']
        ws.cell(row=row_idx, column=1, value=log.log_date.strftime('%Y-%m-%d'))
        ws.cell(row=row_idx, column=2, value=weekday_names[log.log_date.weekday()])
        ws.cell(row=row_idx, column=3, value=eng.display_name)
        ws.cell(row=row_idx, column=4, value=log.project.code if log.project else '')
        ws.cell(row=row_idx, column=5, value=log.country or '')
        ws.cell(row=row_idx, column=6, value=log.contact_method or '')
        ws.cell(row=row_idx, column=7, value=log.content_summary or '')
        for col in range(1, len(headers) + 1):
            c = ws.cell(row=row_idx, column=col)
            c.alignment = WRAP_ALIGN
            c.border = THIN_BORDER
        ws.row_dimensions[row_idx].height = calculate_row_height_for_content(log.content_summary or '')
        row_idx += 1

    widths = [14, 8, 14, 14, 10, 18, 60]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def parse_import_excel(file_stream):
    """
    解析匯入的歷史週報 Excel 檔案
    預期欄位：部門｜組別｜人員｜已執行工作精要｜客戶/專案代號｜計劃工作重點
    回傳解析後的 list of dict，供上層程式建立資料
    """
    wb = load_workbook(file_stream, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    results = []
    last_department, last_group, last_name = '', '', ''
    for row in rows:
        if row is None or all(v is None for v in row):
            continue
        department, group, name, last_week, project_code, this_week = (list(row) + [None] * 6)[:6]
        department = department or last_department
        group = group or last_group
        name = name or last_name
        last_department, last_group, last_name = department, group, name
        results.append({
            'department': department,
            'group': group,
            'engineer_name': name,
            'last_week_content': last_week or '',
            'project_code': project_code or '',
            'this_week_content': this_week or '',
        })
    return results
