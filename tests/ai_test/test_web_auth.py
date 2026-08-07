"""
test_web_auth.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import importlib
from unittest.mock import patch

from . import common
from .common import (
    FakeRequest,
    install_import_stubs,
    setup_web_test_base,
    teardown_web_test_base,
)


_WEB_APP = None
web_auth = None
web_pages = None

class AuthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        global web_auth, web_pages, _WEB_APP
        web_auth = importlib.import_module("source.web.auth")
        web_pages = importlib.import_module("source.web.pages")
        cls.web_auth = web_auth
        cls.app_module = app_module
        setup_web_test_base()
        global _WEB_APP
        _WEB_APP = common._WEB_APP

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def test_auth_blocks_protected_api_when_not_logged_in(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.request = FakeRequest(
                {},
                endpoint="api_save_concurrency",
                method="POST",
                path="/api/settings/concurrency",
            )

            with patch.object(web_auth, "has_admin_password", return_value=True):
                result, status = web_auth.require_auth_for_protected_routes()

            self.assertEqual(status, 401)
            self.assertTrue(result["auth_required"])

    def test_auth_login_sets_permanent_session_and_token(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.request = FakeRequest({"password": "secret"})

            with patch.object(web_auth, "verify_admin_password", return_value=True), \
                 patch.object(web_auth, "get_admin_password", return_value="hash-v1"):
                result = web_auth.api_auth_login()

            self.assertEqual(result["status"], "ok")
            self.assertTrue(web_auth.session.permanent)
            self.assertTrue(web_auth.session["admin_authenticated"])
            self.assertEqual(
                web_auth.session["admin_auth_token"],
                web_auth._admin_auth_token("hash-v1"),
            )

    def test_auth_rejects_legacy_session_without_password_token(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.session["admin_authenticated"] = True

            with patch.object(web_auth, "has_admin_password", return_value=True), \
                 patch.object(web_auth, "get_admin_password", return_value="hash-v1"):
                self.assertFalse(web_auth.is_authenticated())

    def test_auth_rejects_session_after_password_hash_changes(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.session["admin_authenticated"] = True
            web_auth.session["admin_auth_token"] = web_auth._admin_auth_token("hash-v1")

            with patch.object(web_auth, "has_admin_password", return_value=True), \
                 patch.object(web_auth, "get_admin_password", return_value="hash-v2"):
                self.assertFalse(web_auth.is_authenticated())

    def test_set_admin_password_refreshes_current_session_token(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.session.permanent = True
            web_auth.session["admin_authenticated"] = True
            web_auth.session["admin_auth_token"] = web_auth._admin_auth_token("old-hash")
            web_auth.request = FakeRequest({
                "current_password": "old-secret",
                "new_password": "new-secret",
            })

            with patch.object(web_auth, "has_admin_password", return_value=True), \
                 patch.object(web_auth, "verify_admin_password", return_value=True), \
                 patch.object(web_auth, "set_admin_password") as set_password, \
                 patch.object(web_auth, "get_admin_password", return_value="new-hash"):
                result = web_auth.api_set_admin_password()

            self.assertEqual(result["status"], "ok")
            set_password.assert_called_once_with("new-secret")
            self.assertTrue(web_auth.session.permanent)
            self.assertTrue(web_auth.session["admin_authenticated"])
            self.assertEqual(
                web_auth.session["admin_auth_token"],
                web_auth._admin_auth_token("new-hash"),
            )

    def test_clear_admin_password_requires_current_password(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.request = FakeRequest({"current_password": "wrong"})

            with patch.object(web_auth, "has_admin_password", return_value=True), \
                 patch.object(web_auth, "verify_admin_password", return_value=False), \
                 patch.object(web_auth, "set_admin_password") as set_password:
                result, status = web_auth.api_clear_admin_password()

            self.assertEqual(status, 403)
            self.assertEqual(result["status"], "error")
            set_password.assert_not_called()

    def test_todo_add_remove_are_public_even_when_password_enabled(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()

            for endpoint, method in (("api_add_todo", "POST"), ("api_remove_todo", "DELETE")):
                with self.subTest(endpoint=endpoint):
                    web_auth.request = FakeRequest(
                        {},
                        endpoint=endpoint,
                        method=method,
                        path="/api/paper/2601.00001/todo",
                    )
                    with patch.object(web_auth, "has_admin_password", return_value=True):
                        self.assertIsNone(web_auth.require_auth_for_protected_routes())

    def test_promo_pages_are_public_even_when_password_enabled(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()

            for endpoint, path in (("about_page", "/about"), ("vision_page", "/vision")):
                with self.subTest(endpoint=endpoint):
                    web_auth.request = FakeRequest(
                        endpoint=endpoint,
                        method="GET",
                        path=path,
                    )
                    with patch.object(web_auth, "has_admin_password", return_value=True):
                        self.assertIsNone(web_auth.require_auth_for_protected_routes())

    def test_promo_page_routes_render_their_public_templates(self):
        app_module = self.app_module

        for view_name, template_name in (
            ("about_page", "about.html"),
            ("vision_page", "vision.html"),
        ):
            with self.subTest(view_name=view_name), \
                 patch.object(web_pages, "render_template", return_value=template_name) as render:
                result = getattr(web_pages, view_name)()

                self.assertEqual(result, template_name)
                render.assert_called_once_with(template_name)

    def test_todo_read_status_changes_still_require_login(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.request = FakeRequest(
                {},
                endpoint="api_mark_read",
                method="POST",
                path="/api/paper/2601.00001/todo/read",
            )

            with patch.object(web_auth, "has_admin_password", return_value=True):
                result, status = web_auth.require_auth_for_protected_routes()

            self.assertEqual(status, 401)
            self.assertTrue(result["auth_required"])

    def test_learning_write_api_requires_login_when_password_enabled(self):
        web_auth = self.web_auth
        with _WEB_APP.test_request_context():
            web_auth.session.clear()
            web_auth.request = FakeRequest(
                {"message": "请解释这篇论文"},
                endpoint="api_paper_chat_send",
                method="POST",
                path="/api/paper/2601.00001/chat/messages",
            )

            with patch.object(web_auth, "has_admin_password", return_value=True):
                result, status = web_auth.require_auth_for_protected_routes()

            self.assertEqual(status, 401)
            self.assertTrue(result["auth_required"])


if __name__ == "__main__":
    unittest.main()
