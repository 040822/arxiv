import unittest

from source.storage.row_mapping import parse_paper_row


class PaperRowMappingTests(unittest.TestCase):
    def test_parse_paper_row_converts_json_list_fields_without_mutating_input(self):
        row = {
            "id": 7,
            "authors": '["Alice", "Bob"]',
            "categories": '["cs.RO"]',
            "tags": '["VLA"]',
        }

        parsed = parse_paper_row(row)

        self.assertEqual(parsed["authors"], ["Alice", "Bob"])
        self.assertEqual(parsed["categories"], ["cs.RO"])
        self.assertEqual(parsed["tags"], ["VLA"])
        self.assertEqual(row["authors"], '["Alice", "Bob"]')

    def test_parse_paper_row_tolerates_malformed_and_non_list_json(self):
        row = {
            "arxiv_id": "2607.00001",
            "authors": "[not-json",
            "categories": '{"primary": "cs.RO"}',
            "tags": "",
        }

        with self.assertLogs("source.storage.row_mapping", level="WARNING") as logs:
            parsed = parse_paper_row(row)

        self.assertEqual(parsed["authors"], [])
        self.assertEqual(parsed["categories"], [])
        self.assertEqual(parsed["tags"], [])
        self.assertEqual(len(logs.output), 2)


if __name__ == "__main__":
    unittest.main()
