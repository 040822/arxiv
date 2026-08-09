import io
import json
import os
import sqlite3
import tempfile
from unittest.mock import patch

import fitz
import unittest

from source.imports import PaperImportError, preview_import, validate_public_http_url
from source.documents import remove_paper_pdf_files, store_uploaded_pdf


class FakeResponse:
    def __init__(self, payload=None, text="", headers=None, status_code=200):
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class PaperImportPreviewTests(unittest.TestCase):
    def test_openreview_url_is_resolved_to_editable_metadata(self):
        def fake_get(url, **kwargs):
            self.assertIn("api2.openreview.net/notes", url)
            return FakeResponse({
                "notes": [{
                    "id": "or-note-1",
                    "forum": "or-note-1",
                    "content": {
                        "title": {"value": "OpenReview Paper"},
                        "authors": {"value": ["Alice", "Bob"]},
                        "abstract": {"value": "An abstract."},
                        "venue": {"value": "ICLR 2026"},
                    },
                }]
            })

        draft = preview_import(
            source_url="https://openreview.net/forum?id=or-note-1",
            http_get=fake_get,
        )

        self.assertEqual(draft["source_type"], "openreview")
        self.assertEqual(draft["source_id"], "or-note-1")
        self.assertEqual(draft["title"], "OpenReview Paper")
        self.assertEqual(draft["authors"], ["Alice", "Bob"])
        self.assertEqual(draft["venue"], "ICLR 2026")
        self.assertEqual(draft["pdf_url"], "https://openreview.net/pdf?id=or-note-1")

    def test_doi_url_uses_crossref_metadata(self):
        def fake_get(url, **kwargs):
            self.assertIn("api.crossref.org/works/10.1234%2Frobot.42", url)
            return FakeResponse({
                "message": {
                    "DOI": "10.1234/robot.42",
                    "title": ["Journal Robot Paper"],
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                        {"name": "Robotics Group"},
                    ],
                    "abstract": "<jats:p>Structured abstract.</jats:p>",
                    "container-title": ["Robotics Journal"],
                    "published": {"date-parts": [[2025, 6, 2]]},
                    "URL": "https://doi.org/10.1234/robot.42",
                    "link": [{"URL": "https://publisher.example/paper.pdf", "content-type": "application/pdf"}],
                }
            })

        draft = preview_import(
            source_url="https://doi.org/10.1234/Robot.42",
            http_get=fake_get,
        )

        self.assertEqual(draft["source_type"], "doi")
        self.assertEqual(draft["source_id"], "10.1234/robot.42")
        self.assertEqual(draft["title"], "Journal Robot Paper")
        self.assertEqual(draft["authors"], ["Alice Smith", "Robotics Group"])
        self.assertEqual(draft["abstract"], "Structured abstract.")
        self.assertEqual(draft["published_date"], "2025-06-02")
        self.assertEqual(draft["pdf_url"], "https://publisher.example/paper.pdf")

    def test_doi_is_checked_before_arxiv_shaped_numbers(self):
        def fake_get(url, **kwargs):
            return FakeResponse({
                "message": {
                    "title": ["Numeric DOI Paper"],
                    "author": [],
                    "URL": "https://doi.org/10.1234/2025.12345",
                }
            })

        with patch("source.ingestion.lookup_paper_by_id") as arxiv_lookup:
            draft = preview_import("https://doi.org/10.1234/2025.12345", http_get=fake_get)
        self.assertEqual(draft["source_type"], "doi")
        self.assertEqual(draft["title"], "Numeric DOI Paper")
        arxiv_lookup.assert_not_called()

    def test_publisher_url_with_arxiv_shaped_number_stays_a_web_source(self):
        page = '<meta name="citation_title" content="Publisher Paper">'

        def fake_get(url, **kwargs):
            return FakeResponse(text=page)

        with patch("source.ingestion.lookup_paper_by_id") as arxiv_lookup:
            draft = preview_import(
                "https://publisher.example/papers/2025.12345",
                http_get=fake_get,
            )

        self.assertEqual(draft["source_type"], "web")
        self.assertEqual(draft["title"], "Publisher Paper")
        arxiv_lookup.assert_not_called()

    def test_publisher_pdf_with_arxiv_shaped_number_stays_a_pdf_link(self):
        with patch("source.ingestion.lookup_paper_by_id") as arxiv_lookup:
            draft = preview_import(
                "https://publisher.example/files/2025.12345.pdf",
            )

        self.assertEqual(draft["source_type"], "web")
        self.assertEqual(
            draft["pdf_url"],
            "https://publisher.example/files/2025.12345.pdf",
        )
        arxiv_lookup.assert_not_called()

    def test_generic_scholarly_page_reads_declared_citation_metadata(self):
        page = """
        <html><head>
          <meta name="citation_title" content="Conference Paper">
          <meta name="citation_author" content="Alice">
          <meta name="citation_author" content="Bob">
          <meta name="citation_abstract" content="Page abstract.">
          <meta name="citation_conference_title" content="RSS 2025">
          <meta name="citation_publication_date" content="2025/07/01">
          <meta name="citation_pdf_url" content="/files/paper.pdf">
        </head></html>
        """

        def fake_get(url, **kwargs):
            return FakeResponse(text=page, headers={"Content-Type": "text/html"})

        draft = preview_import(
            source_url="https://conference.example/papers/42",
            http_get=fake_get,
        )

        self.assertEqual(draft["source_type"], "web")
        self.assertEqual(draft["title"], "Conference Paper")
        self.assertEqual(draft["authors"], ["Alice", "Bob"])
        self.assertEqual(draft["abstract"], "Page abstract.")
        self.assertEqual(draft["venue"], "RSS 2025")
        self.assertEqual(draft["published_date"], "2025-07-01")
        self.assertEqual(draft["pdf_url"], "https://conference.example/files/paper.pdf")

    def test_generic_page_without_metadata_returns_editable_blank_draft(self):
        def fake_get(url, **kwargs):
            return FakeResponse(text="<html><head><title>Publisher</title></head></html>")

        draft = preview_import(
            source_url="https://publisher.example/papers/42",
            http_get=fake_get,
        )

        self.assertEqual(draft["source_type"], "web")
        self.assertEqual(draft["source_url"], "https://publisher.example/papers/42")
        self.assertEqual(draft["title"], "")
        self.assertTrue(draft["warnings"])
    def test_arxiv_preview_does_not_insert_the_paper(self):
        metadata = {
            "source_type": "arxiv",
            "source_id": "2607.12345",
            "arxiv_id": "2607.12345",
            "title": "An arXiv Paper",
        }
        with patch("source.ingestion.lookup_paper_by_id", return_value=metadata) as lookup:
            draft = preview_import("https://arxiv.org/abs/2607.12345")
        self.assertEqual(draft, metadata)
        lookup.assert_called_once_with("2607.12345")

    def test_private_network_url_is_rejected(self):
        urls = ("http://127.0.0.1/paper", "http://[::1]/paper", "http://localhost/paper")
        for url in urls:
            with self.subTest(url=url), self.assertRaises(PaperImportError):
                validate_public_http_url(url)



