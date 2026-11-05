# -*- coding: utf-8 -*-
"""
seed_data.py - 系統最小初始化邏輯（獨立於 app.py，避免循環 import）
供 init_db.py（手動執行）與 app.py（雲端環境可選擇自動執行一次）共用。
呼叫前必須已經在 Flask app context 中。

重要：這裡「不會」建立任何假的工程師、專案、組別或範例週報資料。
系統第一次啟動時只會建立一個可以登入的管理者帳號，讓管理者能夠登入後，
自行透過「工程師帳號管理」「專案清單管理」頁面建立真實資料，或用 CSV 批次匯入。
Email 範本是系統運作所需的預設文字（用於提醒信排程功能），不算業務假資料，
會一併建立，管理者可以在「Email提醒設定」頁面自行編輯內容。
"""
from decimal import Decimal
from extensions import db
from models import Engineer, EmailTemplate, ScoreCriteria, DeptIndicatorLibrary, ModuleBSoftDimensionConfig

ADMIN_EMP_ID = 'A171'
ADMIN_PASSWORD = 'admin123'
ADMIN_NAME = '系統管理者'
import os as _os
from config import Config as _Config
ADMIN_EMAIL = _os.environ.get('ADMIN_EMAIL') or f'admin@{_Config.PLACEHOLDER_EMAIL_DOMAIN}'

# 考評項目與權重：對照「原績效考核系統」原本的考核表設計（合計權重 = 1.0）
DEFAULT_CRITERIA = [
    ('execution_result',
     '執行成效：（對公司的整體貢獻度）個人擔任職務或所領導部門，在增加公司營收、獲利、'
     '降低成本、降低風險、防範或改善缺失、達成職務工作任務、規劃或執行特別專案任務，'
     '是否有良好的績效成果？', Decimal('0.50'), 1),
    ('attitude',
     '工作態度：是否主動、積極、負責？是否有工作熱情、學習熱忱、企圖心，'
     '真心盡責想把工作做好？或需上級或其他人員不斷的督促？', Decimal('0.15'), 2),
    ('teamwork',
     '團隊精神：是否以公司整體利益為其個人工作時優先的考量基礎？樂意接受公司交付的專案'
     '工作任務，並和相關人員、部門、公司分工合作，真心想把工作做好？是否願意協助他人完成'
     '工作任務？或是以個人或部門利益為其工作時優先的考量基礎？', Decimal('0.15'), 3),
    ('execution_ability',
     '執行力：做事是否能分辨輕重緩急，能抓住重點/目標，有系統、有計劃、有方法、有步驟、'
     '有效率、有執行力？能協調、溝通、整合公司內外部所有人力物力資源，有品質並準時完成'
     '工作或公司交付任務？', Decimal('0.20'), 4),
]

# 考核系統模組B：部門硬指標庫的預設種子資料（GD/RD/SD 三個部門）。
# 格式：(部門, 指標名稱, 衡量標準)
DEFAULT_DEPT_INDICATORS = [
    ('RD', '缺陷率', '產品或程式碼缺陷率統計'),
    ('RD', '交付準時率', '專案交付日期符合原定時程的比例'),
    ('RD', 'OKR達成率', '對照季度OKR目標的達成百分比'),
    ('SD', '業績達成率', '對照年度業績目標的百分比'),
    ('SD', '新客戶開發數', '本季新增客戶數量'),
    ('SD', '毛利率', '訂單毛利率平均值'),
    ('SD', '客戶續約率', '既有客戶合約續約比例'),
    ('SD', '應收帳款回收天數', '平均收款天數'),
    ('GD', '內部客戶滿意度', '內部服務滿意度調查分數'),
    ('GD', '流程效率', '請購案等作業平均處理天數'),
    ('GD', '成本節省金額', '本季累計節省成本金額'),
    ('GD', '任務完成率', '交辦任務準時完成比例'),
]

# 考核系統模組B：軟指標（BARS）錨點文字的預設種子資料，全公司統一適用
# 格式：(構面代碼, 顯示名稱, 5分描述, 3分描述, 1分描述, 排序)
DEFAULT_SOFT_DIMENSION_SEED = [
    ('autonomy', '自主性（Autonomy）',
     '主動發現問題並提出解決方案，不待主管指示即行動，且事後回報結果',
     '能在主管明確交辦後獨立完成，但較少主動發現潛在問題',
     '需要主管逐步指示才能執行，遇到障礙即停滯等待', 1),
    ('ownership', '責任感（Ownership）',
     '出錯時第一時間主動告知並提出補救方案，對承諾的交付全力兌現',
     '能完成交辦任務，但出錯時傾向被動等主管發現才處理',
     '出錯後隱瞞或歸咎他人，交付常跳票且無事前預警', 2),
    ('collaboration', '協作（Collaboration）',
     '主動跨部門/跨團隊溝通協調，樂於分享資源與知識，能適時支援他人並促成團隊目標達成',
     '能配合團隊要求完成分工任務，但較少主動協助他人或跨部門溝通',
     '只顧完成自己份內工作，對團隊需求消極應對，甚至造成溝通協調上的困難', 3),
    ('growth', '成長性（Growth）',
     '主動學習新技能/新知識並實際應用在工作上，積極尋求回饋並持續調整精進',
     '能配合公司安排的訓練與要求學習，但較少主動尋求成長機會',
     '對學習成長被動甚至抗拒，重複相同錯誤而未見改善', 4),
]


