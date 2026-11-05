# -*- coding: utf-8 -*-
"""
utils/report_utils.py - 週報共用邏輯
包含：ISO 年週計算、上週資料取得、未完成事項自動延續、卡關關鍵字偵測
"""
from datetime import date, datetime, time, timedelta
from flask import current_app
from extensions import db
from models import WeeklyReport, WeeklyReportItem, Engineer
from utils.timezone_utils import taipei_today, taipei_now

# 需要填寫週報的角色：工程師本來就要填；部門主管、組別主管現在也有填寫週報的
# 功能，所以「應填寫人數」「未填寫追蹤」「全部預覽」這幾個地方都要把他們算進去。
# 管理者、唯讀不用填週報，不列入。
REPORT_WRITING_ROLES = ('engineer', 'dept_manager', 'group_manager')

# 「我的考核表」可以填寫的角色範圍：跟填寫週報的角色（REPORT_WRITING_ROLES）
# 不完全一樣——人資主管需要填自己的績效自評，但不需要填週報，所以獨立成
# 一個常數，不直接併進 REPORT_WRITING_ROLES（那個常數也同時控制了週報填寫
# 相關的邏輯，例如 get_fill_status_summary() 判斷「應填寫人數」，混在一起
# 會導致人資主管被誤算進週報的應填寫/未填寫統計裡）。
REVIEW_SELF_ROLES = REPORT_WRITING_ROLES + ('hr_manager',)


def current_year_week(d=None):
    """
    回傳指定日期（預設今天）的 ISO 年度與週次。
    這裡刻意用台灣當地日期而不是系統/容器的本地日期——雲端容器的系統時區
    通常是 UTC，如果直接用 date.today()，在台灣時間半夜 0 點到早上 8 點之間，
    UTC 日期還停留在前一天，會誤判成上一週，導致「現在明明是週一了，系統卻還
    顯示上週」的錯亂。
    """
    d = d or taipei_today()
    iso_year, iso_week, _ = d.isocalendar()
    return iso_year, iso_week


def tracking_start_datetime(year, week):
    """
    回傳某個年度/週次「未填寫追蹤」開始統計的時間點：當週週四早上 9:00。
    在這個時間點之前，週一到週三這幾天還在正常填寫期間內，不應該就開始
    把還沒填的人列成「未填寫」名單，容易造成不必要的提醒疲勞。
    """
    monday = date.fromisocalendar(year, week, 1)
    thursday = monday + timedelta(days=3)
    return datetime.combine(thursday, time(9, 0))


def has_reached_tracking_start(year, week):
    """
    檢查某個年度/週次是否已經到達「未填寫追蹤」開始統計的時間點（當週週四
    早上 9:00）。只有在檢視「本週」資料時才需要判斷這個時間點——如果是在看
    已經過去的週次（例如上週、上個月），時間點必然早就已經到了，不需要
    特別判斷，直接回傳 True。
    """
    current_year, current_week = current_year_week()
    if (year, week) != (current_year, current_week):
        return True
    return taipei_now() >= tracking_start_datetime(year, week)


def week_date_range(year, week):
    """回傳指定 ISO 年週的起訖日期 (星期一, 星期日)，供頁面顯示「YYYY年第WW週 (MM/DD - MM/DD)」使用"""
    monday = date.fromisocalendar(year, week, 1)
    sunday = date.fromisocalendar(year, week, 7)
    return monday, sunday


def format_week_range(year, week):
    """回傳格式化好的日期區間字串，例如 2026/07/13 - 2026/07/19"""
    monday, sunday = week_date_range(year, week)
    return f'{monday.strftime("%Y/%m/%d")} - {sunday.strftime("%Y/%m/%d")}'


def get_deadline_text():
    """
    讀取「設定提醒」頁面設定的填寫截止日／截止時間（星期幾＋幾點），組成一句
    人看得懂的說明文字，例如「星期五 17:00」，供提醒信內文使用。
    找不到設定值（例如全新安裝、管理者還沒存過）就回傳預設的「星期五 17:00」。
    """
    from models import SystemSetting
    weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

    weekday_setting = SystemSetting.query.get('deadline_weekday')
    time_setting = SystemSetting.query.get('deadline_time')

    try:
        weekday_index = int(weekday_setting.value) if weekday_setting and weekday_setting.value else 4
    except ValueError:
        weekday_index = 4
    weekday_index = weekday_index if 0 <= weekday_index <= 6 else 4
    deadline_time = time_setting.value if time_setting and time_setting.value else '17:00'

    return f'{weekday_names[weekday_index]} {deadline_time}'


