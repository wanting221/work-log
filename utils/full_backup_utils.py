# -*- coding: utf-8 -*-
"""
utils/full_backup_utils.py - 完整系統資料備份/還原（資料庫無關，SQLite/Cloud SQL 都適用）

跟 utils/backup_utils.py 舊版「直接複製 SQLite 檔案」的做法不同：
這裡是把「所有資料表的實際內容」查出來、序列化成 JSON、加密後存成 .enc 檔案。
這樣不管系統實際上是接 SQLite 還是 Cloud SQL（MySQL/PostgreSQL），備份／還原都能正常運作，
不會因為換了資料庫種類就失效。

還原時會先清空所有資料表，再依照備份內容重新寫入（保留原本的 id，確保帳號、專案、
週報彼此之間的關聯不會跑掉）。
"""
import base64
import hashlib
import json
from datetime import datetime, date
from decimal import Decimal

from cryptography.fernet import Fernet

from extensions import db
from models import (
    Group, Engineer, Project, WeeklyReport, WeeklyReportItem,
    Attachment, ReportNote, AuditLog, EmailTemplate, SystemSetting,
    DailyLog, PersonalProject,
    ReviewPeriod, ScoreCriteria, ReviewForm, CriteriaScore, RankingResult,
    DeptIndicatorLibrary, DeptWeightConfig, DeptGoalTemplateItem, ModuleBSoftDimensionConfig,
    ModuleBForm, ModuleBHardIndicator, ModuleBSoftIndicator, ModuleBRankingResult,
)

BACKUP_FORMAT_VERSION = 1

# 匯出／寫入資料庫時都要照這個順序：父資料表在前、子資料表在後，
# 這樣寫入時才不會因為外鍵關聯（例如週報項目要參照週報主表）而失敗。
# 清空資料表時則是反過來，從子資料表先刪到父資料表。
#
# 這份清單務必跟著 models.py 同步更新——這裡曾經漏掉日誌、個人客戶/專案
# 追蹤、以及績效考核模組A、模組B全部相關的資料表（一開始只有最早期、
# 週報系統剛上線時的那幾張表），漏掉的資料表在備份/還原時會被「悄悄
# 跳過」，不會有任何錯誤訊息提醒——這是最危險的地方，備份「看起來」
# 執行成功，實際上卻遺漏了一大部分資料。之後每次新增資料表，
# 都要記得同步補進這份清單，並注意新表格對其他表格的外鍵依賴順序。
MODEL_ORDER = [
    ('groups', Group),
    ('engineers', Engineer),
    ('projects', Project),
    ('weekly_reports', WeeklyReport),
    ('weekly_report_items', WeeklyReportItem),
    ('attachments', Attachment),
    ('report_notes', ReportNote),
    ('audit_logs', AuditLog),
    ('email_templates', EmailTemplate),
    ('system_settings', SystemSetting),
    ('daily_logs', DailyLog),
    ('personal_projects', PersonalProject),
    ('review_periods', ReviewPeriod),
    ('score_criteria', ScoreCriteria),
    ('review_forms', ReviewForm),
    ('criteria_scores', CriteriaScore),
    ('ranking_results', RankingResult),
    ('dept_indicator_library', DeptIndicatorLibrary),
    ('dept_weight_config', DeptWeightConfig),
    ('dept_goal_template_items', DeptGoalTemplateItem),
    ('module_b_soft_dimension_config', ModuleBSoftDimensionConfig),
    ('module_b_forms', ModuleBForm),
    ('module_b_hard_indicators', ModuleBHardIndicator),
    ('module_b_soft_indicators', ModuleBSoftIndicator),
    ('module_b_ranking_results', ModuleBRankingResult),
]

# engineers 這張表有兩個「自己參照自己」的外鍵欄位（supervisor_id、
# top_supervisor_id：直屬主管、最高直屬主管都是另一筆 engineers 紀錄）。
# 還原時如果照原本 JSON 裡的順序直接整批寫入，剛好遇到「主管的 id 比
# 下屬還大」（例如後來才升任主管、或帳號建立順序跟職位高低沒有對應）
# 就會在插入下屬那一列時，因為主管那筆紀錄還不存在，被資料庫的外鍵
# 檢查擋下來（MySQL/PostgreSQL 都是在執行當下就檢查外鍵，不會等整批
# 都寫完才檢查）。
# 解法：分兩階段寫入——第一階段先把這兩個欄位都清空（None）寫進去，
# 這樣每一列都不依賴任何其他列，一定能成功寫入；等全部人員都寫好之後，
# 第二階段再把這兩個欄位依照備份內容個別補回去，這時候不管指向誰，
# 對方都已經存在了。
_ENGINEER_SELF_REF_COLUMNS = ('supervisor_id', 'top_supervisor_id')


