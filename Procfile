# Procfile - 供 Google Cloud Buildpacks（自動偵測建置，不使用 Dockerfile 時）
# 或其他遵循 Procfile 慣例的平台（Heroku 風格）使用，用來指定啟動指令。
#
# 如果 Cloud Run 的部署管線是用 Dockerfile 建置，這個檔案會被忽略，
# 實際啟動指令以 Dockerfile 裡的 CMD 為準；兩邊的指令刻意保持一致，
# 不管走哪種建置路徑，行為都相同。
#
# --workers 1：刻意只開一個工作行程，避免容器內的 APScheduler 排程
#             （提醒信/自動備份）因為多個 worker 各自啟動一份而重複執行
# --timeout 0：避免匯出大型 Excel 或寄信等較久的請求被 gunicorn 預設
#             30 秒逾時中斷
web: gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