def previous_year_week(year, week):
    """回傳指定年週的前一週 (year, week)"""
    # 用該週的星期一往前推 7 天，再取其 ISO 年週，可正確跨年處理
    monday = date.fromisocalendar(year, week, 1)
    prev_monday = monday - timedelta(days=7)
    return prev_monday.isocalendar()[0], prev_monday.isocalendar()[1]


def next_year_week(year, week):
    """回傳指定年週的下一週 (year, week)，可正確跨年處理"""
    monday = date.fromisocalendar(year, week, 1)
    next_monday = monday + timedelta(days=7)
    return next_monday.isocalendar()[0], next_monday.isocalendar()[1]


def week_range_list(year_start, week_start, year_end, week_end, max_weeks=104):
    """
    列出從 (year_start, week_start) 到 (year_end, week_end) 之間（含頭尾）的
    所有 (year, week) 組合，正確處理跨年（例如去年第 52 週到今年第 3 週）。

    若起始週次晚於結束週次，會自動視為只查詢起始這一週（避免使用者不小心
    把起訖顛倒，導致查詢範圍變成空的或無限迴圈）。
    max_weeks 是防呆上限（預設 2 年），避免使用者不小心選了一個超大區間，
    導致這裡要迭代、後面資料庫也要查詢非常多週次的資料，拖慢頁面甚至卡死。
    """
    try:
        start_monday = date.fromisocalendar(year_start, week_start, 1)
        end_monday = date.fromisocalendar(year_end, week_end, 1)
    except ValueError:
        # 年週次數字本身不合法（例如週次輸入 60），退回只查起始這一週
        return [(year_start, week_start)]

    if end_monday < start_monday:
        return [(year_start, week_start)]

    result = [(year_start, week_start)]
    cur_year, cur_week = year_start, week_start
    while len(result) < max_weeks:
        cur_year, cur_week = next_year_week(cur_year, cur_week)
        cur_monday = date.fromisocalendar(cur_year, cur_week, 1)
        if cur_monday > end_monday:
            break
        result.append((cur_year, cur_week))
    return result


def get_or_create_report(engineer_id, year, week):
    """取得或建立指定工程師、年週的週報"""
    report = WeeklyReport.query.filter_by(
        engineer_id=engineer_id, year=year, week_number=week
    ).first()
    if not report:
        monday = date.fromisocalendar(year, week, 1)
        report = WeeklyReport(
            engineer_id=engineer_id, year=year, week_number=week,
            report_date=monday, status='未填寫'
        )
        db.session.add(report)
        db.session.commit()
    return report


def carry_over_to_next_week(report):
    """
    自動延續機制：工程師送出本週週報後，把「下週計劃工作重點」自動帶入
    下一週週報的「本週已執行工作精要」，不需要工程師手動勾選。
    用 chain_id 判斷是否已經延續過，避免同一份週報被重複解鎖重送時，
    同一件事被重複帶入好几次。若下一週的週報已經送出鎖定，則不覆寫。
    """
    next_year, next_week = next_year_week(report.year, report.week_number)
    next_report = get_or_create_report(report.engineer_id, next_year, next_week)

    if next_report.locked:
        return next_report

    this_week_items = [i for i in report.items if i.item_type == 'this_week']
    if not this_week_items:
        return next_report

    existing_chain_ids = {
        i.chain_id for i in next_report.items
        if i.item_type == 'last_week' and i.chain_id
    }
    max_order = len([i for i in next_report.items if i.item_type == 'last_week'])

    for item in this_week_items:
        if item.chain_id and item.chain_id in existing_chain_ids:
            continue  # 已經延續過，避免重複建立
        db.session.add(WeeklyReportItem(
            report_id=next_report.id, item_type='last_week',
            project_id=item.project_id, content=item.content,
            sort_order=max_order, is_carried_over=True, chain_id=item.chain_id,
        ))
        max_order += 1

    if next_report.status == '未填寫':
        next_report.status = '草稿'
    db.session.commit()
    return next_report


