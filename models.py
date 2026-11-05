# -*- coding: utf-8 -*-
"""
models.py - 資料庫 ORM 模型定義
包含：Group（組別）、Engineer（工程師/使用者）、Project（客戶／專案）、
      WeeklyReport（週報主表）、WeeklyReportItem（週報項目明細）、
      Attachment（附件）、AuditLog（操作紀錄）、EmailTemplate（Email範本）
"""
import uuid
from flask_login import UserMixin
from extensions import db, bcrypt
from utils.timezone_utils import taipei_now


def gen_uuid():
    """產生唯一識別碼，用於卡關事項的追蹤鏈 (chain_id)"""
    return str(uuid.uuid4())


class Group(db.Model):
    """RD 組別，例如 ID/ME、EE/FW、SW/APP，可由管理者自行新增"""
    __tablename__ = 'groups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    department = db.Column(db.String(50), nullable=True, index=True)  # 歸屬部門，部門主管只看得到自己部門的組別
    sort_order = db.Column(db.Integer, default=0, index=True)  # 拖曳排序用
    created_at = db.Column(db.DateTime, default=taipei_now)

    engineers = db.relationship('Engineer', backref='group', lazy=True)

    def __repr__(self):
        return f'<Group {self.name}>'


class Engineer(db.Model, UserMixin):
    """工程師/使用者資料表（同時作為登入帳號主體）"""
    __tablename__ = 'engineers'

    id = db.Column(db.Integer, primary_key=True)
    emp_id = db.Column(db.String(20), unique=True, nullable=False)      # 工號
    name = db.Column(db.String(50), nullable=False)                    # 姓名
    nickname = db.Column(db.String(50), nullable=True)                 # 小名（匯出報表時優先顯示）
    department = db.Column(db.String(50), nullable=True, index=True)   # 部門（比組別更上一層的分類，純文字）
    group_id = db.Column(db.Integer, db.ForeignKey('groups.id'), nullable=True, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='engineer')  # admin / engineer / viewer
    status = db.Column(db.String(20), nullable=False, default='active', index=True)  # active / inactive(離職)
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)  # 顯示順序，可拖曳調整
    hire_date = db.Column(db.Date, nullable=True)  # 到職日期
    # 直屬主管：對整個人事結構而言的預設主管（不一定等於部門主管/組別主管角色，
    # 可以指定成任何一位既有人員，比較貼近實際的彙報關係）
    supervisor_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True, index=True)
    # 最高直屬主管：不一定等於「一路往上追 supervisor_id 追到底」算出來的人，
    # 有些組織的最終升遷/裁決權在特定高層手上、不見得跟正式的彙報鏈一致，
    # 所以做成一個可以獨立指定的欄位，而不是每次都動態計算。
    top_supervisor_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=taipei_now)

    # 功能開關：可以個別針對每個人開啟/關閉「週報」「日誌」「客戶/專案」三項功能。
    # 預設一律開啟（True），避免既有帳號在欄位補上去的當下被意外關閉功能。
    # 關閉週報：填寫本週週報、我的歷史週報都會關閉；關閉日誌：填寫日誌關閉；
    # 關閉客戶/專案：我的客戶/專案關閉。
    weekly_report_enabled = db.Column(db.Boolean, nullable=False, default=True)
    diary_enabled = db.Column(db.Boolean, nullable=False, default=True)
    client_project_enabled = db.Column(db.Boolean, nullable=False, default=True)

    reports = db.relationship('WeeklyReport', backref='engineer', lazy=True,
                               foreign_keys='WeeklyReport.engineer_id')
    supervisor = db.relationship('Engineer', remote_side=[id], foreign_keys=[supervisor_id])
    top_supervisor = db.relationship('Engineer', remote_side=[id], foreign_keys=[top_supervisor_id])

    # ---- 密碼處理 ----
    def set_password(self, raw_password):
        self.password_hash = bcrypt.generate_password_hash(raw_password).decode('utf-8')

    def check_password(self, raw_password):
        return bcrypt.check_password_hash(self.password_hash, raw_password)

    # ---- 角色判斷輔助方法 ----
    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def is_engineer(self):
        return self.role == 'engineer'

    @property
    def is_viewer(self):
        return self.role == 'viewer'

    @property
    def is_active_employee(self):
        return self.status == 'active'

    @property
    def display_name(self):
        """優先顯示小名，若未設定小名則退回顯示姓名（匯出報表等場合使用）"""
        return self.nickname.strip() if self.nickname and self.nickname.strip() else self.name

    def __repr__(self):
        return f'<Engineer {self.emp_id} {self.name}>'


