import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from config import TAG_CANDIDATES, RATING_CRITERIA, ANALYSIS_CONCURRENCY
from settings import get_ai_config, get_prompts
from database import insert_analysis, get_unanalyzed_papers
from pdf_reader import get_paper_full_text

logger = logging.getLogger(__name__)


def get_openai_client():
    cfg = get_ai_config()
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
    )


def _call_ai(system_prompt, user_prompt, paper_data, include_qa=False):
    client = get_openai_client()
    cfg = get_ai_config()

    try:
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],
        )

        content = response.choices[0].message.content.strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])

        result = json.loads(content)

        if "tags" not in result or not isinstance(result["tags"], list):
            result["tags"] = ["Unknown"]
        if "summary_cn" not in result:
            result["summary_cn"] = ""
        if "summary_en" not in result:
            result["summary_en"] = ""
        if "rating" not in result or not isinstance(result["rating"], int):
            result["rating"] = 0
        if "value_comment" not in result:
            result["value_comment"] = ""

        result["rating"] = max(0, min(5, result["rating"]))

        if not include_qa:
            result.pop("qa_analysis", None)

        return result, None

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)
    except Exception as e:
        logger.error(f"API error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)


def analyze_paper_basic(paper_data):
    prompts = get_prompts()
    system_prompt = prompts.get("system_prompt", "")
    user_prompt = prompts.get("user_prompt", "")

    authors = paper_data["authors"]
    if isinstance(authors, list):
        authors = ", ".join(authors)

    abstract = paper_data.get("abstract", "")

    formatted_user = user_prompt.format(
        title=paper_data["title"],
        authors=authors,
        abstract=abstract,
        tag_candidates=", ".join(TAG_CANDIDATES[:30]),
        rating_criteria=RATING_CRITERIA,
    )

    result, error = _call_ai(system_prompt, formatted_user, paper_data, include_qa=False)
    return paper_data, result, error


def analyze_paper_full(paper_data):
    prompts = get_prompts()
    system_prompt = prompts.get("system_prompt", "")
    user_prompt = prompts.get("user_prompt", "")

    pdf_url = paper_data.get("pdf_url", "")
    arxiv_id = paper_data.get("arxiv_id", "")
    full_text = None
    if pdf_url and arxiv_id:
        full_text = get_paper_full_text(pdf_url, arxiv_id)

    if full_text:
        abstract_or_text = full_text
    else:
        abstract_or_text = paper_data.get("abstract", "")

    authors = paper_data["authors"]
    if isinstance(authors, list):
        authors = ", ".join(authors)

    formatted_user = user_prompt.format(
        title=paper_data["title"],
        authors=authors,
        abstract=abstract_or_text,
        tag_candidates=", ".join(TAG_CANDIDATES[:30]),
        rating_criteria=RATING_CRITERIA,
    )

    result, error = _call_ai(system_prompt, formatted_user, paper_data, include_qa=True)
    return paper_data, result, error


def analyze_pending_papers(limit=50, concurrency=None, progress_callback=None):
    papers = get_unanalyzed_papers(limit=limit)
    if not papers:
        logger.info("No unanalyzed papers found.")
        return 0

    if concurrency is None:
        concurrency = ANALYSIS_CONCURRENCY

    total = len(papers)
    success_count = 0
    skip_count = 0
    fail_count = 0
    completed_count = 0

    logger.info(f"Starting parallel basic analysis: {total} papers, concurrency={concurrency}")

    if progress_callback:
        progress_callback({"current": 0, "total": total, "status": "running", "message": "开始基础分析..."})

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(analyze_paper_basic, paper): paper for paper in papers}

        for future in as_completed(futures):
            paper_data, result, error = future.result()
            arxiv_id = paper_data.get("arxiv_id", "unknown")
            completed_count += 1

            if result:
                inserted = insert_analysis(paper_data["id"], result)
                if inserted:
                    success_count += 1
                    logger.info(f"[{completed_count}/{total}] ✅ {arxiv_id} | "
                                f"{'★' * result['rating']}{'☆' * (5 - result['rating'])} | "
                                f"{', '.join(result['tags'])}")
                else:
                    skip_count += 1
                    logger.info(f"[{completed_count}/{total}] ⏭️ {arxiv_id} already analyzed")
            else:
                fail_count += 1
                logger.warning(f"[{completed_count}/{total}] ❌ {arxiv_id}: {error}")

            if progress_callback:
                progress_callback({
                    "current": completed_count,
                    "total": total,
                    "status": "running",
                    "success": success_count,
                    "skip": skip_count,
                    "fail": fail_count,
                    "arxiv_id": arxiv_id,
                    "rating": result.get("rating", 0) if result else 0,
                    "tags": result.get("tags", []) if result else [],
                    "message": f"[{completed_count}/{total}] {arxiv_id}"
                })

    logger.info(f"Basic analysis complete: {success_count} new, {skip_count} skipped, {fail_count} failed.")
    if progress_callback:
        progress_callback({
            "current": total,
            "total": total,
            "status": "completed",
            "success": success_count,
            "skip": skip_count,
            "fail": fail_count,
            "message": f"基础分析完成：{success_count} 篇新增，{skip_count} 跳过，{fail_count} 失败"
        })
    return success_count
