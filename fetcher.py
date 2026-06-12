"""
arXiv 论文抓取模块

本模块负责从 arXiv API 抓取 AI/机器人领域的最新论文，支持：
- 按分类批量抓取（cs.RO, cs.AI, cs.CV 等）
- 按日期范围抓取（支持分批抓取大量历史数据）
- 按单个 arXiv ID 精确抓取
- 自动去重（内存 + 数据库双重去重）
- 代理配置支持
- 可配置的请求延迟和重试策略

依赖：
- arxiv: arXiv API 客户端库
- database: 本项目的数据库操作模块
- settings: 运行时配置管理模块
"""

import arxiv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from config import ARXIV_CATEGORIES, MAX_PAPERS_PER_CATEGORY
from database import paper_exists, insert_paper
from settings import get_proxy_config, get_fetch_config

# 模块级日志记录器
logger = logging.getLogger(__name__)


# ============================================================
# 代理配置
# ============================================================

def _apply_proxy():
    """
    应用代理配置到环境变量。

    从 settings 中读取代理配置，如果代理已启用则设置 http_proxy 和 https_proxy
    环境变量；如果未启用则清除这些环境变量，确保不会使用过期的代理设置。

    注意：arxiv 库底层使用 requests/urllib，会自动读取环境变量中的代理配置。
    """
    proxy = get_proxy_config()
    if proxy.get("enabled"):
        # 代理启用：设置对应的环境变量
        if proxy.get("http"):
            os.environ["http_proxy"] = proxy["http"]
        if proxy.get("https"):
            os.environ["https_proxy"] = proxy["https"]
    else:
        # 代理禁用：清除环境变量，防止残留旧配置
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)


def _is_rate_limit_error(error):
    """识别 arXiv/HTTP 客户端返回的限流错误。"""
    text = str(error).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def _format_fetch_error(category, error):
    """生成给 Web/API 展示的抓取错误信息。"""
    if _is_rate_limit_error(error):
        return f"{category}: arXiv 请求被限流（429），请稍后重试或增大请求间隔"
    return f"{category}: {error}"


# ============================================================
# 核心抓取逻辑
# ============================================================

