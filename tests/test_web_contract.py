import json
import os
import subprocess
import sys
import unittest


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
        actual = json.loads(result.stdout)
        with open("tests/fixtures/web_routes.json", "r", encoding="utf-8") as handle:
            expected = json.load(handle)

        self.assertEqual(len(actual), 86)
        self.assertEqual(actual, expected)


    def test_blueprint_auth_keeps_public_and_protected_behavior(self):
        code = """
import json
import app
import source.web.auth as auth
auth.has_admin_password = lambda: True
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
        actual = json.loads(result.stdout)
        self.assertEqual(actual, {
            "about": 200,
            "settings": 302,
            "settings_location": "/login?next=/settings",
            "analyze": 401,
        })


if __name__ == "__main__":
    unittest.main()