def _derive_key(passphrase):
    """由設定的密碼字串衍生出 Fernet 所需的 32 bytes urlsafe base64 金鑰"""
    digest = hashlib.sha256(passphrase.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def _serialize_value(value):
    """
    把 Python 的 datetime/date/Decimal 物件轉成 JSON 看得懂的型別，其餘型別
    JSON 可以直接處理的就原樣回傳。

    Decimal 特別轉成字串（不是 float）：這裡存的是薪資、考核評分、指標權重
    這類數字，float 在轉換時可能因為二進位浮點數的精度限制產生誤差（例如
    Decimal('12.10') 轉成 float 有可能變成 12.099999999999999），字串可以
    完整保留原始數值，還原時再轉回 Decimal 就不會有這個問題。
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _deserialize_value(model, column_name, value):
    """依照資料表欄位的實際型別，把字串還原回對應的 Python 型別（日期時間、數字）"""
    if value is None:
        return None
    column_type = str(model.__table__.columns[column_name].type).upper()
    if 'DATETIME' in column_type:
        return datetime.fromisoformat(value)
    if 'DATE' in column_type:
        return date.fromisoformat(value)
    if 'NUMERIC' in column_type or 'DECIMAL' in column_type:
        return Decimal(value)
    return value


def export_all_data():
    """
    把所有資料表的內容查出來，組成一份可以直接 json.dumps 的 dict。
    回傳的資料結構：{"version": 1, "exported_at": "...", "tables": {"engineers": [...], ...}}
    """
    from utils.timezone_utils import taipei_now

    result = {
        'version': BACKUP_FORMAT_VERSION,
        'exported_at': taipei_now().isoformat(),
        'tables': {},
    }
    for table_name, model in MODEL_ORDER:
        columns = [c.name for c in model.__table__.columns]
        rows = model.query.all()
        result['tables'][table_name] = [
            {col: _serialize_value(getattr(row, col)) for col in columns}
            for row in rows
        ]
    return result


def export_all_data_encrypted(encryption_key):
    """匯出所有資料並加密，回傳加密後的 bytes，供存成 .enc 檔案或直接下載"""
    data = export_all_data()
    json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
    fernet = Fernet(_derive_key(encryption_key))
    return fernet.encrypt(json_bytes)


def decrypt_and_parse(encrypted_bytes, encryption_key):
    """
    解密備份檔並解析成 dict，同時做基本的格式驗證。
    金鑰錯誤或內容不是合法的備份格式時會丟出例外，呼叫端請自行 try/except 處理。
    """
    fernet = Fernet(_derive_key(encryption_key))
    json_bytes = fernet.decrypt(encrypted_bytes)  # 金鑰錯誤或檔案損毀會在這裡丟出例外
    data = json.loads(json_bytes.decode('utf-8'))
    if 'tables' not in data or 'version' not in data:
        raise ValueError('檔案內容不是合法的系統備份格式')
    return data


def reset_all_data():
    """
    資料庫 RESET：清空所有資料表，回到全新安裝的初始狀態
    （只剩一個預設管理者帳號 A171 / admin123，以及系統運作所需的預設 Email 範本）。
    這是比「匯入還原」更徹底的操作——匯入還原至少會換成備份檔裡的資料，
    RESET 則是完全清空、不留任何業務資料，執行前呼叫端必須自行確認已經過
    嚴格的身份與意圖確認（見 routes/audit.py 的 reset_database 路由）。
    """
    for table_name, model in reversed(MODEL_ORDER):
        db.session.query(model).delete()
    db.session.commit()

    from seed_data import seed_demo_data
    seed_demo_data(verbose=False)


def import_all_data(data):
    """
    還原備份資料：會先清空「目前資料庫裡所有資料表」，再依照備份內容重新寫入。
    這是「完全覆蓋」，不是合併，執行前呼叫端必須自行確認使用者已經確認過這個風險。
    回傳每個資料表實際還原的筆數，供畫面顯示還原結果摘要。
    """
    tables_data = data.get('tables', {})

    # 清空順序：子資料表 → 父資料表（reversed），避免外鍵限制擋下刪除動作
    for table_name, model in reversed(MODEL_ORDER):
        db.session.query(model).delete()
    db.session.commit()

    # 寫入順序：父資料表 → 子資料表，且明確帶入原本的 id，確保彼此的關聯（外鍵）不會跑掉
    summary = {}
    pending_engineer_self_refs = []  # [(engineer_id, {'supervisor_id': ..., 'top_supervisor_id': ...}), ...]

    for table_name, model in MODEL_ORDER:
        rows = tables_data.get(table_name, [])
        columns = [c.name for c in model.__table__.columns]
        for row in rows:
            parsed_row = {col: _deserialize_value(model, col, row.get(col)) for col in columns}
            if model is Engineer:
                # 見檔案開頭 _ENGINEER_SELF_REF_COLUMNS 的說明：先把直屬主管／
                # 最高直屬主管兩個欄位記下來、清成 None 再寫入，避免主管的
                # 紀錄還沒寫入時，下屬那筆就先參照到它而被外鍵擋下來
                self_refs = {col: parsed_row.get(col) for col in _ENGINEER_SELF_REF_COLUMNS}
                if any(v is not None for v in self_refs.values()):
                    pending_engineer_self_refs.append((parsed_row['id'], self_refs))
                for col in _ENGINEER_SELF_REF_COLUMNS:
                    parsed_row[col] = None
            db.session.add(model(**parsed_row))
        db.session.commit()
        summary[table_name] = len(rows)

    # 第二階段：所有人員都已經寫入完成，現在回頭把直屬主管／最高直屬主管
    # 的關聯補上，這時候不管指向誰，對方一定都已經存在了
    for engineer_id, self_refs in pending_engineer_self_refs:
        engineer = db.session.get(Engineer, engineer_id)
        if engineer is None:
            continue
        for col, value in self_refs.items():
            setattr(engineer, col, value)
    if pending_engineer_self_refs:
        db.session.commit()

    _reset_postgres_sequences_if_needed()

    return summary


def _reset_postgres_sequences_if_needed():
    """
    還原時是明確指定原本的 id 寫入（保留關聯），這點不管資料庫種類為何都
    一樣。但 MySQL 的 AUTO_INCREMENT 只要插入比目前計數器大的明確 id，
    計數器會自動跟著往上跳；PostgreSQL 的序列（sequence）則完全不會——序列
    的計數狀態跟資料表裡實際的 id 是兩回事，插入明確 id 完全不會影響到它。

    如果還原目標是 PostgreSQL 卻不處理這件事，序列還是停在還原前的初始
    值，之後系統正常運作、新增資料（不指定 id、交給資料庫自動產生）時，
    產生出來的 id 遲早會撞上已經還原進來的既有資料，出現
    「duplicate key value violates unique constraint」這類錯誤——而且不是
    還原當下馬上發生，是還原完成、新資料一筆一筆累加一段時間之後才會出現，
    不容易在還原的當下就被發現。

    這裡在還原完成後，對每一張表的序列做一次校正：只在資料庫種類確實是
    PostgreSQL 時才執行（SQLite、MySQL 不需要，也沒有 pg_depend 這個系統
    目錄可查），把序列重設為該表目前實際的 MAX(id)，之後系統自動產生的
    下一個 id 就會正確接續在已還原資料的後面，不會再撞號。
    """
    if db.engine.dialect.name != 'postgresql':
        return
    from sqlalchemy import text
    db.session.execute(text("""
        DO $do$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN
                SELECT
                    t.relname AS table_name,
                    a.attname AS column_name,
                    s.relname AS seq_name
                FROM pg_class s
                JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
                JOIN pg_class t ON d.refobjid = t.oid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                WHERE s.relkind = 'S'
            LOOP
                EXECUTE format(
                    'SELECT setval(%L, COALESCE((SELECT MAX(%I) FROM %I), 1), (SELECT MAX(%I) FROM %I) IS NOT NULL)',
                    r.seq_name, r.column_name, r.table_name, r.column_name, r.table_name
                );
            END LOOP;
        END $do$;
    """))
    db.session.commit()
