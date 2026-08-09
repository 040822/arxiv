#!/usr/bin/env python3
"""统一测试入口:unittest + pytest(随机顺序)+ 模块乱序验证。

用法:
  python scripts/run_all_tests.py                 # 标准流程,失败即停
  python scripts/run_all_tests.py --quick         # 只跑 pytest 一次(日常快速验证)
  python scripts/run_all_tests.py --cov           # pytest 阶段附带覆盖率表格(term-missing)
  python scripts/run_all_tests.py --shuffle-seed N   # 覆盖乱序种子(默认 42)
  python scripts/run_all_tests.py --verbose       # 失败时输出完整 traceback
"""

import argparse
import random
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
SHUFFLE_RUNS = 3


def test_modules():
    """返回可被 `python -m unittest tests.<module>` 加载的模块点路径列表。"""
    modules = sorted(p.stem for p in TESTS_DIR.glob("test_*.py"))
    ai_dir = TESTS_DIR / "ai_test"
    modules += sorted(f"ai_test.{p.stem}" for p in ai_dir.glob("test_*.py"))
    return modules


def extract_coverage_table(stdout):
    """从 pytest --cov 输出中提取覆盖率表格(第一条长分隔线到末尾)。"""
    lines = stdout.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("-") and len(line) > 10),
        None,
    )
    if start is None:
        return None
    return "\n".join(lines[start:])


def extract_failed_tests(stdout, stderr):
    """提取 unittest 与 pytest 摘要中的失败测试名称。"""
    failed = []
    patterns = (
        re.compile(r"^(?:ERROR|FAIL):\s+(\S+)"),
        re.compile(r"^(?:FAILED|ERROR)\s+(\S+)"),
    )
    for line in stdout.splitlines() + stderr.splitlines():
        stripped = line.strip()
        for pattern in patterns:
            match = pattern.match(stripped)
            if not match:
                continue
            name = match.group(1)
            if name not in failed:
                failed.append(name)
            break
    return failed


def run_command(cmd, label, verbose=False, cov=False):
    print(f"[{label}] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    summary = "OK"
    count = ""
    if result.returncode != 0:
        summary = "FAILED"
        failed = extract_failed_tests(result.stdout, result.stderr)
        if failed:
            print(f"[{label}] FAILED ({len(failed)} failed):")
            for name in failed:
                print(f"    {name}")
        else:
            print(f"[{label}] FAILED (unable to parse failing test names)")
        if verbose:
            print(result.stdout)
            print(result.stderr)
    else:
        match = re.search(r"Ran (\d+) tests", result.stdout)
        if match:
            count = f" ({match.group(1)} tests)"
        else:
            match = re.search(r"(\d+) passed", result.stdout)
            if match:
                count = f" ({match.group(1)} passed)"
        if cov:
            table = extract_coverage_table(result.stdout)
            if table:
                print(table)
    print(f"[{label}] {summary}{count}")
    return result.returncode


def run_unittest(modules=None, label="unittest", verbose=False):
    if modules is None:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    else:
        cmd = [sys.executable, "-m", "unittest"] + ["tests." + m for m in modules]
    return run_command(cmd, label, verbose)


def run_pytest(seed=None, label="pytest", verbose=False, cov=False):
    cmd = [sys.executable, "-m", "pytest"]
    if seed is not None:
        cmd.append(f"--randomly-seed={seed}")
    if cov:
        cmd += ["--cov=.", "--cov-report=term-missing"]
    cmd += ["-q", "-p", "no:cacheprovider", "tests/"]
    return run_command(cmd, label, verbose, cov=cov)


def run_shuffles(seed, verbose=False):
    modules = test_modules()
    failures = 0
    for i in range(SHUFFLE_RUNS):
        rng = random.Random(seed + i)
        order = modules[:]
        rng.shuffle(order)
        code = run_unittest(order, label=f"shuffle-{i + 1}", verbose=verbose)
        failures += 1 if code != 0 else 0
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="只跑 pytest 一次")
    parser.add_argument("--cov", action="store_true", help="pytest 阶段附带覆盖率表格(term-missing)")
    parser.add_argument("--shuffle-seed", type=int, default=42, help="乱序种子(默认 42)")
    parser.add_argument("--verbose", action="store_true", help="失败时输出完整 traceback")
    args = parser.parse_args()

    steps = []
    if args.quick:
        steps = [("pytest", lambda: run_pytest(args.shuffle_seed, verbose=args.verbose, cov=args.cov))]
    else:
        steps = [
            ("unittest", lambda: run_unittest(verbose=args.verbose)),
            ("pytest", lambda: run_pytest(args.shuffle_seed, verbose=args.verbose, cov=args.cov)),
            ("shuffle", lambda: run_shuffles(args.shuffle_seed, verbose=args.verbose)),
        ]

    executed = []
    for i, (name, fn) in enumerate(steps, 1):
        print(f"--- [{i}/{len(steps)}] {name}")
        executed.append(name)
        code = fn()
        if code != 0:
            print(f"--- [{i}/{len(steps)}] {name} FAILED, 中止后续阶段")
            sys.exit(1)
    print(f"ALL PASS ({'/'.join(executed)})")


if __name__ == "__main__":
    main()
