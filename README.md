# 工程師週報系統 (RD Weekly Report Portal)

企業內部使用的工程師週報管理系統。供 RD 各組（ID/ME、EE/FW、SW/APP 等）工程師每週填寫「本週已執行工作精要」與「下週計劃工作重點」，並提供管理者依「工程師」與「客戶／專案」兩種角度檢視、篩選、匯出與追蹤未填寫名單。

## 技術架構

- **Frontend**: HTML + Bootstrap 5 + JavaScript (SortableJS 拖曳排序)
- **Backend**: Python Flask（Application Factory 架構）
- **Database**: SQLite（透過 SQLAlchemy ORM，未來可直接改接 MySQL / PostgreSQL，只需修改 `.env` 中的 `DATABASE_URL`）
- **Excel 匯出入**: openpyxl
- **Email 通知**: SMTP（支援 Gmail、Microsoft 365）
- **排程工作**: APScheduler（截止日提醒、每日自動備份）
- **密碼安全**: Flask-Bcrypt
- **CSRF 防護**: Flask-WTF

## 資料夾架構

```
rd_weekly_report/
├── app.py                 # 應用程式進入點 (Application Factory)
├── config.py              # 設定檔（讀取 .env）
├── extensions.py          # Flask 擴充套件集中初始化
├── models.py               # SQLAlchemy 資料庫模型
├── jobs.py                 # APScheduler 排程工作 (提醒信、每日備份)
├── init_db.py              # 資料庫初始化腳本
├── requirements.txt
├── .env.example
├── README.md
├── routes/                 # 所有路由 (Blueprint)
│   ├── auth.py              # 登入/登出
│   ├── dashboard.py         # 管理者首頁 Dashboard
│   ├── report.py            # 工程師填寫週報
│   ├── view_engineer.py     # 依工程師檢視
│   ├── view_project.py      # 依專案檢視
│   ├── tracking.py          # 未填寫追蹤 + 一鍵提醒
│   ├── admin_project.py     # 專案／客戶清單管理
│   ├── admin_engineer.py    # 工程師帳號管理
│   ├── export.py            # 匯出/匯入報表
│   ├── email_settings.py    # Email 提醒設定
│   └── audit.py             # 操作紀錄 + 資料庫備份管理
├── utils/                   # 共用工具函式
│   ├── decorators.py         # 角色權限裝飾器 + Audit Log
│   ├── excel_utils.py        # Excel 匯出/匯入
│   ├── email_utils.py        # SMTP 寄信
│   ├── backup_utils.py       # 加密備份/還原
│   └── report_utils.py       # 週報共用邏輯 (年週計算、卡關偵測)
├── templates/               # Jinja2 樣板 (Bootstrap 5, RWD)
├── static/
│   ├── css/style.css
│   └── js/
├── instance/                # SQLite 資料庫檔案存放處
├── backups/                 # 加密備份檔案存放處
└── uploads/                 # 附件上傳暫存區
```

## 快速開始

### 1. 安裝套件

```bash
pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，填入 SECRET_KEY、SMTP 帳密、備份加密金鑰等
```

### 3. 初始化資料庫（建立資料表 + 管理者帳號）

```bash
python init_db.py
```

會建立：
- 所有資料表
- 1 個管理者帳號：`A171`（密碼 `admin123`）
- 3 組 Email 範本（截止日提醒／手動提醒／彙整報表通知，屬系統預設文字，可於「Email提醒設定」頁面自行編輯）

**不會**建立任何假的工程師帳號、組別、專案或範例週報資料——系統一開始就是空白的，
請登入後自行透過「工程師帳號管理」「專案清單管理」頁面建立真實資料，或使用 CSV 批次匯入功能。

### 4. 執行系統

```bash
python app.py
```

開啟瀏覽器至 `http://localhost:5000`，使用管理者帳號 `A171` / `admin123` 登入即可。登入後請立即：
1. 到「工程師帳號管理」建立真實的工程師帳號（或用 CSV 匯入）
2. 到「專案清單管理」建立真實的客戶／專案清單（或用 CSV 匯入）
3. 修改管理者帳號密碼

## 角色說明

