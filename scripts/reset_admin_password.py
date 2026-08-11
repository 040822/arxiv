#!/usr/bin/env python3
"""Reset the single-administrator password from the local server shell."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from source.settings import reset_admin_password


def main():
    result = reset_admin_password()
    print("管理密码已重置；旧登录会话将失效。")
    print(f"新管理密码（仅显示本次，请登录后尽快修改）：{result.generated_password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
