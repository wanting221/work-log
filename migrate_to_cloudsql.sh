#!/bin/bash
# ============================================================
# migrate_to_cloudsql.sh
# 一鍵將 RD 週報系統從 SQLite 遷移到 Cloud SQL (MySQL)
#
# 使用方式：
#   1. 在 Google Cloud Shell（或本機已裝好、登入好 gcloud 的終端機）開啟這個檔案
#   2. 檢查下面「可調整的設定值」區塊，尤其是 SERVICE_NAME、REGION，
#      確認跟你實際的 Cloud Run 服務一致（如果不確定，跑一次
#      `gcloud run services list` 看實際的服務名稱/地區）
#   3. 執行：bash migrate_to_cloudsql.sh
#   4. 腳本跑完後，照畫面上印出的「後續步驟」操作
#
# 這個腳本會做的事：
#   1. 啟用必要的 API（Cloud SQL Admin API）
#   2. 建立一個 Cloud SQL for MySQL 執行個體（如果還沒有的話）
#   3. 建立資料庫（utf8mb4 字元集，正確支援中文）
#   4. 建立資料庫使用者，密碼自動產生一組高強度亂數密碼
#   5. 授權 Cloud Run 服務所使用的服務帳戶連線到這個 Cloud SQL 執行個體
#   6. 更新 Cloud Run 服務：接上 Cloud SQL 連線 + 設定 DATABASE_URL 環境變數
#      （用 --update-env-vars，只會新增/更新這一個變數，不會動到你其他既有
#      的環境變數如 SECRET_KEY、SMTP 設定等）
#
# 這個腳本不會做的事（安全考量，刻意留給你自己確認）：
#   - 不會刪除你現有的 SQLite 資料（原本的資料還在容器裡，直到下次重啟才會消失）
#   - 不會自動把 AUTO_SEED_DEMO_DATA 設為 False（腳本跑完後請自行評估是否要關閉）
#   - 不會自動執行 python init_db.py 在新資料庫建表（第一次連上新資料庫、
#     Cloud Run 重新啟動時，系統會自動偵測空白資料庫並建表+建立管理者帳號，
#     不需要手動介入）
# ============================================================

set -e  # 任何一個指令失敗就立刻停止，避免後面的步驟建立在錯誤的基礎上

# ---------------- 可調整的設定值 ----------------
PROJECT_ID="weekly-report"
REGION="asia-east1"
SERVICE_NAME="weekly-report-git"          # 你的 Cloud Run 服務名稱，不確定請跑 gcloud run services list 確認
SQL_INSTANCE_NAME="weekly-report-mysql"   # 要建立的 Cloud SQL 執行個體名稱，可自訂
SQL_TIER="db-f1-micro"                    # 最小規格，適合這種輕量內部工具；流量變大可之後再升級
DB_NAME="rd_weekly_report"
DB_USER="rd_weekly_report_app"
# -------------------------------------------------

echo "============================================================"
echo "RD 週報系統 → Cloud SQL 遷移腳本"
echo "專案: $PROJECT_ID / 地區: $REGION / 服務: $SERVICE_NAME"
echo "============================================================"

echo ""
echo "[0/6] 設定目前操作的專案..."
gcloud config set project "$PROJECT_ID"

echo ""
echo "[1/6] 啟用 Cloud SQL Admin API（如果已經啟用過，這行會直接跳過）..."
gcloud services enable sqladmin.googleapis.com

echo ""
echo "[2/6] 檢查 Cloud SQL 執行個體是否已存在..."
if gcloud sql instances describe "$SQL_INSTANCE_NAME" >/dev/null 2>&1; then
  echo "  執行個體 $SQL_INSTANCE_NAME 已存在，略過建立步驟。"
