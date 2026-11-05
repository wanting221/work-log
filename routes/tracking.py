# -*- coding: utf-8 -*-
"""
routes/tracking.py - 未填寫追蹤 + 一鍵重新提醒
"""
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user
from models import EmailTemplate, SystemSetting
from utils.report_utils import current_year_week, get_fill_status_summary
from utils.email_utils import send_email, render_template_vars
from utils.decorators import log_action

tracking_bp = Blueprint('tracking', __name__)


@tracking_bp.route('/tracking')
@login_required
def index():
    if current_user.role == 'engineer':
        abort(403)

    year = request.args.get('year', type=int)
    week = request.args.get('week', type=int)
    if not year or not week:
        year, week = current_year_week()

    # 部門主管只看得到自己部門的未填寫名單；組別主管只看得到自己組別的
    department = current_user.department if current_user.role == 'dept_manager' else None
    group_id = current_user.group_id if current_user.role == 'group_manager' else None
    summary = get_fill_status_summary(year, week, department=department, group_id=group_id)
    return render_template('tracking.html', summary=summary, year=year, week=week)


@tracking_bp.route('/tracking/remind/<int:engineer_id>', methods=['POST'])
@login_required
def remind_single(engineer_id):
    """一鍵重新提醒單一未填寫工程師"""
    if current_user.role not in ('admin', 'dept_manager', 'group_manager'):
        abort(403)

    from models import Engineer
    engineer = Engineer.query.get_or_404(engineer_id)

    if current_user.role == 'dept_manager' and (engineer.department or '') != (current_user.department or ''):
        # 安全檢查：部門主管直接改網址想提醒別部門的人也要擋下來，不能只靠
        # 頁面只列出自己部門的人來擋（頁面只是 UI 上的方便，不能當作真正的權限控管）
        abort(403)
    if current_user.role == 'group_manager' and engineer.group_id != current_user.group_id:
        abort(403)

    template = EmailTemplate.query.filter_by(key='manual_reminder').first()
    year, week = current_year_week()

    if template:
        subject = render_template_vars(template.subject, {'name': engineer.name, 'year': year, 'week': week})
        body = render_template_vars(template.body, {'name': engineer.name, 'year': year, 'week': week})
    else:
        subject = f'【提醒】請儘速填寫第{week}週工程師週報'
        body = f'{engineer.name} 您好，<br>提醒您第{year}年第{week}週週報尚未填寫，請儘速登入系統完成填寫。'

    ok, msg = send_email(engineer.email, subject, body)
    log_action('remind', 'Engineer', engineer.id,
               f'{current_user.name} 手動提醒 {engineer.name}，結果: {msg}')
    return jsonify({'success': ok, 'message': msg if not ok else f'已寄送提醒信給 {engineer.name}'})


@tracking_bp.route('/tracking/remind_all', methods=['POST'])
@login_required
def remind_all():
    """一鍵提醒所有未填寫工程師（部門主管只會提醒到自己部門的人，組別主管只會提醒到自己組別的人）"""
    if current_user.role not in ('admin', 'dept_manager', 'group_manager'):
        abort(403)

    year, week = current_year_week()
    # 部門主管/組別主管一鍵提醒時，只會提醒到自己範圍內的未填寫人員，不會誤發給其他人
    department = current_user.department if current_user.role == 'dept_manager' else None
    group_id = current_user.group_id if current_user.role == 'group_manager' else None
    summary = get_fill_status_summary(year, week, department=department, group_id=group_id)
    template = EmailTemplate.query.filter_by(key='manual_reminder').first()

    results = []
    for eng in summary['not_submitted']:
        if template:
            subject = render_template_vars(template.subject, {'name': eng.name, 'year': year, 'week': week})
            body = render_template_vars(template.body, {'name': eng.name, 'year': year, 'week': week})
        else:
            subject = f'【提醒】請儘速填寫第{week}週工程師週報'
            body = f'{eng.name} 您好，<br>提醒您第{year}年第{week}週週報尚未填寫，請儘速登入系統完成填寫。'
        ok, msg = send_email(eng.email, subject, body)
        results.append({'name': eng.name, 'success': ok})

    log_action('remind_all', 'Engineer', None,
               f'{current_user.name} 一鍵提醒全部未填寫工程師 ({len(results)}人)')
    success_count = sum(1 for r in results if r['success'])
    return jsonify({'success': True, 'message': f'已寄送 {success_count}/{len(results)} 封提醒信', 'results': results})


