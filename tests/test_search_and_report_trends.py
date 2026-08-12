import os
import tempfile
import unittest
from unittest.mock import patch

from source.reports import generate_report_content
from source.storage import (
    get_report_trends, hide_paper, init_db, insert_analysis, insert_paper,
    search_papers, update_analysis, update_recommendation_result,
)
from source.storage import connection as db_connection
from source.reports import renderer as report_renderer


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_dir = db_connection.DB_DIR
        self.original_db_path = db_connection.DB_PATH
        db_connection.DB_DIR = self.tmp.name
        db_connection.DB_PATH = os.path.join(self.tmp.name, "papers.db")
        init_db()

    def tearDown(self):
        db_connection.DB_DIR = self.original_db_dir
        db_connection.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def add_paper(self, arxiv_id, title, abstract="abstract", published_date="2026-07-10", tags=None,
                  summary_cn="中文摘要", qa_analysis=""):
        paper_id = insert_paper({
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": ["Alice"],
            "abstract": abstract,
            "categories": ["cs.RO"],
            "primary_category": "cs.RO",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            "published_date": published_date,
            "updated_date": published_date,
        })
        insert_analysis(paper_id, {
            "tags": tags or [],
            "summary_cn": summary_cn,
            "summary_en": "",
            "rating": 3,
            "value_comment": "评价",
            "qa_analysis": qa_analysis,
        })
        return paper_id


class SearchPapersTests(DatabaseTestCase):
    def test_multiple_terms_may_match_different_fields_but_all_are_required(self):
        self.add_paper("2607.00001", "Dexterous Policy", tags=["World Model"])
        self.add_paper("2607.00002", "Dexterous Baseline", tags=["Imitation Learning"])

        results = search_papers("dexterous world", limit=50)

        self.assertEqual([paper["arxiv_id"] for paper in results], ["2607.00001"])

    def test_malformed_paper_json_does_not_break_search_results(self):
        paper_id = self.add_paper("2607.00014", "Corrupt Metadata Paper")
        with db_connection.get_connection() as conn:
            conn.execute("UPDATE papers SET authors = ? WHERE id = ?", ("[broken", paper_id))
            conn.execute("UPDATE analysis SET tags = ? WHERE paper_id = ?", ("{}", paper_id))

        with self.assertLogs("source.storage.row_mapping", level="WARNING"):
            results = search_papers("corrupt", limit=50)

        self.assertEqual(results[0]["authors"], [])
        self.assertEqual(results[0]["tags"], [])
        self.assertEqual(results[0]["categories"], ["cs.RO"])

    def test_results_are_ranked_by_weighted_matching_field_before_rating(self):
        title_id = self.add_paper("2607.00003", "World Model for Robots", tags=["Planning"])
        abstract_id = self.add_paper(
            "2607.00004",
            "Highly Rated Baseline",
            abstract="A world model baseline.",
            tags=["Planning"],
        )
        update_analysis(title_id, {"rating": 1})
        update_analysis(abstract_id, {"rating": 5})

        results = search_papers("world", limit=50)

        self.assertEqual([paper["arxiv_id"] for paper in results], ["2607.00003", "2607.00004"])
        self.assertGreater(results[0]["search_score"], results[1]["search_score"])

    def test_like_wildcards_are_matched_as_literal_characters(self):
        self.add_paper("2607.00005", "Policy 100%_Safe")
        self.add_paper("2607.00006", "Policy 100X Safe")

        results = search_papers("100%_", limit=50)

        self.assertEqual([paper["arxiv_id"] for paper in results], ["2607.00005"])

    def test_versioned_arxiv_id_keeps_exact_lookup_behavior(self):
        self.add_paper("2607.12345", "Unrelated title")

        results = search_papers("2607.12345v3", limit=50)

        self.assertEqual([paper["arxiv_id"] for paper in results], ["2607.12345"])

    def test_duplicate_terms_do_not_inflate_relevance_score(self):
        self.add_paper("2607.00008", "World Model")

        single = search_papers("world", limit=50)
        duplicate = search_papers("world WORLD world", limit=50)

        self.assertEqual(duplicate[0]["search_score"], single[0]["search_score"])

    def test_relevance_ties_fall_back_to_rating_date_and_arxiv_id(self):
        cases = [
            ("2607.00010", "2026-07-01", 5),
            ("2607.00011", "2026-07-10", 4),
            ("2607.00012", "2026-07-09", 4),
            ("2607.00009", "2026-07-10", 4),
        ]
        for arxiv_id, published_date, rating in cases:
            paper_id = self.add_paper(arxiv_id, "World Model", published_date=published_date)
            update_analysis(paper_id, {"rating": rating})

        results = search_papers("world", limit=50)

        self.assertEqual(
            [paper["arxiv_id"] for paper in results],
            ["2607.00010", "2607.00009", "2607.00011", "2607.00012"],
        )

    def test_hidden_papers_remain_publicly_searchable(self):
        self.add_paper("2607.00013", "Hidden World Model")
        hide_paper("2607.00013")

        self.assertEqual(search_papers("world", limit=50)[0]["arxiv_id"], "2607.00013")
        self.assertEqual(search_papers("2607.00013v2", limit=50)[0]["arxiv_id"], "2607.00013")

    def test_oversized_queries_are_rejected_with_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "200"):
            search_papers("x" * 201)

        with self.assertRaisesRegex(ValueError, "10"):
            search_papers("one two three four five six seven eight nine ten eleven")

    def test_search_result_builds_highlight_segments_without_marking_database_html_safe(self):
        from source.web import pages as web_pages

        prepared = web_pages._prepare_search_result({
            "title": "<script>alert(1)</script> World Model",
            "abstract": "A safe robotics abstract.",
            "summary_cn": "中文摘要",
            "qa_analysis": "",
            "tags": ["World Model"],
        }, ["world"])
        with open("templates/search.html", "r", encoding="utf-8") as template_file:
            template = template_file.read()

        matched = [part["text"] for part in prepared["title_highlight"] if part["match"]]
        plain = "".join(part["text"] for part in prepared["title_highlight"] if not part["match"])
        self.assertEqual(matched, ["World"])
        self.assertIn("<script>alert(1)</script>", plain)
        self.assertIn('<mark class="search-highlight">{{ part.text }}</mark>', template)
        self.assertNotIn("| safe", template)