def detect_stuck_items(keywords, threshold_weeks):
    """
    偵測「卡關」風險項目：
    同一個 chain_id 的項目，若最新一筆內容仍含卡關關鍵字，
    且該 chain 已連續出現 >= threshold_weeks 週，則視為風險項目
    回傳風險項目清單 [{item, weeks_count, engineer, project}, ...]

    效能說明：先前的寫法會用 WeeklyReportItem.query.all() 把「全部歷史週報項目」
    整張表讀進記憶體，資料量隨著使用週數增加只會越來越大，且每筆還要額外查詢
    report/engineer，造成嚴重的 N+1 問題，是 Dashboard 載入變慢的主因之一。
    改成：先在資料庫端用 GROUP BY 篩出「筆數 >= threshold_weeks」的 chain_id
    （通常是一小群），再針對這些 chain_id 才查詢實際內容，並用 joinedload
    一次把 report/engineer/project 都撈出來，避免逐筆觸發額外查詢。
    """
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    # 第一步：只在資料庫端計算每個 chain_id 的筆數，篩出可能符合條件的 chain
    # （這一步不需要把內容抓回來，資料庫做 GROUP BY COUNT 非常快）
    chain_counts = dict(
        db.session.query(WeeklyReportItem.chain_id, func.count(WeeklyReportItem.id))
        .filter(WeeklyReportItem.chain_id.isnot(None))
        .group_by(WeeklyReportItem.chain_id)
        .having(func.count(WeeklyReportItem.id) >= threshold_weeks)
        .all()
    )
    if not chain_counts:
        return []

    # 第二步：只針對通過篩選的 chain_id，一次查出所有項目（含關聯資料），
    # 資料量已經大幅縮小，且用 joinedload 避免逐筆觸發 report/engineer 查詢
    items = (
        WeeklyReportItem.query
        .filter(WeeklyReportItem.chain_id.in_(chain_counts.keys()))
        .options(
            joinedload(WeeklyReportItem.report).joinedload(WeeklyReport.engineer),
            joinedload(WeeklyReportItem.project),
        )
        .order_by(WeeklyReportItem.chain_id, WeeklyReportItem.created_at)
        .all()
    )

    chains = {}
    for item in items:
        chains.setdefault(item.chain_id, []).append(item)

    risky = []
    for chain_id, chain_items in chains.items():
        latest = chain_items[-1]  # 已依 created_at 排序，最後一筆即最新
        if latest.contains_stuck_keyword(keywords):
            report = latest.report
            risky.append({
                'item': latest,
                'weeks_count': chain_counts[chain_id],
                'engineer': report.engineer if report else None,
                'project': latest.project,
            })

    # 依卡關週數排序，越久越前面
    risky.sort(key=lambda x: x['weeks_count'], reverse=True)
    return risky


def build_project_report_excel(year, week, allowed_project_ids=None):
    """
    建立指定年度/週次的「依專案分類」週報 Excel（BytesIO）。
    這裡是共用邏輯，「匯出報表」頁面下載、以及「各組別填寫完成率達 100% 時
    自動寄送彙整報表通知」功能都會呼叫這個函式，避免兩處各寫一份重複的組資料邏輯。

    allowed_project_ids：選填，限定只匯出這個集合裡的專案 ID（部門主管角色用來
    限縮只匯出自己部門的專案；預設 None 表示不限制，匯出全部專案，管理者/一般
    匯出情境維持原本行為）。
    """
    from models import Project, WeeklyReport, WeeklyReportItem
    from sqlalchemy.orm import contains_eager, joinedload
    from utils.excel_utils import export_reports_by_project

    projects_query = Project.query.order_by(Project.sort_order, Project.id)
    if allowed_project_ids is not None:
        projects_query = projects_query.filter(Project.id.in_(allowed_project_ids))
    projects = projects_query.all()
    all_items = (
        WeeklyReportItem.query
        .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
        .options(contains_eager(WeeklyReportItem.report).joinedload(WeeklyReport.engineer))
        .filter(WeeklyReport.year == year, WeeklyReport.week_number == week,
                WeeklyReportItem.project_id.isnot(None))
        .all()
    )
    items_by_project = {}
    for item in all_items:
        items_by_project.setdefault(item.project_id, []).append(item)

    project_report_map = {}
    for project in projects:
        items = items_by_project.get(project.id)
        if not items:
            continue
        by_eng = {}
        for item in items:
            eng = item.report.engineer
            by_eng.setdefault(eng.id, {'name': eng.display_name, 'last': '', 'this': ''})
            if item.item_type == 'last_week':
                by_eng[eng.id]['last'] = (by_eng[eng.id]['last'] + '\n' + item.content).strip()
            else:
                by_eng[eng.id]['this'] = (by_eng[eng.id]['this'] + '\n' + item.content).strip()
        entries = [(v['name'], v['last'], v['this']) for v in by_eng.values()]
        project_report_map[project] = entries

    return export_reports_by_project(project_report_map, year, week)


