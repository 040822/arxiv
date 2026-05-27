import os
import re
import time
import threading
import logging
import requests
import fitz  # PyMuPDF
from config import DB_DIR, PDF_DOWNLOAD_RATE, PDF_DOWNLOAD_CAPACITY

logger = logging.getLogger(__name__)

PDF_CACHE_DIR = os.path.join(DB_DIR, "pdf_cache")


class TokenBucket:
    def __init__(self, rate=1.0, capacity=2):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_time = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, tokens=1):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_time = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0
            else:
                wait_time = (tokens - self.tokens) / self.rate
                self.tokens = 0
                self.last_time += wait_time
                return wait_time


_pdf_bucket = TokenBucket(rate=PDF_DOWNLOAD_RATE, capacity=PDF_DOWNLOAD_CAPACITY)


def _ensure_cache_dir():
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)


def download_pdf(pdf_url, arxiv_id):
    _ensure_cache_dir()
    cache_path = os.path.join(PDF_CACHE_DIR, f"{arxiv_id.replace('/', '_')}.pdf")

    if os.path.exists(cache_path):
        return cache_path

    wait = _pdf_bucket.acquire()
    if wait > 0:
        logger.debug(f"Rate limited, waiting {wait:.1f}s for {arxiv_id}")
        time.sleep(wait)

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ArxivPaperDB/1.0)"}
        resp = requests.get(pdf_url, headers=headers, timeout=60)
        resp.raise_for_status()
        with open(cache_path, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded PDF: {arxiv_id} ({len(resp.content) / 1024:.0f} KB)")
        return cache_path
    except Exception as e:
        logger.error(f"Failed to download PDF for {arxiv_id}: {e}")
        return None


def extract_text_from_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
        doc.close()

        full_text = "\n\n".join(pages)

        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        full_text = re.sub(r' {2,}', ' ', full_text)
        full_text = full_text.strip()

        return full_text
    except Exception as e:
        logger.error(f"Failed to extract text from {pdf_path}: {e}")
        return None


def get_paper_full_text(pdf_url, arxiv_id, max_chars=6000000):
    pdf_path = download_pdf(pdf_url, arxiv_id)
    if not pdf_path:
        return None

    text = extract_text_from_pdf(pdf_path)
    if not text:
        return None

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... 文本已截断，以上为论文前部分内容 ...]"
        logger.info(f"Truncated paper text for {arxiv_id} to {max_chars} chars")

    return text