class ReportTrendTests(DatabaseTestCase):
    def test_trends_use_the_latest_seven_data_dates_and_identify_new_tags(self):
        dates = [
            "2026-06-29",
            "2026-07-01",
            "2026-07-02",
            "2026-07-04",
            "2026-07-05",
            "2026-07-08",
            "2026-07-09",
            "2026-07-10",
        ]
        for index, published_date in enumerate(dates):
            tags = ["Robot"]
            if published_date == "2026-07-10":
                tags.append("VLA")
            self.add_paper(
                f"2607.{index + 100:05d}",
                f"Paper {index}",
                published_date=published_date,
                tags=tags,
            )

        trends = get_report_trends("2026-07-10", interest_hash="hash-v1")

        self.assertEqual(trends["dates"], dates[1:])
        robot = next(item for item in trends["tag_series"] if item["tag"] == "Robot")
        self.assertEqual(robot["counts"], [1, 1, 1, 1, 1, 1, 1])
        self.assertEqual(trends["new_tags"], [{"tag": "VLA", "count": 1}])
        self.assertTrue(trends["has_tag_history"])

    def test_recommendation_distribution_uses_only_the_current_interest_hash(self):
        scores = [(20, "hash-v1"), (70, "hash-v1"), (90, "hash-v1"), (95, "old-hash"), (None, "")]
        for index, (score, interest_hash) in enumerate(scores):
            paper_id = self.add_paper(f"2607.{index + 200:05d}", f"Paper {index}", tags=["Robot"])
            if score is not None:
                update_recommendation_result(paper_id, score, "reason", interest_hash)

        trends = get_report_trends("2026-07-10", interest_hash="hash-v1")
        recommendation = trends["recommendation"]

        self.assertEqual([bucket["count"] for bucket in recommendation["buckets"]], [1, 1, 1])
        self.assertEqual(recommendation["scored"], 3)
        self.assertEqual(recommendation["unscored"], 2)

    def test_web_report_renders_trends_and_escapes_trend_labels(self):
        self.add_paper("2607.00300", "Earlier", published_date="2026-07-09", tags=["Robot"])
        paper_id = self.add_paper(
            "2607.00301",
            "Current",
            published_date="2026-07-10",
            tags=["Robot", "<img src=x onerror=alert(1)>"],
        )
        update_recommendation_result(paper_id, 88, "reason", "hash-v1")

        with patch.object(report_renderer, "get_personalization_config", return_value={"research_interests": "robotics"}), \
             patch.object(report_renderer, "get_research_interest_hash", return_value="hash-v1"):
            content, _, _, _ = generate_report_content("2026-07-10")

        self.assertIn("近 7 个有数据日趋势", content)
        self.assertIn("标签走势", content)
        self.assertIn("新标签", content)
        self.assertIn("推荐分分布", content)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)
        self.assertNotIn("<img src=x", content)

    def test_tag_trends_are_limited_to_the_five_highest_totals(self):
        for index in range(6):
            self.add_paper(
                f"2607.{index + 400:05d}",
                f"Paper {index}",
                tags=[f"Tag{tag_index}" for tag_index in range(index + 1)],
            )

        trends = get_report_trends("2026-07-10", top_tags=5)

        self.assertEqual([item["tag"] for item in trends["tag_series"]], ["Tag0", "Tag1", "Tag2", "Tag3", "Tag4"])
        self.assertEqual([item["total"] for item in trends["tag_series"]], [6, 5, 4, 3, 2])

    def test_single_data_date_has_no_new_tag_baseline(self):
        self.add_paper("2607.00500", "Only day", tags=["Robot"])

        trends = get_report_trends("2026-07-10")

        self.assertFalse(trends["has_tag_history"])
        self.assertEqual(trends["new_tags"], [])

    def test_missing_research_interest_disables_recommendation_distribution(self):
        self.add_paper("2607.00501", "No interest", tags=["Robot"])

        trends = get_report_trends("2026-07-10", interest_hash="")

        self.assertFalse(trends["recommendation"]["enabled"])
        self.assertEqual(trends["recommendation"]["scored"], 0)
        self.assertEqual(trends["recommendation"]["unscored"], 1)



if __name__ == "__main__":
    unittest.main()
