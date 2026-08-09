"""Shared web-test setup/teardown lifecycle contracts."""

import unittest

from flask import has_app_context, jsonify as flask_jsonify

from . import common


class WebTestBaseLifecycleTests(unittest.TestCase):
    def tearDown(self):
        if common._WEB_APP_CONTEXT is not None:
            common.teardown_web_test_base()

    def test_setup_rejects_leaks_and_teardown_restores_jsonify(self):
        common.setup_web_test_base()
        modules = (
            common.web_auth,
            common.web_pages,
            common.web_papers_api,
            common.web_providers_api,
            common.web_settings_api,
            common.web_tasks_api,
            common.web_learning_api,
        )

        with self.assertRaisesRegex(RuntimeError, "context"):
            common.setup_web_test_base()
        for module in modules:
            self.assertIs(module.jsonify, common._plain_jsonify)

        common.teardown_web_test_base()

        self.assertIsNone(common._WEB_APP_CONTEXT)
        self.assertIsNone(common._WEB_APP)
        self.assertEqual(common._ORIGINAL_JSONIFY, {})
        self.assertFalse(has_app_context())
        for module in modules:
            self.assertIs(module.jsonify, flask_jsonify)


if __name__ == "__main__":
    unittest.main()