def _fetch_date_range(categories, start_date, end_date, request_delay=None):
    """
    抓取指定日期范围内的论文（内部函数）。

    这是最底层的抓取函数，其他抓取函数最终都会调用本函数。
    遍历所有指定分类，通过 arXiv API 按提交日期降序拉取论文，
    并进行日期过滤和去重处理。

    参数:
        categories (list): arXiv 分类列表，如 ['cs.RO', 'cs.AI']
        start_date (datetime): 起始日期（含），必须是带时区的 datetime
        end_date (datetime): 结束日期（不含），必须是带时区的 datetime
        request_delay (float | None): 请求间隔秒数；None 时使用运行时抓取配置

    返回:
        list[dict]: 新抓取的论文数据列表，每个元素为论文字典
    """
    # 应用代理配置
    _apply_proxy()

    # 从配置文件读取请求延迟（如果未指定则使用运行时配置）
    fetch_cfg = get_fetch_config()
    delay = fetch_cfg["request_delay"] if request_delay is None else request_delay

    all_papers = []
    # 内存去重集合：避免同一批次中出现重复论文
    seen_ids = set()
    successful_categories = 0
    errors = []

    # 遍历每个 arXiv 分类进行抓取
    for category in categories:
        logger.info(f"Fetching {category} from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        try:
            # 创建 arXiv 客户端，配置分页大小、请求延迟和重试次数
            client = arxiv.Client(
                page_size=200,
                delay_seconds=delay,
                num_retries=3,
            )

            # 构建查询：按分类过滤
            # 注意：使用 cat: 而非 primary_category:，后者在某些情况下结果不完整
            query = f"cat:{category}"

            # 创建搜索对象：
            # - max_results=10000: 足够大的上限，后续通过日期过滤控制实际数量
            # - sort_by=SubmittedDate: 按提交日期排序
            # - sort_order=Descending: 降序，最新的论文优先
            search = arxiv.Search(
                query=query,
                max_results=10000,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )

            count = 0
            # 标记是否已经到达比 start_date 更早的论文（用于提前终止）
            reached_old = False

            # 遍历搜索结果
            for result in client.results(search):
                # 解析 arXiv ID：从 URL 中提取，去掉版本号（如 v1, v2）
                arxiv_id = result.entry_id.split("/abs/")[-1]
                if "." in arxiv_id:
                    arxiv_id = arxiv_id.split("v")[0]

                # 第一层去重：内存去重，避免同批次内重复
                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)

                # 日期过滤：
                # arXiv API 的 submittedDate 查询语法不可靠，因此在代码中手动过滤
                published_dt = result.published
                if published_dt:
                    published_date = published_dt.strftime("%Y-%m-%d")
                    # 如果论文发布时间早于 start_date，说明已经超出范围，停止遍历
                    if published_dt < start_date:
                        reached_old = True
                        break
                    # 如果论文发布时间晚于等于 end_date，跳过（还未到目标范围）
                    if published_dt >= end_date:
                        continue
                else:
                    # 没有发布时间的论文直接跳过
                    continue

                # 第二层去重：数据库去重，避免与历史数据重复
                if paper_exists(arxiv_id):
                    continue

                # 提取更新日期
                updated = result.updated.strftime("%Y-%m-%d") if result.updated else ""

                # 提取作者列表和分类列表
                authors = [str(a) for a in result.authors]
                categories_list = [str(c) for c in result.categories]

                # 构建论文数据字典
                paper_data = {
                    "arxiv_id": arxiv_id,
                    "title": result.title.replace("\n", " ").strip(),
                    "authors": authors,
                    "abstract": result.summary.replace("\n", " ").strip(),
                    "categories": categories_list,
                    "primary_category": str(result.primary_category),
                    "url": result.entry_id,
                    "pdf_url": result.pdf_url,
                    "published_date": published_date,
                    "updated_date": updated,
                }

                # 写入数据库并记录
                paper_id = insert_paper(paper_data)
                if paper_id:
                    paper_data["id"] = paper_id
                    all_papers.append(paper_data)
                    count += 1

            logger.info(f"  {category}: {count} new papers")
            successful_categories += 1

        except Exception as e:
            # 单个分类抓取失败不影响其他分类
            errors.append(_format_fetch_error(category, e))
            logger.error(f"Error fetching {category}: {e}")
            continue

    if errors and successful_categories == 0:
        raise RuntimeError("arXiv 抓取失败：" + "；".join(errors))

    return all_papers


def fetch_latest_papers(categories=None, max_results=None, days=None):
    """
    抓取最新论文（主入口函数）。

    根据参数决定抓取策略：
    - 如果指定了 days 参数，则调用 fetch_batch() 进行分批抓取
    - 否则按分类逐个抓取最新的 max_results 篇论文

    参数:
        categories (list, optional): arXiv 分类列表，默认使用 config 中的配置
        max_results (int, optional): 每个分类最多抓取的论文数，默认使用 config 中的配置
        days (int, optional): 抓取最近 N 天的论文，启用分批抓取模式

    返回:
        list[dict]: 新抓取的论文数据列表
    """
    if categories is None:
        categories = ARXIV_CATEGORIES
    if max_results is None:
        max_results = MAX_PAPERS_PER_CATEGORY

    # 应用代理配置
    _apply_proxy()

    # 读取抓取配置
    fetch_cfg = get_fetch_config()

    # 如果指定了天数，使用分批抓取模式
    if days:
        return fetch_batch(categories=categories, total_days=days,
                           batch_days=fetch_cfg["batch_days"],
                           batch_delay=fetch_cfg["batch_delay"])

    all_papers = []
    # 内存去重集合
    seen_ids = set()
    successful_categories = 0
    errors = []

    # 按分类逐个抓取
    for category in categories:
        logger.info(f"Fetching papers from category: {category}")
        try:
            # 创建 arXiv 客户端
            client = arxiv.Client(
                page_size=max_results,
                delay_seconds=fetch_cfg["request_delay"],
                num_retries=3,
            )

            # 使用 cat: 查询更可靠，再在代码中保留主分类匹配的论文
            search_limit = max_results * 5
            search = arxiv.Search(
                query=f"cat:{category}",
                max_results=search_limit,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )

            category_count = 0
            for result in client.results(search):
                if str(result.primary_category) != category:
                    continue

                # 解析 arXiv ID，去掉版本号
                arxiv_id = result.entry_id.split("/abs/")[-1]
                if "." in arxiv_id:
                    arxiv_id = arxiv_id.split("v")[0]

                # 内存去重
                if arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)

                # 数据库去重
                if paper_exists(arxiv_id):
                    continue

                # 格式化日期
                published = result.published.strftime("%Y-%m-%d") if result.published else ""
                updated = result.updated.strftime("%Y-%m-%d") if result.updated else ""

                # 提取作者和分类
                authors = [str(a) for a in result.authors]
                categories_list = [str(c) for c in result.categories]

                # 构建论文数据字典
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

                # 写入数据库
                paper_id = insert_paper(paper_data)
                if paper_id:
                    paper_data["id"] = paper_id
                    all_papers.append(paper_data)
                    category_count += 1
                    logger.info(f"  New paper: {arxiv_id} - {paper_data['title'][:60]}...")
                    if category_count >= max_results:
                        break

        except Exception as e:
            # 单个分类失败不影响整体
            errors.append(_format_fetch_error(category, e))
            logger.error(f"Error fetching category {category}: {e}")
            continue
        successful_categories += 1

    if errors and successful_categories == 0:
        raise RuntimeError("arXiv 抓取失败：" + "；".join(errors))

    logger.info(f"Total new papers fetched: {len(all_papers)}")
    return all_papers


