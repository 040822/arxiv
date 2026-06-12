"""
AI 论文分析模块

本模块负责调用 OpenAI 兼容 API 对 arXiv 论文进行深度阅读分析。
支持两种分析模式：
  - 基础分析（basic）：仅使用论文摘要，生成标签、评级和中文翻译，不含 Q&A 深度阅读
  - 完整分析（full）：下载 PDF 提取全文，生成包含 Q&A 深度阅读的完整分析报告

核心流程：
  1. 从数据库获取未分析的论文
  2. 构建 prompt（系统提示 + 用户提示，包含论文信息和标签候选）
  3. 并发调用 AI API 进行分析
  4. 解析 JSON 响应并写入数据库
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from config import TAG_CANDIDATES, RATING_CRITERIA
from settings import build_chat_completion_kwargs, get_ai_config, get_prompts, get_concurrency
from database import insert_analysis, get_unanalyzed_papers
from pdf_reader import get_paper_full_text

# 模块级日志记录器
logger = logging.getLogger(__name__)


# ============================================================
# 客户端初始化
# ============================================================

def get_openai_client():
    """
    创建并返回 OpenAI 客户端实例。

    从运行时配置（data/settings.json）中读取当前激活供应商的 API 密钥和基础 URL，
    创建一个 OpenAI SDK 客户端。该客户端支持所有 OpenAI 兼容 API
    （如 DeepSeek、通义千问、Moonshot 等）。

    Returns:
        OpenAI: 配置好的 OpenAI 客户端实例
    """
    cfg = get_ai_config()
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


def _call_ai(system_prompt, user_prompt, paper_data, include_qa=False):
    """
    核心 AI 调用函数：发送 prompt 到 AI API 并解析返回的 JSON 结果。

    该函数是所有分析模式的底层实现，负责：
      1. 构建 API 请求参数（模型、消息、温度、最大 token 数）
      2. 发送请求并提取响应内容
      3. 清理响应格式（去除 markdown 代码块标记）
      4. 解析 JSON 并校验/补全必要字段
      5. 根据分析模式决定是否保留 Q&A 内容

    Args:
        system_prompt (str): 系统提示，定义 AI 的角色和输出格式要求
        user_prompt (str): 用户提示，包含论文的具体信息（标题、作者、摘要/全文等）
        paper_data (dict): 论文数据字典，至少包含 'arxiv_id' 和 'id' 字段
        include_qa (bool): 是否保留 Q&A 深度阅读内容。基础分析为 False，完整分析为 True

    Returns:
        tuple: (result, error)
            - result (dict | None): 成功时返回解析后的分析结果，包含字段：
                tags, summary_cn, summary_en, rating, value_comment, qa_analysis
            - error (str | None): 失败时返回错误信息字符串
    """
    # 获取 OpenAI 客户端和当前供应商配置
    client = get_openai_client()
    cfg = get_ai_config()

    try:
        messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
        ]
        kwargs = build_chat_completion_kwargs(cfg, messages)

        # 调用 AI API：请求参数由 settings.build_chat_completion_kwargs 统一处理
        response = client.chat.completions.create(**kwargs)

        # 提取响应文本内容并去除首尾空白
        content = (response.choices[0].message.content or "").strip()

        # 清理 AI 返回的 JSON 内容
        content = _clean_json_content(content)

        # 解析 JSON 响应
        result = json.loads(content)

        # ---- 字段校验与默认值补全 ----
        # 确保 tags 字段存在且为列表，否则标记为 Unknown
        if "tags" not in result or not isinstance(result["tags"], list):
            result["tags"] = ["Unknown"]
        # 确保中文翻译字段存在
        if "summary_cn" not in result:
            result["summary_cn"] = ""
        # 确保英文摘要字段存在
        if "summary_en" not in result:
            result["summary_en"] = ""
        # 确保评级字段存在且为整数
        if "rating" not in result or not isinstance(result["rating"], int):
            result["rating"] = 0
        # 确保价值评价字段存在
        if "value_comment" not in result:
            result["value_comment"] = ""

        # 评级范围限制：强制约束在 0-5 星之间
        result["rating"] = max(0, min(5, result["rating"]))

        # 基础分析模式下移除 Q&A 内容（基础分析不需要深度阅读）
        if not include_qa:
            result.pop("qa_analysis", None)

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
# 分析模式：基础分析与完整分析
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
    # 获取 prompt 模板
    prompts = get_prompts()
    system_prompt = prompts.get("system_prompt", "")
    user_prompt = prompts.get("user_prompt", "")

    # 处理作者字段：如果是列表则转为逗号分隔的字符串
    authors = paper_data["authors"]
    if isinstance(authors, list):
        authors = ", ".join(authors)

    # 基础分析仅使用摘要
    abstract = paper_data.get("abstract", "")

    # 格式化用户提示：填充论文信息、标签候选（取前30个）和评级标准
    formatted_user = user_prompt.format(
        title=paper_data["title"],
        authors=authors,
        abstract=abstract,
        tag_candidates=", ".join(TAG_CANDIDATES[:30]),
        rating_criteria=RATING_CRITERIA,
    )

    # 调用 AI，不包含 Q&A 深度阅读
    result, error = _call_ai(system_prompt, formatted_user, paper_data, include_qa=False)
    return paper_data, result, error


def analyze_paper_full(paper_data):
    """
    完整分析模式：下载 PDF 并提取全文进行深度 AI 分析。

    该模式会尝试下载论文 PDF 并提取全文内容。如果 PDF 下载或提取失败，
    自动回退到使用摘要进行分析。分析结果包含完整的 Q&A 深度阅读。

    Args:
        paper_data (dict): 论文数据字典，需包含：
            id, arxiv_id, title, authors, abstract, pdf_url

    Returns:
        tuple: (paper_data, result, error) — 原始论文数据透传
    """
    # 获取 prompt 模板
    prompts = get_prompts()
    system_prompt = prompts.get("system_prompt", "")
    user_prompt = prompts.get("user_prompt", "")

    # 尝试下载 PDF 并提取全文
    pdf_url = paper_data.get("pdf_url", "")
    arxiv_id = paper_data.get("arxiv_id", "")
    full_text = None
    if pdf_url and arxiv_id:
        full_text = get_paper_full_text(pdf_url, arxiv_id)

    # 优先使用 PDF 全文，提取失败时回退到摘要
    if full_text:
        abstract_or_text = full_text
    else:
        abstract_or_text = paper_data.get("abstract", "")

    # 处理作者字段
    authors = paper_data["authors"]
    if isinstance(authors, list):
        authors = ", ".join(authors)

    # 格式化用户提示（变量名虽为 abstract，实际可能是论文全文）
    formatted_user = user_prompt.format(
        title=paper_data["title"],
        authors=authors,
        abstract=abstract_or_text,
        tag_candidates=", ".join(TAG_CANDIDATES[:30]),
        rating_criteria=RATING_CRITERIA,
    )

    # 调用 AI，包含 Q&A 深度阅读
    result, error = _call_ai(system_prompt, formatted_user, paper_data, include_qa=True)
    return paper_data, result, error


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
