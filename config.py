# -*- coding: utf-8 -*-
"""
config.py - 系統設定檔
從 .env 讀取環境變數，集中管理所有設定值
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    """Flask 應用程式設定類別"""

    # ---- 基本安全設定 ----
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-please-change-in-production')

    # ---- 公司／品牌設定 ----
    # 同一份程式碼要給多家公司各自部署時，用環境變數區分，不用改程式碼。
    # 沒設定時預設為 Mellow雲奧；部署給其他公司時用環境變數覆蓋即可。
    COMPANY_NAME = os.environ.get('COMPANY_NAME', 'Mellow雲奧')
    COMPANY_SHORT_NAME = os.environ.get('COMPANY_SHORT_NAME', 'Mellow雲奧')
    COMPANY_NAME_EN = os.environ.get('COMPANY_NAME_EN', '')
    # 批次匯入／管理者新增帳號時的預設密碼（建議每家公司都設成自己的，並要求員工登入後修改）
    DEFAULT_NEW_ACCOUNT_PASSWORD = os.environ.get('DEFAULT_NEW_ACCOUNT_PASSWORD', 'ChangeMe-2026!')
    # 新增人員表單預先帶入的 Email（留空表示不預填）
    DEFAULT_NEW_ACCOUNT_EMAIL = os.environ.get('DEFAULT_NEW_ACCOUNT_EMAIL', '')
    # 系統自動產生的暫時 Email 網域（匯入時對不到帳號的人員會用到）
    PLACEHOLDER_EMAIL_DOMAIN = os.environ.get('PLACEHOLDER_EMAIL_DOMAIN', 'example.local')
    # 入口網站是否顯示「打卡系統／請假系統」這類連到其他系統的卡片，以及打卡系統網址
    PORTAL_SHOW_OTHER_SYSTEMS = os.environ.get('PORTAL_SHOW_OTHER_SYSTEMS', 'False').strip().lower() != 'false'
    PORTAL_PUNCH_URL = os.environ.get('PORTAL_PUNCH_URL', '')
    # 初始化資料庫時，是否預先塞入模組B部門指標範例（RD／SD／GD）；預設不塞，由各公司自行建立
    SEED_DEFAULT_DEPT_INDICATORS = os.environ.get('SEED_DEFAULT_DEPT_INDICATORS', 'False').strip().lower() != 'false'

    # ---- 資料庫設定 ----
    # 預設使用 SQLite，未來可改為 MySQL / PostgreSQL，只需改這行連線字串即可
    _default_sqlite_uri = 'sqlite:///' + os.path.join(basedir, 'instance', 'weekly_report.db')
    _env_db_url = os.environ.get('DATABASE_URL', '').strip()

    if not _env_db_url:
        # 改用「拆開的環境變數」自動組合連線字串：DB_ENGINE + DB_USER + DB_PASSWORD +
        # DB_NAME + INSTANCE_CONNECTION_NAME。這個方式比手動組一整串連線字串更不容易出錯，
        # 因為密碼裡如果剛好有 #、@、/ 這類符號，之前都要使用者自己小心處理，
        # 這裡改用 urllib.parse.quote_plus() 自動幫密碼做正確的 URL 編碼，不會再因為
        # 密碼裡的特殊符號而導致連線字串解析錯誤。
        import urllib.parse
        _db_engine = os.environ.get('DB_ENGINE', '').strip()
        _db_user = os.environ.get('DB_USER', '').strip()
        # 密碼變數名稱同時接受 DB_PASSWORD 或 DB_PASS，避免因為 Cloud Run 主控台
        # 手動設定時打的名稱跟這裡預期的不完全一樣，導致明明設定了卻讀不到
        _db_password = os.environ.get('DB_PASSWORD', '').strip() or os.environ.get('DB_PASS', '').strip()
        _db_name = os.environ.get('DB_NAME', '').strip()
        _instance_connection_name = os.environ.get('INSTANCE_CONNECTION_NAME', '').strip()

        if _db_engine and _db_user and _db_password and _db_name and _instance_connection_name:
            # DB_ENGINE 可以填完整的 SQLAlchemy driver（例如 "mysql+pymysql"），
            # 也可以只填簡單的名稱（"mysql" 或 "postgresql"），這裡都能正確辨識
            _engine_lower = _db_engine.lower()
            if _engine_lower.startswith('postgres'):
                _driver = _db_engine if '+' in _db_engine else 'postgresql+psycopg2'
                _socket_param = 'host'
            else:
                _driver = _db_engine if '+' in _db_engine else 'mysql+pymysql'
                _socket_param = 'unix_socket'

            _encoded_password = urllib.parse.quote_plus(_db_password)
            _env_db_url = (
                f'{_driver}://{_db_user}:{_encoded_password}@/{_db_name}'
                f'?{_socket_param}=/cloudsql/{_instance_connection_name}'
            )

    if _env_db_url.startswith('sqlite:///') and not _env_db_url.startswith('sqlite:////'):
        # 將相對路徑的 SQLite URL 轉為絕對路徑，避免因執行目錄不同造成 "unable to open database file"
        _relative_path = _env_db_url.replace('sqlite:///', '', 1)
        if not os.path.isabs(_relative_path):
            _env_db_url = 'sqlite:///' + os.path.join(basedir, _relative_path)
    SQLALCHEMY_DATABASE_URI = _env_db_url or _default_sqlite_uri
    # 供程式其他地方判斷目前是否為 SQLite（例如：自訂備份功能只在 SQLite 時才有意義，
    # 改接 Cloud SQL 後應該用 Cloud SQL 自己的自動備份機制，而不是這裡的檔案型備份）
    IS_SQLITE = SQLALCHEMY_DATABASE_URI.startswith('sqlite')

    # SQLite 專用：設定連線逾時秒數，降低多個請求同時寫入時發生
    # "database is locked" 錯誤的機率（例如同時有人送出週報、排程備份、寫入操作紀錄等）
    if IS_SQLITE:
        SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 15}}
    else:
        # Cloud SQL（MySQL/PostgreSQL）專用：
        # - pool_pre_ping：每次要用連線前先偷偷送一個小查詢確認連線還活著，
        #   如果連線已經斷了會自動重新建立，避免「連線閒置太久被資料庫斷開」
        #   導致的 "MySQL server has gone away" 之類錯誤
        # - pool_recycle：連線最多重複使用 280 秒就強制汰換，比 Cloud SQL 預設
        #   閒置逾時（通常 600 秒起）短，確保連線在被資料庫端斷開前就先換新的
        # - pool_size / max_overflow：明確調小連線池上限，不用 SQLAlchemy 預設值
        #   （預設 pool_size=5、max_overflow=10，上限共 15 條）。這裡改成
        #   3 + 5 = 8 條，剛好對應 gunicorn 實際只開 1 個 worker、8 個 threads
        #   的設定（見 Dockerfile／Procfile）——就算 8 個 threads 同一瞬間都在
        #   跑需要查資料庫的請求，也剛好每個都拿得到連線，不會互相等待；
        #   同時把這個系統對資料庫的連線數上限，從原本最多 15 條降到最多 8 條，
        #   因為可能跟其他系統共用同一台資料庫執行個體，這裡少用一點，
        #   就多留一點餘裕給另外兩個系統
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True, 'pool_recycle': 280,
            'pool_size': 3, 'max_overflow': 5,
        }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Session 設定 ----
    # 一般登入：閒置這麼久沒動作就自動登出（滑動式逾時，每次請求都會重新
    # 計時，只要還在操作就不會被登出）。這是內部公司系統，不是對外的高
    # 安全性應用，預設拉長到 480 分鐘（8小時，對應一般上班時數），避免
    # 填寫日誌/週報這種可能會停留、輸入比較久的頁面，閒置一段時間後回來
    # 才發現剛才輸入的內容因為被登出而沒存到。
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=int(os.environ.get('SESSION_TIMEOUT_MINUTES', 480)))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    # 部署為 HTTPS 時，建議將以下設定改為 True
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False') == 'True'

    # 勾選「記住我」登入：改用效期長得多的獨立 cookie（預設 30 天），
    # 讓瀏覽器關掉、甚至過了 PERMANENT_SESSION_LIFETIME 那麼久沒動作，
    # 重新打開網站時還是能維持登入狀態，不用重新輸入帳密
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.environ.get('REMEMBER_COOKIE_DAYS', 30)))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False') == 'True'

    # ---- CSRF 設定 ----
    WTF_CSRF_TIME_LIMIT = None  # 不限制 CSRF token 過期時間（以 session 逾時為主）

    # ---- 上傳附件設定 ----
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 單次上傳最大 10MB

    # ---- 備份設定 ----
    BACKUP_FOLDER = os.path.join(basedir, 'backups')
    BACKUP_RETENTION_DAYS = int(os.environ.get('BACKUP_RETENTION_DAYS', 90))
    # 每月完整系統資料備份：實際生效的「是否啟用」「幾號」以「資料庫備份」頁面
    # 設定的值為主（存在 SystemSetting 裡），這裡只是系統從未設定過時的預設值。
    # 時間固定為台灣時間凌晨 03:00，可選 1~28 號（避免月底日期不存在的問題）。
    FULL_BACKUP_DEFAULT_DAY = int(os.environ.get('FULL_BACKUP_DEFAULT_DAY', 8))
    # 備份加密密碼（用於加密備份檔案）
    BACKUP_ENCRYPTION_KEY = os.environ.get('BACKUP_ENCRYPTION_KEY', 'please-change-this-backup-key-32bytes!')

    # ---- SMTP Email 設定 ----
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True') == 'True'
    MAIL_SENDER_NAME = os.environ.get('MAIL_SENDER_NAME', f'{COMPANY_NAME}週報系統')

    # ---- 週報提醒設定 ----
    # 每週提醒時間 (星期幾 0=星期一 ... 4=星期五, 時:分)
    REMINDER_WEEKDAY = int(os.environ.get('REMINDER_WEEKDAY', 4))  # 預設週五
    REMINDER_TIME = os.environ.get('REMINDER_TIME', '17:00')

    # ---- 卡關關鍵字偵測設定 ----
    STUCK_KEYWORDS = ['待回覆', '待安排', '待確認', '待客戶回覆', '待供應商', '待簽核', '待報價']
    STUCK_WEEKS_THRESHOLD = int(os.environ.get('STUCK_WEEKS_THRESHOLD', 2))  # 連續N週未更新視為風險

    # ---- 雲端部署（如 Google Cloud Run）相關設定 ----
    # Cloud Run 可能同時啟動多個容器實例，若每個實例都各自跑一份 APScheduler，
    # 會導致提醒信/備份被重複觸發多次。部署到這類環境時，建議將此設為 False，
    # 改用 Google Cloud Scheduler 定時呼叫下方的 /internal/trigger/* 端點觸發，
    # 只會有一次執行。本機或單一伺服器部署則維持 True 即可（預設值）。
    ENABLE_INTERNAL_SCHEDULER = os.environ.get('ENABLE_INTERNAL_SCHEDULER', 'True') == 'True'
    # 呼叫 /internal/trigger/* 端點時所需的密鑰（請務必更改，並只告知 Cloud Scheduler 使用）
    INTERNAL_TRIGGER_TOKEN = os.environ.get('INTERNAL_TRIGGER_TOKEN', 'please-change-this-token')

    # 是否在啟動時自動建立系統最小初始化資料（僅在資料庫完全空白時才會執行）。
    # 只會建立「一個可登入的管理者帳號」，不會建立任何假的工程師/專案/組別資料。
    # 預設為 True，方便部署後不需額外設定任何環境變數即可直接使用管理者帳號登入。
    # 正式上線、密碼已改過後，建議把這個環境變數設為 False，避免資料庫萬一被清空
    # 時又自動長出已知密碼的管理者帳號；設定方式：Cloud Run 服務環境變數加上
    # AUTO_SEED_DEMO_DATA=False，或本機 .env 檔案內設定。
    AUTO_SEED_DEMO_DATA = os.environ.get('AUTO_SEED_DEMO_DATA', 'True') == 'True'

