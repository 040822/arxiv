import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_full_pipeline():
    from database import init_db
    from fetcher import fetch_latest_papers
    from analyzer import analyze_pending_papers
    from markdown_gen import generate_all_markdown

    logger.info("=" * 60)
    logger.info("AI Paper Database - Full Pipeline")
    logger.info("=" * 60)

    logger.info("[1/4] Initializing database...")
    init_db()

    logger.info("[2/4] Fetching latest papers from arXiv...")
    new_papers = fetch_latest_papers()
    logger.info(f"Fetched {len(new_papers)} new papers.")

    logger.info("[3/4] Analyzing papers with AI...")
    analyzed_count = analyze_pending_papers(limit=100)
    logger.info(f"Analyzed {analyzed_count} papers.")

    logger.info("[4/4] Generating Markdown reports...")
    readme_path, daily_path = generate_all_markdown()
    logger.info(f"README: {readme_path}")
    logger.info(f"Daily report: {daily_path}")

    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info("=" * 60)


def run_fetch_only():
    from database import init_db
    from fetcher import fetch_latest_papers

    logger.info("Fetching papers only...")
    init_db()
    new_papers = fetch_latest_papers()
    logger.info(f"Fetched {len(new_papers)} new papers.")


def run_analyze_only():
    from database import init_db
    from analyzer import analyze_pending_papers

    logger.info("Analyzing pending papers only...")
    init_db()
    analyzed_count = analyze_pending_papers(limit=100)
    logger.info(f"Analyzed {analyzed_count} papers.")


def run_generate_only():
    from database import init_db
    from markdown_gen import generate_all_markdown

    logger.info("Generating Markdown only...")
    init_db()
    readme_path, daily_path = generate_all_markdown()
    logger.info(f"README: {readme_path}")
    logger.info(f"Daily report: {daily_path}")


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "fetch":
            run_fetch_only()
        elif cmd == "analyze":
            run_analyze_only()
        elif cmd == "generate":
            run_generate_only()
        elif cmd == "run":
            run_full_pipeline()
        else:
            print("Usage: python main.py [fetch|analyze|generate|run]")
            print("  fetch    - Only fetch new papers from arXiv")
            print("  analyze  - Only analyze unanalyzed papers")
            print("  generate - Only generate Markdown reports")
            print("  run      - Full pipeline (fetch + analyze + generate)")
    else:
        run_full_pipeline()


if __name__ == "__main__":
    main()
