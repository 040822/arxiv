#!/usr/bin/env python3
"""Reset the database-backed admin password from the local server shell."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from source.storage import init_db, reset_admin_password_local


def main():
    init_db()
    temporary_password = reset_admin_password_local()
    print("管理密码已重置；旧登录会话将失效。")
    print(f"新管理密码（仅显示本次，请登录后尽快修改）：{temporary_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
