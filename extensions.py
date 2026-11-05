# -*- coding: utf-8 -*-
"""
extensions.py - 集中初始化 Flask 擴充套件，避免循環 import
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_bcrypt import Bcrypt
from flask_compress import Compress
from apscheduler.schedulers.background import BackgroundScheduler

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
bcrypt = Bcrypt()
compress = Compress()
scheduler = BackgroundScheduler(timezone='Asia/Taipei')

# 登入頁面設定
login_manager.login_view = 'auth.login'
login_manager.login_message = '請先登入以繼續使用系統'
login_manager.login_message_category = 'warning'