def check_and_notify_all_groups_complete(year, week):
    """
    每次有工程師送出週報後呼叫：檢查這一週「是否所有在職工程師都已送出」，
    如果是，且這一週還沒通知過，寄一封彙整報表通知信給所有管理者信箱，
    並附上「依專案分類」的當週 Excel。用 SystemSetting 記錄「這一週已經通知過」，
    避免重複送信（例如工程師之後又編輯/重新送出，不會再收到第二次通知）。
    """
    from models import Engineer, SystemSetting
    from extensions import db
    from utils.email_utils import send_email

    summary = get_fill_status_summary(year, week)
    if summary['total'] == 0 or summary['not_submitted_count'] > 0:
        return  # 還沒全部送出，或這週根本沒有任何在職工程師，都不需要通知

    setting_key = f'weekly_complete_notified_{year}_{week}'
    already_notified = SystemSetting.query.get(setting_key)
    if already_notified and already_notified.value == 'True':
        return  # 這一週已經通知過，避免重複寄信

    admin_emails = [
        e.email for e in Engineer.query.filter_by(role='admin', status='active').all() if e.email
    ]
    if not admin_emails:
        return  # 沒有可通知的管理者信箱

    try:
        excel_buf = build_project_report_excel(year, week)
        subject = f'【彙整報表通知】第{year}年第{week}週 工程師週報已全數送出'
        body = (
            f'您好，<br>第 {year} 年第 {week} 週的工程師週報，所有在職工程師'
            f'（共 {summary["total"]} 位）都已完成送出，各組別填寫完成率均達 100%。'
            f'<br>詳細內容請參考附件的依專案分類彙整報表。'
        )
        ok, _ = send_email(
            admin_emails, subject, body,
            attachments=[(f'RD週報_{year}W{week}_依專案.xlsx', excel_buf.getvalue(), 'xlsx')]
        )
        if ok:
            if already_notified:
                already_notified.value = 'True'
            else:
                db.session.add(SystemSetting(key=setting_key, value='True'))
            db.session.commit()
    except Exception:
        # 通知信寄送失敗不應該影響工程師送出週報這個主要操作，
        # 這裡吞掉例外，不讓整個送出流程因為寄信問題而失敗
        import traceback
        from flask import current_app
        current_app.logger.error(f'[自動通知] 寄送彙整報表通知失敗：\n{traceback.format_exc()}')


def get_group_project_ids(group_id):
    """
    取得「屬於某組別」的專案 ID 集合，供組別主管角色限縮可見範圍使用。
    專案沒有組別層級的直接歸屬欄位（只有部門層級的 Project.department），
    這裡純粹用推斷規則：專案的負責工程師是該組別的人，或該組別任何人
    曾經在任一週的週報裡處理過這個專案（只要碰過就算，不限本週）。
    """
    from models import Project, WeeklyReportItem, WeeklyReport, Engineer
    from extensions import db

    if not group_id:
        return set()

    owner_project_ids = {
        p.id for p in Project.query.all()
        if p.owner and p.owner.group_id == group_id
    }
    touched_project_ids = {
        row[0] for row in (
            db.session.query(WeeklyReportItem.project_id)
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .join(Engineer, WeeklyReport.engineer_id == Engineer.id)
            .filter(Engineer.group_id == group_id, WeeklyReportItem.project_id.isnot(None))
            .distinct()
            .all()
        )
    }
    return owner_project_ids | touched_project_ids


