# -*- coding: utf-8 -*-
"""
repair_db.py - 資料庫健康檢查與修復工具
執行方式: python repair_db.py

用途：當系統出現「登入後 500 內部錯誤」等異常時，用來檢查 SQLite 資料庫
是否有殘留的鎖定檔案 (journal/wal/shm) 或損毀情形，並嘗試自動修復。

執行前請務必先停止所有 python app.py 程序！
"""
import os
import sqlite3
import shutil
from datetime import datetime
from utils.timezone_utils import taipei_now

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'weekly_report.db')


def check_and_repair():
    print('=' * 60)
    print('資料庫健康檢查工具')
    print('=' * 60)

    if not os.path.exists(DB_PATH):
        print(f'找不到資料庫檔案: {DB_PATH}')
        print('請先執行 python init_db.py 建立資料庫')
        return

    # 1. 檢查是否有殘留的鎖定檔案
    journal = DB_PATH + '-journal'
    wal = DB_PATH + '-wal'
    shm = DB_PATH + '-shm'
    stale_files = [f for f in [journal, wal, shm] if os.path.exists(f)]
    if stale_files:
        print(f'發現殘留鎖定檔案: {[os.path.basename(f) for f in stale_files]}')
        print('（這通常是伺服器在寫入資料庫途中被強制中斷所留下，例如先前 debug reloader 誤觸重啟的情況）')
    else:
        print('未發現殘留鎖定檔案，狀況正常。')

    # 2. 執行 SQLite 完整性檢查
    print('\n正在執行資料庫完整性檢查 (PRAGMA integrity_check)...')
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()
        cursor.execute('PRAGMA integrity_check')
        result = cursor.fetchone()[0]
        conn.close()
        if result == 'ok':
            print('✓ 資料庫完整性檢查通過，資料庫本身沒有損毀。')
        else:
            print(f'✗ 資料庫完整性檢查失敗: {result}')
            print('建議從備份還原（見 backups/ 資料夾，或使用系統內「資料庫備份」頁面還原）。')
            return
    except sqlite3.OperationalError as e:
        print(f'✗ 無法開啟資料庫進行檢查: {e}')
        print('可能原因：仍有 python app.py 程序在執行中導致鎖定。請先完全停止所有伺服器程序後再試一次。')
        return

    # 3. 清除殘留的鎖定檔案（在確認資料庫完整性正常後才清除，較安全）
    if stale_files:
        confirm = input('\n是否要清除上述殘留鎖定檔案？建議先備份 instance/ 資料夾。 (y/N): ')
        if confirm.lower() == 'y':
            backup_dir = os.path.join(os.path.dirname(__file__), 'instance',
                                       f'_repair_backup_{taipei_now().strftime("%Y%m%d_%H%M%S")}')
            os.makedirs(backup_dir, exist_ok=True)
            for f in stale_files:
                shutil.copy2(f, backup_dir)
                os.remove(f)
            print(f'已清除殘留檔案（原始檔已備份至 {backup_dir}）')
        else:
            print('已略過清除動作。')

    print('\n檢查完成。請重新啟動系統 (python app.py) 並再次嘗試登入。')
    print('=' * 60)


if __name__ == '__main__':
    check_and_repair()
