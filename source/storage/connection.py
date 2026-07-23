"""SQLite connection implementation."""

import html as html_module
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from config import DB_DIR, DB_PATH


def get_connection():
    """获取数据库连接。
    
    配置：
    - WAL 模式：支持读写并发，提升 Web 服务性能
    - 外键约束：启用级联删除，保证数据完整性
    - Row 工厂：返回字典风格的行对象
    
    返回：
        sqlite3.Connection: 配置好的数据库连接
    """
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 写前日志模式，提升并发性能
    conn.execute("PRAGMA foreign_keys=ON")    # 启用外键约束
    return conn
