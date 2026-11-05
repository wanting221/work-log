# -*- coding: utf-8 -*-
"""
update_admin_account.py - 更新既有管理者帳號的工號與密碼（本機/有終端機存取權限時使用）
用途：如果資料庫已經用舊的預設值（工號 A171 或 A001）建立過管理者帳號，
      執行此腳本可將其改為新的工號 A00007 / 密碼 admin123，不需要刪除整個資料庫重建。

執行方式: python update_admin_account.py

注意：若是部署在 Cloud Run 等無法直接下指令的環境，請改用網頁上的
      /internal/fix-admin-account 端點（見 routes/internal.py 說明）。
"""
from app import create_app
from extensions import db
from seed_data import fix_admin_account


def main():
    app = create_app()
    with app.app_context():
        message = fix_admin_account()
        print(message)


if __name__ == '__main__':
    main()