else
  echo "  建立新的 Cloud SQL for MySQL 執行個體（這一步大約需要 5~10 分鐘，請耐心等待）..."
  ROOT_PASSWORD=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)
  gcloud sql instances create "$SQL_INSTANCE_NAME" \
    --database-version=MYSQL_8_0 \
    --tier="$SQL_TIER" \
    --region="$REGION" \
    --root-password="$ROOT_PASSWORD" \
    --storage-auto-increase

  echo ""
  echo "  ⚠️  已產生 root 密碼，請自行保存到安全的地方（例如 Secret Manager 或密碼管理工具）："
  echo "  Root 密碼: $ROOT_PASSWORD"
  echo "  （這組密碼只用於管理 Cloud SQL 本身，應用程式實際使用的是下面另外建立的資料庫使用者）"
fi

echo ""
echo "[3/6] 建立資料庫（若已存在會直接跳過，utf8mb4 字元集確保中文正確顯示）..."
if gcloud sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE_NAME" >/dev/null 2>&1; then
  echo "  資料庫 $DB_NAME 已存在，略過建立步驟。"
else
  gcloud sql databases create "$DB_NAME" \
    --instance="$SQL_INSTANCE_NAME" \
    --charset=utf8mb4 \
    --collation=utf8mb4_unicode_ci
fi

echo ""
echo "[4/6] 建立應用程式專用的資料庫使用者..."
DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)
if gcloud sql users list --instance="$SQL_INSTANCE_NAME" --format="value(name)" | grep -qx "$DB_USER"; then
  echo "  使用者 $DB_USER 已存在，改成重設密碼（沿用原本的帳號）..."
  gcloud sql users set-password "$DB_USER" --instance="$SQL_INSTANCE_NAME" --password="$DB_PASSWORD"
else
  gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE_NAME" --password="$DB_PASSWORD"
fi

CONNECTION_NAME=$(gcloud sql instances describe "$SQL_INSTANCE_NAME" --format="value(connectionName)")
echo "  連線名稱: $CONNECTION_NAME"

echo ""
echo "[5/6] 授權 Cloud Run 服務的服務帳戶可以連線到 Cloud SQL..."
SERVICE_ACCOUNT=$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" \
  --format="value(spec.template.spec.serviceAccountName)")
if [ -z "$SERVICE_ACCOUNT" ]; then
  # 沒有指定自訂服務帳戶時，Cloud Run 用的是專案預設的 Compute Engine 服務帳戶
  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
  SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi
echo "  服務帳戶: $SERVICE_ACCOUNT"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/cloudsql.client" \
  --condition=None

echo ""
echo "[6/6] 更新 Cloud Run 服務：接上 Cloud SQL 連線 + 設定 DATABASE_URL..."
DATABASE_URL="mysql+pymysql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?unix_socket=/cloudsql/${CONNECTION_NAME}"

gcloud run services update "$SERVICE_NAME" \
  --region="$REGION" \
  --add-cloudsql-instances="$CONNECTION_NAME" \
  --update-env-vars="DATABASE_URL=${DATABASE_URL}"

echo ""
echo "============================================================"
echo "✅ 遷移完成！"
echo "============================================================"
echo ""
echo "Cloud SQL 連線名稱: $CONNECTION_NAME"
echo "資料庫使用者: $DB_USER"
echo "資料庫密碼: $DB_PASSWORD"
echo "（請把這組密碼存到安全的地方，例如 Secret Manager，這裡不會再顯示第二次）"
echo ""
echo "後續步驟："
echo "1. Cloud Run 會自動用新設定重新啟動一個修訂版本，等 1~2 分鐘讓它完成"
echo "2. 打開你的服務網址，用預設管理者帳號登入：A00007 / admin123"
echo "   （因為是全新資料庫，系統會自動建好這個管理者帳號，不需要手動初始化）"
echo "3. 登入後，到「工程師帳號管理」「專案清單管理」重新建立正式資料，"
echo "   或用 CSV 批次匯入功能（如果你手邊有之前系統的資料匯出檔）"
echo "4. 確認一切正常後，建議："
echo "   - 把 Cloud Run 環境變數 AUTO_SEED_DEMO_DATA 設為 False"
echo "   - 立刻登入後修改管理者帳號的預設密碼"
echo "   - 到 Cloud SQL 主控台設定自動備份（左側選單「備份」）"
echo "============================================================"
