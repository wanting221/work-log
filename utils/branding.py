# -*- coding: utf-8 -*-
"""
utils/branding.py - 公司／品牌相關設定的單一出口

數值來源是 config.py（會先載入 .env），這裡只是讓不在 Flask 應用程式
情境內的模組（例如匯入、種子資料）也能直接取用。
"""
from config import Config

DEFAULT_NEW_ACCOUNT_PASSWORD = Config.DEFAULT_NEW_ACCOUNT_PASSWORD
PLACEHOLDER_EMAIL_DOMAIN = Config.PLACEHOLDER_EMAIL_DOMAIN