class Project(db.Model):
    """客戶／專案資料表"""
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)         # 專案代號
    full_name = db.Column(db.String(200), nullable=False)                # 專案全名/客戶全名
    status = db.Column(db.String(20), nullable=False, default='進行中', index=True)   # 進行中 / 已結案
    note = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(20), nullable=False, default='primary')  # 專案代號徽章顏色 (Bootstrap 色系)
    department = db.Column(db.String(50), nullable=True, index=True)  # 歸屬部門，只有這個部門的部門主管看得到（未設定則沿用推斷邏輯）
    owner_engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True, index=True)  # 主要負責工程師
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)  # 顯示順序，可拖曳調整
    created_at = db.Column(db.DateTime, default=taipei_now)

    owner = db.relationship('Engineer', foreign_keys=[owner_engineer_id])

    def __repr__(self):
        return f'<Project {self.code}>'


class WeeklyReport(db.Model):
    """週報主表：一位工程師一週一筆"""
    __tablename__ = 'weekly_reports'

    id = db.Column(db.Integer, primary_key=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    week_number = db.Column(db.Integer, nullable=False)       # ISO 週次
    report_date = db.Column(db.Date, nullable=False)          # 對應填報日期，如 2026-07-20
    status = db.Column(db.String(20), nullable=False, default='未填寫')  # 未填寫 / 草稿 / 已送出
    locked = db.Column(db.Boolean, default=False)             # 送出後鎖定
    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)
    submitted_at = db.Column(db.DateTime, nullable=True)

    items = db.relationship('WeeklyReportItem', backref='report', lazy=True,
                             cascade='all, delete-orphan',
                             order_by='WeeklyReportItem.sort_order')
    notes = db.relationship('ReportNote', backref='report', lazy=True,
                             cascade='all, delete-orphan',
                             order_by='ReportNote.created_at.desc()')

    __table_args__ = (
        db.UniqueConstraint('engineer_id', 'year', 'week_number', name='uq_engineer_week'),
        # 許多查詢（Dashboard統計、未填寫追蹤、匯出報表）是直接用 year+week_number
        # 篩選、不帶 engineer_id，上面的 UniqueConstraint 索引以 engineer_id 為第一欄，
        # 這類查詢用不到，額外建一個索引才能真正加速
        db.Index('ix_weekly_reports_year_week', 'year', 'week_number'),
    )

    def __repr__(self):
        return f'<WeeklyReport {self.engineer_id} {self.year}-W{self.week_number}>'


class WeeklyReportItem(db.Model):
    """週報項目明細，一週報可有多筆（本週已執行 / 下週計劃）"""
    __tablename__ = 'weekly_report_items'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('weekly_reports.id'), nullable=False, index=True)
    item_type = db.Column(db.String(20), nullable=False)   # last_week(本週已執行) / this_week(下週計劃)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    content = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0)           # 越小越重要
    is_carried_over = db.Column(db.Boolean, default=False)  # 是否延續自上週未完成事項
    is_completed = db.Column(db.Boolean, default=False)     # (適用 this_week 類型的上週項目) 是否已完成
    chain_id = db.Column(db.String(64), default=gen_uuid, index=True)   # 用於追蹤同一件事跨週延續的鏈
    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    project = db.relationship('Project')
    attachments = db.relationship('Attachment', backref='item', lazy=True,
                                   cascade='all, delete-orphan')

    def contains_stuck_keyword(self, keywords):
        """檢查內容是否含卡關關鍵字"""
        return any(kw in (self.content or '') for kw in keywords)

    def __repr__(self):
        return f'<Item {self.id} {self.item_type}>'