def seed_demo_data(verbose=True):
    """
    系統最小初始化：只建立一個管理者帳號（不含任何假工程師/專案/組別/範例週報），
    以及提醒信功能所需的預設 Email 範本。可安全地重複執行，已存在的資料不會被覆蓋。
    回傳 True 表示有實際新增資料，False 表示資料庫已完整、無需變動。
    """
    def log(msg):
        if verbose:
            print(msg)

    changed = False

    log('正在檢查管理者帳號...')
    admin = Engineer.query.filter_by(emp_id=ADMIN_EMP_ID).first()
    if not admin:
        admin = Engineer(
            emp_id=ADMIN_EMP_ID, name=ADMIN_NAME, email=ADMIN_EMAIL,
            role='admin', status='active', group_id=None,
        )
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
        changed = True
        log(f'已建立管理者帳號 {ADMIN_EMP_ID}')
    else:
        log('管理者帳號已存在，略過建立。')

    log('正在檢查 Email 範本...')
    templates_data = [
        ('reminder', '截止日自動提醒',
         '【自動提醒】第{year}年第{week}週工程師週報尚未填寫',
         '{name} 您好，<br><br>系統偵測到您尚未填寫第{year}年第{week}週的工程師週報，'
         '請於{deadline}前登入系統完成填寫。<br><br>如有任何問題，請聯繫RD主管。<br><br>此為系統自動發送信件，請勿直接回覆。'),
        ('manual_reminder', '手動一鍵提醒',
         '【提醒】請儘速填寫第{week}週工程師週報',
         '{name} 您好，<br><br>提醒您第{year}年第{week}週週報尚未填寫，請儘速登入系統完成填寫，謝謝。'),
        ('summary', '週報彙整報表通知',
         '第{year}年第{week}週 工程師週報彙整報表',
         '主管您好，<br><br>本週工程師週報已彙整完成，詳細內容請見附件報表。<br><br>此為系統自動發送信件。'),
    ]
    for key, name, subject, body in templates_data:
        if not EmailTemplate.query.filter_by(key=key).first():
            db.session.add(EmailTemplate(key=key, name=name, subject=subject, body=body))
            db.session.commit()
            changed = True

    log('正在檢查績效考核系統的預設考評項目...')
    for code, name, weight, sort_order in DEFAULT_CRITERIA:
        if not ScoreCriteria.query.filter_by(code=code).first():
            db.session.add(ScoreCriteria(code=code, name=name, weight=weight, sort_order=sort_order, is_active=True))
            db.session.commit()
            changed = True

    log('正在檢查考核系統模組B的部門硬指標庫（GD/RD/SD）...')
    for i, (department, name, measurement_standard) in enumerate(DEFAULT_DEPT_INDICATORS if _Config.SEED_DEFAULT_DEPT_INDICATORS else [], start=1):
        if not DeptIndicatorLibrary.query.filter_by(department=department, name=name).first():
            db.session.add(DeptIndicatorLibrary(
                department=department, name=name, measurement_standard=measurement_standard,
                sort_order=i, is_active=True,
            ))
            db.session.commit()
            changed = True

    log('正在檢查考核系統模組B的軟指標（BARS）內容...')
    for dimension, label, anchor_5, anchor_3, anchor_1, sort_order in DEFAULT_SOFT_DIMENSION_SEED:
        if not ModuleBSoftDimensionConfig.query.filter_by(dimension=dimension).first():
            db.session.add(ModuleBSoftDimensionConfig(
                dimension=dimension, label=label, anchor_5=anchor_5, anchor_3=anchor_3,
                anchor_1=anchor_1, sort_order=sort_order,
            ))
            db.session.commit()
            changed = True

    log('=' * 60)
    log('系統初始化完成！')
    log(f'  管理者(Admin) : {ADMIN_EMP_ID} / 密碼 {ADMIN_PASSWORD}')
    log('  尚未建立任何工程師帳號、組別或專案，請登入後自行於')
    log('  「工程師帳號管理」「專案清單管理」頁面建立，或使用 CSV 批次匯入。')
    log('=' * 60)
    return changed


def fix_admin_account():
    """
    修復/建立管理者帳號，處理三種情況：
    1. 資料庫還是舊的 A001 -> 改成 A171，密碼重設為 admin123
    2. 資料庫已經是 A171 -> 只重設密碼為 admin123
    3. 資料庫完全空白，尚無任何帳號 -> 執行最小初始化 (seed_demo_data)
    呼叫前必須已經在 Flask app context 中。回傳說明文字。
    """
    old_admin = Engineer.query.filter_by(emp_id='A001').first()
    if old_admin:
        old_admin.emp_id = ADMIN_EMP_ID
        old_admin.set_password(ADMIN_PASSWORD)
        db.session.commit()
        return f'已將管理者帳號從 A001 更新為 {ADMIN_EMP_ID}，密碼已重設為 {ADMIN_PASSWORD}'

    new_admin = Engineer.query.filter_by(emp_id=ADMIN_EMP_ID).first()
    if new_admin:
        new_admin.set_password(ADMIN_PASSWORD)
        db.session.commit()
        return f'管理者帳號 {ADMIN_EMP_ID} 已存在，密碼已重設為 {ADMIN_PASSWORD}'

    seed_demo_data(verbose=False)
    return f'資料庫原本是空的，已建立管理者帳號（{ADMIN_EMP_ID} / 密碼 {ADMIN_PASSWORD}），未建立任何假資料'