def get_department_project_ids(department):
    """
    取得「屬於某部門」的專案 ID 集合，供部門主管角色限縮可見範圍使用。

    判斷優先順序：
    1. 專案本身直接設定的「歸屬部門」（Project.department）——這是最明確、
       具排他性的判斷依據。只要專案明確設定了歸屬部門，就只有那個部門看得到，
       就算其他部門的人曾經處理過這個專案的項目，也不會因為下面的推斷規則
       而洩漏給其他部門看到。
    2. 對於「還沒設定歸屬部門」的專案，才會用推斷規則當備援判斷（負責工程師
       是該部門的人，或該部門任何人曾經在任一週的週報裡處理過這個專案），
       避免管理者還沒來得及一一設定每個專案的部門前，部門主管原本看得到的
       專案突然全部消失。
    """
    from models import Project, WeeklyReportItem, WeeklyReport, Engineer
    from extensions import db

    # 明確設定歸屬部門的專案：只認這個欄位，具排他性
    explicitly_assigned_ids = {
        p.id for p in Project.query.filter(Project.department == department).all()
    }

    # 還沒設定歸屬部門的專案，才套用推斷規則
    unassigned_projects = Project.query.filter(
        db.or_(Project.department.is_(None), Project.department == '')
    ).all()
    unassigned_ids = {p.id for p in unassigned_projects}

    owner_project_ids = {
        p.id for p in unassigned_projects
        if p.owner and (p.owner.department or '') == department
    }
    touched_project_ids = {
        row[0] for row in (
            db.session.query(WeeklyReportItem.project_id)
            .join(WeeklyReport, WeeklyReportItem.report_id == WeeklyReport.id)
            .join(Engineer, WeeklyReport.engineer_id == Engineer.id)
            .filter(Engineer.department == department, WeeklyReportItem.project_id.isnot(None))
            .distinct()
            .all()
        )
    }
    inferred_ids = (owner_project_ids | touched_project_ids) & unassigned_ids

    return explicitly_assigned_ids | inferred_ids


def get_fill_status_summary(year, week, department=None, group_id=None):
    """
    取得本週填寫狀態統計：應填人數/已填人數/未填人數與名單。
    只計算「有填寫週報功能」的角色（工程師、部門主管、組別主管）——管理者、
    唯讀不需要填寫週報，不應該被算進應填寫人數，
    也不會因為沒填而出現在未填寫追蹤名單裡；唯讀角色本來就不能填寫，同樣排除。

    另外也只算「週報功能開關開啟」的人（Engineer.weekly_report_enabled）——
    角色雖然是工程師/部門主管/組別主管，但如果人員帳號管理頁面把這個人的
    「功能」設定成「關閉」或「日誌」，代表這個人現在不用填週報，就不應該
    被算進應填寫人數，也不該因為沒填週報而出現在未填寫名單裡。

    department 參數是給「部門主管」角色限縮統計範圍用的；group_id 參數是給
    「組別主管」角色限縮統計範圍用的：傳入時只計算該部門/組別的工程師；
    預設都是 None 則維持原本「檢查全公司」的行為（例如「各組別填寫完成率
    全數達 100% 時自動通知」這個功能，就是刻意要看全公司的狀態，不能因為
    呼叫端多傳了 department/group_id 就被誤縮限）。
    """
    from sqlalchemy.orm import joinedload
    # joinedload 一次把 group 關聯撈出來，避免「未填寫追蹤」頁面顯示每位工程師
    # 組別時（e.group.name），每一列都各自觸發一次額外查詢。
    # 排序比照「人員帳號管理」頁面可拖曳調整的順序 (sort_order)，讓「未填寫追蹤」
    # 頁面的名單順序跟「依工程師檢視」頁面看到的順序一致。
    query = (
        Engineer.query
        .options(joinedload(Engineer.group))
        .filter(
            Engineer.status == 'active', Engineer.role.in_(REPORT_WRITING_ROLES),
            Engineer.weekly_report_enabled.is_(True)
        )
    )
    if department:
        query = query.filter_by(department=department)
    if group_id:
        query = query.filter_by(group_id=group_id)
    active_engineers = query.order_by(Engineer.sort_order, Engineer.id).all()
    submitted_ids = set(
        r.engineer_id for r in WeeklyReport.query.filter_by(
            year=year, week_number=week, status='已送出'
        ).all()
    )
    submitted = [e for e in active_engineers if e.id in submitted_ids]
    not_submitted = [e for e in active_engineers if e.id not in submitted_ids]
    return {
        'total': len(active_engineers),
        'submitted_count': len(submitted),
        'not_submitted_count': len(not_submitted),
        'submitted': submitted,
        'not_submitted': not_submitted,
    }