class ReportNote(db.Model):
    """
    週報附註／需要討論留言：附掛在單一週報 (WeeklyReport) 下的對話串。
    工程師可以在填寫週報時留言（例如卡關、需要主管協助的事項），
    管理者在「依工程師檢視」頁面看到同一份週報時可以回覆，形成一問一答的對話記錄。
    """
    __tablename__ = 'report_notes'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('weekly_reports.id'), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True)
    author_name = db.Column(db.String(50), nullable=False)   # 留言當下的姓名快照，帳號刪除後仍看得出是誰留言
    author_role = db.Column(db.String(20), nullable=False)   # 留言當下的角色快照 (admin/engineer)，用於畫面上區分顯示樣式
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='待討論', index=True)  # 待討論 / 討論中 / 已處理
    created_at = db.Column(db.DateTime, default=taipei_now, index=True)

    author = db.relationship('Engineer', foreign_keys=[author_id])

    def __repr__(self):
        return f'<ReportNote {self.id} report={self.report_id}>'


class Attachment(db.Model):
    """附件（截圖／圖片），以 Base64 存於 DB"""
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('weekly_report_items.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    data_base64 = db.Column(db.Text, nullable=False)  # 圖片以 Base64 字串存放
    created_at = db.Column(db.DateTime, default=taipei_now)


class AuditLog(db.Model):
    """操作紀錄：記錄誰在何時新增/修改/刪除哪一筆資料"""
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True, index=True)
    user_name = db.Column(db.String(50), nullable=True)   # 保留姓名快照，避免帳號刪除後查無資料
    action = db.Column(db.String(50), nullable=False, index=True)     # create / update / delete / login / export / lock / unlock ...
    target_type = db.Column(db.String(50), nullable=False)  # WeeklyReport / Project / Engineer ...
    target_id = db.Column(db.String(50), nullable=True)
    detail = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=taipei_now, index=True)

    def __repr__(self):
        return f'<AuditLog {self.action} {self.target_type}:{self.target_id}>'


class EmailTemplate(db.Model):
    """Email 通知範本，可由 Admin 於後台編輯"""
    __tablename__ = 'email_templates'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)  # reminder / summary / manual_reminder
    name = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)   # 可用 {name} {week} {deadline} 等變數
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)


class SystemSetting(db.Model):
    """系統設定 key-value（例如填寫截止日、提醒時間等可調參數）"""
    __tablename__ = 'system_settings'

    key = db.Column(db.String(50), primary_key=True)
    # 用 Text（不限長度）而不是原本的 VARCHAR(500)：像「週報填寫原則」這種內容
    # 很容易超過 500 字，SQLite 不會嚴格檢查長度所以本機測試不會出錯，但接上
    # Cloud SQL（MySQL）後會嚴格擋下超長內容，噴出 500 錯誤。
    value = db.Column(db.Text, nullable=True)