class UploadedPdfTests(unittest.TestCase):
    def test_uploaded_pdf_is_validated_and_stored_as_a_durable_paper_file(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "A readable paper")
        payload = document.tobytes()
        document.close()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "source.documents.PAPER_FILES_DIR", os.path.join(tmp, "paper_files")
        ):
            info = store_uploaded_pdf(io.BytesIO(payload), "p_example", "paper.pdf")
            absolute_path = os.path.join(tmp, info["local_path"])
            self.assertTrue(os.path.exists(absolute_path))
            self.assertEqual(info["local_path"], "paper_files/p_example.pdf")
            self.assertEqual(info["size_bytes"], len(payload))
            self.assertEqual(len(info["sha256"]), 64)


    def test_uploaded_pdf_replace_failure_preserves_existing_file(self):
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Replacement paper")
        payload = document.tobytes()
        document.close()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "source.documents.PAPER_FILES_DIR", tmp
        ), patch("source.documents.os.replace", side_effect=OSError("disk full")):
            target = os.path.join(tmp, "p_existing.pdf")
            with open(target, "wb") as handle:
                handle.write(b"original")

            with self.assertRaises(OSError):
                store_uploaded_pdf(io.BytesIO(payload), "p_existing", "paper.pdf")

            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"original")
            self.assertEqual(os.listdir(tmp), ["p_existing.pdf"])

    def test_pdf_cleanup_rejects_paths_outside_data_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            outside = os.path.join(tmp, "outside.pdf")
            with open(outside, "wb") as handle:
                handle.write(b"keep")

            with patch("source.documents.DB_DIR", data_dir):
                remove_paper_pdf_files({
                    "paper_key": "",
                    "pdf_local_path": "../outside.pdf",
                })

            self.assertTrue(os.path.exists(outside))

class PaperImportRollbackTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask

        self.app = Flask(__name__)

    def test_confirm_cleans_uploaded_pdf_when_insert_has_database_error(self):
        from source.web import import_api

        data = {
            "metadata": json.dumps({"title": "Paper", "source_type": "upload"}),
            "pdf_file": (io.BytesIO(b"pdf"), "paper.pdf"),
            "run_basic": "false",
            "run_deep": "false",
        }
        info = {
            "local_path": "paper_files/p_test.pdf",
            "sha256": "abc",
            "size_bytes": 3,
        }
        with self.app.test_request_context(
            "/api/paper/import", method="POST", data=data,
            content_type="multipart/form-data",
        ), patch.object(
            import_api, "get_paper_by_key", return_value=None
        ), patch.object(import_api, "store_uploaded_pdf", return_value=info), patch.object(
            import_api, "insert_paper", side_effect=sqlite3.OperationalError("locked")
        ), patch.object(import_api, "remove_paper_pdf_files") as cleanup:
            response, status = import_api.api_confirm_paper_import()

        self.assertEqual(status, 500)
        self.assertEqual(response.get_json()["status"], "error")
        cleanup.assert_called_once()

    def test_attach_restores_file_when_update_has_database_error(self):
        from source.web import import_api

        paper = {"id": 1, "paper_key": "p_test", "pdf_local_path": None}
        info = {
            "local_path": "paper_files/p_test.pdf",
            "sha256": "abc",
            "size_bytes": 3,
        }
        data = {"pdf_file": (io.BytesIO(b"pdf"), "paper.pdf")}
        with self.app.test_request_context(
            "/api/paper/p_test/pdf", method="POST", data=data,
            content_type="multipart/form-data",
        ), patch.object(import_api, "get_paper_by_key", return_value=paper), patch.object(
            import_api, "store_uploaded_pdf", return_value=info
        ), patch.object(
            import_api, "update_paper_document", side_effect=sqlite3.OperationalError("locked")
        ), patch.object(import_api, "remove_paper_pdf_files") as cleanup:
            response, status = import_api.api_attach_paper_pdf("p_test")

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()["status"], "error")
        cleanup.assert_called_once()


    def test_duplicate_arxiv_import_preserves_existing_pdf(self):
        from source.web import import_api

        def make_pdf(text):
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), text)
            payload = document.tobytes()
            document.close()
            return payload

        original = make_pdf("original")
        replacement = make_pdf("replacement")
        metadata = {
            "title": "Duplicate",
            "source_type": "arxiv",
            "source_id": "2607.12345",
            "arxiv_id": "2607.12345",
        }
        data = {
            "metadata": json.dumps(metadata),
            "pdf_file": (io.BytesIO(replacement), "paper.pdf"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "2607.12345.pdf")
            with open(target, "wb") as handle:
                handle.write(original)
            with self.app.test_request_context(
                "/api/paper/import", method="POST", data=data,
                content_type="multipart/form-data",
            ), patch("source.documents.PAPER_FILES_DIR", tmp), patch.object(
                import_api, "get_paper_by_key", return_value={"paper_key": "2607.12345"}
            ), patch.object(import_api, "insert_paper", return_value=None) as insert:
                response, status = import_api.api_confirm_paper_import()

            self.assertEqual(status, 409)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), original)
            insert.assert_not_called()


    def test_concurrent_arxiv_loser_only_cleans_its_own_pdf(self):
        from source.web import import_api

        def make_pdf(text):
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), text)
            payload = document.tobytes()
            document.close()
            return payload

        original = make_pdf("winner")
        replacement = make_pdf("loser")
        metadata = {
            "title": "Concurrent duplicate",
            "source_type": "arxiv",
            "source_id": "2607.12345",
            "arxiv_id": "2607.12345",
        }
        data = {
            "metadata": json.dumps(metadata),
            "pdf_file": (io.BytesIO(replacement), "paper.pdf"),
        }
        fake_uuid = type("FakeUuid", (), {"hex": "concurrent"})()
        with tempfile.TemporaryDirectory() as tmp:
            paper_dir = os.path.join(tmp, "paper_files")
            os.makedirs(paper_dir)
            winner_path = os.path.join(paper_dir, "2607.12345.pdf")
            with open(winner_path, "wb") as handle:
                handle.write(original)
            with self.app.test_request_context(
                "/api/paper/import", method="POST", data=data,
                content_type="multipart/form-data",
            ), patch("source.documents.DB_DIR", tmp), patch(
                "source.documents.PAPER_FILES_DIR", paper_dir
            ), patch.object(
                import_api.uuid, "uuid4", return_value=fake_uuid
            ), patch.object(
                import_api, "get_paper_by_key", return_value=None
            ), patch.object(import_api, "insert_paper", return_value=None):
                response, status = import_api.api_confirm_paper_import()

            self.assertEqual(status, 409)
            self.assertEqual(response.get_json()["status"], "error")
            with open(winner_path, "rb") as handle:
                self.assertEqual(handle.read(), original)
            self.assertFalse(os.path.exists(
                os.path.join(paper_dir, "p_concurrent.pdf")
            ))


if __name__ == "__main__":
    unittest.main()
