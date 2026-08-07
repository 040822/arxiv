"""
test_template_safety.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os


class TemplateSafetyTests(unittest.TestCase):
    def test_settings_separates_provider_connections_from_task_inference_options(self):
        with open("templates/settings.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("验证连接并刷新模型列表", html)
        self.assertIn("taskModelListId", html)
        self.assertIn("onTaskProviderChange", html)
        self.assertIn("testAiTaskRoute", html)
        self.assertIn("/api/settings/ai-tasks/${taskKey}/test", html)
        self.assertNotIn('id="add-model"', html)
        self.assertNotIn('id="add-temperature"', html)
        self.assertNotIn("activateProvider(", html)

    def test_paper_pages_share_sanitized_markdown_and_math_renderer(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            paper = f.read()
        with open("templates/paper_chat.html", "r", encoding="utf-8") as f:
            chat = f.read()
        with open("static/rich_text.js", "r", encoding="utf-8") as f:
            renderer = f.read()

        for template in (paper, chat):
            self.assertIn('/static/vendor/marked.min.js', template)
            self.assertIn('/static/vendor/katex.min.js', template)
            self.assertIn('/static/vendor/auto-render.min.js', template)
            self.assertIn('/static/rich_text.js', template)
        self.assertIn('/static/vendor/katex.min.css', paper)
        self.assertNotIn('function renderMd(', paper)
        self.assertNotIn('function renderRich(', chat)
        self.assertIn('global.RichText =', renderer)
        self.assertIn("script,style,iframe,object,embed,link,meta", renderer)
        self.assertIn("startsWith('on')", renderer)
        self.assertIn('javascript:', renderer)

    def test_paper_detail_handles_flexible_qa_headings_and_visible_warnings(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            paper = f.read()
        with open("static/style.css", "r", encoding="utf-8") as f:
            style = f.read()

        self.assertIn("/^###\\s*Q(\\d+)\\s*:\\s*([\\s\\S]*)/i", paper)
        self.assertIn("container.innerHTML = RichText.render(raw)", paper)
        self.assertIn("RichText.renderMath(container)", paper)
        self.assertIn(".action-status.warning", style)

    def test_paper_processing_page_excludes_schedule_stats_and_logs(self):
        with open("templates/tasks.html", "r", encoding="utf-8") as f:
            template = f.read()

        self.assertIn("论文处理 - AI 论文数据库", template)
        self.assertIn("抓取、分析并生成报告", template)
        self.assertNotIn("schedule-enabled", template)
        self.assertNotIn("task-stats", template)
        self.assertNotIn("/api/tasks/logs", template)
        self.assertNotIn("运行中的任务", template)

    def test_settings_has_independent_schedule_tab_with_email_and_logs(self):
        with open("templates/settings.html", "r", encoding="utf-8") as f:
            template = f.read()

        ai_start = template.index('id="tab-ai"')
        schedule_start = template.index('id="tab-schedule"')
        db_start = template.index('id="tab-db"')
        self.assertLess(ai_start, schedule_start)
        self.assertLess(schedule_start, db_start)
        self.assertNotIn('id="grp-email"', template[ai_start:schedule_start])
        self.assertIn('id="grp-email"', template[schedule_start:db_start])
        self.assertIn('id="schedule-task-logs"', template[schedule_start:db_start])
        self.assertIn("固定执行流程", template[schedule_start:db_start])

    def test_public_promo_page_presents_the_complete_research_workflow(self):
        with open("templates/about.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('href="/static/promo.css"', html)
        self.assertIn("从发现论文，", html)
        self.assertIn("到真正读懂。", html)
        for label in ("每日抓取", "AI 筛选", "个性化推荐", "PDF 精读", "主动问答", "报告与备份"):
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertIn('href="/reports"', html)
        self.assertIn('href="/browse"', html)
        self.assertNotIn('href="/vision"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertTrue(os.path.exists("static/promo.css"))

    def test_vision_page_separates_current_capabilities_from_the_roadmap(self):
        with open("templates/vision.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('href="/static/promo.css"', html)
        self.assertIn("从个人阅读工具，", html)
        self.assertIn("进化为实验室科研情报基础设施", html)
        for status in ("已实现", "下一步", "长期愿景"):
            with self.subTest(status=status):
                self.assertIn(status, html)
        for phase in ("实验室方向雷达", "协作型科研工作台", "实验室研究记忆"):
            with self.subTest(phase=phase):
                self.assertIn(phase, html)
        for tool in ("Cool Papers", "Elicit", "ResearchRabbit", "OpenClaw"):
            with self.subTest(tool=tool):
                self.assertIn(tool, html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_home_links_to_public_promo_without_exposing_internal_vision(self):
        with open("templates/index.html", "r", encoding="utf-8") as f:
            index_html = f.read()

        self.assertIn('href="/about"', index_html)
        self.assertIn("项目介绍", index_html)
        for path in (
            "templates/index.html",
            "templates/about.html",
            "templates/browse.html",
            "templates/search.html",
            "templates/reports.html",
            "templates/reading_list.html",
        ):
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as f:
                    self.assertNotIn('href="/vision"', f.read())

    def test_paper_inline_json_handlers_use_single_quoted_attributes(self):
        with open("templates/paper.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("onclick='toggleTodo({{ paper.paper_key | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t.strip() | tojson }})'", html)
        self.assertIn("onclick='removeTag({{ t | tojson }})'", html)
        self.assertNotIn('onclick="toggleTodo({{ paper.arxiv_id | tojson }})"', html)
        self.assertNotIn('onclick="removeTag({{ t | tojson }})"', html)

    def test_report_detail_has_regenerate_action_for_current_date(self):
        with open("templates/report_detail.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("重新生成该日报告", html)
        self.assertIn("const reportDate = {{ report.report_date | tojson }};", html)
        self.assertIn("/api/generate?date=${encodeURIComponent(reportDate)}", html)
        self.assertIn("window.location.reload()", html)

    def test_list_templates_show_spaced_star_rating_without_numeric_suffix(self):
        for path in ("templates/index.html", "templates/browse.html", "templates/search.html", "templates/reading_list.html"):
            with self.subTest(path=path):
                with open(path, "r", encoding="utf-8") as f:
                    html = f.read()
                self.assertIn("{% if not loop.last %} {% endif %}", html)
                self.assertNotIn("{{ paper.rating }}★", html)
        with open("templates/browse.html", "r", encoding="utf-8") as f:
            browse_html = f.read()
        self.assertNotIn("{{ i }}★", browse_html)
        self.assertNotIn("{{ min_rating }}★", browse_html)
        self.assertNotIn("{{ max_rating }}★", browse_html)

    def test_settings_has_network_proxy_tab_and_diagnostics(self):
        with open("templates/settings.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("网络与代理", html)
        self.assertIn("switchTab('network'", html)
        self.assertIn("id=\"tab-network\"", html)
        self.assertIn("testProxy()", html)
        self.assertNotIn("testLlmConnection()", html)
        self.assertNotIn("/api/network/test-llm", html)


if __name__ == "__main__":
    unittest.main()
