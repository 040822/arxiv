#!/usr/bin/env python3
"""Check data/settings.json for default-template rebuild signals.

用法:
  python scripts/check_settings_guardrail.py

退出码: 0 = 无告警（OK）；1 = 检测到疑似默认模板重建。
不修改任何文件，可挂入 cron 或写进涉及 data/ 的执行计划完成清单。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from source.settings.guardrail import warn_on_settings_rebuild


def main():
    warnings = warn_on_settings_rebuild()
    if not warnings:
        print("OK: data/settings.json 未检测到默认模板重建")
        return 0
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(
        "检测到疑似默认模板重建：请检查 data/settings.json 用户配置，"
        "必要时从备份恢复",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
