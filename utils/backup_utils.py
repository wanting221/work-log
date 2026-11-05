# -*- coding: utf-8 -*-
"""
utils/backup_utils.py - 備份檔案清單管理工具
實際的匯出/匯入邏輯在 utils/full_backup_utils.py（資料庫無關，SQLite/Cloud SQL 都適用）；
這裡只負責管理 backups/ 資料夾裡的檔案：列出清單、清除過期檔案。
"""
import os
from datetime import timedelta
from utils.timezone_utils import taipei_now, taipei_from_timestamp


def cleanup_old_backups(backup_folder, retention_days):
    """清除超過保留天數的舊備份檔案"""
    if not os.path.exists(backup_folder):
        return
    cutoff = taipei_now() - timedelta(days=retention_days)
    for fname in os.listdir(backup_folder):
        fpath = os.path.join(backup_folder, fname)
        if os.path.isfile(fpath) and fname.endswith('.enc'):
            mtime = taipei_from_timestamp(os.path.getmtime(fpath))
            if mtime < cutoff:
                os.remove(fpath)


def list_backups(backup_folder):
    """列出所有備份檔案，依時間新到舊排序"""
    if not os.path.exists(backup_folder):
        return []
    files = []
    for fname in os.listdir(backup_folder):
        fpath = os.path.join(backup_folder, fname)
        if os.path.isfile(fpath) and fname.endswith('.enc'):
            if fname.startswith('monthly_backup_'):
                backup_type = '每月自動備份'
            elif fname.startswith('full_backup_'):
                backup_type = '手動備份'
            elif fname.startswith('imported_'):
                backup_type = '匯入'
            else:
                backup_type = '備份'
            files.append({
                'filename': fname,
                'path': fpath,
                'size': os.path.getsize(fpath),
                'created_at': taipei_from_timestamp(os.path.getmtime(fpath)),
                'type': backup_type,
            })
    files.sort(key=lambda x: x['created_at'], reverse=True)
    return files
