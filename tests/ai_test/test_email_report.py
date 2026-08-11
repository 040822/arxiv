"""
test_email_report.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import json
import tempfile
from unittest.mock import patch

from source.settings import store as settings_store
from source.settings import (
    get_email_report_config,
    load_settings,
    save_email_report_config,
    update_email_report_status,
)


class EmailReportTests(unittest.TestCase):
    def _sample_email_data(self, config=None):
        return {
            "report_date": "2026-06-17",
            "papers": [],
            "important": [],
            "overview": [],
            "total": 2,
            "analyzed": 1,
            "avg_rating": 3.5,
            "ai_summary": "今日导读",
            "ai_summary_error": "",
            "full_report_url": "",
            "config": config or {"site_url": ""},
            "important_score_threshold": 80,
            "overview_limit": 20,
        }

    def test_email_report_settings_preserve_password_and_mask_get(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")

                loaded = load_settings()
                self.assertIn("email_report", loaded)
                self.assertFalse(loaded["email_report"]["enabled"])

                save_email_report_config({
                    "enabled": True,
                    "smtp_host": "smtp.example.com",
                    "smtp_port": "587",
                    "security": "starttls",
                    "username": "alice@example.com",
                    "password": "secret",
                    "sender": "",
                    "recipients": "bob@example.com; carol@example.com\nbob@example.com",
                    "subject_template": "Daily {date}",
                    "site_url": "https://papers.example.com/",
                })
                save_email_report_config({
                    "enabled": True,
                    "smtp_host": "smtp2.example.com",
                    "smtp_port": "465",
                    "security": "ssl",
                    "username": "alice@example.com",
                    "password": "",
                    "sender": "",
                    "recipients": ["bob@example.com", "carol@example.com"],
                    "subject_template": "Daily {date}",
                    "site_url": "https://papers.example.com/",
                })

                full = get_email_report_config(mask_password=False)
                masked = get_email_report_config(mask_password=True)

            self.assertEqual(full["password"], "secret")
            self.assertEqual(full["recipients"], ["bob@example.com", "carol@example.com"])
            self.assertEqual(full["site_url"], "https://papers.example.com")
            self.assertNotIn("password", masked)
            self.assertEqual(masked["password_masked"], "******")
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_email_report_status_only_success_updates_sent_date(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                load_settings()

                update_email_report_status("success", report_date="2026-06-16")
                update_email_report_status(
                    "error",
                    error="smtp down",
                    report_date="2026-06-17",
                )
                failed = get_email_report_config(mask_password=False)

                update_email_report_status("success")
                manual = get_email_report_config(mask_password=False)

            self.assertEqual(failed["last_status"], "error")
            self.assertEqual(failed["last_error"], "smtp down")
            self.assertEqual(failed["last_sent_report_date"], "2026-06-16")
            self.assertEqual(manual["last_status"], "success")
            self.assertEqual(manual["last_error"], "")
            self.assertEqual(manual["last_sent_report_date"], "2026-06-16")
        finally:
            settings_store.SETTINGS_PATH = original_path

    def test_report_email_html_uses_digest_layout_and_site_links(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        def paper(arxiv_id, score, rating=3):
            return {
                "arxiv_id": arxiv_id,
                "title": f"Paper {arxiv_id}",
                "authors": ["Alice", "Bob"],
                "abstract": f"abstract {arxiv_id}",
                "categories": ["cs.RO"],
                "tags": ["VLA"],
                "rating": rating,
                "summary_cn": f"summary {arxiv_id}",
                "value_comment": f"comment {arxiv_id}",
                "recommendation_reason": f"reason {arxiv_id}",
                "current_recommendation_score": score,
                "analysis_id": 1,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            }

        papers = [paper("2606.00081", 81, 5), paper("2606.00080", 80, 4)]
        papers.extend(paper(f"2606.{i:05d}", 70 - i, 3) for i in range(25))
        report = {
            "report_date": "2026-06-17",
            "content": "<div>legacy web report should not be reused</div>",
            "paper_count": len(papers),
            "analyzed_count": len(papers),
            "avg_rating": 4.5,
        }
        with patch.object(email_content, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(
                report,
                {"site_url": "https://papers.example.com/"},
                ai_summary="今日趋势\n重点方向",
            )
            html = email_report.build_report_email_html(report, email_data=data)

        self.assertEqual([p["arxiv_id"] for p in data["important"]], ["2606.00081"])
        self.assertEqual(len(data["overview"]), 20)
        self.assertIn("今日趋势<br>重点方向", html)
        self.assertIn("重点精读", html)
        self.assertIn("快速速览", html)
        self.assertIn('href="https://papers.example.com/paper/2606.00081"', html)
        self.assertIn('href="https://papers.example.com/reports/2026-06-17"', html)
        self.assertIn("Paper 2606.00080", html)
        self.assertNotIn("legacy web report should not be reused", html)

    def test_report_email_without_recommendations_uses_overview_only(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        papers = [
            {
                "arxiv_id": "2606.00001",
                "title": "High rating without recommendation",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "tags": ["Robot Learning"],
                "rating": 5,
                "summary_cn": "",
                "value_comment": "valuable",
                "recommendation_reason": "",
                "current_recommendation_score": None,
                "analysis_id": 1,
                "url": "https://arxiv.org/abs/2606.00001",
                "pdf_url": "",
            }
        ]
        report = {"report_date": "2026-06-17", "paper_count": 1, "analyzed_count": 1, "avg_rating": 5}

        with patch.object(email_content, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(report, {"site_url": ""}, ai_summary=None, ai_summary_error="boom")
            html = email_report.build_report_email_html(report, email_data=data)

        self.assertEqual(data["important"], [])
        self.assertEqual([p["arxiv_id"] for p in data["overview"]], ["2606.00001"])
        self.assertIn("AI 导读暂不可用", html)
        self.assertIn("今天没有推荐分高于 80", html)
        self.assertIn("High rating without recommendation", html)

    def test_email_thresholds_from_config_override_defaults(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        def paper(arxiv_id, score, rating=3):
            return {
                "arxiv_id": arxiv_id,
                "title": f"Paper {arxiv_id}",
                "authors": ["Alice"],
                "abstract": f"abstract {arxiv_id}",
                "categories": ["cs.RO"],
                "tags": ["VLA"],
                "rating": rating,
                "summary_cn": f"summary {arxiv_id}",
                "value_comment": f"comment {arxiv_id}",
                "recommendation_reason": f"reason {arxiv_id}",
                "current_recommendation_score": score,
                "analysis_id": 1,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            }

        papers = [paper("2606.00001", 70, 5)]
        papers.extend(paper(f"2606.{i:05d}", 60) for i in range(10))
        report = {"report_date": "2026-07-12", "paper_count": len(papers), "analyzed_count": len(papers), "avg_rating": 3.5}

        with patch.object(email_content, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(
                report,
                {"site_url": "", "important_score_threshold": 60, "overview_limit": 5},
                ai_summary=None,
            )
            html = email_report.build_report_email_html(report, email_data=data)

        self.assertEqual(data["important_score_threshold"], 60)
        self.assertEqual(data["overview_limit"], 5)
        # 严格大于阈值：70 入重点，60 不入
        self.assertEqual([p["arxiv_id"] for p in data["important"]], ["2606.00001"])
        self.assertEqual(len(data["overview"]), 5)
        self.assertIn("推荐分 &gt; 60", html)
        self.assertIn("最多 5 篇", html)

    def test_email_threshold_zero_is_not_swallowed_by_falsy_short_circuit(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        def paper(arxiv_id, score):
            return {
                "arxiv_id": arxiv_id,
                "title": f"Paper {arxiv_id}",
                "authors": [],
                "abstract": "",
                "categories": [],
                "tags": [],
                "rating": 0,
                "summary_cn": "",
                "value_comment": "",
                "recommendation_reason": "",
                "current_recommendation_score": score,
                "analysis_id": 1,
                "url": "",
                "pdf_url": "",
            }

        papers = [paper("2606.00001", 5), paper("2606.00002", 0), paper("2606.00003", None)]
        report = {"report_date": "2026-07-12", "paper_count": 3, "analyzed_count": 3, "avg_rating": 0}

        with patch.object(email_content, "_load_report_papers", return_value=papers):
            data = email_report.build_report_email_data(
                report,
                {"site_url": "", "important_score_threshold": 0, "overview_limit": 0},
                ai_summary=None,
            )
            html = email_report.build_report_email_html(report, email_data=data)
            email_report.build_report_email_text(report, email_data=data)

        # 阈值 0 不能被 `or DEFAULT` 短路成默认 80
        self.assertEqual(data["important_score_threshold"], 0)
        self.assertEqual(data["overview_limit"], 0)
        # 严格大于 0：score=5 入重点，score=0 不入，None 不入
        self.assertEqual([p["arxiv_id"] for p in data["important"]], ["2606.00001"])
        # overview_limit=0：没有速览
        self.assertEqual(data["overview"], [])
        self.assertIn("推荐分 &gt; 0", html)
        self.assertIn("最多 0 篇", html)

        # 全部论文 score<=0：重点为空时 text 文案也用阈值 0
        no_important_papers = [paper("2606.00010", 0), paper("2606.00011", None)]
        with patch.object(email_content, "_load_report_papers", return_value=no_important_papers):
            empty_text = email_report.build_report_email_text(
                report,
                {"site_url": "", "important_score_threshold": 0, "overview_limit": 0},
            )
        self.assertIn("今天没有推荐分高于 0 的重点精读论文。", empty_text)

    def test_email_report_config_clamps_threshold_and_limit(self):

        original_path = settings_store.SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings_store.SETTINGS_PATH = os.path.join(tmp, "settings.json")
                load_settings()

                save_email_report_config({
                    "enabled": False,
                    "important_score_threshold": 200,
                    "overview_limit": 9999,
                })
                clamped = get_email_report_config(mask_password=False)

                save_email_report_config({
                    "enabled": False,
                    "important_score_threshold": -1,
                    "overview_limit": -5,
                })
                below = get_email_report_config(mask_password=False)
        finally:
            settings_store.SETTINGS_PATH = original_path

        self.assertEqual(clamped["important_score_threshold"], 100)
        self.assertEqual(clamped["overview_limit"], 50)
        self.assertEqual(below["important_score_threshold"], 0)
        self.assertEqual(below["overview_limit"], 0)

    def test_runtime_normalize_clamps_threshold_and_limit(self):
        from source.reports.email.config import _normalize_runtime_config

        clamped = _normalize_runtime_config({
            "important_score_threshold": 200,
            "overview_limit": 9999,
        })
        below = _normalize_runtime_config({
            "important_score_threshold": -1,
            "overview_limit": -5,
        })
        invalid = _normalize_runtime_config({
            "important_score_threshold": "abc",
            "overview_limit": "",
        })

        self.assertEqual(clamped["important_score_threshold"], 100)
        self.assertEqual(clamped["overview_limit"], 50)
        self.assertEqual(below["important_score_threshold"], 0)
        self.assertEqual(below["overview_limit"], 0)
        self.assertEqual(invalid["important_score_threshold"], 80)
        self.assertEqual(invalid["overview_limit"], 20)

    def test_send_report_email_supports_starttls_ssl_and_plain_smtp(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 2,
            "analyzed_count": 1,
            "avg_rating": 3.5,
        }

        class FakeSMTP:
            def __init__(self, kind, host, port, **kwargs):
                self.kind = kind
                self.host = host
                self.port = port
                self.kwargs = kwargs
                calls.append(("connect", kind, host, port))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self, context=None):
                calls.append(("starttls", self.kind))

            def login(self, username, password):
                calls.append(("login", username, password))

            def send_message(self, message):
                calls.append(("send", self.kind, message["Subject"], message["To"]))

        def smtp_factory(kind):
            def factory(host, port, **kwargs):
                return FakeSMTP(kind, host, port, **kwargs)
            return factory

        base_config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "alice@example.com",
            "password": "secret",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date} - {paper_count}",
            "site_url": "",
        }

        for security, expected_kind, should_starttls in (
            ("starttls", "smtp", True),
            ("ssl", "ssl", False),
            ("none", "smtp", False),
        ):
            calls = []
            config = {**base_config, "security": security, "smtp_port": 465 if security == "ssl" else 587}
            with patch.object(email_transport, "get_proxy_config", return_value={"enabled": False, "http": "", "https": ""}), \
                 patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
                 patch.object(email_transport.smtplib, "SMTP", smtp_factory("smtp")), \
                 patch.object(email_transport.smtplib, "SMTP_SSL", smtp_factory("ssl")):
                result = email_report.send_report_email(config=config, report=report, force=True, record_status=False)

            self.assertEqual(result["status"], "ok")
            self.assertIn(("connect", expected_kind, "smtp.example.com", config["smtp_port"]), calls)
            self.assertEqual(any(call[0] == "starttls" for call in calls), should_starttls)
            self.assertIn(("login", "alice@example.com", "secret"), calls)
            self.assertTrue(any(call[0] == "send" and call[1] == expected_kind for call in calls))

    def test_test_send_does_not_update_automatic_deduplication_date(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        report = {
            "report_date": "2026-06-17",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["reader@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        with patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_service, "_send_message"), \
             patch.object(email_service, "update_email_report_status") as update_status:
            result = email_report.send_report_email(
                report,
                config=config,
                force=True,
                record_status=True,
            )

        self.assertEqual(result["status"], "ok")
        update_status.assert_called_once_with("success", report_date="")

    def test_automatic_send_records_report_date_and_failure_does_not(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        report = {
            "report_date": "2026-06-17",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["reader@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        with patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_service, "_send_message"), \
             patch.object(email_service, "update_email_report_status") as update_status:
            email_report.send_report_email(report, config=config, force=False, record_status=True)

        update_status.assert_called_once_with("success", report_date="2026-06-17")

        with patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_service, "_send_message", side_effect=RuntimeError("smtp down")), \
             patch.object(email_service, "update_email_report_status") as update_status:
            with self.assertRaisesRegex(RuntimeError, "smtp down"):
                email_report.send_report_email(report, config=config, force=False, record_status=True)

        update_status.assert_called_once_with("error", error="smtp down")

    def test_smtp_proxy_url_prefers_https_and_falls_back_to_http(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        self.assertEqual(email_transport._resolve_smtp_proxy_url({
            "enabled": True,
            "http": "http://http-proxy.local:7890",
            "https": "http://https-proxy.local:7890",
        }), "http://https-proxy.local:7890")
        self.assertEqual(email_transport._resolve_smtp_proxy_url({
            "enabled": True,
            "http": "http://http-proxy.local:7890",
            "https": "",
        }), "http://http-proxy.local:7890")
        self.assertEqual(email_transport._resolve_smtp_proxy_url({
            "enabled": False,
            "http": "http://http-proxy.local:7890",
            "https": "http://https-proxy.local:7890",
        }), "")

    def test_proxy_tunnel_sends_connect_request_and_basic_auth(self):
        import base64
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        class FakeSocket:
            def __init__(self):
                self.sent = b""
                self.closed = False

            def sendall(self, data):
                self.sent += data

            def recv(self, size):
                return b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: fake\r\n\r\n"

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        with patch.object(email_transport.socket, "create_connection", return_value=fake_socket) as create_connection:
            sock = email_transport._create_proxy_tunnel(
                "smtp.example.com",
                587,
                30,
                "http://alice:secret@proxy.example.com:8080",
            )

        connect_request = fake_socket.sent.decode("ascii")
        expected_token = base64.b64encode(b"alice:secret").decode("ascii")
        self.assertIs(sock, fake_socket)
        create_connection.assert_called_once_with(("proxy.example.com", 8080), timeout=30)
        self.assertIn("CONNECT smtp.example.com:587 HTTP/1.1", connect_request)
        self.assertIn("Host: smtp.example.com:587", connect_request)
        self.assertIn(f"Proxy-Authorization: Basic {expected_token}", connect_request)
        self.assertFalse(fake_socket.closed)

    def test_send_report_email_routes_security_modes_through_proxy_classes(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 2,
            "analyzed_count": 1,
            "avg_rating": 3.5,
        }
        base_config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "username": "alice@example.com",
            "password": "secret",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        class FakeProxySMTP:
            def __init__(self, kind, host, port, **kwargs):
                self.kind = kind
                calls.append(("connect", kind, host, port, kwargs.get("proxy_url")))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self, context=None):
                calls.append(("starttls", self.kind))

            def login(self, username, password):
                calls.append(("login", self.kind, username, password))

            def send_message(self, message):
                calls.append(("send", self.kind, message["To"]))

        def smtp_factory(kind):
            def factory(host, port, **kwargs):
                return FakeProxySMTP(kind, host, port, **kwargs)
            return factory

        for security, expected_kind, should_starttls in (
            ("starttls", "smtp", True),
            ("ssl", "ssl", False),
            ("none", "smtp", False),
        ):
            calls = []
            config = {**base_config, "security": security, "smtp_port": 465 if security == "ssl" else 587}
            with patch.object(email_transport, "get_proxy_config", return_value={
                "enabled": True,
                "http": "http://http-proxy.local:7890",
                "https": "http://https-proxy.local:7891",
            }), \
                 patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
                 patch.object(email_transport, "_ProxySMTP", smtp_factory("smtp")), \
                 patch.object(email_transport, "_ProxySMTP_SSL", smtp_factory("ssl")):
                result = email_report.send_report_email(config=config, report=report, force=True, record_status=False)

            self.assertEqual(result["status"], "ok")
            self.assertIn(("connect", expected_kind, "smtp.example.com", config["smtp_port"], "http://https-proxy.local:7891"), calls)
            self.assertEqual(any(call[0] == "starttls" for call in calls), should_starttls)
            self.assertTrue(any(call[0] == "send" and call[1] == expected_kind for call in calls))

    def test_proxy_errors_are_readable_and_record_status(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        report = {
            "report_date": "2026-06-17",
            "content": "<div>report</div>",
            "paper_count": 1,
            "analyzed_count": 1,
            "avg_rating": 4,
        }
        config = {
            "enabled": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "security": "none",
            "username": "",
            "password": "",
            "sender": "daily@example.com",
            "recipients": ["bob@example.com"],
            "subject_template": "Daily {date}",
            "site_url": "",
        }

        class RaisingProxySMTP:
            def __init__(self, host, port, **kwargs):
                email_transport._create_proxy_tunnel(host, port, kwargs.get("timeout"), kwargs.get("proxy_url"))

        with patch.object(email_transport, "get_proxy_config", return_value={
            "enabled": True,
            "http": "socks5://127.0.0.1:1080",
            "https": "",
        }), \
             patch.object(email_service, "build_report_email_data", return_value=self._sample_email_data(config)), \
             patch.object(email_transport, "_ProxySMTP", RaisingProxySMTP), \
             patch.object(email_service, "update_email_report_status") as update_status:
            with self.assertRaisesRegex(ValueError, "仅支持 HTTP CONNECT"):
                email_report.send_report_email(config=config, report=report, force=True, record_status=True)

        update_status.assert_called_once()
        self.assertEqual(update_status.call_args.args[0], "error")
        self.assertIn("仅支持 HTTP CONNECT", update_status.call_args.kwargs["error"])

    def test_proxy_tunnel_rejects_non_200_connect_response(self):
        import source.reports.email as email_report
        import source.reports.email.content as email_content
        import source.reports.email.service as email_service
        import source.reports.email.transport as email_transport

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def sendall(self, data):
                pass

            def recv(self, size):
                return b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n"

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        with patch.object(email_transport.socket, "create_connection", return_value=fake_socket):
            with self.assertRaisesRegex(ConnectionError, "407 Proxy Authentication Required"):
                email_transport._create_proxy_tunnel(
                    "smtp.example.com",
                    587,
                    30,
                    "http://proxy.example.com:8080",
                )
        self.assertTrue(fake_socket.closed)


if __name__ == "__main__":
    unittest.main()
