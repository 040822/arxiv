import json
import os
import subprocess
import sys
import unittest


def _parse_json_stdout(stdout):
    """解析子进程 stdout 中的 JSON，容忍第三方库（如 PyMuPDF 1.28+）在
    import 时向 stdout 打印的弃用警告。"""
    for marker in ("[", "{"):
        start = stdout.find(marker)
        if start != -1:
            return json.loads(stdout[start:])
    raise ValueError(f"stdout 中未找到 JSON: {stdout[:200]!r}")


class WebContractTests(unittest.TestCase):
    def test_root_app_exposes_the_complete_http_route_contract(self):
        code = """
import json
import app
rows = [
    {"rule": rule.rule, "methods": sorted(rule.methods - {"HEAD", "OPTIONS"})}
    for rule in app.app.url_map.iter_rules()
    if rule.endpoint != "static"
]
rows.sort(key=lambda item: (item["rule"], item["methods"]))
print(json.dumps(rows, ensure_ascii=False))
"""
        env = dict(os.environ)
        env["FLASK_SECRET_KEY"] = "route-contract-test"
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        actual = _parse_json_stdout(result.stdout)
        with open("tests/fixtures/web_routes.json", "r", encoding="utf-8") as handle:
            expected = json.load(handle)

        self.assertEqual(len(actual), 93)
        self.assertEqual(actual, expected)


    def test_blueprint_auth_keeps_public_and_protected_behavior(self):
        code = """
import json
import app
client = app.app.test_client()
settings_response = client.get("/settings")
print(json.dumps({
    "about": client.get("/about").status_code,
    "settings": settings_response.status_code,
    "settings_location": settings_response.headers.get("Location"),
    "analyze": client.post("/api/analyze").status_code,
}))
"""
        env = dict(os.environ)
        env["FLASK_SECRET_KEY"] = "route-contract-test"
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        actual = _parse_json_stdout(result.stdout)
        self.assertEqual(actual, {
            "about": 200,
            "settings": 302,
            "settings_location": "/login?next=/settings",
            "analyze": 401,
        })


if __name__ == "__main__":
    unittest.main()