# ---------------------------------------------------------------------------
# 以下為「原績效考核系統」整合進來的資料模型
# ---------------------------------------------------------------------------
# 原本績效考核系統有自己獨立的 User/Department 資料表，但這裡整合進週報系統後，
# 「人」統一用週報系統原本就有的 Engineer（工號、姓名、部門、組別都已經存在，
# 不重複建一份），不另外建 Department 資料表（部門一樣沿用 Engineer.department
# 這個純文字欄位，跟週報系統其他地方的部門篩選邏輯一致）。
#
# 另外，原系統「主管評分」與「高管覆評」是用 ReviewForm.supervisor_id /
# executive_reviewer_id 明確指定「這張表單誰能評分」；這裡簡化成跟週報系統
# 其他頁面一樣的「範圍制」：部門主管能評自己部門的人、組別主管能評自己組別
# 的人，不需要每張表單各自指定評分者，也拿掉「高管覆評」這一層（原系統的
# needs_executive_review / executive_score），維持「員工自評 → 主管評分」
# 兩段式流程，排名/排名調整功能也不在這次整合範圍內。
class ReviewPeriod(db.Model):
    """績效考核期間，例如 115 年第 2 季 (115Q2)"""
    __tablename__ = 'review_periods'

    id = db.Column(db.Integer, primary_key=True)
    roc_year = db.Column(db.Integer, nullable=False)   # 民國年，例如 115
    quarter = db.Column(db.Integer, nullable=False)    # 1~4
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    self_report_deadline = db.Column(db.Date, nullable=True)  # 員工自評填寫截止日，主管評分不受此限制
    is_open = db.Column(db.Boolean, default=True, nullable=False)  # 是否開放填寫/評分
    forms_generated_at = db.Column(db.DateTime, nullable=True)  # 最近一次按下「產生考核表」的時間
    # 這個期間使用「模組A」（現行的固定考評項目：執行成效/工作態度/團隊精神/
    # 執行力）還是「模組B」（部門硬指標庫+BARS軟指標，各部門硬/軟權重不同）。
    # 一個考核期間只能用其中一種模組，不會混用；不同期間可以各自選擇，
    # 例如這一季先用模組A，下一季試辦模組B。
    module = db.Column(db.String(1), nullable=False, default='A')
    created_at = db.Column(db.DateTime, default=taipei_now)

    # cascade='all, delete-orphan'：刪除考核期間時，底下所有考核表（連同各自的
    # 考評項目評分、排名結果）一併刪除，避免留下指向已刪除期間的孤兒資料，也
    # 避免資料庫外鍵限制擋下刪除動作（MySQL 預設會檢查外鍵完整性）。
    review_forms = db.relationship('ReviewForm', backref='period', lazy=True, cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('roc_year', 'quarter', name='uq_review_period_year_quarter'),
    )

    @property
    def label(self):
        return f'{self.roc_year}Q{self.quarter}'

    def __repr__(self):
        return f'<ReviewPeriod {self.label}>'