| 角色 | 說明 |
|------|------|
| **管理者 (Admin)** | 管理工程師帳號、組別、專案清單，檢視/匯出全部週報，設定截止日與提醒，解鎖週報，資料庫備份還原 |
| **工程師 (Engineer)** | 填寫/編輯自己的週報，查詢自己歷史紀錄 |
| **唯讀 (Viewer)** | 僅可檢視彙整報表，無法修改內容（供 PM/業務主管使用） |

## 核心功能重點

1. **未完成事項自動延續**：填寫本週週報時，系統會顯示上週「下週計劃工作重點」清單，工程師可勾選「延續到本週」自動帶入本週已執行欄位，避免遺漏追蹤。
2. **卡關關鍵字自動偵測**：系統會偵測「待回覆」「待安排」「待確認」等關鍵字，若同一件事（透過 chain_id 追蹤鏈）連續超過設定週數仍未更新，會在 Dashboard 標示為風險項目。
3. **週報鎖定機制**：工程師送出週報後自動鎖定，如需修改須由管理者於「依工程師檢視」頁面解鎖，所有解鎖動作都會記錄於 Audit Log。
4. **雙視角檢視**：管理者可依「工程師」或「客戶／專案」兩種角度檢視全體週報進度。
5. **Excel 匯出格式比照現行版面**：「RD組別｜工程師｜已執行工作精要｜客戶業務｜計劃工作重點」，也支援依專案分類的彙整格式。
6. **歷史資料批次匯入**：供系統上線初期，將既有 Excel 週報資料轉入系統。
7. **每日自動加密備份**：使用 Fernet 對稱加密，可於「資料庫備份」頁面手動觸發、下載或還原。

## 修改 SQL 建表語法（供未來改用 MySQL / PostgreSQL 參考）

系統使用 SQLAlchemy ORM，執行 `python init_db.py` 時會自動依 `models.py` 產生對應的資料表結構（`db.create_all()`）。若要改用 MySQL / PostgreSQL：

1. 安裝對應驅動：`pip install pymysql` 或 `pip install psycopg2-binary`
2. 修改 `.env` 中的 `DATABASE_URL`，例如：
   ```
   DATABASE_URL=mysql+pymysql://user:password@localhost:3306/rd_weekly_report
   ```
3. 重新執行 `python init_db.py` 建立資料表結構

## 部署建議

- **Windows Server**：可搭配 `waitress` (`pip install waitress`) 作為 WSGI Server，或使用 IIS + wfastcgi。
- **Linux**：建議使用 `gunicorn` + `nginx` 反向代理，並設定 HTTPS（Let's Encrypt）。
- 部署為 HTTPS 後，請將 `.env` 中的 `SESSION_COOKIE_SECURE` 設為 `True`。
- 正式環境請務必更改 `SECRET_KEY` 與 `BACKUP_ENCRYPTION_KEY`，並修改管理者帳號的預設密碼。

## Google Cloud Run 部署說明（重要）

本專案已附上 `Dockerfile`、`.dockerignore`、`cloudbuild.yaml`，可直接透過 Cloud Build 建置並部署到 Cloud Run。但部署前請務必了解以下兩個「無狀態容器」帶來的限制：

### 1. 資料庫持久化問題（強烈建議）

Cloud Run 容器是**無狀態**的：每次重新部署新版本、容器自動擴縮、或閒置後被回收，容器內的檔案系統都會被重置。這代表如果直接用預設的 SQLite（存在 `instance/` 資料夾內），**週報資料可能在下次部署或重啟後消失**。

正式上線前，強烈建議改接 **Cloud SQL**（MySQL 或 PostgreSQL）：

1. 建立一個 Cloud SQL 執行個體
2. 安裝對應驅動：`pip install pymysql` 或 `pip install psycopg2-binary`（並加進 `requirements.txt`）
3. 修改 Cloud Run 服務的環境變數 `DATABASE_URL`，例如：
   ```
   # MySQL，透過 Cloud SQL Unix Socket 連線
   DATABASE_URL=mysql+pymysql://user:password@/dbname?unix_socket=/cloudsql/PROJECT:REGION:INSTANCE

   # PostgreSQL
   DATABASE_URL=postgresql+psycopg2://user:password@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
   ```
