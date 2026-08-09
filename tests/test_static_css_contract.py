"""Static stylesheet loading and template/CSS boundary contracts."""

import json
import os
import pathlib
import re
import subprocess
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "templates"
CSS_ROOT = REPO_ROOT / "static" / "css"
BASE = ["/static/css/core.css", "/static/css/components.css"]
EXPECTED = {
    "index.html": [*BASE, "/static/css/pages/library.css"],
    "browse.html": [*BASE, "/static/css/pages/library.css"],
    "search.html": [*BASE, "/static/css/pages/library.css"],
    "reading_list.html": [*BASE, "/static/css/pages/library.css"],
    "paper.html": [*BASE, "/static/css/rich-text.css", "/static/css/pages/paper.css"],
    "paper_chat.html": [*BASE, "/static/css/rich-text.css", "/static/css/pages/learning.css"],
    "tasks.html": [*BASE, "/static/css/pages/tasks.css"],
    "settings.html": [*BASE, "/static/css/pages/settings.css"],
    "reports.html": [*BASE, "/static/css/pages/reports.css"],
    "report_detail.html": [*BASE, "/static/css/pages/reports.css"],
    "login.html": [*BASE, "/static/css/pages/auth.css"],
}


class StaticCssContractTests(unittest.TestCase):
    def test_business_templates_load_their_exact_css_matrix(self):
        pattern = re.compile(r'<link\s+rel="stylesheet"\s+href="([^"]+\.css)">')
        for name, expected in EXPECTED.items():
            with self.subTest(template=name):
                content = (TEMPLATES / name).read_text(encoding="utf-8")
                actual = [href for href in pattern.findall(content) if "/static/css/" in href]
                self.assertEqual(actual, expected)

    def test_legacy_aggregator_is_gone_and_unreferenced(self):
        self.assertFalse((REPO_ROOT / "static" / "style.css").exists())
        for template in TEMPLATES.glob("*.html"):
            with self.subTest(template=template.name):
                self.assertNotIn("/static/style.css", template.read_text(encoding="utf-8"))

    def test_every_css_resource_in_the_matrix_is_served(self):
        urls = sorted({url for values in EXPECTED.values() for url in values})
        code = """
import json
from source.web.application import app
client = app.test_client()
print(json.dumps({url: [client.get(url).status_code, client.get(url).content_type] for url in %r}))
""" % urls
        env = dict(os.environ, FLASK_SECRET_KEY="css-contract")
        result = subprocess.run(
            [sys.executable, "-c", code], check=True, capture_output=True, text=True, env=env,
        )
        statuses = json.loads(result.stdout.strip().splitlines()[-1])
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(statuses[url][0], 200)
                self.assertIn("text/css", statuses[url][1])

    def test_each_template_class_is_defined_by_a_stylesheet_it_loads(self):
        matrices = {
            **EXPECTED,
            "about.html": ["/static/promo.css"],
            "vision.html": ["/static/promo.css"],
        }
        dynamic_tokens = {"cat", "current_status", "endif", "i", "if", "not", "r", "tag", "tag_name"}
        for template_name, urls in matrices.items():
            content = (TEMPLATES / template_name).read_text(encoding="utf-8")
            css = "\n".join(
                (REPO_ROOT / url.removeprefix("/")).read_text(encoding="utf-8")
                for url in urls
            )
            used = set()
            for raw in re.findall(r"class=[\"\x27\`]([^\"\x27\`]+)", content):
                for token in raw.split():
                    if re.fullmatch(r"[A-Za-z_][\w-]*", token) and token not in dynamic_tokens:
                        used.add(token)
            for class_name in sorted(used):
                with self.subTest(template=template_name, class_name=class_name):
                    self.assertRegex(css, rf"\.{re.escape(class_name)}(?![-_a-zA-Z0-9])")


if __name__ == "__main__":
    unittest.main()
