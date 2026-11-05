# -*- coding: utf-8 -*-
"""
routes/auth.py - 登入 / 登出
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from models import Engineer
from utils.decorators import log_action

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/weekly_report_login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        emp_id = request.form.get('emp_id', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        engineer = Engineer.query.filter_by(emp_id=emp_id).first()

        if not engineer or not engineer.check_password(password):
            flash('工號或密碼錯誤', 'danger')
            return render_template('login.html')

        if engineer.status != 'active':
            flash('此帳號已離職停用，如有疑問請聯繫管理者', 'danger')
            return render_template('login.html')

        login_user(engineer, remember=remember)
        # 一律設成 permanent session，搭配 PERMANENT_SESSION_LIFETIME 這個
        # 「閒置多久沒動作就登出」的滑動式逾時（Flask 預設每次請求都會重新
        # 整理逾時時間，只要還有在操作，就不會真的被登出）。
        # 這裡不再依照「記住我」勾選與否來決定是否 permanent——如果不設成
        # permanent，session cookie 會變成瀏覽器的「工作階段 cookie」（沒有
        # 明確的到期時間，理論上要瀏覽器關掉才會失效），但現代瀏覽器為了
        # 省記憶體，常常會把閒置很久沒切換過去的分頁背景「卸載」，使用者
        # 感覺分頁「還開著」，但實際上有些瀏覽器在這種情況下對工作階段
        # cookie 的處理不夠一致，導致填寫日誌這種可能會停留、輸入比較久的
        # 頁面，過一段時間回來就被登出、剛輸入的內容還沒存到就跳錯誤訊息。
        # 一律 permanent 之後，逾時時間變成明確可預期的
        # PERMANENT_SESSION_LIFETIME（見 config.py，已經拉長到符合一般工作
        # 情境的長度），不會再受瀏覽器分頁卸載這種不確定因素影響。
        # 「記住我」勾選的效果改由 Flask-Login 自己的 remember cookie
        # （REMEMBER_COOKIE_DURATION，預設 30 天）負責，兩者不衝突。
        session.permanent = True
        log_action('login', 'Engineer', engineer.id, f'{engineer.name} 登入系統')
        next_page = request.args.get('next')
        return redirect(next_page or url_for('dashboard.index'))

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    log_action('logout', 'Engineer', current_user.id, f'{current_user.name} 登出系統')
    logout_user()
    flash('已成功登出', 'info')
    return redirect(url_for('portal.index'))
