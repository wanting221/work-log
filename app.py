# -*- coding: utf-8 -*-
"""
app.py - Flask 應用程式進入點
工程師週報系統 (RD Weekly Report Portal)
執行方式: pip install -r requirements.txt && python app.py
"""
import os
from flask import Flask, render_template, request
from flask_login import current_user
from config import Config
from extensions import db, login_manager, csrf, bcrypt, compress, scheduler


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config['DB_STARTUP_ERROR'] = None

    # ---- 資料庫連線失敗時的保護機制（刻意放在最前面註冊）----
    # 這個 before_request 必須在 db.init_app() / login_manager.init_app() 之前註冊，
    # 這樣才能保證它在每個請求裡「最先」執行。原因：Flask-Login 自己也會註冊一個
    # before_request，每個請求都會嘗試從資料庫重新查詢目前登入的使用者
    # （current_user）；如果資料庫本身就連不上，且 Flask-Login 的檢查先執行，
    # 使用者一登入過的瀏覽器每次請求都會在這裡再次觸發連線例外，導致下面的
    # 錯誤頁面永遠沒有機會顯示、只會看到不知所云的 500 錯誤。
    # 這裡故意不用 render_template（不繼承 base.html），因為 base.html 也會
    # 存取 current_user，一樣有上述風險；改用完全獨立、不需要查詢任何資料的
    # 純 HTML 字串，才能保證不管使用者原本是什麼登入狀態都能正確顯示。
    @app.before_request
    def _check_db_startup_error():
        if app.config.get('DB_STARTUP_ERROR') and request.endpoint != 'static':
            from markupsafe import escape
            error_detail = escape(app.config['DB_STARTUP_ERROR'])
            html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<title>系統暫時無法使用</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Microsoft JhengHei", sans-serif;
          display: flex; align-items: center; justify-content: center; min-height: 100vh;
          margin: 0; background: #f5f5f7; }}
  .box {{ text-align: center; max-width: 560px; padding: 32px; }}
  .code {{ font-size: 3rem; font-weight: 700; color: #d70015; }}
  .msg {{ color: #6e6e73; margin-top: 8px; }}
  .detail {{ margin-top: 16px; padding: 12px; background: #fff; border-radius: 10px;
             font-size: 0.82rem; color: #d70015; word-break: break-all; text-align: left; }}
</style></head>
<body>
  <div class="box">
    <div class="code">503</div>
    <div class="msg">系統無法連接資料庫，請聯繫管理者檢查資料庫連線設定（帳密、Cloud SQL 連線設定、使用者權限等）。</div>
    <div class="detail">錯誤訊息：{error_detail}</div>
  </div>
</body></html>"""
            return html, 503

    # ---- 開機自我檢查：記錄目前實際連接的資料庫類型 ----
    # 這是刻意做在啟動流程最前面的，不管容器有沒有順利跑到後面的功能，
    # 只要程式啟動過，log 裡就一定會留下這筆紀錄，方便隨時去 Cloud Run
    # 的記錄檔確認「這次重啟後，系統連的到底是不是持久化的資料庫」。
    from utils.db_info import get_database_info, get_startup_diagnostics
    db_info = get_database_info(app.config['SQLALCHEMY_DATABASE_URI'], app.config['IS_SQLITE'])
    startup_diagnostics = get_startup_diagnostics()
    if db_info['is_sqlite']:
        if startup_diagnostics['env_var_present']:
            env_diag_line = f"容器實際讀到的 DATABASE_URL：{startup_diagnostics['env_var_masked']}（這個值本身還是指向 SQLite，請確認設定的值是否正確）"
        elif startup_diagnostics['using_separate_vars']:
            env_diag_line = (
                f"偵測到部分「拆開的資料庫環境變數」({', '.join(startup_diagnostics['present_separate_vars'])})，"
                f"但缺少：{', '.join(startup_diagnostics['missing_separate_vars'])}，必須五個變數都設定齊全才會生效"
            )
        else:
            env_diag_line = (
                "容器裡完全沒有 DATABASE_URL，也沒有拆開的資料庫環境變數"
                "（DB_ENGINE / DB_USER / DB_PASSWORD / DB_NAME / INSTANCE_CONNECTION_NAME）。"
                "代表 Cloud Run 服務設定沒有成功套用，或設定在了沒有服務流量的修訂版本上"
            )
        app.logger.warning(
            '=' * 60 + '\n'
            '⚠️  警告：目前使用 SQLite 檔案資料庫\n'
            '雲端容器環境下（如 Cloud Run），容器重啟/重新部署時，\n'
            '這個資料庫檔案會被清空，所有資料都會消失！\n'
            '如果這是正式環境，請盡快遷移到 Cloud SQL。\n'
            f'[診斷] {env_diag_line}\n'
            + '=' * 60
        )
    else:
        app.logger.info(f"[開機自我檢查] 目前連接的資料庫：{db_info['display_label']}")

    # 確保必要資料夾存在
    os.makedirs(os.path.join(app.root_path, 'instance'), exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['BACKUP_FOLDER'], exist_ok=True)

    # 靜態檔案（CSS/JS）快取 12 小時，減少每次換頁都重新下載相同檔案；
    # 樣板裡的 static_url() 會自動帶上檔案修改時間當版本號，之後只要更新過
    # CSS 檔案，使用者瀏覽器會立刻抓到新版本，不會被快取卡住看到舊樣式。
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 43200

    # ---- 初始化擴充套件 ----
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    bcrypt.init_app(app)

    # 啟用 gzip 壓縮回應內容：HTML/CSS/JS/JSON 這類文字內容通常可壓縮到 20~30%
    # 大小，對於前端頁面較大（例如 Dashboard、週報填寫頁）的情境，能明顯縮短
    # 使用者在網路狀況較差時的載入時間。
    app.config['COMPRESS_MIMETYPES'] = [
        'text/html', 'text/css', 'text/xml', 'application/json',
        'application/javascript', 'text/javascript',
    ]
    app.config['COMPRESS_LEVEL'] = 6
    app.config['COMPRESS_MIN_SIZE'] = 500
    compress.init_app(app)

    # SQLite 專用：啟用 WAL (Write-Ahead Logging) 模式，允許讀取與寫入同時進行，
    # 大幅降低多人同時使用系統時發生 "database is locked" 的機率
    if app.config['IS_SQLITE']:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, 'connect')
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            # WAL 模式搭配 synchronous=NORMAL 是 SQLite 官方建議的效能組合：
            # 預設的 synchronous=FULL 每次 commit 都會強制等待磁碟完整同步(fsync)，
            # 在 Cloud Run 這種網路磁碟/虛擬化儲存環境下，每次「儲存週報」都要
            # 付出額外的同步延遲；改成 NORMAL 後，WAL 模式本身已提供足夠的損毀保護，
            # 只在 checkpoint 時才強制同步，一般寫入操作可以明顯加快。
            cursor.execute('PRAGMA synchronous=NORMAL')
            # 鎖定逾時從 15 秒調降為 5 秒：WAL 模式下讀寫互不阻塞，真正需要
            # 等待的情境很少；萬一真的卡住，5 秒讓使用者更快看到明確的錯誤訊息，
            # 而不是靜靜卡住 15 秒才有反應，體感上會覺得「這系統是不是壞了」。
            cursor.execute('PRAGMA busy_timeout=5000')
            cursor.close()

    # ---- 確保資料表存在 ----
    # 重要：在 Cloud Run 等無狀態容器環境中，每次容器是全新的檔案系統，
    # 若忘記先執行 init_db.py 建表，登入時查詢 engineers 資料表會直接噴
    # "no such table" 例外並回傳 500。這裡在啟動時自動呼叫 db.create_all()
    # （已存在的資料表不會被覆蓋，是安全的重複執行）確保系統一定能開機使用。
    # 注意：models 模組必須先被 import，SQLAlchemy 才能知道有哪些資料表要建立，
    # 所以這段一定要放在 `from models import ...` 之後。
    from models import Engineer

    # 這裡刻意用 try/except 包起來：如果資料庫連線失敗（密碼錯誤、Cloud SQL
    # 連線設定有問題等），過去的寫法會讓整個應用程式在啟動階段就丟出例外，
    # 導致 gunicorn worker 完全無法啟動——Cloud Run 找不到任何一個「活著」的
    # 容器可以處理請求，使用者只會看到 Cloud Run 本身的通用 "Service Unavailable"
    # 頁面，看不出真正的原因，也沒有 log 以外的管道可以診斷。
    # 改成：連線失敗時記錄詳細錯誤、應用程式仍然正常啟動，並在每個請求前
    # 顯示清楚的錯誤頁面（見上方 before_request），讓問題可以直接從網頁上看到，
    # 不需要每次都去翻 Cloud Logging。
    with app.app_context():
        try:
            db.create_all()
            _run_schema_upgrades()
            _run_universal_schema_upgrades()
            # 僅在明確設定 AUTO_SEED_DEMO_DATA=True，且資料庫完全沒有任何帳號時，
            # 才會自動建立最小初始化資料（僅一個管理者帳號，不含任何假資料）。
            # 正式環境如果密碼已改過，建議設為 False，改用 python init_db.py 手動初始化。
            if app.config['AUTO_SEED_DEMO_DATA']:
                from seed_data import seed_demo_data
                seed_demo_data(verbose=False)
        except Exception as e:
            import traceback
            app.config['DB_STARTUP_ERROR'] = str(e)
            app.logger.error(
                '=' * 60 + '\n'
                '❌ 嚴重錯誤：啟動時無法連接資料庫或初始化資料表\n'
                f'錯誤訊息：{e}\n'
                '應用程式仍會啟動，但所有頁面都會顯示錯誤提示，直到這個問題修正為止。\n'
                '常見原因：資料庫帳密不正確、Cloud SQL 連線設定錯誤、\n'
                '資料庫使用者權限不足、網路/防火牆限制。\n'
                + traceback.format_exc() +
                '=' * 60
            )

    @login_manager.user_loader
    def load_user(user_id):
        return Engineer.query.get(int(user_id))

    # ---- 註冊 Blueprint ----
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.portal import portal_bp
    from routes.review_admin import review_admin_bp
    from routes.module_b_admin import module_b_admin_bp
    from routes.module_b_review import module_b_review_bp
    from routes.review_self import review_self_bp
    from routes.review_scoring import review_scoring_bp
    from routes.review_executive import review_executive_bp
    from routes.report import report_bp
    from routes.diary import diary_bp
    from routes.diary_view import diary_view_bp
    from routes.personal_project import personal_project_bp
    from routes.view_engineer import view_engineer_bp
    from routes.view_project import view_project_bp
    from routes.reports_view import reports_view_bp
    from routes.tracking import tracking_bp
    from routes.admin_project import admin_project_bp
    from routes.admin_engineer import admin_engineer_bp
    from routes.export import export_bp
    from routes.reports_export import reports_export_bp
    from routes.email_settings import email_settings_bp
    from routes.audit import audit_bp
    from routes.internal import internal_bp
    from routes.guidelines import guidelines_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(review_admin_bp)
    app.register_blueprint(module_b_admin_bp)
    app.register_blueprint(module_b_review_bp)
    app.register_blueprint(review_self_bp)
    app.register_blueprint(review_scoring_bp)
    app.register_blueprint(review_executive_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(diary_bp)
    app.register_blueprint(diary_view_bp)
    app.register_blueprint(personal_project_bp)
    app.register_blueprint(view_engineer_bp)
    app.register_blueprint(view_project_bp)
    app.register_blueprint(reports_view_bp)
    app.register_blueprint(tracking_bp)
    app.register_blueprint(admin_project_bp)
    app.register_blueprint(admin_engineer_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(reports_export_bp)
    app.register_blueprint(email_settings_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(guidelines_bp)
    # 內部觸發端點供 Cloud Scheduler 等外部服務以 API 方式呼叫，改用 Header 密鑰驗證，
    # 因此排除 CSRF 保護（CSRF 是防止瀏覽器端偽造請求，不適用於伺服器對伺服器的呼叫）
    csrf.exempt(internal_bp)

    # ---- 錯誤處理 ----
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('error.html', code=403, message='您沒有權限存取此頁面'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('error.html', code=404, message='找不到此頁面'), 404

    @app.errorhandler(401)
    def unauthorized(e):
        return render_template('error.html', code=401, message='請先登入'), 401

    @app.errorhandler(500)
    def internal_error(e):
        import traceback
        db.session.rollback()
        # 完整記錄錯誤堆疊 (traceback)，方便從終端機/log檔案找出真正出錯的程式碼位置
        app.logger.error('Internal Server Error:\n' + traceback.format_exc())
        return render_template('error.html', code=500, message='系統發生內部錯誤，請稍後再試或聯繫管理者'), 500

    # ---- 樣板可用的全域變數 ----
    from utils.report_utils import current_year_week, format_week_range

    def static_url(filename):
        """
        產生帶檔案修改時間版本號的靜態檔案網址，例如 /static/css/style.css?v=1234567890。
        搭配上面設定的 12 小時瀏覽器快取：檔案沒改版本號就不變、瀏覽器直接用快取，
        換頁不用重新下載；一旦這次部署裡檔案真的改了，版本號跟著變，瀏覽器會抓新檔案，
        不會被快取卡住看到舊版樣式。
        """
        try:
            file_path = os.path.join(app.static_folder, filename)
            version = int(os.path.getmtime(file_path))
        except OSError:
            version = 0
        from flask import url_for
        return f"{url_for('static', filename=filename)}?v={version}"

    @app.context_processor
    def inject_globals():
        year, week = current_year_week()
        # 側邊欄「待評分」「待覆評」連結是否顯示，不是看角色，而是看「有沒有人
        # 的直屬主管/最高直屬主管欄位指定了我」——只查 exists，不撈實際資料，
        # 搭配 ix_engineers_supervisor_id / ix_engineers_top_supervisor_id 這兩個
        # 索引，即使人數很多也是很輕量的查詢。
        has_pending_scoring = False
        has_pending_executive_review = False
        if current_user.is_authenticated:
            from models import Engineer as _Engineer
            has_pending_scoring = db.session.query(
                _Engineer.query.filter_by(supervisor_id=current_user.id).exists()
            ).scalar()
            has_pending_executive_review = db.session.query(
                _Engineer.query.filter_by(top_supervisor_id=current_user.id).exists()
            ).scalar()

        # 模組B相關的後台管理頁面（部門硬指標庫、權重設定、目標設定審核、
        # 校準報表），只有在「至少建立過一個模組B的考核期間」時才會出現在
        # 側邊欄——公司還沒開始用模組B之前，這些管理頁面沒有意義，不需要
        # 佔用側邊欄版面造成困惑。
        from models import ReviewPeriod as _ReviewPeriod
        has_module_b_period = db.session.query(
            _ReviewPeriod.query.filter_by(module='B').exists()
        ).scalar()
        has_module_a_period = db.session.query(
            _ReviewPeriod.query.filter_by(module='A').exists()
        ).scalar()

        return {
            'cur_year': year, 'cur_week': week,
            'format_week_range': format_week_range,
            'static_url': static_url,
            'db_info': db_info,
            'startup_diagnostics': startup_diagnostics,
            'has_pending_scoring': has_pending_scoring,
            'has_pending_executive_review': has_pending_executive_review,
            'has_module_b_period': has_module_b_period,
            'has_module_a_period': has_module_a_period,
            # 公司／品牌（見 config.py）
            'company_name': app.config['COMPANY_NAME'],
            'company_short_name': app.config['COMPANY_SHORT_NAME'],
            'company_name_en': app.config['COMPANY_NAME_EN'],
            'brand_line': (app.config['COMPANY_NAME_EN'] + ' ' + app.config['COMPANY_NAME']).strip(),
            'default_new_password': app.config['DEFAULT_NEW_ACCOUNT_PASSWORD'],
            'default_new_email': app.config['DEFAULT_NEW_ACCOUNT_EMAIL'],
            'portal_show_other_systems': app.config['PORTAL_SHOW_OTHER_SYSTEMS'],
            'portal_punch_url': app.config['PORTAL_PUNCH_URL'],
        }

    # ---- 排程工作（提醒信 / 每日備份） ----
    # 雲端多實例部署（如 Cloud Run）時，若 ENABLE_INTERNAL_SCHEDULER=False，
    # 則不啟動內建 APScheduler，改由 Google Cloud Scheduler 呼叫 /internal/trigger/* 端點觸發，
    # 避免多個容器實例各自排程造成提醒信/備份重複執行。
    #
    # 這裡也要用 try/except 保護：_register_scheduled_jobs() 會查詢 SystemSetting
    # 資料表（讀取自動備份設定），如果資料庫本身就連不上（上面 DB_STARTUP_ERROR
    # 已經記錄），這裡一定也會失敗；如果沒有這層保護，同樣會讓整個應用程式在
    # 啟動階段崩潰、gunicorn 完全無法啟動。資料庫已知有問題時直接跳過排程註冊即可，
    # 等資料庫連線修好、容器重新啟動後就會恢復正常。
    if app.config['ENABLE_INTERNAL_SCHEDULER'] and not app.config['DB_STARTUP_ERROR']:
        try:
            with app.app_context():
                _register_scheduled_jobs(app)
                if not scheduler.running:
                    scheduler.start()
        except Exception as e:
            app.logger.error(f'❌ 排程工作註冊失敗（不影響網站本身啟動）：{e}')

    return app


def _run_schema_upgrades():
    """
    輕量級資料庫升級：檢查既有資料表是否缺少新版程式新增的欄位/索引，若缺少則自動補上。
    這裡沒有使用 Flask-Migrate 等完整遷移工具，只針對本專案已知會新增的變動做
    "ALTER TABLE ADD COLUMN" / "CREATE INDEX"，適合小型專案；已存在時會直接跳過，
    可安全重複執行，執行一次大約 < 0.1 秒，不影響開機時間。

    注意：這裡用的 SQL 語法（例如 CREATE INDEX IF NOT EXISTS）是針對 SQLite 寫的，
    只在既有 SQLite 資料庫需要「從舊版程式升級到新版程式」時才需要執行。
    如果是全新的 Cloud SQL（MySQL/PostgreSQL）資料庫，db.create_all() 已經會
    直接依照目前 models.py 建出包含所有欄位/索引的完整結構，不需要這些補丁，
    這裡直接跳過，避免不同資料庫語法不相容導致啟動失敗。
    """
    from flask import current_app
    if not current_app.config.get('IS_SQLITE', True):
        return

    from sqlalchemy import text, inspect

    inspector = inspect(db.engine)
    if 'projects' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('projects')}
        if 'color' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN color VARCHAR(20) DEFAULT 'primary'"))
                conn.commit()
        if 'sort_order' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.commit()
            _initialize_sort_order('projects', order_by_sql='code')
        if 'department' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN department VARCHAR(50)"))
                conn.commit()

    if 'groups' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('groups')}
        if 'department' not in existing_columns:
            with db.engine.connect() as conn:
                # 注意：'groups' 是 MySQL 8.0+ 的保留字（GROUPS 用在 window function 的
                # frame 語法裡），沒有加引號直接寫「ALTER TABLE groups ...」在 MySQL 上
                # 會噴出 SQL 語法錯誤（SQLite 不會，所以本機測試不會發現）。用
                # identifier_preparer.quote() 讓 SQLAlchemy 依照實際連線的資料庫方言
                # 自動加上正確的引號（MySQL 用反引號、PostgreSQL 用雙引號、SQLite 不需要）。
                quoted_table = db.engine.dialect.identifier_preparer.quote('groups')
                conn.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN department VARCHAR(50)"))
                conn.commit()
        if 'sort_order' not in existing_columns:
            with db.engine.connect() as conn:
                # 同樣要注意 'groups' 是 MySQL 8.0+ 保留字，用 identifier_preparer.quote()
                # 依照實際連線的資料庫方言自動加上正確的引號
                quoted_table = db.engine.dialect.identifier_preparer.quote('groups')
                conn.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.commit()
            _initialize_sort_order('groups', order_by_sql='name')

    if 'engineers' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('engineers')}
        if 'sort_order' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.commit()
            _initialize_sort_order('engineers', order_by_sql='name')
        if 'nickname' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN nickname VARCHAR(50)"))
                conn.commit()
        if 'department' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN department VARCHAR(50)"))
                conn.commit()
        if 'hire_date' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN hire_date DATE"))
                conn.commit()
        if 'supervisor_id' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN supervisor_id INTEGER"))
                conn.commit()
        if 'top_supervisor_id' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN top_supervisor_id INTEGER"))
                conn.commit()
        if 'weekly_report_enabled' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN weekly_report_enabled BOOLEAN DEFAULT 1"))
                conn.commit()
        if 'diary_enabled' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN diary_enabled BOOLEAN DEFAULT 1"))
                conn.commit()
        if 'client_project_enabled' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE engineers ADD COLUMN client_project_enabled BOOLEAN DEFAULT 1"))
                conn.commit()

    if 'personal_projects' in inspector.get_table_names():
        # 「我的客戶/專案」的「專案」欄位原本是自由輸入的文字（project_name），
        # 改成參照公司正式的 Project 資料表（project_id），跟「填寫週報」頁面
        # 的專案選單用同一份資料。舊資料裡的 project_name 文字沒辦法自動對應到
        # 正式的專案，這裡只能先把 project_id 留空，麻煩使用者之後自己補選。
        existing_columns = {col['name'] for col in inspector.get_columns('personal_projects')}
        if 'project_id' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE personal_projects ADD COLUMN project_id INTEGER"))
                conn.commit()
        if 'project_name' in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE personal_projects DROP COLUMN project_name"))
                conn.commit()
        if 'start_date' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE personal_projects ADD COLUMN start_date DATE"))
                conn.commit()
        if 'time_info' in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE personal_projects DROP COLUMN time_info"))
                conn.commit()
        if 'progress_update' in existing_columns:
            # 「進度更新」不再是手動填寫、持久保存的欄位，改成每次開啟頁面時
            # 即時彙整當週週報+當週日誌算出來，資料庫不需要再存這個欄位
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE personal_projects DROP COLUMN progress_update"))
                conn.commit()

    if 'daily_logs' in inspector.get_table_names():
        # 「填寫日誌」的「客戶」欄位原本是自由輸入的文字（client_name），
        # 改成參照公司正式的 Project 資料表（project_id），跟「填寫週報」頁面
        # 的專案選單用同一份資料。舊資料裡的 client_name 文字沒辦法自動對應到
        # 正式的專案，這裡只能先把 project_id 留空，麻煩使用者之後自己補選。
        existing_columns = {col['name'] for col in inspector.get_columns('daily_logs')}
        if 'project_id' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE daily_logs ADD COLUMN project_id INTEGER"))
                conn.commit()
        if 'client_name' in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE daily_logs DROP COLUMN client_name"))
                conn.commit()

    if 'review_periods' in inspector.get_table_names():
        # 考核系統「模組B」（部門硬指標庫 + BARS 軟指標）上線，考核期間需要
        # 一個 module 欄位來判斷這個期間走模組A還是模組B，舊資料一律預設A
        # （沿用原本的固定考評項目流程，不影響既有資料）。
        existing_columns = {col['name'] for col in inspector.get_columns('review_periods')}
        if 'module' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE review_periods ADD COLUMN module VARCHAR(1) NOT NULL DEFAULT 'A'"))
                conn.commit()

    if 'report_notes' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('report_notes')}
        if 'status' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE report_notes ADD COLUMN status VARCHAR(20) DEFAULT '待討論'"))
                conn.commit()

    if 'criteria_scores' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('criteria_scores')}
        # 早期版本欄位叫 score（只有主管單階段評分），這裡改成 supervisor_score
        # 並新增 executive_score，支援「直屬主管評分 + 最高直屬主管覆評」兩階段流程
        if 'score' in existing_columns and 'supervisor_score' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE criteria_scores RENAME COLUMN score TO supervisor_score"))
                conn.commit()
            existing_columns.discard('score')
            existing_columns.add('supervisor_score')
        if 'executive_score' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE criteria_scores ADD COLUMN executive_score NUMERIC(5, 2)"))
                conn.commit()

    if 'review_forms' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('review_forms')}
        if 'executive_signed_at' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE review_forms ADD COLUMN executive_signed_at DATETIME"))
                conn.commit()

    if 'review_periods' in inspector.get_table_names():
        existing_columns = {col['name'] for col in inspector.get_columns('review_periods')}
        if 'forms_generated_at' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE review_periods ADD COLUMN forms_generated_at DATETIME"))
                conn.commit()

    # ---- 效能優化索引 ----
    # SQLite 不會自動幫外鍵欄位建索引，先前的版本沒有這些索引，導致幾乎每個
    # 頁面內的 JOIN／篩選查詢都變成全表掃描，資料量一多就會明顯變慢。
    # 這裡針對既有資料庫「補建」索引；全新資料庫則會由 db.create_all() 直接建立，
    # 這裡的檢查會發現索引已存在而跳過，不會重複建立或報錯。
    existing_tables = set(inspector.get_table_names())
    with db.engine.connect() as conn:
        for index_name, table_name, columns in PERFORMANCE_INDEX_DEFINITIONS:
            if table_name not in existing_tables:
                continue
            existing_indexes = {ix['name'] for ix in inspector.get_indexes(table_name)}
            if index_name not in existing_indexes:
                cols_sql = ', '.join(columns)
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({cols_sql})'))
        conn.commit()


# 效能相關索引清單：SQLite（_run_schema_upgrades）與 Cloud SQL（MySQL/PostgreSQL，
# _upgrade_add_missing_indexes）兩條既有資料庫升級路徑共用同一份清單，確保兩邊
# 不會因為各自維護一份清單而慢慢不同步。
PERFORMANCE_INDEX_DEFINITIONS = [
    ('ix_engineers_group_id', 'engineers', ['group_id']),
    ('ix_engineers_status', 'engineers', ['status']),
    ('ix_projects_status', 'projects', ['status']),
    ('ix_projects_owner_engineer_id', 'projects', ['owner_engineer_id']),
    ('ix_weekly_reports_year_week', 'weekly_reports', ['year', 'week_number']),
    ('ix_weekly_report_items_report_id', 'weekly_report_items', ['report_id']),
    ('ix_weekly_report_items_project_id', 'weekly_report_items', ['project_id']),
    ('ix_weekly_report_items_chain_id', 'weekly_report_items', ['chain_id']),
    ('ix_attachments_item_id', 'attachments', ['item_id']),
    ('ix_audit_logs_user_id', 'audit_logs', ['user_id']),
    ('ix_audit_logs_action', 'audit_logs', ['action']),
    ('ix_audit_logs_timestamp', 'audit_logs', ['timestamp']),
    ('ix_report_notes_status', 'report_notes', ['status']),
    ('ix_report_notes_report_id', 'report_notes', ['report_id']),
    ('ix_report_notes_created_at', 'report_notes', ['created_at']),
    ('ix_projects_sort_order', 'projects', ['sort_order']),
    ('ix_engineers_sort_order', 'engineers', ['sort_order']),
    ('ix_groups_sort_order', 'groups', ['sort_order']),
    ('ix_engineers_supervisor_id', 'engineers', ['supervisor_id']),
    ('ix_engineers_top_supervisor_id', 'engineers', ['top_supervisor_id']),
    ('ix_engineers_department', 'engineers', ['department']),
    ('ix_projects_department', 'projects', ['department']),
    ('ix_groups_department', 'groups', ['department']),
]


def _run_universal_schema_upgrades():
    """
    跟 _run_schema_upgrades() 不同：這裡的升級邏輯「不限 SQLite」，SQLite/
    Cloud SQL（MySQL/PostgreSQL）都會執行，用來處理既有資料庫需要補齊的欄位
    型別調整或新欄位。

    重要：下面每一項都寫成獨立的函式呼叫，而不是寫在同一個函式主體裡用
    return 提早結束——因為如果寫在同一個函式裡，前面一項如果符合「已經
    處理過，不需要再做」而提早 return，會連帶跳過後面其他項目，導致後面
    新加的遷移項目永遠不會執行到（這是實際發生過的 bug：新增 department
    欄位的邏輯，因為寫在 system_settings.value 那段的後面，一旦
    system_settings.value 已經是 TEXT（例如先前部署已經修過），就會被
    提早 return 卡住，department 永遠補不上）。
    """
    _upgrade_system_settings_value_to_text()
    _upgrade_engineers_add_department_column()
    _upgrade_engineers_add_supervisor_columns()
    _upgrade_projects_add_department_column()
    _upgrade_groups_add_department_column()
    _upgrade_groups_add_sort_order_column()
    _upgrade_review_forms_add_executive_columns()
    _upgrade_personal_projects_project_id_column()
    _upgrade_daily_logs_project_id_column()
    _upgrade_review_periods_add_module_column()
    _upgrade_add_missing_indexes()


def _upgrade_system_settings_value_to_text():
    """
    把 system_settings.value 欄位從舊版的 VARCHAR(500) 放寬成不限長度的 TEXT。
    背景：這個欄位原本設定長度上限 500 字，「週報填寫原則」這類內容很容易
    超過這個長度。SQLite 不會嚴格檢查 VARCHAR 長度，所以本機測試不會發現問題，
    但接上 Cloud SQL 後，MySQL 會嚴格擋下超長內容、噴出 500 錯誤。
    這裡只處理「既有資料庫」的情況：全新資料庫由 db.create_all() 建立時，
    已經會直接套用 models.py 最新的 Text 型別，不需要這段修正。
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if 'system_settings' not in inspector.get_table_names():
        return

    columns = inspector.get_columns('system_settings')
    value_col = next((c for c in columns if c['name'] == 'value'), None)
    if not value_col:
        return

    # 只有型別還是舊的、有長度限制的字串型態時才需要改；已經是 TEXT 就跳過
    type_str = str(value_col['type']).upper()
    if 'TEXT' in type_str:
        return

    with db.engine.connect() as conn:
        if db.engine.dialect.name == 'sqlite':
            # SQLite 本身不會強制檢查 VARCHAR 長度，所以其實不修改也不會出錯，
            # 但仍然更新一下欄位型別描述，讓資料庫結構跟 models.py 保持一致
            conn.execute(text('ALTER TABLE system_settings ADD COLUMN value_new TEXT'))
            conn.execute(text('UPDATE system_settings SET value_new = value'))
            conn.execute(text('ALTER TABLE system_settings DROP COLUMN value'))
            conn.execute(text('ALTER TABLE system_settings RENAME COLUMN value_new TO value'))
        else:
            # MySQL / PostgreSQL 都支援直接 MODIFY/ALTER 欄位型別成 TEXT
            try:
                conn.execute(text('ALTER TABLE system_settings MODIFY COLUMN value TEXT'))
            except Exception:
                # PostgreSQL 語法跟 MySQL 不同，MODIFY 失敗的話改用 PostgreSQL 語法再試一次
                conn.execute(text('ALTER TABLE system_settings ALTER COLUMN value TYPE TEXT'))
        conn.commit()


def _upgrade_engineers_add_department_column():
    """
    補上 engineers.department 欄位（新增的「部門」屬性，位階在組別之上）。
    SQLite 版的 _run_schema_upgrades() 已經有處理，這裡專門補給既有的
    Cloud SQL 資料庫（MySQL/PostgreSQL）：新資料庫由 db.create_all() 建立時
    已經包含這個欄位，不需要這段；只有「升級前就已經存在」的資料表才需要
    額外補上。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'engineers' not in inspector.get_table_names():
        return

    engineer_columns = {c['name'] for c in inspector.get_columns('engineers')}
    if 'department' in engineer_columns:
        return

    with db.engine.connect() as conn:
        conn.execute(text('ALTER TABLE engineers ADD COLUMN department VARCHAR(50)'))
        conn.commit()


def _upgrade_engineers_add_supervisor_columns():
    """
    補上 engineers.hire_date / supervisor_id / top_supervisor_id 欄位
    （到職日期、直屬主管、最高直屬主管）。SQLite 版的 _run_schema_upgrades()
    已經有處理，這裡專門補給既有的 Cloud SQL 資料庫（MySQL/PostgreSQL）。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'engineers' not in inspector.get_table_names():
        return

    engineer_columns = {c['name'] for c in inspector.get_columns('engineers')}
    with db.engine.connect() as conn:
        if 'hire_date' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN hire_date DATE'))
            conn.commit()
        if 'supervisor_id' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN supervisor_id INTEGER'))
            conn.commit()
        if 'top_supervisor_id' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN top_supervisor_id INTEGER'))
            conn.commit()
        if 'weekly_report_enabled' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN weekly_report_enabled BOOLEAN DEFAULT TRUE'))
            conn.commit()
        if 'diary_enabled' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN diary_enabled BOOLEAN DEFAULT TRUE'))
            conn.commit()
        if 'client_project_enabled' not in engineer_columns:
            conn.execute(text('ALTER TABLE engineers ADD COLUMN client_project_enabled BOOLEAN DEFAULT TRUE'))
            conn.commit()


def _upgrade_projects_add_department_column():
    """
    補上 projects.department 欄位（專案歸屬部門，只有該部門的部門主管看得到）。
    SQLite 版的 _run_schema_upgrades() 已經有處理，這裡專門補給既有的
    Cloud SQL 資料庫（MySQL/PostgreSQL）：新資料庫由 db.create_all() 建立時
    已經包含這個欄位，不需要這段；只有「升級前就已經存在」的資料表才需要
    額外補上。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'projects' not in inspector.get_table_names():
        return

    project_columns = {c['name'] for c in inspector.get_columns('projects')}
    if 'department' in project_columns:
        return

    with db.engine.connect() as conn:
        conn.execute(text('ALTER TABLE projects ADD COLUMN department VARCHAR(50)'))
        conn.commit()


def _upgrade_groups_add_department_column():
    """
    補上 groups.department 欄位（組別歸屬部門，部門主管只看得到自己部門的組別）。
    SQLite 版的 _run_schema_upgrades() 已經有處理，這裡專門補給既有的
    Cloud SQL 資料庫（MySQL/PostgreSQL）：新資料庫由 db.create_all() 建立時
    已經包含這個欄位，不需要這段；只有「升級前就已經存在」的資料表才需要
    額外補上。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'groups' not in inspector.get_table_names():
        return

    group_columns = {c['name'] for c in inspector.get_columns('groups')}
    if 'department' in group_columns:
        return

    with db.engine.connect() as conn:
        # 注意：'groups' 是 MySQL 8.0+ 的保留字（GROUPS 用在 window function 的
        # frame 語法裡），沒有加引號直接寫「ALTER TABLE groups ...」在 MySQL 上
        # 會噴出 SQL 語法錯誤（這正是造成 Cloud Run 上 503 錯誤的原因——SQLite
        # 不會這樣，所以本機測試時完全沒發現）。用 identifier_preparer.quote()
        # 讓 SQLAlchemy 依照實際連線的資料庫方言自動加上正確的引號。
        quoted_table = db.engine.dialect.identifier_preparer.quote('groups')
        conn.execute(text(f'ALTER TABLE {quoted_table} ADD COLUMN department VARCHAR(50)'))
        conn.commit()


def _upgrade_groups_add_sort_order_column():
    """
    補上 groups.sort_order 欄位（組別清單拖曳排序用）。SQLite 版的
    _run_schema_upgrades() 已經有處理，這裡專門補給既有的 Cloud SQL 資料庫
    （MySQL/PostgreSQL）。

    這裡刻意不重用 _initialize_sort_order() 這個共用函式——那個函式組 SQL
    字串時沒有幫資料表名稱加上引號，只適合 SQLite 專用的升級路徑（'groups'
    在 SQLite 不是保留字，不需要引號也不會出錯）。這裡是給 MySQL/PostgreSQL
    用的路徑，所有語句都要用 identifier_preparer.quote() 處理過的表名，
    避免重蹈 'groups' 保留字造成語法錯誤的覆轍。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'groups' not in inspector.get_table_names():
        return

    group_columns = {c['name'] for c in inspector.get_columns('groups')}
    quoted_table = db.engine.dialect.identifier_preparer.quote('groups')

    if 'sort_order' not in group_columns:
        with db.engine.connect() as conn:
            conn.execute(text(f'ALTER TABLE {quoted_table} ADD COLUMN sort_order INTEGER DEFAULT 0'))
            conn.commit()

        # 幫既有組別依名稱排序，補上初始的 sort_order 值 0, 1, 2...，
        # 讓剛升級完的既有資料庫一開啟拖曳排序功能，看到的順序跟升級前一致
        with db.engine.connect() as conn:
            rows = conn.execute(text(f'SELECT id FROM {quoted_table} ORDER BY name')).fetchall()
            for idx, row in enumerate(rows):
                conn.execute(text(f'UPDATE {quoted_table} SET sort_order = :idx WHERE id = :id'),
                             {'idx': idx, 'id': row[0]})
            conn.commit()


def _upgrade_review_forms_add_executive_columns():
    """
    補上 criteria_scores.executive_score（最高直屬主管覆評分數）、
    review_forms.executive_signed_at（覆評完成時間），並把 criteria_scores
    早期版本的 score 欄位改名為 supervisor_score，支援「直屬主管評分 +
    最高直屬主管覆評」兩階段流程。SQLite 版的 _run_schema_upgrades() 已經
    有處理，這裡專門補給既有的 Cloud SQL 資料庫（MySQL/PostgreSQL）。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)

    if 'criteria_scores' in inspector.get_table_names():
        cs_columns = {c['name'] for c in inspector.get_columns('criteria_scores')}
        quoted_cs = db.engine.dialect.identifier_preparer.quote('criteria_scores')
        with db.engine.connect() as conn:
            if 'score' in cs_columns and 'supervisor_score' not in cs_columns:
                conn.execute(text(f'ALTER TABLE {quoted_cs} RENAME COLUMN score TO supervisor_score'))
                conn.commit()
                cs_columns.discard('score')
                cs_columns.add('supervisor_score')
            if 'executive_score' not in cs_columns:
                conn.execute(text(f'ALTER TABLE {quoted_cs} ADD COLUMN executive_score NUMERIC(5, 2)'))
                conn.commit()

    if 'review_forms' in inspector.get_table_names():
        rf_columns = {c['name'] for c in inspector.get_columns('review_forms')}
        if 'executive_signed_at' not in rf_columns:
            quoted_rf = db.engine.dialect.identifier_preparer.quote('review_forms')
            with db.engine.connect() as conn:
                conn.execute(text(f'ALTER TABLE {quoted_rf} ADD COLUMN executive_signed_at DATETIME'))
                conn.commit()

    if 'review_periods' in inspector.get_table_names():
        rp_columns = {c['name'] for c in inspector.get_columns('review_periods')}
        if 'forms_generated_at' not in rp_columns:
            quoted_rp = db.engine.dialect.identifier_preparer.quote('review_periods')
            with db.engine.connect() as conn:
                conn.execute(text(f'ALTER TABLE {quoted_rp} ADD COLUMN forms_generated_at DATETIME'))
                conn.commit()


def _upgrade_personal_projects_project_id_column():
    """
    補上 personal_projects.project_id 欄位（改參照公司正式的 Project 資料表，
    取代原本自由輸入文字的 project_name）。SQLite 版的 _run_schema_upgrades()
    已經有處理，這裡專門補給既有的 Cloud SQL 資料庫（MySQL/PostgreSQL）。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    if 'personal_projects' not in inspector.get_table_names():
        return

    existing_columns = {c['name'] for c in inspector.get_columns('personal_projects')}
    with db.engine.connect() as conn:
        if 'project_id' not in existing_columns:
            conn.execute(text('ALTER TABLE personal_projects ADD COLUMN project_id INTEGER'))
            conn.commit()
        if 'project_name' in existing_columns:
            conn.execute(text('ALTER TABLE personal_projects DROP COLUMN project_name'))
            conn.commit()
        if 'start_date' not in existing_columns:
            conn.execute(text('ALTER TABLE personal_projects ADD COLUMN start_date DATE'))
            conn.commit()
        if 'time_info' in existing_columns:
            conn.execute(text('ALTER TABLE personal_projects DROP COLUMN time_info'))
            conn.commit()
        if 'progress_update' in existing_columns:
            conn.execute(text('ALTER TABLE personal_projects DROP COLUMN progress_update'))
            conn.commit()


def _upgrade_daily_logs_project_id_column():
    """
    補上 daily_logs.project_id 欄位（改參照公司正式的 Project 資料表，
    取代原本自由輸入文字的 client_name）。SQLite 版的 _run_schema_upgrades()
    已經有處理，這裡專門補給既有的 Cloud SQL 資料庫（MySQL/PostgreSQL）。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return

    inspector = inspect(db.engine)
    if 'daily_logs' not in inspector.get_table_names():
        return

    existing_columns = {c['name'] for c in inspector.get_columns('daily_logs')}
    with db.engine.connect() as conn:
        if 'project_id' not in existing_columns:
            conn.execute(text('ALTER TABLE daily_logs ADD COLUMN project_id INTEGER'))
            conn.commit()
        if 'client_name' in existing_columns:
            conn.execute(text('ALTER TABLE daily_logs DROP COLUMN client_name'))
            conn.commit()


def _upgrade_review_periods_add_module_column():
    """
    補上 review_periods.module 欄位（考核系統模組A/B切換）。SQLite 版的
    _run_schema_upgrades() 已經有處理，這裡專門補給既有的 Cloud SQL 資料庫
    （MySQL/PostgreSQL）。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return

    inspector = inspect(db.engine)
    if 'review_periods' not in inspector.get_table_names():
        return

    existing_columns = {c['name'] for c in inspector.get_columns('review_periods')}
    if 'module' not in existing_columns:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE review_periods ADD COLUMN module VARCHAR(1) NOT NULL DEFAULT 'A'"))
            conn.commit()


def _upgrade_add_missing_indexes():
    """
    補上既有 Cloud SQL（MySQL/PostgreSQL）資料庫缺少的效能索引。

    背景：原本的索引補建機制（PERFORMANCE_INDEX_DEFINITIONS 那份清單）只寫在
    SQLite 專用的 _run_schema_upgrades() 裡，從來沒有給 Cloud SQL 用過——也就是說，
    如果 Cloud SQL 資料庫是在某些索引被加進 models.py「之前」就建立的，這些索引
    會永遠沒有機會補上去，多年下來會讓部門篩選、狀態篩選這類查詢在資料量變大時
    明顯變慢，卻又不容易察覺（不會報錯，只是查詢越來越慢）。

    這裡刻意不用 SQLite 那邊的「CREATE INDEX IF NOT EXISTS」語法：標準 MySQL
    （不同於 MariaDB／PostgreSQL／SQLite）並不支援 CREATE INDEX 的
    IF NOT EXISTS 子句，寫了會直接噴 SQL 語法錯誤。改成用 Python 端先檢查索引
    是否已存在（inspector.get_indexes），確認真的不存在才執行不帶 IF NOT EXISTS
    的純 CREATE INDEX 語句，這樣在 MySQL / PostgreSQL / SQLite 都能正確運作。

    另外，資料表名稱一律用 identifier_preparer.quote() 處理過——'groups' 這種
    在 MySQL 8.0+ 是保留字的資料表名稱，沒加引號會噴語法錯誤（就是先前那次
    503 錯誤的原因），這裡直接比照辦理，避免同一類問題在索引補建這裡重演。
    """
    from sqlalchemy import inspect, text
    if db.engine.dialect.name == 'sqlite':
        return  # SQLite 由 _run_schema_upgrades() 處理，這裡不用重複做

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    preparer = db.engine.dialect.identifier_preparer

    with db.engine.connect() as conn:
        for index_name, table_name, columns in PERFORMANCE_INDEX_DEFINITIONS:
            if table_name not in existing_tables:
                continue
            existing_indexes = {ix['name'] for ix in inspector.get_indexes(table_name)}
            if index_name in existing_indexes:
                continue
            quoted_table = preparer.quote(table_name)
            quoted_cols = ', '.join(preparer.quote(c) for c in columns)
            conn.execute(text(f'CREATE INDEX {index_name} ON {quoted_table} ({quoted_cols})'))
        conn.commit()


def _initialize_sort_order(table_name, order_by_sql):
    """
    幫既有資料庫的資料表補上 sort_order 初始值：依原本的排序方式（例如姓名或代號）
    給每一列依序編上 0, 1, 2...，讓剛升級完的既有資料庫一開啟拖曳排序功能，
    看到的順序跟升級前的顯示順序一致，而不是全部變成同一個值（0）而順序不確定。
    只會在該資料表所有列的 sort_order 都還是預設值 0 時執行，避免覆蓋掉使用者
    已經手動排序過的結果。
    """
    from sqlalchemy import text
    with db.engine.connect() as conn:
        all_zero = conn.execute(
            text(f'SELECT COUNT(*) FROM {table_name} WHERE sort_order != 0')
        ).scalar() == 0
        if not all_zero:
            return
        rows = conn.execute(text(f'SELECT id FROM {table_name} ORDER BY {order_by_sql}')).fetchall()
        for idx, row in enumerate(rows):
            conn.execute(text(f'UPDATE {table_name} SET sort_order = :idx WHERE id = :id'),
                         {'idx': idx, 'id': row[0]})
        conn.commit()


def _register_scheduled_jobs(app):
    """註冊 APScheduler 排程工作：截止日提醒、每月完整系統資料備份"""
    from jobs import send_deadline_reminders, run_monthly_full_backup
    from models import SystemSetting

    # 每週提醒未填寫工程師：優先讀取「設定提醒」頁面存在資料庫裡的設定值，
    # 找不到（例如全新安裝、管理者還沒存過）才退回 app.config 的預設值
    # （環境變數 REMINDER_WEEKDAY / REMINDER_TIME，預設週五 17:00）。
    # 管理者之後在頁面上儲存設定時，routes/email_settings.py 會呼叫
    # scheduler.add_job(..., replace_existing=True) 立即套用新排程，
    # 不需要重新啟動系統——這裡只是系統啟動當下讀取一次目前生效的設定值。
    weekday_setting = SystemSetting.query.get('reminder_weekday')
    time_setting = SystemSetting.query.get('reminder_time')
    try:
        weekday = int(weekday_setting.value) if weekday_setting and weekday_setting.value else app.config['REMINDER_WEEKDAY']
    except ValueError:
        weekday = app.config['REMINDER_WEEKDAY']
    weekday = weekday if 0 <= weekday <= 6 else app.config['REMINDER_WEEKDAY']
    reminder_time = time_setting.value if time_setting and time_setting.value else app.config['REMINDER_TIME']
    try:
        hour, minute = map(int, reminder_time.split(':'))
    except (ValueError, AttributeError):
        hour, minute = map(int, app.config['REMINDER_TIME'].split(':'))
    day_map = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

    # 系統啟動時也要檢查「設定提醒」頁面是否已經關閉自動提醒，是的話就不註冊
    # 這個排程 job，避免關閉後系統重啟又悄悄恢復寄送
    enabled_setting = SystemSetting.query.get('reminder_enabled')
    reminder_enabled = not (enabled_setting and enabled_setting.value == 'False')
    if reminder_enabled:
        scheduler.add_job(
            func=lambda: send_deadline_reminders(app),
            trigger='cron', day_of_week=day_map[weekday], hour=hour, minute=minute,
            id='weekly_reminder', replace_existing=True
        )

    # 每月完整系統資料備份：資料庫無關（SQLite/Cloud SQL 都適用），
    # 是否啟用／備份日期以「資料庫備份」頁面設定的值為主（存在 SystemSetting 裡），
    # 管理者在頁面上儲存設定時會即時呼叫 scheduler 更新，不需要重新啟動系統；
    # 這裡只是系統啟動當下讀取一次目前生效的設定值來註冊初始排程。
    enabled_setting = SystemSetting.query.get('full_backup_auto_enabled')
    day_setting = SystemSetting.query.get('full_backup_day')
    auto_enabled = enabled_setting.value == 'True' if enabled_setting else False
    backup_day = int(day_setting.value) if day_setting and day_setting.value else 8

    if auto_enabled:
        scheduler.add_job(
            func=lambda: run_monthly_full_backup(app),
            trigger='cron', day=backup_day, hour=3, minute=0,
            id='monthly_full_backup', replace_existing=True
        )


app = create_app()


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'True') == 'True'
    # 重要：debug 模式下的自動重載器 (reloader) 預設會監控整個專案目錄下所有檔案的變動。
    # 由於 SQLite 資料庫檔案 (instance/)、備份檔 (backups/)、附件 (uploads/) 都會在使用者
    # 操作時被寫入，若不排除這些路徑，reloader 會誤判成「程式碼變更」而重新啟動伺服器，
    # 導致當時處理中的請求連線中斷，前端出現 Internal Server Error 或連線失敗。
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=debug_mode,
        exclude_patterns=[
            '*/instance/*',
            '*/backups/*',
            '*/uploads/*',
            '*.db', '*.sqlite', '*.sqlite3',
        ],
    )