4. 部署時加上 `--add-cloudsql-instances=PROJECT:REGION:INSTANCE`（`cloudbuild.yaml` 裡已預留註解位置）

如果暫時只是要測試展示、資料遺失可接受，才維持用 SQLite，但請務必了解風險。附件上傳、備份檔案（`uploads/`、`backups/`）也同樣會在重啟後消失，正式環境建議改存到 Cloud Storage。

### 2. 排程重複執行問題

系統內建用 APScheduler 在應用程式行程裡跑「截止日提醒信」與「每日自動備份」排程。Cloud Run 若同時起多個容器實例，每個實例都會各自執行一份排程，導致**同一封提醒信可能被重複寄送好幾次**。

`cloudbuild.yaml` 目前設定 `--max-instances=1 --min-instances=1`，先用「只允許一個實例」的方式簡單解決此問題。若之後系統使用量變大需要讓 Cloud Run 自由擴縮，建議改用外部排程觸發：

1. 在 Cloud Run 服務的環境變數設定 `ENABLE_INTERNAL_SCHEDULER=False`（關閉內建排程）
2. 到 Google Cloud Scheduler 建立兩個排程工作，分別在你要的時間，以 HTTP POST 呼叫：
   - `https://your-service-url/internal/trigger/reminder`
   - `https://your-service-url/internal/trigger/backup`
   並在 Header 加上 `X-Internal-Token: <.env裡設定的INTERNAL_TRIGGER_TOKEN>`
3. 這樣無論 Cloud Run 開幾個實例，排程只會由 Cloud Scheduler 觸發一次

### 3. 部署步驟摘要

```bash
# 本機測試映像檔是否能正常建置與啟動
docker build -t rd-weekly-report .
docker run -p 8080:8080 --env-file .env rd-weekly-report

# 透過 Cloud Build 建置並部署（需先設定好 Artifact Registry repository）
gcloud builds submit --config cloudbuild.yaml
```

