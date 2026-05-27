import arxiv
import json
import logging
from datetime import datetime, timedelta
from config import ARXIV_CATEGORIES, MAX_PAPERS_PER_CATEGORY
from database import paper_exists, insert_paper

logger = logging.getLogger(__name__)


def fetch_latest_papers(categories=None, max_results=None):
    if categories is None:
        categories = ARXIV_CATEGORIES
    if max_results is None:
        max_results = MAX_PAPERS_PER_CATEGORY

    all_papers = []
    seen_ids = set()

    for category in categories:
        logger.info(f"Fetching papers from category: {category}")
        try:
            client = arxiv.Client(
                page_size=max_results,
                delay_seconds=3.0,
                num_retries=3,
            )

            search = arxiv.Search(
                query=f"cat:{category}",
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )

            for result in client.results(search):
                arxiv_id = result.entry_id.split("/abs/")[-1]
                if "." in arxiv_id:
                    arxiv_id = arxiv_id.split("v")[0]

                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)

                if paper_exists(arxiv_id):
                    continue

                published = result.published.strftime("%Y-%m-%d") if result.published else ""
                updated = result.updated.strftime("%Y-%m-%d") if result.updated else ""

                authors = [str(a) for a in result.authors]
                categories_list = [str(c) for c in result.categories]

                paper_data = {
                    "arxiv_id": arxiv_id,
                    "title": result.title.replace("\n", " ").strip(),
                    "authors": authors,
                    "abstract": result.summary.replace("\n", " ").strip(),
                    "categories": categories_list,
                    "primary_category": str(result.primary_category),
                    "url": result.entry_id,
                    "pdf_url": result.pdf_url,
                    "published_date": published,
                    "updated_date": updated,
                }

                paper_id = insert_paper(paper_data)
                if paper_id:
                    paper_data["id"] = paper_id
                    all_papers.append(paper_data)
                    logger.info(f"  New paper: {arxiv_id} - {paper_data['title'][:60]}...")

        except Exception as e:
            logger.error(f"Error fetching category {category}: {e}")
            continue

    logger.info(f"Total new papers fetched: {len(all_papers)}")
    return all_papers


def fetch_papers_by_date(date_str, categories=None):
    if categories is None:
        categories = ARXIV_CATEGORIES

    all_papers = []
    seen_ids = set()

    for category in categories:
        logger.info(f"Fetching papers from {category} for date {date_str}")
        try:
            client = arxiv.Client(
                page_size=100,
                delay_seconds=3.0,
                num_retries=3,
            )

            search = arxiv.Search(
                query=f"cat:{category}",
                max_results=200,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )

            for result in client.results(search):
                arxiv_id = result.entry_id.split("/abs/")[-1]
                if "." in arxiv_id:
                    arxiv_id = arxiv_id.split("v")[0]

                if arxiv_id in seen_ids:
                    continue

                published = result.published.strftime("%Y-%m-%d") if result.published else ""
                if published != date_str:
                    continue

                seen_ids.add(arxiv_id)

                if paper_exists(arxiv_id):
                    continue

                authors = [str(a) for a in result.authors]
                categories_list = [str(c) for c in result.categories]

                paper_data = {
                    "arxiv_id": arxiv_id,
                    "title": result.title.replace("\n", " ").strip(),
                    "authors": authors,
                    "abstract": result.summary.replace("\n", " ").strip(),
                    "categories": categories_list,
                    "primary_category": str(result.primary_category),
                    "url": result.entry_id,
                    "pdf_url": result.pdf_url,
                    "published_date": published,
                    "updated_date": result.updated.strftime("%Y-%m-%d") if result.updated else "",
                }

                paper_id = insert_paper(paper_data)
                if paper_id:
                    paper_data["id"] = paper_id
                    all_papers.append(paper_data)

        except Exception as e:
            logger.error(f"Error fetching category {category} for date {date_str}: {e}")
            continue

    logger.info(f"Total new papers fetched for {date_str}: {len(all_papers)}")
    return all_papers
