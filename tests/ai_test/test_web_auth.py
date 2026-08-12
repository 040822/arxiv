"""Authentication, authorization, session revocation, and CSRF contracts."""

import importlib
import unittest
from unittest.mock import patch

from flask import render_template

from . import common
from .common import install_import_stubs, setup_web_test_base, teardown_web_test_base


_WEB_APP = None
web_auth = None
web_pages = None


def _user(role="member", *, must_change=False, version=1):
    return {
        "id": 7 if role == "member" else 1,
        "username": "reader" if role == "member" else "admin",
        "display_name": None,
        "role": role,
        "enabled": 1,
        "must_change_password": int(must_change),
        "session_version": version,
    }


class AuthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        global web_auth, web_pages, _WEB_APP
        web_auth = importlib.import_module("source.web.auth")
        web_pages = importlib.import_module("source.web.pages")
        cls.app_module = app_module
        setup_web_test_base()
        _WEB_APP = common._WEB_APP

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    def setUp(self):
        web_auth._login_failures.clear()

    def test_guest_member_api_is_401_and_member_admin_api_is_403(self):
        with _WEB_APP.test_request_context("/api/reading-list"):
            with patch.object(web_auth, "current_user", return_value=None):
                _, status = web_auth.enforce_request_policy()
            self.assertEqual(status, 401)

        with _WEB_APP.test_request_context("/api/settings/ai-tasks"):
            with patch.object(web_auth, "current_user", return_value=_user()):
                _, status = web_auth.enforce_request_policy()
            self.assertEqual(status, 403)

    def test_public_paper_and_hidden_browse_routes_need_no_session(self):
        for endpoint in ("paper_detail", "browse", "search"):
            with self.subTest(endpoint=endpoint), _WEB_APP.test_request_context("/browse"):
                with patch.object(web_auth, "_endpoint_name", return_value=endpoint), \
                     patch.object(web_auth, "current_user", return_value=None):
                    self.assertIsNone(web_auth.enforce_request_policy())

    def test_login_accepts_normalized_username_and_stores_user_version(self):
        member = _user()
        with _WEB_APP.test_request_context(
            "/api/auth/login", method="POST", json={"username": " Reader ", "password": "secret"}
        ):
            web_auth.session["csrf_token"] = "csrf"
            with patch.object(web_auth, "authenticate_user", return_value=member) as authenticate:
                result = web_auth.api_auth_login()

            authenticate.assert_called_once_with("reader", "secret")
            self.assertEqual(result["status"], "ok")
            self.assertTrue(web_auth.session.permanent)
            self.assertEqual(web_auth.session["user_id"], member["id"])
            self.assertEqual(web_auth.session["session_version"], 1)
            self.assertNotIn("password_hash", result["user"])

    def test_session_version_enabled_and_existence_are_checked_each_request(self):
        with _WEB_APP.test_request_context("/"):
            web_auth.session.update(user_id=7, session_version=1)
            with patch.object(web_auth, "get_user_by_id", return_value=_user(version=2)):
                self.assertIsNone(web_auth.current_user())
            self.assertNotIn("user_id", web_auth.session)

        with _WEB_APP.test_request_context("/"):
            web_auth.session.update(user_id=7, session_version=1)
            disabled = {**_user(), "enabled": 0}
            with patch.object(web_auth, "get_user_by_id", return_value=disabled):
                self.assertIsNone(web_auth.current_user())

    def test_unsafe_requests_require_session_backed_csrf_including_login(self):
        with _WEB_APP.test_request_context("/api/auth/login", method="POST", json={}):
            web_auth.session["csrf_token"] = "expected"
            with patch.object(web_auth, "current_user", return_value=None):
                _, status = web_auth.enforce_request_policy()
            self.assertEqual(status, 403)

        with _WEB_APP.test_request_context(
            "/api/auth/login", method="POST", json={}, headers={"X-CSRF-Token": "expected"}
        ):
            web_auth.session["csrf_token"] = "expected"
            with patch.object(web_auth, "current_user", return_value=None):
                self.assertIsNone(web_auth.enforce_request_policy())

    def test_first_login_is_blocked_except_password_change_and_logout(self):
        with _WEB_APP.test_request_context("/api/reading-list"):
            with patch.object(web_auth, "current_user", return_value=_user(must_change=True)):
                response, status = web_auth.enforce_request_policy()
            self.assertEqual(status, 403)
            self.assertTrue(response["password_change_required"])

    def test_login_failure_message_is_uniform_and_rate_limited_by_ip_and_username(self):
        with _WEB_APP.test_request_context(
            "/api/auth/login", method="POST", json={"username": "missing", "password": "wrong"}
        ):
            with patch.object(web_auth, "authenticate_user", return_value=None):
                for _ in range(5):
                    result, status = web_auth.api_auth_login()
                    self.assertEqual((result["message"], status), ("用户名或密码错误", 403))
                result, status, headers = web_auth.api_auth_login()
            self.assertEqual(status, 429)
            self.assertEqual(headers["Retry-After"], str(result["retry_after"]))

    def test_route_policy_is_fail_closed_and_explicitly_exposes_member_capabilities(self):
        self.assertEqual(web_auth.route_policy("api_paper_chat_send", "POST"), "member")
        self.assertEqual(web_auth.route_policy("api_update_paper_analysis", "PUT"), "member")
        self.assertEqual(web_auth.route_policy("api_attach_paper_pdf", "POST"), "admin")
        self.assertEqual(web_auth.route_policy("future_unclassified_write", "POST"), "admin")

    def test_every_current_non_static_route_has_an_explicit_policy(self):
        endpoints = {
            rule.endpoint.rsplit(".", 1)[-1]
            for rule in _WEB_APP.url_map.iter_rules()
            if rule.endpoint != "static"
        }
        explicit = (
            web_auth.PUBLIC_ENDPOINTS | web_auth.PUBLIC_GET_ENDPOINTS
            | web_auth.MEMBER_ENDPOINTS | web_auth.ADMIN_ENDPOINTS
        )
        self.assertEqual(endpoints, explicit)


    def test_tasks_template_exposes_import_to_members_but_admin_jobs_only_to_admin(self):
        with _WEB_APP.test_request_context():
            member = render_template("tasks.html", is_authenticated=True, is_admin=False)
            admin = render_template("tasks.html", is_authenticated=True, is_admin=True)
        self.assertIn('id="import-source-url"', member)
        self.assertNotIn('id="fetch-category"', member)
        self.assertNotIn('href="/settings"', member)
        self.assertIn('id="fetch-category"', admin)
        self.assertIn('href="/settings"', admin)

    def test_paper_template_splits_member_and_admin_controls(self):
        paper = {
            "paper_key": "2601.00001", "source_type": "arxiv", "arxiv_id": "2601.00001",
            "title": "Test Paper", "authors": ["Author"], "abstract": "Abstract",
            "categories": ["cs.RO"], "primary_category": "cs.RO",
            "url": "https://arxiv.org/abs/2601.00001", "pdf_url": None,
            "pdf_local_path": None, "published_date": "2026-01-01", "venue": None, "hidden": 0,
        }
        analysis = {"rating": 4, "tags": ["VLA"], "summary_cn": "摘要", "qa_analysis": "### Q1: 问题\n回答", "value_comment": "评价"}
        with _WEB_APP.test_request_context():
            guest = render_template("paper.html", paper=paper, analysis=analysis, is_authenticated=False, is_admin=False)
            member = render_template("paper.html", paper=paper, analysis=analysis, is_authenticated=True, is_admin=False)
            admin = render_template("paper.html", paper=paper, analysis=analysis, is_authenticated=True, is_admin=True)

        self.assertNotIn('id="todo-toggle"', guest)
        self.assertIn('id="todo-toggle"', member)
        self.assertIn('id="reanalyze-btn"', member)
        self.assertNotIn('class="danger-zone"', member)
        self.assertIn('class="danger-zone"', admin)
        self.assertNotIn("toggleSummaryEdit", member.split("<script", 1)[0])


if __name__ == "__main__":
    unittest.main()
