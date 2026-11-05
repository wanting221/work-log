# -*- coding: utf-8 -*-
"""
utils/db_info.py - 偵測目前連接的資料庫類型，供開機自我檢查/管理看板顯示使用。
"""
import os
import re


def get_database_info(database_uri, is_sqlite):
    """
    依 SQLALCHEMY_DATABASE_URI 判斷目前實際連接的資料庫類型，
    回傳一份易讀的資訊供畫面顯示、開機時記錄到 log。
    """
    if is_sqlite:
        return {
            'is_sqlite': True,
            'engine_name': 'SQLite',
            'display_label': 'SQLite（暫存檔案，非持久化）',
            'risk_level': 'danger',
        }

    uri_lower = database_uri.lower()
    if uri_lower.startswith('mysql'):
        engine_name = 'MySQL'
    elif uri_lower.startswith('postgresql') or uri_lower.startswith('postgres'):
        engine_name = 'PostgreSQL'
    else:
        engine_name = '未知資料庫類型'

    # 進一步判斷是否為 Cloud SQL 連線（透過 unix_socket 參數帶有 /cloudsql/ 這個路徑特徵）
    is_cloud_sql = '/cloudsql/' in database_uri

    return {
        'is_sqlite': False,
        'engine_name': engine_name,
        'display_label': f'{engine_name}' + ('（Cloud SQL）' if is_cloud_sql else '（外部資料庫）'),
        'short_label': 'Cloud SQL' if is_cloud_sql else engine_name,
        'risk_level': 'success',
    }


def mask_database_url(database_uri):
    """把連線字串裡的密碼遮住，只留下看得出資料庫類型/主機的部分，安全地顯示在畫面上除錯用"""
    # 格式通常是 scheme://user:password@host/dbname?params，把 : 密碼 @ 中間的部分換成 ****
    return re.sub(r'(://[^:/?]+:)[^@]+(@)', r'\1****\2', database_uri)


def get_startup_diagnostics():
    """
    開機診斷資訊：直接讀取容器裡「原始」的環境變數狀態（不是程式最後解析出來套用的值），
    用來排查「明明已經在 Cloud Run 設定了環境變數，畫面卻還是顯示 SQLite」這類問題。
    系統支援兩種設定資料庫連線的方式：
    1. 單一的 DATABASE_URL（完整連線字串）
    2. 拆開的 DB_ENGINE / DB_USER / DB_PASSWORD / DB_NAME / INSTANCE_CONNECTION_NAME
       （更不容易因為密碼裡的特殊符號或手動組字串打錯而出錯，建議優先使用這個方式）
    這裡會兩種都檢查，並回報實際偵測到哪一種、缺少哪些必要變數。
    """
    database_url = os.environ.get('DATABASE_URL')

    separate_vars = {
        'DB_ENGINE': os.environ.get('DB_ENGINE'),
        'DB_USER': os.environ.get('DB_USER'),
        'DB_PASSWORD': os.environ.get('DB_PASSWORD') or os.environ.get('DB_PASS'),
        'DB_NAME': os.environ.get('DB_NAME'),
        'INSTANCE_CONNECTION_NAME': os.environ.get('INSTANCE_CONNECTION_NAME'),
    }
    present_separate_vars = {k: v for k, v in separate_vars.items() if v}
    missing_separate_vars = [k for k, v in separate_vars.items() if not v]

    return {
        'env_var_present': database_url is not None,
        'env_var_masked': mask_database_url(database_url) if database_url else None,
        'using_separate_vars': len(present_separate_vars) > 0,
        'present_separate_vars': list(present_separate_vars.keys()),
        'missing_separate_vars': missing_separate_vars,
    }
