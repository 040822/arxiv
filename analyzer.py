"""
AI 论文分析模块

本模块负责调用 OpenAI 兼容 API 对 arXiv 论文进行深度阅读分析。
支持两种分析模式：
  - 基础分析（basic）：仅使用论文摘要，生成标签、评级和中文翻译，不含 Q&A 深度阅读
  - 深度阅读（full）：下载 PDF 提取全文，只生成 Q&A 深度阅读，不覆盖基础分析字段

核心流程：
  1. 从数据库获取未分析的论文
  2. 构建 prompt（稳定任务说明 + 动态论文 JSON）
  3. 并发调用 AI API 进行分析
  4. 解析 JSON 响应并写入数据库
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from config import TAG_CANDIDATES, RATING_CRITERIA
from settings import build_chat_completion_kwargs, get_ai_config, get_ai_task_config, get_prompt_profile, get_concurrency
from database import get_connection, insert_analysis, get_unanalyzed_papers, record_ai_usage
from pdf_reader import get_paper_full_text

# 模块级日志记录器
logger = logging.getLogger(__name__)


# ============================================================
# 客户端初始化
# ============================================================

def get_openai_client(cfg=None):
    """
    创建并返回 OpenAI 客户端实例。

    从运行时配置（data/settings.json）中读取当前激活供应商的 API 密钥和基础 URL，
    创建一个 OpenAI SDK 客户端。该客户端支持所有 OpenAI 兼容 API
    （如 DeepSeek、通义千问、Moonshot 等）。

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例
    """
    cfg = cfg or get_ai_config()
    return OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
    )


# ============================================================
# 核心 AI 调用逻辑
# ============================================================

def _clean_json_content(content):
    """清理 AI 返回的 JSON 内容，修复常见的格式问题。

    AI 模型返回的 JSON 可能存在以下问题：
    1. 包裹在 markdown 代码块中：```json ... ```
    2. 包含无效的反斜杠转义：\\_、\\[、\\] 等
    3. 前后包含多余文本（解释说明等）

    Args:
        content (str): AI 返回的原始文本

    Returns:
        str: 清理后的 JSON 字符串
    """
    import re

    # 步骤 1：去除 markdown 代码块标记
    # AI 有时会将 JSON 包裹在 ```json ... ``` 中
    if content.startswith("```"):
        lines = content.split("\n")
        # 去除首尾的 ``` 行
        content = "\n".join(lines[1:])
        if content.endswith("```"):
            content = content[:-3].strip()

    # 步骤 2：提取 JSON 对象
    # 如果响应中包含非 JSON 文本（如解释说明），只提取 { ... } 部分
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        content = json_match.group(0)

    # 步骤 3：修复无效的反斜杠转义
    # AI 有时会在 LaTeX 公式或特殊字符中使用无效的 JSON 转义
    # 有效转义：\" \\ \/ \b \f \n \r \t \uXXXX
    # 无效转义：\_ \[ \] \{ \} \* \+ \. 等
    # 策略：将无效转义中的反斜杠移除
    content = re.sub(r'\\(?!["\\/bfnrtu])', '', content)

    return content


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _int_value(obj, key):
    try:
        return int(_value(obj, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _extract_usage(response):
    """从 OpenAI SDK 响应中提取 token 用量，兼容 dict 和对象响应。"""
    usage = _value(response, "usage", {}) or {}
    prompt_tokens = _int_value(usage, "prompt_tokens")
    completion_tokens = _int_value(usage, "completion_tokens")
    total_tokens = _int_value(usage, "total_tokens")
    details = _value(usage, "prompt_tokens_details", None) or _value(usage, "input_tokens_details", None) or {}
    cached_tokens = _int_value(details, "cached_tokens") or _int_value(details, "cache_read_input_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
    }


def _record_usage(task_key, cfg, paper_data, response):
    """记录一次 AI 调用的 token 用量；失败不影响主流程。"""
    try:
        usage = _extract_usage(response)
        record_ai_usage({
            "task_key": task_key,
            "provider_key": cfg.get("provider_key", ""),
            "provider_name": cfg.get("provider_name", ""),
            "model": cfg.get("model", ""),
            "paper_id": paper_data.get("id"),
            "arxiv_id": paper_data.get("arxiv_id", ""),
            **usage,
        })
    except Exception as e:
        logger.debug(f"Failed to record AI usage: {e}")


def _authors_text(paper_data):
    authors = paper_data.get("authors", "")
    if isinstance(authors, list):
        return ", ".join(authors)
    return authors


def _render_instruction(instruction):
    """渲染稳定 prompt 前缀；论文动态数据会放在单独 message 中。"""
    return (instruction or "").replace(
        "{tag_candidates}",
        ", ".join(TAG_CANDIDATES[:30]),
    ).replace(
        "{rating_criteria}",
        RATING_CRITERIA,
    )


def _build_task_messages(task_key, payload):
    profile = get_prompt_profile(task_key)
    return [
        {"role": "system", "content": profile.get("system", "")},
        {"role": "user", "content": _render_instruction(profile.get("instruction", ""))},
        {
            "role": "user",
            "content": "动态输入数据（JSON，固定字段顺序）：\n" + json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]


def _normalise_analysis_result(result):
    """校验并补齐基础分析结果字段。"""
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
    result.pop("qa_analysis", None)
    return result


def _normalise_deep_reading_result(result):
    """深度阅读只保留 Q&A，避免覆盖基础分析字段。"""
    qa_analysis = ""
    if isinstance(result, dict):
        qa_analysis = result.get("qa_analysis", "")
    if not isinstance(qa_analysis, str):
        qa_analysis = json.dumps(qa_analysis, ensure_ascii=False)
    return {"qa_analysis": qa_analysis.strip()}


def _call_ai(messages, paper_data, task_key):
    """
    核心 AI 调用函数：按任务配置发送 messages 到 AI API 并解析 JSON。

    该函数是所有分析模式的底层实现，负责：
      1. 构建 API 请求参数（模型、消息、温度、最大 token 数）
      2. 发送请求并提取响应内容
      3. 清理响应格式（去除 markdown 代码块标记）
      4. 解析 JSON 并校验/补全必要字段
      5. 根据分析模式决定是否保留 Q&A 内容

    Args:
        messages (list): OpenAI Chat Completions 消息列表
        paper_data (dict): 论文数据字典，至少包含 'arxiv_id' 和 'id' 字段
        task_key (str): basic_analysis/deep_reading/report_summary

    Returns:
        tuple: (result, error)
            - result (dict | None): 成功时返回解析后的 JSON 字典
            - error (str | None): 失败时返回错误信息字符串
    """
    cfg = get_ai_task_config(task_key)
    client = get_openai_client(cfg)

    try:
        kwargs = build_chat_completion_kwargs(cfg, messages)

        # 调用 AI API：请求参数由 settings.build_chat_completion_kwargs 统一处理
        response = client.chat.completions.create(**kwargs)
        _record_usage(task_key, cfg, paper_data, response)

        # 提取响应文本内容并去除首尾空白，兼容 SDK 对象和测试中的 dict
        choices = _value(response, "choices", []) or []
        first_choice = choices[0] if choices else {}
        message = _value(first_choice, "message", {}) or {}
        content = (_value(message, "content", "") or "").strip()

        # 清理 AI 返回的 JSON 内容
        content = _clean_json_content(content)

        # 解析 JSON 响应
        result = json.loads(content)
        return result, None

    # JSON 解析失败：AI 返回的内容不是有效 JSON
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)
    # 其他异常：网络错误、API 限流、认证失败等
    except Exception as e:
        logger.error(f"API error for paper {paper_data.get('arxiv_id', 'unknown')}: {e}")
        return None, str(e)


# ============================================================
# 分析模式：基础分析与深度阅读
# ============================================================

def analyze_paper_basic(paper_data):
    """
    基础分析模式：仅使用论文摘要进行 AI 分析，不下载 PDF。

    该模式速度较快，适合大批量快速分析。分析结果包含：
      - 标签分类、中文翻译、评级、价值评价
      - 不包含 Q&A 深度阅读

    Args:
        paper_data (dict): 论文数据字典，需包含：
            id, arxiv_id, title, authors, abstract

    Returns:
        tuple: (paper_data, result, error) — 原始论文数据透传
    """
    payload = {
        "arxiv_id": paper_data.get("arxiv_id", ""),
        "title": paper_data.get("title", ""),
        "authors": _authors_text(paper_data),
        "abstract": paper_data.get("abstract", ""),
    }
    messages = _build_task_messages("basic_analysis", payload)
    result, error = _call_ai(messages, paper_data, "basic_analysis")
    if result:
        result = _normalise_analysis_result(result)
    return paper_data, result, error


def analyze_paper_full(paper_data):
    """
    深度阅读模式：下载 PDF 并提取全文进行 Q&A 分析。

    该模式会尝试下载论文 PDF 并提取全文内容。如果 PDF 下载或提取失败，
    自动回退到使用摘要进行分析。分析结果只包含 Q&A 深度阅读。

    Args:
        paper_data (dict): 论文数据字典，需包含：
            id, arxiv_id, title, authors, abstract, pdf_url

    Returns:
        tuple: (paper_data, result, error) — 原始论文数据透传
    """
    # 尝试下载 PDF 并提取全文
    pdf_url = paper_data.get("pdf_url", "")
    arxiv_id = paper_data.get("arxiv_id", "")
    full_text = None
    if pdf_url and arxiv_id:
        full_text = get_paper_full_text(pdf_url, arxiv_id, max_chars=None)

    # 优先使用 PDF 全文，提取失败时回退到摘要
    if full_text:
        abstract_or_text = full_text
    else:
        abstract_or_text = paper_data.get("abstract", "")

    payload = {
        "arxiv_id": paper_data.get("arxiv_id", ""),
        "title": paper_data.get("title", ""),
        "authors": _authors_text(paper_data),
        "abstract": paper_data.get("abstract", ""),
        "paper_text": abstract_or_text,
        "used_pdf_full_text": bool(full_text),
    }
    messages = _build_task_messages("deep_reading", payload)
    result, error = _call_ai(messages, paper_data, "deep_reading")
    if result:
        result = _normalise_deep_reading_result(result)
    return paper_data, result, error


def _get_report_summary_context(report_date, limit=30):
    """读取报告导读所需的轻量论文上下文，避免把全文再次送给模型。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.arxiv_id, p.title, p.authors, p.categories, a.tags, a.rating, a.value_comment, a.summary_cn
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
        ORDER BY COALESCE(a.rating, 0) DESC, p.arxiv_id
        LIMIT ?
    """, (report_date, limit))
    rows = cursor.fetchall()
    conn.close()

    papers = []
    for row in rows:
        item = dict(row)
        for field in ("authors", "categories", "tags"):
            if item.get(field) and isinstance(item[field], str):
                try:
                    item[field] = json.loads(item[field])
                except json.JSONDecodeError:
                    item[field] = []
        papers.append({
            "arxiv_id": item.get("arxiv_id", ""),
            "title": item.get("title", ""),
            "authors": item.get("authors") or [],
            "categories": item.get("categories") or [],
            "tags": item.get("tags") or [],
            "rating": item.get("rating") or 0,
            "value_comment": item.get("value_comment") or "",
            "summary_cn": item.get("summary_cn") or "",
        })
    return papers


def generate_report_ai_summary(report_date):
    """使用 report_summary 任务模型生成一段可选的日报导读。"""
    papers = _get_report_summary_context(report_date)
    if not papers:
        return None, "无论文数据"

    payload = {
        "report_date": report_date,
        "paper_count": len(papers),
        "papers": papers,
    }
    paper_data = {"id": None, "arxiv_id": f"report:{report_date}"}
    messages = _build_task_messages("report_summary", payload)
    result, error = _call_ai(messages, paper_data, "report_summary")
    if error:
        return None, error
    summary = (result or {}).get("summary", "")
    if not summary:
        return None, "模型未返回 summary 字段"
    return summary.strip(), None


# ============================================================
# 批量分析与并发控制
# ============================================================

def analyze_papers(papers, concurrency=None, progress_callback=None):
    """
    批量分析指定论文列表：使用线程池并发执行基础分析。

    该函数是批量分析的入口，负责：
      1. 使用 ThreadPoolExecutor 并发执行基础分析
      2. 将分析结果写入数据库（含去重检查）
      3. 通过回调函数报告进度（支持 Web 前端实时显示）

    Args:
        papers (list[dict]): 要分析的论文列表
        concurrency (int | None): 并发线程数，None 时使用 config.py 中的默认值
        progress_callback (callable | None): 进度回调函数，接收 dict 参数

    Returns:
        int: 成功新增分析的论文数量
    """
    if not papers:
        logger.info("No unanalyzed papers found.")
        return 0

    # 确定并发数：未指定时使用运行时设置
    if concurrency is None:
        concurrency = get_concurrency()

    # 初始化统计计数器
    total = len(papers)
    success_count = 0   # 成功新增分析数
    skip_count = 0      # 已分析跳过数
    fail_count = 0      # 分析失败数
    completed_count = 0 # 已完成总数

    logger.info(f"Starting parallel basic analysis: {total} papers, concurrency={concurrency}")

    # 发送初始进度通知
    if progress_callback:
        progress_callback({"current": 0, "total": total, "status": "running", "message": "开始基础分析..."})

    # 使用线程池并发执行分析任务
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        # 提交所有任务，建立 future -> paper 的映射
        futures = {executor.submit(analyze_paper_basic, paper): paper for paper in papers}

        # 按完成顺序处理结果（as_completed 保证先完成的先返回）
        for future in as_completed(futures):
            source_paper = futures[future]
            try:
                paper_data, result, error = future.result()
            except Exception as e:
                paper_data = source_paper
                result = None
                error = str(e)
            arxiv_id = paper_data.get("arxiv_id", "unknown")
            completed_count += 1

            if result:
                # 尝试将分析结果写入数据库（insert_analysis 内部有重复检查）
                inserted = insert_analysis(paper_data["id"], result)
                if inserted:
                    success_count += 1
                    logger.info(f"[{completed_count}/{total}] ✅ {arxiv_id} | "
                                f"{'★' * result['rating']}{'☆' * (5 - result['rating'])} | "
                                f"{', '.join(result['tags'])}")
                else:
                    # 数据库中已存在该论文的分析结果
                    skip_count += 1
                    logger.info(f"[{completed_count}/{total}] ⏭️ {arxiv_id} already analyzed")
            else:
                # AI 调用失败或 JSON 解析失败
                fail_count += 1
                logger.warning(f"[{completed_count}/{total}] ❌ {arxiv_id}: {error}")

            # 每完成一篇都发送进度更新（用于 Web 前端实时显示）
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

    # 批量分析完成，记录汇总日志
    logger.info(f"Basic analysis complete: {success_count} new, {skip_count} skipped, {fail_count} failed.")
    # 发送最终完成通知
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


def analyze_pending_papers(limit=50, concurrency=None, progress_callback=None):
    """
    批量分析未处理的论文：从数据库获取指定数量后调用 analyze_papers()。

    Args:
        limit (int): 最大处理论文数量，默认 50
        concurrency (int | None): 并发线程数，None 时使用 config.py 中的默认值
        progress_callback (callable | None): 进度回调函数，接收 dict 参数

    Returns:
        int: 成功新增分析的论文数量
    """
    papers = get_unanalyzed_papers(limit=limit)
    return analyze_papers(papers, concurrency=concurrency, progress_callback=progress_callback)