正式環境的機密設定（`SECRET_KEY`、SMTP 密碼、備份加密金鑰、`INTERNAL_TRIGGER_TOKEN` 等）建議透過 [Secret Manager](https://cloud.google.com/secret-manager) 管理，並在 Cloud Run 服務設定中以「密鑰參照」掛載為環境變數，不要直接寫在 Dockerfile 或原始碼裡。

## ⚠️ 資料持久化重要提醒（雲端部署必看）

如果系統部署在 Cloud Run 這類無狀態容器環境，且**還在使用預設的 SQLite**（`DATABASE_URL` 是 `sqlite:///...`），請務必了解：**容器內的檔案不會持久保存**。只要重新部署、服務閒置後重啟、或容器被 Google 基礎設施回收，資料庫檔案就會整個歸零——所有工程師帳號、專案、週報資料都會消失（只會因為 `AUTO_SEED_DEMO_DATA=True` 自動重建管理者帳號，讓你「還能登入」但看不到任何資料）。

**如果系統已經有正式資料在使用，這不是可以忽略的小問題，請盡快改接 Cloud SQL**（MySQL 或 PostgreSQL），作法請見上方「Google Cloud Run 部署說明」章節。改接 Cloud SQL 後，記得同時把 `AUTO_SEED_DEMO_DATA` 設為 `False`。

## 系統時區

系統所有時間（週報時間戳記、附註留言時間、操作紀錄時間、排程觸發時間、備份檔名）都統一使用**台灣當地時間 (Asia/Taipei, UTC+8)**，不論部署環境的容器系統時區設定為何（雲端環境預設通常是 UTC）。這是集中在 `utils/timezone_utils.py` 處理的，如果之後要調整時區，只需要改這一個檔案。

## 系統資料備份

備份機制是**資料庫無關**的：直接把所有資料表的實際內容（帳號、專案、週報、SMTP 設定等）查出來、
加密後存成 `.enc` 檔案，不管系統實際接的是 SQLite 還是 Cloud SQL（MySQL/PostgreSQL）都適用。

「資料庫備份」頁面提供三個功能：

1. **系統資料備份**：點「立即匯出備份」手動觸發一次完整備份
2. **下載加密備份檔 + 自動備份設定**：可以啟用「每月自動備份」，選擇每月 1~28 號其中一天，
   固定在台灣時間凌晨 03:00 執行；**勾選或變更日期會自動儲存並立即生效，不需要重新啟動系統**。
   若當月已經過了設定的備份日、卻找不到成功的備份紀錄，頁面最上方會顯示明顯的提醒。
   備份檔案清單可以直接下載。
3. **選擇加密備份檔並匯入還原**：上傳 `.enc` 備份檔即可還原，**這是完全覆蓋**（清空目前所有
   資料、換成備份檔內容），操作前頁面上有明顯警告與二次確認。

## 效能優化說明

若系統回應變慢（換頁、儲存週報都慢），已在程式碼層面做了以下優化，全新部署或既有資料庫重啟後都會自動套用：

- **資料庫索引**：SQLite 預設不會自動幫外鍵欄位建索引，先前版本幾乎每個頁面的 JOIN／篩選查詢都是全表掃描。已補上 12 個索引（工程師組別/狀態、專案狀態/負責人、週報年度週次、週報項目的報表/專案/延續鏈、附件、操作紀錄等）。既有資料庫重啟時會自動偵測並補上，不需要手動處理。
- **消除 N+1 查詢**：Dashboard 的卡關偵測原本會把「全部歷史週報項目」整張表讀進記憶體，資料量隨使用週數只會越堆越多；Dashboard、依專案檢視、匯出報表等頁面也都有「迴圈裡逐筆觸發額外查詢」的問題。已全部改用資料庫端聚合查詢或一次性關聯查詢（joinedload / selectinload / contains_eager），大幅降低每次換頁時實際執行的 SQL 查詢次數。
- **SQLite 寫入效能**：搭配 WAL 模式加上 `synchronous=NORMAL`（SQLite 官方建議組合），避免每次儲存週報都要等待完整磁碟同步；鎖定逾時從 15 秒調降為 5 秒，萬一真的卡住也能更快得到明確錯誤，而不是長時間無反應。
- **回應壓縮與快取**：啟用 gzip 壓縮頁面內容；靜態檔案（CSS）設定 12 小時瀏覽器快取並自動帶版本號，換頁不用重新下載相同檔案，且更新部署後會自動抓到新版本不受快取影響。

### 如果優化後還是覺得慢，請檢查 Cloud Run 這幾個設定

以下是程式碼管不到、但同樣會顯著影響速度的基礎設施設定：

1. **服務所在地區（Region）**：確認跟主要使用者所在地理位置相近（例如使用者在台灣，地區選 `asia-east1`），跨洲際的網路延遲每次換頁都會多花 200~300ms 以上，這在程式碼層面無法解決。
2. **最小執行個體數（min-instances）**：若設為 0，服務閒置一段時間後會完全關閉，下次有人訪問時要重新啟動容器（冷啟動），第一個請求可能要等好幾秒。建議至少設為 1。
3. **CPU 配置**：確認「CPU 配置」選項是否為「僅在處理要求時配置」，如果同時有背景排程（提醒信、自動備份）在跑，配置不足會讓一般請求也變慢；記憶體與 CPU 建議至少 512Mi / 1 vCPU 起跳。
4. **並行請求數（Concurrency）**：目前 `gunicorn` 是 `--workers 1 --threads 8`（刻意只開一個 worker，避免背景排程重複執行），若同時使用人數較多，這個設定可能會是瓶頸；但因為排程限制，不建議貿然調高 workers 數，若真的需要更高並行度，建議先參考先前建議把排程改用 Cloud Scheduler 觸發，才能安全地把 workers 調高或讓 Cloud Run 自由擴縮。

## 安全性重點



- 密碼使用 bcrypt 雜湊儲存，不會以明文存放。
- SMTP 帳密透過 `.env` 管理，不寫死於程式碼中。
- 所有表單皆有 CSRF Token 防護。
- Session 逾時會自動登出（預設 60 分鐘，可於 `.env` 調整）。
- 完整操作紀錄 (Audit Log)：記錄誰在何時新增/修改/刪除/匯出/解鎖哪一筆資料。
- 資料庫備份採用 Fernet 對稱加密，備份金鑰與資料庫檔案分開保管更安全。
