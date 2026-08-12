"""
test_permission_matrix.py — full guest/member/admin route policy contract.

For every non-static route and HTTP method, the expected outcome of
enforce_request_policy() is derived from the documented role classification
and asserted for guest, member, admin, and first-login member profiles.
"""

import unittest
from unittest.mock import patch

from . import common
from .common import install_import_stubs, setup_web_test_base, teardown_web_test_base


def _user(role="member", *, must_change=False):
    return {
        "id": 7 if role == "member" else 1,
        "username": "reader" if role == "member" else "admin",
        "display_name": None,
        "role": role,
        "enabled": 1,
        "must_change_password": int(must_change),
        "session_version": 1,
    }


class PermissionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_import_stubs()
        import app as app_module
        import source.web.auth as web_auth
        cls.app_module = app_module
        cls.web_auth = web_auth
        setup_web_test_base()
        cls.app = common._WEB_APP

    @classmethod
    def tearDownClass(cls):
        teardown_web_test_base()

    SPEC_CLASSIFICATIONS = {
        "public": (
            "index", "about_page", "vision_page", "paper_detail", "search",
            "browse", "reports_page", "report_detail_page", "api_papers",
            "api_tags", "api_stats", "login_page", "api_auth_status",
            "api_auth_login",
        ),
        "member": (
            "account_password_page", "paper_chat_page", "reading_list_page",
            "tasks_page", "api_account_password", "api_auth_logout",
            "api_update_paper_analysis", "api_reanalyze_paper", "api_add_todo",
            "api_todo_status", "api_remove_todo", "api_mark_read",
            "api_mark_unread", "api_reading_list", "api_paper_chat_messages",
            "api_paper_chat_send", "api_create_quiz_session",
            "api_get_quiz_session", "api_answer_quiz_question",
            "api_create_socratic_session", "api_reply_socratic_session",
            "api_preview_paper_import", "api_confirm_paper_import",
            "api_progress",
        ),
        "admin": (
            "settings_page", "api_add_paper", "api_add_provider", "api_analyze",
            "api_attach_paper_pdf", "api_audit_events",
            "api_batch_analyze_papers", "api_batch_delete_papers",
            "api_batch_hide_papers", "api_clear_logs", "api_db_info",
            "api_delete_paper", "api_delete_provider", "api_fetch",
            "api_generate", "api_get_ai_tasks", "api_get_ai_usage",
            "api_get_email_report", "api_get_fetch_config",
            "api_get_personalization", "api_get_prompts", "api_get_proxy",
            "api_get_schedule_config", "api_get_webdav_backup",
            "api_hide_paper", "api_list_providers", "api_provider_models",
            "api_provider_presets", "api_recalculate_recommendations",
            "api_run", "api_run_webdav_backup", "api_save_ai_tasks",
            "api_save_concurrency", "api_save_email_report",
            "api_save_fetch_config", "api_save_per_page",
            "api_save_personalization", "api_save_prompts", "api_save_proxy",
            "api_save_schedule_config", "api_save_webdav_backup",
            "api_scheduled_tasks", "api_set_admin_password", "api_task_logs",
            "api_task_stats", "api_test_ai_task_route", "api_test_email_report",
            "api_test_proxy", "api_unhide_paper", "api_update_provider",
            "api_user_delete", "api_user_enabled", "api_user_reset_password",
            "api_users_create", "api_users_list",
        ),
    }

    def test_route_policy_matches_independent_spec_classification(self):
        """Pin the classification from the spec, not from the code sets."""
        for expected, endpoints in self.SPEC_CLASSIFICATIONS.items():
            for endpoint in endpoints:
                with self.subTest(endpoint=endpoint, expected=expected):
                    self.assertEqual(
                        self.web_auth.route_policy(endpoint, "GET"), expected,
                    )
                    if expected != "public":
                        self.assertEqual(
                            self.web_auth.route_policy(endpoint, "POST"), expected,
                        )
        explicit = (
            self.web_auth.PUBLIC_ENDPOINTS | self.web_auth.PUBLIC_GET_ENDPOINTS
            | self.web_auth.MEMBER_ENDPOINTS | self.web_auth.ADMIN_ENDPOINTS
        )
        covered = {
            endpoint for endpoints in self.SPEC_CLASSIFICATIONS.values()
            for endpoint in endpoints
        }
        self.assertEqual(explicit, covered)

    def _expected(self, endpoint, method, user, path):
        """Mirror enforce_request_policy(): derive the outcome from the policy.

        The matrix always submits a valid CSRF token, so unsafe requests that
        reach the CSRF check are expected to pass; CSRF failures are asserted
        in a dedicated test below.
        """
        policy = self.web_auth.route_policy(endpoint, method)
        is_api = path.startswith("/api/")
        if policy == "public":
            if user and user.get("must_change_password") and endpoint not in self.web_auth.PASSWORD_CHANGE_ENDPOINTS:
                return ("password-block", is_api)
            return ("pass", None)
        if not user:
            return ("unauthorized", is_api)
        if policy == "admin" and user.get("role") != "admin":
            return ("forbidden", None)
        if user.get("must_change_password") and endpoint not in self.web_auth.PASSWORD_CHANGE_ENDPOINTS:
            return ("password-block", is_api)
        return ("pass", None)

    def _assert_outcome(self, result, outcome, endpoint, method, role):
        kind, is_api = outcome
        if kind == "pass":
            self.assertIsNone(result, f"{endpoint} {method} {role}: expected pass")
        elif kind == "unauthorized":
            if is_api:
                _, status = result
                self.assertEqual(status, 401, f"{endpoint} {method} {role}")
            else:
                self.assertEqual(getattr(result, "status_code", None), 302,
                                 f"{endpoint} {method} {role}: expected login redirect")
        elif kind == "forbidden":
            if isinstance(result, tuple):
                _, status = result
                self.assertEqual(status, 403, f"{endpoint} {method} {role}")
            else:
                self.assertEqual(getattr(result, "status_code", None), 403,
                                 f"{endpoint} {method} {role}")
        elif kind == "password-block":
            if isinstance(result, tuple):
                response, status = result
                self.assertEqual(status, 403, f"{endpoint} {method} {role}")
                self.assertTrue(response.get("password_change_required"),
                                f"{endpoint} {method} {role}")
            else:
                self.assertEqual(getattr(result, "status_code", None), 302,
                                 f"{endpoint} {method} {role}")
        else:  # pragma: no cover
            self.fail(f"unknown outcome {kind}")

    def test_full_route_role_matrix(self):
        roles = {
            "guest": None,
            "member": _user("member"),
            "admin": _user("admin"),
            "member_first_login": _user("member", must_change=True),
        }
        rules = [
            rule for rule in self.app.url_map.iter_rules() if rule.endpoint != "static"
        ]
        self.assertGreater(len(rules), 90)
        for rule in rules:
            endpoint = rule.endpoint.rsplit(".", 1)[-1]
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                path = rule.rule
                for role_name, user in roles.items():
                    with self.subTest(endpoint=endpoint, method=method, role=role_name):
                        kwargs = {"method": method, "path": path}
                        if method not in self.web_auth.SAFE_METHODS:
                            kwargs["headers"] = {"X-CSRF-Token": "expected"}
                        with self.app.test_request_context(**kwargs):
                            self.web_auth.session["csrf_token"] = "expected"
                            with patch.object(self.web_auth, "_endpoint_name", return_value=endpoint), \
                                 patch.object(self.web_auth, "current_user", return_value=user):
                                result = self.web_auth.enforce_request_policy()
                            self._assert_outcome(
                                result,
                                self._expected(endpoint, method, user, path),
                                endpoint, method, role_name,
                            )

    def test_guest_page_routes_redirect_to_login_with_next(self):
        for path, expected_location in (
            ("/settings", "/login?next=/settings"),
            ("/tasks", "/login?next=/tasks"),
            ("/account/password", "/login?next=/account/password"),
        ):
            with self.subTest(path=path), self.app.test_request_context(path):
                with patch.object(self.web_auth, "current_user", return_value=None):
                    result = self.web_auth.enforce_request_policy()
                self.assertEqual(result.status_code, 302)
                self.assertEqual(result.headers["Location"], expected_location)

    def test_unsafe_requests_without_valid_csrf_are_rejected_for_every_role(self):
        cases = (
            ("api_auth_login", "POST", "/api/auth/login", None),
            ("api_auth_login", "POST", "/api/auth/login", _user("member")),
            ("api_update_paper_analysis", "PUT", "/api/paper/p1/analysis", _user("member")),
            ("api_fetch", "POST", "/api/fetch", _user("admin")),
            ("api_delete_paper", "DELETE", "/api/paper/p1", _user("admin")),
        )
        for endpoint, method, path, user in cases:
            with self.subTest(endpoint=endpoint, user=(user or {}).get("role")):
                with self.app.test_request_context(method=method, path=path):
                    with patch.object(self.web_auth, "_endpoint_name", return_value=endpoint), \
                         patch.object(self.web_auth, "current_user", return_value=user):
                        result = self.web_auth.enforce_request_policy()
                    response, status = result
                    self.assertEqual(status, 403)
                    self.assertEqual(response["message"], "CSRF token 无效")


if __name__ == "__main__":
    unittest.main()
