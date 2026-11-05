# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# 系統套件：cryptography / bcrypt 編譯需要基本工具鏈
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 建立必要資料夾（雲端環境下這些內容不會持久保存，詳見部署說明）
RUN mkdir -p instance backups uploads

# Cloud Run 會透過環境變數 PORT 指定監聽埠（預設 8080）
ENV PORT=8080
EXPOSE 8080

# 使用 gunicorn 作為正式環境 WSGI Server
# --workers 1：刻意只開一個工作行程，避免容器內的 APScheduler 排程（提醒信/自動備份）
#             因為多個 worker 各自啟動一份而重複執行（例如同一封提醒信寄送多次）
# --timeout 0：避免匯出大型 Excel 或寄信等較久的請求被 gunicorn 預設 30 秒逾時中斷
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
