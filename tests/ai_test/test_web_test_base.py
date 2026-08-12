"""Shared web-test setup/teardown lifecycle contracts."""

import unittest

from flask import has_app_context, jsonify as flask_jsonify

from . import common


class WebTestBaseLifecycleTests(unittest.TestCase):
    def tearDown(self):
        if common._WEB_APP_CONTEXT is not None:
            common.teardown_web_test_base()

    def test_setup_is_nestable_and_last_teardown_restores_jsonify(self):
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

        # 嵌套 setup 复用同一 context（随机顺序下多测试类交叠使用）
        common.setup_web_test_base()
        self.assertIsNotNone(common._WEB_APP_CONTEXT)
        self.assertEqual(common._WEB_BASE_DEPTH, 2)

        # 内层 teardown 不还原，外层仍可用
        common.teardown_web_test_base()
        self.assertIsNotNone(common._WEB_APP_CONTEXT)
        self.assertEqual(common._WEB_BASE_DEPTH, 1)
        for module in modules:
            self.assertIs(module.jsonify, common._plain_jsonify)

        common.teardown_web_test_base()

        self.assertIsNone(common._WEB_APP_CONTEXT)
        self.assertIsNone(common._WEB_APP)
        self.assertEqual(common._WEB_BASE_DEPTH, 0)
        self.assertEqual(common._ORIGINAL_JSONIFY, {})
        self.assertFalse(has_app_context())
        for module in modules:
            self.assertIs(module.jsonify, flask_jsonify)

    def test_teardown_without_setup_is_safe(self):
        common.teardown_web_test_base()
        self.assertIsNone(common._WEB_APP_CONTEXT)


if __name__ == "__main__":
    unittest.main()