class ScoreCriteria(db.Model):
    """考評項目定義（執行成效/工作態度/團隊精神/執行力），含權重"""
    __tablename__ = 'score_criteria'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.Text, nullable=False)  # 說明文字較長，用 Text
    weight = db.Column(db.Numeric(4, 2), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f'<ScoreCriteria {self.code} w={self.weight}>'


class ReviewForm(db.Model):
    """一張考核表：一位員工在一個考核期間的自評 + 主管評分"""
    __tablename__ = 'review_forms'

    id = db.Column(db.Integer, primary_key=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=False, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey('review_periods.id'), nullable=False, index=True)

    # 使用者自由輸入的長文字一律用 Text，不用 String(n) 限制長度
    revenue_report = db.Column(db.Text, nullable=True)   # 增加營收/獲利的具體成果
    cost_report = db.Column(db.Text, nullable=True)      # 降低成本的具體成果
    risk_report = db.Column(db.Text, nullable=True)      # 降低風險/準時達成任務的具體成果

    status = db.Column(db.String(20), nullable=False, default='draft', index=True)  # draft/submitted/scored

    submitted_at = db.Column(db.DateTime, nullable=True)  # 員工送出自評的時間
    scored_at = db.Column(db.DateTime, nullable=True)     # 直屬主管完成評分的時間
    executive_signed_at = db.Column(db.DateTime, nullable=True)  # 最高直屬主管完成覆評的時間

    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    engineer = db.relationship('Engineer', foreign_keys=[engineer_id])
    criteria_scores = db.relationship('CriteriaScore', backref='review_form', cascade='all, delete-orphan', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('engineer_id', 'period_id', name='uq_review_form_engineer_period'),
    )

    def __repr__(self):
        return f'<ReviewForm engineer={self.engineer_id} period={self.period_id}>'


class CriteriaScore(db.Model):
    """單一考評項目在某張表單上的評分"""
    __tablename__ = 'criteria_scores'

    id = db.Column(db.Integer, primary_key=True)
    review_form_id = db.Column(db.Integer, db.ForeignKey('review_forms.id'), nullable=False, index=True)
    criteria_id = db.Column(db.Integer, db.ForeignKey('score_criteria.id'), nullable=False)
    supervisor_score = db.Column(db.Numeric(5, 2), nullable=True)  # 直屬主管評分，0~100
    executive_score = db.Column(db.Numeric(5, 2), nullable=True)   # 最高直屬主管覆評分數，0~100，不需要覆評則為 NULL

    criteria = db.relationship('ScoreCriteria')

    __table_args__ = (
        db.UniqueConstraint('review_form_id', 'criteria_id', name='uq_criteria_score_form_criteria'),
    )

    @property
    def avg_score(self):
        """
        供排名/總分計算使用：只有同時指定了直屬主管與最高直屬主管，才是
        兩階段流程，取兩邊評分的平均；如果只指定了其中一項（不管是只有
        直屬主管、或只有最高直屬主管），那唯一被指定的那位就是唯一評分者，
        直接採用 supervisor_score（不管實際評分的人是哪一種身分，一律存在
        這個欄位裡），不需要平均。
        """
        if self.supervisor_score is None:
            return None
        engineer = self.review_form.engineer
        needs_executive = engineer.supervisor_id is not None and engineer.top_supervisor_id is not None
        if needs_executive:
            if self.executive_score is None:
                return None
            return (self.supervisor_score + self.executive_score) / 2
        return self.supervisor_score

    @property
    def weighted_score(self):
        avg = self.avg_score
        if avg is None:
            return None
        return avg * self.criteria.weight


class RankingResult(db.Model):
    """
    計算後的總分與排名快取（由排名服務重新計算並覆寫，不手動改動除了
    adjusted_score）。設計參考「原績效考核系統」，department 這裡
    改用 Engineer.department 同款的純文字欄位，不是獨立的部門資料表。
    """
    __tablename__ = 'ranking_results'

    id = db.Column(db.Integer, primary_key=True)
    review_form_id = db.Column(db.Integer, db.ForeignKey('review_forms.id'), nullable=False, unique=True)
    period_id = db.Column(db.Integer, db.ForeignKey('review_periods.id'), nullable=False, index=True)
    department = db.Column(db.String(50), nullable=True, index=True)

    total_score = db.Column(db.Numeric(6, 2), nullable=False)     # 實得分數合計
    department_rank = db.Column(db.Integer, nullable=True)         # 部門內排名
    company_rank = db.Column(db.Integer, nullable=True, index=True)  # 公司內排名（排名頁面固定依此排序）

    adjusted_score = db.Column(db.Numeric(6, 2), nullable=True)   # 排名調整後實得分數（人資可手動微調，可為 NULL）
    adjusted_by_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True)
    adjusted_note = db.Column(db.Text, nullable=True)   # 調整原因說明，長文字用 Text
    adjusted_at = db.Column(db.DateTime, nullable=True)

    calculated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    review_form = db.relationship('ReviewForm', backref=db.backref('ranking_result', uselist=False, cascade='all, delete-orphan'))
    adjusted_by = db.relationship('Engineer', foreign_keys=[adjusted_by_id])

    @property
    def final_score(self):
        return self.adjusted_score if self.adjusted_score is not None else self.total_score


# ============================================================
# 考核系統「模組B」：部門硬指標庫 + BARS 軟指標
# 跟模組A（ScoreCriteria/ReviewForm/CriteriaScore）完全平行、不共用資料表，
# 只共用 ReviewPeriod（用 module 欄位判斷這個期間走哪一套）跟 Engineer。
# ============================================================

class DeptIndicatorLibrary(db.Model):
    """
    部門硬指標庫：人資/CMO 在後台維護，各部門各自一份可選的硬指標清單
    （例如業務部的「業績達成率」「新客戶開發數」），供部門主管在「目標設定」
    階段挑選要放進考核表的項目。同一個部門可以有多筆，不用的可以停用
    （is_active=False）但不刪除，保留歷史紀錄。
    """
    __tablename__ = 'dept_indicator_library'

    id = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(100), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)              # 指標名稱，例如「業績達成率」
    measurement_standard = db.Column(db.Text, nullable=True)       # 衡量標準說明，例如「對照年度業績目標的百分比」
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=taipei_now)

    def __repr__(self):
        return f'<DeptIndicatorLibrary {self.department}/{self.name}>'


