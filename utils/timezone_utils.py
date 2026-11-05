# -*- coding: utf-8 -*-
"""
utils/timezone_utils.py - 統一提供台灣當地時間 (Asia/Taipei, UTC+8)
系統所有需要記錄「現在時間」的地方（資料庫時間戳記、備份檔名、排程判斷等）
都應該使用這裡的函式，而不是 datetime.utcnow() 或 datetime.now()。

原因：Cloud Run 等雲端容器預設系統時區通常是 UTC，如果直接用 datetime.now()，
記錄到的時間會跟台灣使用者實際感受到的時間差 8 小時，容易造成「時間戳記怪怪的」
「明明是晚上卻顯示是白天」的困惑；用 datetime.utcnow() 儲存雖然是常見的最佳實踐，
但這個系統單純只給台灣內部團隊使用，直接統一存台灣當地時間顯示上更直覺、也不需要
額外的時區轉換邏輯。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo('Asia/Taipei')


def taipei_now():
    """回傳目前台灣當地時間（naive datetime，不含時區資訊，方便直接存入資料庫欄位）"""
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


def taipei_today():
    """回傳目前台灣當地日期"""
    return taipei_now().date()


def taipei_from_timestamp(unix_timestamp):
    """
    將 Unix timestamp（例如檔案的修改時間 os.path.getmtime()）轉換為台灣當地時間。
    直接用 datetime.fromtimestamp() 不帶時區參數的話，會用容器系統的本地時區
    （雲端環境通常是 UTC）換算，顯示出來的時間會跟台灣使用者預期的時間差 8 小時。
    """
    return datetime.fromtimestamp(unix_timestamp, tz=TAIPEI_TZ).replace(tzinfo=None)