# ============================================================
# 分批抓取逻辑
# ============================================================

def fetch_batch(categories=None, total_days=30, batch_days=30, batch_delay=5.0, progress_callback=None):
    """
    分批抓取论文（支持大量历史数据）。

    将大的日期范围拆分为多个小批次依次抓取，每批次之间有延迟，
    避免一次性请求过多数据导致超时或被限流。

    数据库去重在底层抓取函数中完成；这里始终按用户指定的最近 N 天窗口抓取，
    避免把较早历史数据误判为最近日期已经完整覆盖。

    参数:
        categories (list, optional): arXiv 分类列表
        total_days (int): 总共抓取最近多少天的数据，默认 30 天
        batch_days (int): 每批次覆盖的天数，默认 30 天
        batch_delay (float): 批次之间的等待秒数，默认 5.0 秒
        progress_callback (callable, optional): 进度回调函数，接收进度字典

    返回:
        list[dict]: 新抓取的论文数据列表
    """
    if categories is None:
        categories = ARXIV_CATEGORIES

    # 读取抓取配置
    fetch_cfg = get_fetch_config()
    batch_days = batch_days or fetch_cfg["batch_days"]
    batch_delay = batch_delay or fetch_cfg["batch_delay"]

    # 计算日期范围：从今天往回 total_days 天
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=total_days)

    all_papers = []
    batch_num = 0
    current_end = end_date

    # 计算总批次数（向上取整）
    total_batches = max(1, (total_days + batch_days - 1) // batch_days)

    # 分批循环：从 end_date 向 start_date 方向逐批抓取
    while current_end > start_date:
        batch_num += 1
        # 计算当前批次的起始日期（不能早于 start_date）
        current_start = max(current_end - timedelta(days=batch_days), start_date)

        # 报告进度
        if progress_callback:
            progress_callback({
                "current": batch_num,
                "total": total_batches,
                "status": "running",
                "message": f"批次 {batch_num}/{total_batches}: {current_start.strftime('%Y-%m-%d %H:%M UTC')} ~ {current_end.strftime('%Y-%m-%d %H:%M UTC')}"
            })

        logger.info(f"Batch {batch_num}/{total_batches}: {current_start.strftime('%Y-%m-%d %H:%M UTC')} ~ {current_end.strftime('%Y-%m-%d %H:%M UTC')}")

        # 调用底层抓取函数获取当前批次的论文
        papers = _fetch_date_range(categories, current_start, current_end)
        all_papers.extend(papers)

        logger.info(f"  Batch {batch_num} done: {len(papers)} new papers")

        # 移动到下一个批次（current_start 成为下一批的 end_date）
        current_end = current_start

        # 批次间延迟（最后一个批次不等待）
        if current_end > start_date:
            logger.info(f"  Waiting {batch_delay}s before next batch...")
            time.sleep(batch_delay)

    logger.info(f"Total new papers fetched: {len(all_papers)}")

    # 最终进度回调
    if progress_callback:
        progress_callback({
            "current": total_batches,
            "total": total_batches,
            "status": "completed",
            "message": f"抓取完成：共 {len(all_papers)} 篇新论文"
        })

    return all_papers


# ============================================================
# 按日期抓取
# ============================================================

def fetch_by_date(date_str, categories=None):
    """
    抓取指定日期的全部论文。

    参数:
        date_str (str): 目标日期，格式为 "YYYY-MM-DD"
        categories (list, optional): arXiv 分类列表

    返回:
        list[dict]: 该日期新抓取的论文数据列表
    """
    if categories is None:
        categories = ARXIV_CATEGORIES

    # 解析日期字符串
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.error(f"Invalid date format: {date_str}")
        return []

    # 计算次日作为结束日期（_fetch_date_range 使用 [start, end) 区间）
    next_date = target_date + timedelta(days=1)
    return _fetch_date_range(categories, target_date, next_date)


# ============================================================
# 工具函数
# ============================================================

def parse_arxiv_id(input_str):
    """
    从字符串中解析 arXiv ID。

    支持多种输入格式：
    - 纯 ID: "2401.12345"
    - 带版本号: "2401.12345v2"
    - 完整 URL: "https://arxiv.org/abs/2401.12345v1"

    参数:
        input_str (str): 包含 arXiv ID 的字符串

    返回:
        str or None: 解析出的 arXiv ID（不含版本号），解析失败返回 None
    """
    import re
    input_str = input_str.strip()
    # 正则匹配：4位年份.4-5位数字，可选版本号 vN
    match = re.search(r'(\d{4}\.\d{4,5})(v\d+)?', input_str)
    if match:
        return match.group(1)
    return None


def fetch_paper_by_id(arxiv_id):
    """
    根据 arXiv ID 抓取单篇论文。

    如果论文已存在于数据库中，直接返回数据库中的记录；
    否则从 arXiv API 获取并写入数据库。

    参数:
        arxiv_id (str): arXiv ID，如 "2401.12345"

    返回:
        dict or None: 论文数据字典，失败返回 None
    """
    logger.info(f"Fetching paper by ID: {arxiv_id}")

    # 应用代理配置
    _apply_proxy()

    # 读取请求延迟配置
    fetch_cfg = get_fetch_config()
    try:
        # 创建客户端并按 ID 查询
        client = arxiv.Client(page_size=1, delay_seconds=fetch_cfg["request_delay"], num_retries=3)
        search = arxiv.Search(id_list=[arxiv_id])
        results = list(client.results(search))

        if not results:
            logger.warning(f"Paper not found: {arxiv_id}")
            return None

        result = results[0]

        # 解析真实 ID（去掉版本号）
        real_id = result.entry_id.split("/abs/")[-1]
        if "." in real_id:
            real_id = real_id.split("v")[0]

        # 检查数据库中是否已存在
        if paper_exists(real_id):
            logger.info(f"Paper already exists: {real_id}")
            from database import get_paper_by_arxiv_id
            return get_paper_by_arxiv_id(real_id)

        # 格式化日期
        published = result.published.strftime("%Y-%m-%d") if result.published else ""
        updated = result.updated.strftime("%Y-%m-%d") if result.updated else ""

        # 提取作者和分类
        authors = [str(a) for a in result.authors]
        categories_list = [str(c) for c in result.categories]

        # 构建论文数据字典
        paper_data = {
            "arxiv_id": real_id,
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

        # 写入数据库
        paper_id = insert_paper(paper_data)
        if paper_id:
            paper_data["id"] = paper_id
            logger.info(f"Paper added: {real_id} - {paper_data['title'][:60]}...")
            return paper_data
        return None
    except Exception as e:
        logger.error(f"Error fetching paper {arxiv_id}: {e}")
        return None