class DeptWeightConfig(db.Model):
    """
    部門硬/軟指標權重比例設定，例如業務部 70/30、研發部 60/40。每個部門
    只有一筆設定；找不到對應部門設定時，由呼叫端套用一個保守的預設值
    （50/50），不會因為新部門還沒設定就無法使用模組B。
    """
    __tablename__ = 'dept_weight_config'

    id = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(100), nullable=False, unique=True)
    hard_weight_pct = db.Column(db.Numeric(4, 1), nullable=False)   # 例如 70.0
    soft_weight_pct = db.Column(db.Numeric(4, 1), nullable=False)   # 例如 30.0
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    def __repr__(self):
        return f'<DeptWeightConfig {self.department} {self.hard_weight_pct}/{self.soft_weight_pct}>'


# 軟指標固定四個構面（自主性/責任感/協作/成長性），全公司統一（跟部門無關）。
# 這裡只是列出四個構面的代號，方便程式內部需要「固定順序跑一輪」時使用；
# 實際的 BARS 錨點文字（label、5/3/1分描述）存在 ModuleBSoftDimensionConfig
# 資料表，管理者/人資可以在「部門硬指標庫」頁面直接編輯，不是寫死的常數
# ——四個構面「有哪些」是固定的設計，但每個構面「怎麼描述」是可以調整的。
MODULE_B_SOFT_DIMENSIONS = ('autonomy', 'ownership', 'collaboration', 'growth')


class ModuleBForm(db.Model):
    """
    模組B的一張考核表：一位員工在一個考核期間的目標設定 + 自評 + 主管評分
    （+ 視情況需要的覆評）。跟模組A的 ReviewForm 平行但完全獨立的資料表。

    status 流程（比模組A多了最前面「目標設定」相關的兩個狀態）：
    goal_setting（部門主管挑選硬指標中）
      → pending_hr_approval（主管送出，待人資/CMO審核）
      → （人資核准）draft（可以開始自評，跟模組A的 draft 同義）
        或（人資駁回）回到 goal_setting，主管修改後重新送審
      → submitted（員工送出自評）
      → scored（直屬主管完成評分）
      → （若需要覆評）executive_signed_at 有值即代表覆評完成
    """
    __tablename__ = 'module_b_forms'

    id = db.Column(db.Integer, primary_key=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=False, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey('review_periods.id'), nullable=False, index=True)

    status = db.Column(db.String(20), nullable=False, default='goal_setting', index=True)

    goal_submitted_at = db.Column(db.DateTime, nullable=True)     # 主管送出目標設定、等待人資審核的時間
    goal_approved_at = db.Column(db.DateTime, nullable=True)      # 人資核准目標設定的時間
    goal_approved_by_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True)

    submitted_at = db.Column(db.DateTime, nullable=True)          # 員工送出自評的時間
    scored_at = db.Column(db.DateTime, nullable=True)             # 直屬主管完成評分的時間
    executive_signed_at = db.Column(db.DateTime, nullable=True)   # 最高直屬主管完成覆評的時間

    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    period = db.relationship('ReviewPeriod', backref=db.backref('module_b_forms', cascade='all, delete-orphan'))
    engineer = db.relationship('Engineer', foreign_keys=[engineer_id])
    goal_approved_by = db.relationship('Engineer', foreign_keys=[goal_approved_by_id])
    hard_indicators = db.relationship('ModuleBHardIndicator', backref='form', cascade='all, delete-orphan', lazy=True)
    soft_indicators = db.relationship('ModuleBSoftIndicator', backref='form', cascade='all, delete-orphan', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('engineer_id', 'period_id', name='uq_module_b_form_engineer_period'),
    )

    def __repr__(self):
        return f'<ModuleBForm engineer={self.engineer_id} period={self.period_id} status={self.status}>'