def get_diary_fill_status_summary(year, week, department=None, group_id=None):
    """
    取得本週「日誌」填寫狀態統計：應填人數/已填人數/未填人數與名單。
    只計算「日誌功能開關開啟」的人（Engineer.diary_enabled），跟週報版的
    get_fill_status_summary() 邏輯對應，但改成看這週（週一到週五）有沒有
    至少一筆 DailyLog 紀錄，而不是看 WeeklyReport 有沒有送出。

    department 參數是給「部門主管」角色限縮統計範圍用的；group_id 參數是給
    「組別主管」角色限縮統計範圍用的，用法跟週報版一致。
    """
    from datetime import date
    from sqlalchemy.orm import joinedload
    from models import DailyLog

    query = (
        Engineer.query
        .options(joinedload(Engineer.group))
        .filter(Engineer.status == 'active', Engineer.diary_enabled.is_(True))
    )
    if department:
        query = query.filter_by(department=department)
    if group_id:
        query = query.filter_by(group_id=group_id)
    active_engineers = query.order_by(Engineer.sort_order, Engineer.id).all()

    monday = date.fromisocalendar(year, week, 1)
    friday = monday + timedelta(days=4)
    submitted_ids = set(
        r.engineer_id for r in DailyLog.query.filter(
            DailyLog.log_date >= monday, DailyLog.log_date <= friday
        ).all()
    )
    submitted = [e for e in active_engineers if e.id in submitted_ids]
    not_submitted = [e for e in active_engineers if e.id not in submitted_ids]
    return {
        'total': len(active_engineers),
        'submitted_count': len(submitted),
        'not_submitted_count': len(not_submitted),
        'submitted': submitted,
        'not_submitted': not_submitted,
    }


# ============================================================
# 考核系統模組B：BARS（行為錨定評分法）軟指標定義
# 全公司統一適用的四個構面，1/3/5 分的行為描述當作評分錨點，主管評分時
# 對照這些描述打分（實際填寫時，5-4分/3-2分/1分這種區間怎麼判斷由主管
# 自行斟酌，錨點只提供三個代表性的參考點，不強制只能打1/3/5這三個分數）。
# 「具體事例」欄位是必填，讓抽象的評分標準有實際依據可以佐證。
# ============================================================
DEFAULT_SOFT_DIMENSIONS = {
    'autonomy': {
        'label': '自主性（Autonomy）',
        'anchors': {
            5: '主動發現問題並提出解決方案，不待主管指示即行動，且事後回報結果',
            3: '能在主管明確交辦後獨立完成，但較少主動發現潛在問題',
            1: '需要主管逐步指示才能執行，遇到障礙即停滯等待',
        },
    },
    'ownership': {
        'label': '責任感（Ownership）',
        'anchors': {
            5: '出錯時第一時間主動告知並提出補救方案，對承諾的交付全力兌現',
            3: '能完成交辦任務，但出錯時傾向被動等主管發現才處理',
            1: '出錯後隱瞞或歸咎他人，交付常跳票且無事前預警',
        },
    },
    'collaboration': {
        'label': '協作（Collaboration）',
        'anchors': {
            5: '主動跨部門/跨團隊溝通協調，樂於分享資源與知識，能適時支援他人並促成團隊目標達成',
            3: '能配合團隊要求完成分工任務，但較少主動協助他人或跨部門溝通',
            1: '只顧完成自己份內工作，對團隊需求消極應對，甚至造成溝通協調上的困難',
        },
    },
    'growth': {
        'label': '成長性（Growth）',
        'anchors': {
            5: '主動學習新技能/新知識並實際應用在工作上，積極尋求回饋並持續調整精進',
            3: '能配合公司安排的訓練與要求學習，但較少主動尋求成長機會',
            1: '對學習成長被動甚至抗拒，重複相同錯誤而未見改善',
        },
    },
}

