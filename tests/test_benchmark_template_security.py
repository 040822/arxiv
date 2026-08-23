"""Regression tests for the benchmark admin page's DOM rendering boundary."""

from pathlib import Path
import re
import unittest


TEMPLATE = Path(__file__).parents[1] / "templates" / "benchmark.html"
UNTRUSTED_PAYLOADS = (
    '<img src=x onerror="window.__benchmark_xss=1">',
    '"quoted attribute" <script>alert("xss")</script>',
)


class BenchmarkTemplateSecurityTests(unittest.TestCase):
    def test_external_payloads_are_rendered_without_unsafe_html_sinks(self):
        template = TEMPLATE.read_text(encoding="utf-8")

        # These payloads model titles, questions, labels, warnings and API errors.
        # The page must never concatenate such values into an HTML parser sink.
        for payload in UNTRUSTED_PAYLOADS:
            self.assertNotIn(payload, template)
        self.assertNotIn("insertAdjacentHTML", template)
        self.assertNotRegex(template, r"\.innerHTML\s*=")
        self.assertNotRegex(template, r"\.innerHTML\s*\+")

    def test_dynamic_dom_content_uses_text_content(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("textContent", template)

    def test_human_override_uses_report_response_and_case_ids(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("item.response_id", template)
        self.assertIn("item.case_id", template)
        self.assertNotIn("run.run.responses", template)


if __name__ == "__main__":
    unittest.main()
