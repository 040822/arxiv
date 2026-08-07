#!/usr/bin/env python3
"""统一测试入口:unittest + pytest(随机顺序)+ 模块乱序验证。

用法:
  python scripts/run_all_tests.py                 # 标准流程,失败即停
  python scripts/run_all_tests.py --quick         # 只跑 pytest 一次(日常快速验证)
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
    return sorted(p.stem for p in TESTS_DIR.glob("test_*.py"))


def run_command(cmd, label, verbose=False):
    print(f"[{label}] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    summary = "OK"
    count = ""
    if result.returncode != 0:
        summary = "FAILED"
        lines = result.stdout.splitlines() + result.stderr.splitlines()
        failed = []
        for line in lines:
            match = re.match(r"^(?:ERROR|FAIL): (\S+)", line.strip())
            if match and match.group(1) not in failed:
                failed.append(match.group(1))
        print(f"[{label}] FAILED ({len(failed)} failed):")
        for name in failed:
            print(f"    {name}")
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
    print(f"[{label}] {summary}{count}")
    return result.returncode


def run_unittest(modules=None, label="unittest", verbose=False):
    if modules is None:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    else:
        cmd = [sys.executable, "-m", "unittest"] + ["tests." + m for m in modules]
    return run_command(cmd, label, verbose)


def run_pytest(seed=None, label="pytest", verbose=False):
    cmd = [sys.executable, "-m", "pytest"]
    if seed is not None:
        cmd.append(f"--randomly-seed={seed}")
    cmd += ["-q", "-p", "no:cacheprovider", "tests/"]
    return run_command(cmd, label, verbose)


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
    parser.add_argument("--shuffle-seed", type=int, default=42, help="乱序种子(默认 42)")
    parser.add_argument("--verbose", action="store_true", help="失败时输出完整 traceback")
    args = parser.parse_args()

    steps = []
    if args.quick:
        steps = [("pytest", lambda: run_pytest(args.shuffle_seed, verbose=args.verbose))]
    else:
        steps = [
            ("unittest", lambda: run_unittest(verbose=args.verbose)),
            ("pytest", lambda: run_pytest(args.shuffle_seed, verbose=args.verbose)),
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
