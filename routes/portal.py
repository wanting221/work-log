# -*- coding: utf-8 -*-
"""
routes/portal.py - 員工入口網站
公司員工的第一站，之後會陸續擴充不同的內部系統（績效考核、請假、打卡等），
每個系統各自有自己的登入頁面，這裡只是一個系統選單的入口，不需要登入即可看到。
"""
from flask import Blueprint, render_template
from flask_login import current_user, logout_user

portal_bp = Blueprint('portal', __name__)


@portal_bp.route('/')
def index():
    # 入口網站是所有系統的第一站，畫面上刻意設計成「訪客頁面」，不帶側邊選單。
    # 如果瀏覽器裡還留著某個系統（例如週報系統）的登入狀態，這裡會直接登出，
    # 確保入口網站永遠是乾淨的畫面，不會意外顯示某個系統的側邊選單。
    if current_user.is_authenticated:
        logout_user()
    return render_template('portal.html')