class ModuleBHardIndicator(db.Model):
    """
    模組B考核表底下，某一筆從部門指標庫挑選出來的硬指標評分明細。
    weight_pct 是這個指標在「硬指標」這個大項目裡佔的權重（同一張表底下
    所有硬指標的 weight_pct 加總應為 100，由目標設定畫面檢查）。
    """
    __tablename__ = 'module_b_hard_indicators'

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('module_b_forms.id'), nullable=False, index=True)
    library_id = db.Column(db.Integer, db.ForeignKey('dept_indicator_library.id'), nullable=False)

    target_value = db.Column(db.Text, nullable=True)      # 目標值/具體目標說明，主管在目標設定階段填寫
    weight_pct = db.Column(db.Numeric(4, 1), nullable=False, default=0)  # 這個指標在硬指標裡的權重

    self_score = db.Column(db.Numeric(5, 2), nullable=True)      # 員工自評分數，0~100
    self_comment = db.Column(db.Text, nullable=True)             # 員工自評的具體事例說明

    supervisor_score = db.Column(db.Numeric(5, 2), nullable=True)
    supervisor_comment = db.Column(db.Text, nullable=True)

    executive_score = db.Column(db.Numeric(5, 2), nullable=True)  # 覆評分數，不需要覆評則為 NULL

    library_indicator = db.relationship('DeptIndicatorLibrary')

    def __repr__(self):
        return f'<ModuleBHardIndicator form={self.form_id} lib={self.library_id}>'


class ModuleBSoftIndicator(db.Model):
    """
    模組B考核表底下，四個固定構面（自主性/責任感/協作/成長性）各一筆的
    BARS 評分明細。行為描述欄位刻意設計成必填（由表單送出時的後端檢查
    強制要求），確保抽象的評分項目有具體事例佐證。
    """
    __tablename__ = 'module_b_soft_indicators'

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey('module_b_forms.id'), nullable=False, index=True)
    dimension = db.Column(db.String(20), nullable=False)   # autonomy/ownership/collaboration/growth

    self_score = db.Column(db.Integer, nullable=True)             # 1~5
    self_behavior_desc = db.Column(db.Text, nullable=True)        # 具體行為描述，必填（後端檢查）

    supervisor_score = db.Column(db.Integer, nullable=True)
    supervisor_behavior_desc = db.Column(db.Text, nullable=True)

    executive_score = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('form_id', 'dimension', name='uq_module_b_soft_form_dimension'),
    )

    def __repr__(self):
        return f'<ModuleBSoftIndicator form={self.form_id} dim={self.dimension}>'


class DeptGoalTemplateItem(db.Model):
    """
    部門目標範本：同一個部門的所有人員、不分考核期間，共用同一份硬指標
    設定（挑哪些指標、目標值、權重），不用每個人、每一季各自重新設定一次。
    管理者/人資在「目標設定」頁面維護的就是這份部門層級的範本；每次幫
    某個人產生模組B的考核表時，會直接把他所屬部門當下的範本內容複製一份
    到他自己的考核表（ModuleBHardIndicator）裡，之後兩者就各自獨立——
    調整範本不會回頭去改已經產生過的考核表，避免評分進行到一半，目標卻
    被悄悄改掉。
    """
    __tablename__ = 'dept_goal_template_items'

    id = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(100), nullable=False, index=True)
    library_id = db.Column(db.Integer, db.ForeignKey('dept_indicator_library.id'), nullable=False)
    target_value = db.Column(db.Text, nullable=True)
    weight_pct = db.Column(db.Numeric(4, 1), nullable=False, default=0)

    library_indicator = db.relationship('DeptIndicatorLibrary')

    def __repr__(self):
        return f'<DeptGoalTemplateItem department={self.department} lib={self.library_id}>'


class ModuleBSoftDimensionConfig(db.Model):
    """
    模組B軟指標（BARS 行為錨定評分）的錨點文字，全公司統一適用（不分部門），
    管理者/人資可以在「部門硬指標庫」頁面直接編輯。固定四個構面（自主性/
    責任感/協作/成長性），初始內容由 seed_data.py 建立，之後就是資料庫裡的
    可編輯內容，不再是寫死在程式碼裡的常數。
    """
    __tablename__ = 'module_b_soft_dimension_config'

    dimension = db.Column(db.String(20), primary_key=True)  # autonomy/ownership/collaboration/growth
    label = db.Column(db.String(50), nullable=False)
    anchor_5 = db.Column(db.Text, nullable=False)
    anchor_3 = db.Column(db.Text, nullable=False)
    anchor_1 = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<ModuleBSoftDimensionConfig {self.dimension}>'


