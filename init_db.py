# -*- coding: utf-8 -*-
"""
init_db.py - 資料庫初始化
執行方式: python init_db.py
會建立所有資料表，並建立一個可登入的管理者帳號（不會建立任何假工程師/專案/組別資料）
"""
from app import create_app
from extensions import db
from seed_data import seed_demo_data


def init_database():
    app = create_app()
    with app.app_context():
        print('正在建立資料表...')
        db.create_all()
        seed_demo_data(verbose=True)


if __name__ == '__main__':
    init_database()
