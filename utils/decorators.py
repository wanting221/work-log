# -*- coding: utf-8 -*-
"""
utils/decorators.py - 角色權限控制裝飾器與稽核紀錄輔助函式
"""
from functools import wraps
from flask import abort, request
from flask_login import current_user
from extensions import db
from models import AuditLog


def roles_required(*roles):
    """限制只有指定角色可存取此路由，例如 @roles_required('admin')"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(f):
    """僅限管理者"""
    return roles_required('admin')(f)


def admin_or_dept_manager_required(f):
    """管理者或部門主管都可以存取（部門主管在頁面內部邏輯裡會再依部門範圍篩選資料）"""
    return roles_required('admin', 'dept_manager')(f)


def admin_or_hr_manager_required(f):
    """
    管理者或人資主管都可以存取，且享有完全一樣、不受範圍限制的完整權限。
    只給「考核期間管理」「考核項目管理」這兩個頁面用——刻意不去動共用的
    admin_required，因為 admin_required 同時也用在操作紀錄、資料庫備份、
    設定提醒、匯出報表這些人資主管不應該有權限的頁面上。
    """
    return roles_required('admin', 'hr_manager')(f)


def admin_hr_or_dept_manager_required(f):
    """
    管理者、人資主管、部門主管都可以存取「人員帳號管理」頁面——人資主管
    享有跟管理者一樣不受部門限制的完整權限，部門主管則在頁面內部邏輯裡
    依自己的部門範圍篩選資料。只給人員帳號管理這一個頁面用，不影響
    admin_or_dept_manager_required 原本掛在週報填寫原則、專案清單管理
    上的行為。
    """
    return roles_required('admin', 'hr_manager', 'dept_manager')(f)


def admin_or_manager_required(f):
    """管理者、部門主管、組別主管都可以存取（各自在頁面內部邏輯裡依部門/組別範圍篩選資料）"""
    return roles_required('admin', 'dept_manager', 'group_manager')(f)


def not_viewer_required(f):
    """
    唯讀角色不可執行的動作（新增/修改/刪除）。這裡也一併擋下人資主管——
    這個裝飾器只掛在 report.py 週報填寫相關的動作上（儲存草稿、送出、
    上傳附件、快速新增專案），人資主管的權限範圍不包含填寫週報。

    另外也檢查這個人自己的「週報」功能開關（Engineer.weekly_report_enabled）
    是否開啟——管理者可以在人員帳號管理頁面針對每個人個別關閉這個功能，
    關閉後這個人不能再填寫/儲存/送出週報。
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if current_user.role in ('viewer', 'hr_manager'):
            abort(403)
        if not current_user.weekly_report_enabled:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def log_action(action, target_type, target_id=None, detail=None):
    """寫入操作紀錄 (Audit Log)"""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        user_name = current_user.name if current_user.is_authenticated else 'system'
    except Exception:
        user_id, user_name = None, 'system'

    log = AuditLog(
        user_id=user_id,
        user_name=user_name,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(log)
    db.session.commit()