class DailyLog(db.Model):
    """
    填寫日誌：每天可以記錄多筆客戶拜訪/聯絡紀錄（客戶、國家、聯絡方式、
    內容摘要），跟週報是分開的獨立功能，只用日期關聯，不綁定到特定的
    WeeklyReport 記錄。「客戶」欄位改參照公司正式的 Project 資料表（跟
    「填寫週報」頁面的專案選單是同一份資料），不是自由輸入的文字。
    """
    __tablename__ = 'daily_logs'

    id = db.Column(db.Integer, primary_key=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=False, index=True)
    log_date = db.Column(db.Date, nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True, index=True)
    country = db.Column(db.String(100), nullable=True)
    contact_method = db.Column(db.String(100), nullable=True)
    content_summary = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    engineer = db.relationship('Engineer', backref=db.backref('daily_logs', cascade='all, delete-orphan'))
    project = db.relationship('Project')


class PersonalProject(db.Model):
    """
    我的客戶/專案：表格自動產生（不是手動新增），依這位員工「最新一週的週報」
    跟「最近五天的日誌」涉及的專案自動列出。「起始日期」是使用者自己輸入、
    需要持久保存的欄位；「進度更新」不存在資料庫裡，是每次開啟頁面時即時
    彙整當週週報+當週日誌內容算出來的，不是手動填寫的欄位；「後續更新」
    則是使用者自己輸入、需要持久保存的欄位。
    """
    __tablename__ = 'personal_projects'

    id = db.Column(db.Integer, primary_key=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=True)
    followup_update = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0, index=True)
    created_at = db.Column(db.DateTime, default=taipei_now)
    updated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    engineer = db.relationship('Engineer', backref=db.backref('personal_projects', cascade='all, delete-orphan'))
    project = db.relationship('Project')


class ModuleBRankingResult(db.Model):
    """
    模組B的計算後總分與排名快取，結構比照模組A用的 RankingResult，只是
    改連結到 ModuleBForm（模組A、B 的評分結構完全不同——模組A是固定4項
    考評項目加權平均，模組B是硬指標（可變數量、依部門權重）+軟指標（固定
    4個構面）依部門硬/軟權重比例組合——所以用獨立的資料表，不共用同一張，
    避免混用造成邏輯複雜、難以維護。
    """
    __tablename__ = 'module_b_ranking_results'

    id = db.Column(db.Integer, primary_key=True)
    module_b_form_id = db.Column(db.Integer, db.ForeignKey('module_b_forms.id'), nullable=False, unique=True)
    period_id = db.Column(db.Integer, db.ForeignKey('review_periods.id'), nullable=False, index=True)
    department = db.Column(db.String(100), nullable=True, index=True)

    total_score = db.Column(db.Numeric(6, 2), nullable=False)
    department_rank = db.Column(db.Integer, nullable=True)
    company_rank = db.Column(db.Integer, nullable=True, index=True)

    adjusted_score = db.Column(db.Numeric(6, 2), nullable=True)
    adjusted_by_id = db.Column(db.Integer, db.ForeignKey('engineers.id'), nullable=True)
    adjusted_note = db.Column(db.Text, nullable=True)
    adjusted_at = db.Column(db.DateTime, nullable=True)

    calculated_at = db.Column(db.DateTime, default=taipei_now, onupdate=taipei_now)

    module_b_form = db.relationship(
        'ModuleBForm', backref=db.backref('ranking_result', uselist=False, cascade='all, delete-orphan')
    )
    adjusted_by = db.relationship('Engineer', foreign_keys=[adjusted_by_id])

    def __repr__(self):
        return f'<ModuleBRankingResult form={self.module_b_form_id} total={self.total_score}>'
