"""
test_report_and_recommendation.py — 由 tests/test_ai_provider_config.py 拆分迁入（Q20），方法体逐字节一致。
"""

import unittest
import os
import tempfile
from unittest.mock import patch

from source.storage import connection as db_connection
from source.reports import renderer as report_renderer


class ReportAndRecommendationTests(unittest.TestCase):
    def test_report_generation_escapes_database_content(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            paper_id = database.insert_paper({
                "arxiv_id": "2601.00001",
                "title": "<script>alert(1)</script>",
                "authors": ["Alice <Admin>"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(paper_id, {
                "tags": ["<tag>"],
                "summary_cn": "<img src=x onerror=alert(1)>",
                "summary_en": "",
                "rating": 5,
                "value_comment": "<b>bad</b>",
                "qa_analysis": "",
            })
            database.update_recommendation_result(paper_id, 95, "<i>rec</i>", "hash-v1")

            with patch.object(report_renderer, "get_personalization_config", return_value={"research_interests": "机器人基础模型\nVLA"}), \
                 patch.object(report_renderer, "get_research_interest_hash", return_value="hash-v1"):
                content, _, _, _ = database.generate_report_content("2026-01-01")

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)
        self.assertIn("&lt;b&gt;bad&lt;/b&gt;", content)
        self.assertIn("&lt;i&gt;rec&lt;/i&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img", content)
        self.assertNotIn("<i>", content)

    def test_recommendation_columns_update_and_report_sorting(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            high_rating_id = database.insert_paper({
                "arxiv_id": "2601.00001",
                "title": "High Rating Low Interest",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00001",
                "pdf_url": "https://arxiv.org/pdf/2601.00001",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            high_interest_id = database.insert_paper({
                "arxiv_id": "2601.00002",
                "title": "Lower Rating High Interest",
                "authors": ["Bob"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00002",
                "pdf_url": "https://arxiv.org/pdf/2601.00002",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(high_rating_id, {
                "tags": ["Robot"],
                "summary_cn": "摘要",
                "summary_en": "",
                "rating": 5,
                "value_comment": "高分",
                "qa_analysis": "",
            })
            database.insert_analysis(high_interest_id, {
                "tags": ["VLA"],
                "summary_cn": "高兴趣中文摘要",
                "summary_en": "",
                "rating": 3,
                "value_comment": "相关",
                "qa_analysis": "",
            })
            self.assertTrue(database.update_recommendation_result(high_rating_id, 20, "弱相关", "hash-v1"))
            self.assertTrue(database.update_recommendation_result(high_interest_id, 95, "强相关", "hash-v1"))

            with patch.object(report_renderer, "get_personalization_config", return_value={"research_interests": "机器人基础模型\nVLA"}), \
                 patch.object(report_renderer, "get_research_interest_hash", return_value="hash-v1"):
                content, _, _, _ = database.generate_report_content("2026-01-01")

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertIn("个性化推荐", content)
        self.assertIn("<strong>研究兴趣:</strong><br>机器人基础模型<br>VLA", content)
        self.assertIn("推荐 95/100", content)
        self.assertIn("★ ★ ★ ☆ ☆", content)
        self.assertNotIn("3★", content)
        self.assertNotIn("5★", content)
        self.assertIn("<strong>中文摘要:</strong> 高兴趣中文摘要", content)
        self.assertIn("<strong>推荐语:</strong> 强相关", content)
        self.assertIn("<strong>评价:</strong> 相关", content)
        self.assertNotIn("高分论文", content)
        self.assertLess(content.index("Lower Rating High Interest"), content.index("High Rating Low Interest"))

    def test_recommendation_candidates_require_existing_analysis(self):
        import database

        original_dir = db_connection.DB_DIR
        original_path = db_connection.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_connection.DB_DIR = tmp
            db_connection.DB_PATH = os.path.join(tmp, "papers.db")
            database.init_db()
            unanalyzed_id = database.insert_paper({
                "arxiv_id": "2601.00003",
                "title": "Unanalyzed",
                "authors": ["Alice"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00003",
                "pdf_url": "https://arxiv.org/pdf/2601.00003",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            analyzed_id = database.insert_paper({
                "arxiv_id": "2601.00004",
                "title": "Analyzed",
                "authors": ["Bob"],
                "abstract": "abstract",
                "categories": ["cs.RO"],
                "primary_category": "cs.RO",
                "url": "https://arxiv.org/abs/2601.00004",
                "pdf_url": "https://arxiv.org/pdf/2601.00004",
                "published_date": "2026-01-01",
                "updated_date": "2026-01-01",
            })
            database.insert_analysis(analyzed_id, {
                "tags": ["VLA"],
                "summary_cn": "摘要",
                "summary_en": "",
                "rating": 3,
                "value_comment": "相关",
                "qa_analysis": "",
            })

            candidates = database.get_papers_for_recommendation(limit=10, date="2026-01-01", interest_hash="hash-v1")

        db_connection.DB_DIR = original_dir
        db_connection.DB_PATH = original_path
        self.assertEqual([p["id"] for p in candidates], [analyzed_id])
        self.assertNotIn(unanalyzed_id, [p["id"] for p in candidates])


if __name__ == "__main__":
    unittest.main()