@tracking_bp.route('/tracking/remind_diary/<int:engineer_id>', methods=['POST'])
@login_required
def remind_single_diary(engineer_id):
    """
    一鍵重新提醒單一未填寫「日誌」的工程師。設計比照 remind_single()（週報版），
    差別只在信件內容改成日誌相關措辭，且不共用週報的 EmailTemplate（避免內容
    對不上），用獨立的 key 'manual_reminder_diary'，範本不存在時退回預設文字。
    """
    if current_user.role not in ('admin', 'dept_manager', 'group_manager'):
        abort(403)

    from models import Engineer
    engineer = Engineer.query.get_or_404(engineer_id)

    if current_user.role == 'dept_manager' and (engineer.department or '') != (current_user.department or ''):
        abort(403)
    if current_user.role == 'group_manager' and engineer.group_id != current_user.group_id:
        abort(403)

    template = EmailTemplate.query.filter_by(key='manual_reminder_diary').first()
    year, week = current_year_week()

    if template:
        subject = render_template_vars(template.subject, {'name': engineer.name, 'year': year, 'week': week})
        body = render_template_vars(template.body, {'name': engineer.name, 'year': year, 'week': week})
    else:
        subject = f'【提醒】請儘速填寫第{week}週日誌'
        body = f'{engineer.name} 您好，<br>提醒您第{year}年第{week}週日誌尚未填寫，請儘速登入系統完成填寫。'

    ok, msg = send_email(engineer.email, subject, body)
    log_action('remind', 'Engineer', engineer.id,
               f'{current_user.name} 手動提醒 {engineer.name} 填寫日誌，結果: {msg}')
    return jsonify({'success': ok, 'message': msg if not ok else f'已寄送提醒信給 {engineer.name}'})


@tracking_bp.route('/tracking/remind_diary_all', methods=['POST'])
@login_required
def remind_all_diary():
    """一鍵提醒所有未填寫「日誌」的工程師（部門主管只會提醒到自己部門的人，組別主管只會提醒到自己組別的人）"""
    if current_user.role not in ('admin', 'dept_manager', 'group_manager'):
        abort(403)

    from utils.report_utils import get_diary_fill_status_summary
    year, week = current_year_week()
    department = current_user.department if current_user.role == 'dept_manager' else None
    group_id = current_user.group_id if current_user.role == 'group_manager' else None
    summary = get_diary_fill_status_summary(year, week, department=department, group_id=group_id)
    template = EmailTemplate.query.filter_by(key='manual_reminder_diary').first()

    results = []
    for eng in summary['not_submitted']:
        if template:
            subject = render_template_vars(template.subject, {'name': eng.name, 'year': year, 'week': week})
            body = render_template_vars(template.body, {'name': eng.name, 'year': year, 'week': week})
        else:
            subject = f'【提醒】請儘速填寫第{week}週日誌'
            body = f'{eng.name} 您好，<br>提醒您第{year}年第{week}週日誌尚未填寫，請儘速登入系統完成填寫。'
        ok, msg = send_email(eng.email, subject, body)
        results.append({'name': eng.name, 'success': ok})

    log_action('remind_all', 'Engineer', None,
               f'{current_user.name} 一鍵提醒全部未填寫日誌工程師 ({len(results)}人)')
    success_count = sum(1 for r in results if r['success'])
    return jsonify({'success': True, 'message': f'已寄送 {success_count}/{len(results)} 封提醒信', 'results': results})