_SOFT_DIMENSION_ORDER = ['autonomy', 'ownership', 'collaboration', 'growth']


def get_soft_dimensions():
    """
    取得模組B軟指標（BARS）四個構面目前的錨點文字，改由
    ModuleBSoftDimensionConfig 資料表讀取（管理者/人資可在「部門硬指標庫」
    頁面編輯），資料庫裡還沒有資料時（例如尚未執行過種子資料）退回
    DEFAULT_SOFT_DIMENSIONS 這份預設內容，確保任何情況下都有東西可用。
    回傳格式跟原本寫死的常數一致：{dimension: {'label':..., 'anchors': {5:.., 3:.., 1:..}}}
    """
    from models import ModuleBSoftDimensionConfig
    configs = {c.dimension: c for c in ModuleBSoftDimensionConfig.query.order_by(ModuleBSoftDimensionConfig.sort_order).all()}
    if not configs:
        return DEFAULT_SOFT_DIMENSIONS

    result = {}
    for dim in _SOFT_DIMENSION_ORDER:
        c = configs.get(dim)
        if c:
            result[dim] = {'label': c.label, 'anchors': {5: c.anchor_5, 3: c.anchor_3, 1: c.anchor_1}}
        elif dim in DEFAULT_SOFT_DIMENSIONS:
            result[dim] = DEFAULT_SOFT_DIMENSIONS[dim]
    return result


MODULE_B_DEFAULT_WEIGHT = {'hard': 50.0, 'soft': 50.0}




def get_dept_weight_config(department):
    """
    取得某部門的硬/軟指標權重比例（模組B用）。找不到對應設定時，回傳
    保守的預設值 50/50，不會因為新部門還沒設定就無法使用模組B。
    回傳 dict：{'hard': Decimal, 'soft': Decimal}
    """
    from models import DeptWeightConfig
    config = DeptWeightConfig.query.filter_by(department=department).first()
    if not config:
        return dict(MODULE_B_DEFAULT_WEIGHT)
    return {'hard': config.hard_weight_pct, 'soft': config.soft_weight_pct}


def ensure_dept_goal_template(department):
    """
    確保這個部門有一份「目標設定範本」（DeptGoalTemplateItem）可用：如果
    完全沒有，就用這個部門所有啟用中的硬指標、平均分配權重，自動產生一份
    預設內容；管理者/人資可以之後再到「目標設定」頁面調整。這個函式同時
    給「目標設定」編輯頁面（第一次打開時）跟「產生考核表」（生成新的模組B
    考核表時）共用，確保不管從哪個入口，這個部門遲早都會有範本可以套用，
    不會有人卡在完全沒有硬指標可填的狀態。
    回傳這個部門目前的範本項目清單（DeptGoalTemplateItem，含 library_indicator）。
    """
    from decimal import Decimal
    from sqlalchemy.orm import joinedload
    from extensions import db
    from models import DeptIndicatorLibrary, DeptGoalTemplateItem

    existing_items = (
        DeptGoalTemplateItem.query.filter_by(department=department)
        .options(joinedload(DeptGoalTemplateItem.library_indicator))
        .all()
    )
    if existing_items:
        return existing_items

    library_options = (
        DeptIndicatorLibrary.query.filter_by(department=department, is_active=True)
        .order_by(DeptIndicatorLibrary.sort_order).all()
    )
    if not library_options:
        return []

    equal_weight = (Decimal('100') / len(library_options)).quantize(Decimal('0.1'))
    for lib in library_options:
        db.session.add(DeptGoalTemplateItem(
            department=department, library_id=lib.id, weight_pct=equal_weight,
        ))
    db.session.commit()

    return (
        DeptGoalTemplateItem.query.filter_by(department=department)
        .options(joinedload(DeptGoalTemplateItem.library_indicator))
        .all()
    )
