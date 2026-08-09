#!/usr/bin/env python3
"""Capture the business-page desktop/mobile visual regression matrix.

Uses the WebDriver HTTP protocol directly, so no Selenium dependency is needed.
Authentication is disabled only inside the spawned Flask process.
"""

import argparse
import base64
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEWPORTS = {"desktop": (1440, 1000), "mobile": (390, 844)}
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

BASE_PAGES = {
    "index": "/",
    "browse": "/browse",
    "search": "/search?q=robot",
    "tasks": "/tasks",
    "settings": "/settings",
    "reports": "/reports",
    "reading-list": "/reading-list",
    "login": "/login",
}


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _request(url, method="GET", payload=None, timeout=10):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with LOCAL_OPENER.open(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _wait_for(url, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with LOCAL_OPENER.open(url, timeout=1):
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}")


def _discover_detail_pages(base_url):
    pages = {}
    try:
        with LOCAL_OPENER.open(base_url + "/", timeout=5) as response:
            index_html = response.read().decode("utf-8", "replace")
        match = re.search(r'href="(/paper/[^"/]+)"', index_html)
        if match:
            pages["paper"] = match.group(1)
            pages["learning"] = match.group(1) + "/chat"
    except (OSError, urllib.error.URLError):
        pass
    try:
        with LOCAL_OPENER.open(base_url + "/reports", timeout=5) as response:
            reports_html = response.read().decode("utf-8", "replace")
        match = re.search(r'href="(/reports/\d{4}-\d{2}-\d{2})"', reports_html)
        if match:
            pages["report-detail"] = match.group(1)
    except (OSError, urllib.error.URLError):
        pass
    return pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="temp/visual-regression")
    parser.add_argument("--label", choices=("before", "after"), default="after")
    parser.add_argument("--base-url", help="Capture an already running server instead of spawning Flask")
    parser.add_argument(
        "--firefox-cli", action="store_true",
        help="Use Firefox native screenshots when geckodriver cannot answer status",
    )
    args = parser.parse_args()

    geckodriver = shutil.which("geckodriver")
    firefox = shutil.which("firefox")
    if not firefox or (not geckodriver and not args.firefox_cli):
        parser.error("Firefox and geckodriver must already be installed")

    server = None
    driver = None
    session_id = None
    try:
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        else:
            port = _free_port()
            base_url = f"http://127.0.0.1:{port}"
            env = dict(os.environ, VISUAL_PORT=str(port), FLASK_SECRET_KEY="visual-regression")
            code = (
                "import os; import source.web.auth as auth; "
                "auth.has_admin_password=lambda: False; "
                "from source.web.application import app; "
                "app.run(host='127.0.0.1', port=int(os.environ['VISUAL_PORT']), "
                "use_reloader=False)"
            )
            server = subprocess.Popen(
                [sys.executable, "-c", code], cwd=ROOT, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _wait_for(base_url + "/")

        if not args.firefox_cli:
            driver_port = _free_port()
            driver = subprocess.Popen(
                [geckodriver, "--port", str(driver_port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            driver_url = f"http://127.0.0.1:{driver_port}"
            _wait_for(driver_url + "/status", timeout=60)
            created = _request(driver_url + "/session", "POST", {
                "capabilities": {"alwaysMatch": {
                    "browserName": "firefox",
                    "moz:firefoxOptions": {"args": ["-headless"]},
                }}
            }, timeout=30)
            session_id = created["value"]["sessionId"]
            session_url = f"{driver_url}/session/{session_id}"

        pages = {**BASE_PAGES, **_discover_detail_pages(base_url)}
        output = ROOT / args.output_dir / args.label
        output.mkdir(parents=True, exist_ok=True)
        for viewport, (width, height) in VIEWPORTS.items():
            if not args.firefox_cli:
                _request(session_url + "/window/rect", "POST", {
                    "x": 0, "y": 0, "width": width, "height": height,
                })
            for name, path in pages.items():
                target = output / f"{name}-{viewport}.png"
                if args.firefox_cli:
                    snap_dir = pathlib.Path.home() / "snap" / "firefox" / "common"
                    snap_dir.mkdir(parents=True, exist_ok=True)
                    temporary = snap_dir / f"arxiv-visual-{os.getpid()}-{name}-{viewport}.png"
                    subprocess.run([
                        firefox, "--headless", "--window-size", f"{width},{height}",
                        "--screenshot", str(temporary), base_url + path,
                    ], check=True, timeout=60, stdout=subprocess.DEVNULL)
                    shutil.copyfile(temporary, target)
                    temporary.unlink(missing_ok=True)
                else:
                    _request(session_url + "/url", "POST", {"url": base_url + path}, timeout=30)
                    time.sleep(0.4)
                    screenshot = _request(session_url + "/screenshot", timeout=30)["value"]
                    target.write_bytes(base64.b64decode(screenshot))
                print(f"captured {name}-{viewport}", flush=True)
    finally:
        if session_id and driver:
            try:
                _request(f"http://127.0.0.1:{driver_port}/session/{session_id}", "DELETE")
            except Exception:
                pass
        for process in (driver, server):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    main()
