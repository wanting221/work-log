# -*- coding: utf-8 -*-
"""
routes/guidelines.py - 週報填寫原則
所有登入的使用者都能檢視這份說明；管理者可以編輯任何部門的版本（或通用預設版本），
部門主管只能編輯自己部門的專屬版本。

檢視邏輯：依「目前登入者自己的部門」決定顯示哪個版本——有專屬版本就顯示專屬版本，
否則顯示通用預設版本。這樣同一頁面，不同部門的人登入看到的內容可以不一樣。

儲存方式：沿用 SystemSetting（key-value），通用預設版本用固定 key
'weekly_report_guidelines'；部門專屬版本用 'wrg_dept_{部門名稱}' 這個 key。
部門名稱長度有防呆截斷，確保 key 不會超過資料表欄位長度上限。
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from extensions import db
from models import SystemSetting, Engineer
from utils.decorators import log_action, admin_or_dept_manager_required

guidelines_bp = Blueprint('guidelines', __name__)

DEFAULT_KEY = 'weekly_report_guidelines'
DEPT_KEY_PREFIX = 'wrg_dept_'
# SystemSetting.key 欄位上限 50 字，扣掉 prefix 長度後，部門名稱最多能用的字元數，
# 這裡防呆截斷避免部門名稱較長時組出超過欄位長度上限的 key
MAX_DEPT_NAME_IN_KEY = 50 - len(DEPT_KEY_PREFIX)

DEFAULT_GUIDELINES = """1. 具體、誠實：寫清楚解決了什麼問題，而不是「持續開發」這種空話。
2. 記得掛專案標籤：每一項工作都選對應的專案，不然這週的努力在「依專案檢視」會找不到。
3. 下週計劃寫明確：下週計劃會自動帶入下週的已執行草稿，寫具體一點，幫未來的自己省力氣。
4. 卡關別悶著：進度卡住、需要跨組支援，直接用「附註／需要討論」留言，狀態設「待討論」。
5. 誠實回報：上週說要做的事沒做完，直接寫清楚卡在哪裡，比裝作沒事更重要。"""


def _dept_key(department):
    """組出某部門專屬版本的 SystemSetting key（防呆截斷過長的部門名稱）"""
    return DEPT_KEY_PREFIX + department[:MAX_DEPT_NAME_IN_KEY]


@guidelines_bp.route('/guidelines')
@login_required
def index():
    if current_user.role == 'hr_manager':
        # 人資主管的權限範圍不包含週報填寫原則，這裡明確擋下，不能只靠
        # 側邊欄不顯示連結——直接改網址還是要能正確擋下來
        abort(403)
    all_departments = sorted({
        d[0] for d in Engineer.query.with_entities(Engineer.department).distinct().all() if d[0]
    })

    # ---- 檢視邏輯：依目前登入者自己的部門決定顯示哪個版本 ----
    viewer_department = current_user.department or ''
    dept_setting = SystemSetting.query.get(_dept_key(viewer_department)) if viewer_department else None
    if dept_setting and dept_setting.value:
        display_content = dept_setting.value
        display_is_department_specific = True
    else:
        default_setting = SystemSetting.query.get(DEFAULT_KEY)
        display_content = default_setting.value if default_setting and default_setting.value else DEFAULT_GUIDELINES
        display_is_department_specific = False

    # ---- 編輯邏輯：管理者可選擇要編輯哪個部門的版本／通用版本；
    #      部門主管固定只能編輯自己部門的版本 ----
    can_edit = current_user.role in ('admin', 'dept_manager')
    if current_user.role == 'dept_manager':
        edit_department = current_user.department or ''
    else:
        edit_department = request.args.get('edit_department', '').strip()

    edit_content = ''
    if can_edit:
        if edit_department:
            edit_setting = SystemSetting.query.get(_dept_key(edit_department))
            edit_content = edit_setting.value if edit_setting and edit_setting.value else ''
        else:
            default_setting = SystemSetting.query.get(DEFAULT_KEY)
            edit_content = default_setting.value if default_setting and default_setting.value else DEFAULT_GUIDELINES

    return render_template(
        'guidelines.html',
        content=display_content, display_is_department_specific=display_is_department_specific,
        viewer_department=viewer_department,
        can_edit=can_edit, edit_department=edit_department, edit_content=edit_content,
        all_departments=all_departments,
    )


@guidelines_bp.route('/guidelines/update', methods=['POST'])
@login_required
@admin_or_dept_manager_required
def update():
    content = request.form.get('content', '').strip()

    if current_user.role == 'dept_manager':
        edit_department = current_user.department or ''
        if not edit_department:
            flash('你的帳號尚未設定部門，請聯繫管理者設定部門後才能編輯專屬版本', 'danger')
            return redirect(url_for('guidelines.index'))
    else:
        edit_department = request.form.get('edit_department', '').strip()

    key = _dept_key(edit_department) if edit_department else DEFAULT_KEY
    setting = SystemSetting.query.get(key)
    if setting:
        setting.value = content
    else:
        db.session.add(SystemSetting(key=key, value=content))
    db.session.commit()

    scope_label = f'「{edit_department}」部門專屬版本' if edit_department else '通用預設版本'
    log_action('update', 'SystemSetting', None, f'更新週報填寫原則（{scope_label}）')
    flash(f'已更新週報填寫原則（{scope_label}）', 'success')

    if current_user.role == 'admin':
        return redirect(url_for('guidelines.index', edit_department=edit_department))
    return redirect(url_for('guidelines.index'))
