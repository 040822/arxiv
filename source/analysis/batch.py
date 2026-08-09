"""Concurrent batch analysis and recommendation orchestration."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from source.settings import get_concurrency, get_personalization_config, get_research_interest_hash
from source.storage import get_papers_for_recommendation, get_unanalyzed_papers, insert_analysis, update_analysis, update_recommendation_result

from .papers import analyze_paper_basic, analyze_paper_recommendation

logger = logging.getLogger(__name__)

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
                                f"{result.get('rating', 0)}★ | {', '.join(result['tags'])}")
                else:
                    # 已有 analysis 记录时，按当前基础分析结果刷新标签、摘要、评价和 AI 初评。
                    update_analysis(paper_data["id"], {
                        "rating": result.get("rating", 0),
                        "tags": result.get("tags", []),
                        "summary_cn": result.get("summary_cn", ""),
                        "summary_en": result.get("summary_en", ""),
                        "value_comment": result.get("value_comment", ""),
                    })
                    success_count += 1
                    logger.info(f"[{completed_count}/{total}] ✅ {arxiv_id} updated basic analysis | "
                                f"{result.get('rating', 0)}★ | {', '.join(result['tags'])}")
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


def recommend_papers(papers, research_interests, interest_hash, concurrency=None, progress_callback=None):
    """批量计算指定论文列表的个性化推荐分。"""
    if not papers or not research_interests or not interest_hash:
        if progress_callback:
            progress_callback({"current": 0, "total": 0, "status": "completed", "message": "无推荐评分任务"})
        return 0
    if concurrency is None:
        concurrency = get_concurrency()

    total = len(papers)
    success_count = 0
    fail_count = 0
    completed_count = 0
    logger.info(f"Starting recommendation scoring: {total} papers, concurrency={concurrency}")

    if progress_callback:
        progress_callback({"current": 0, "total": total, "status": "running", "message": "开始个性化推荐评分..."})

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(analyze_paper_recommendation, paper, research_interests, interest_hash): paper
            for paper in papers
        }
        for future in as_completed(futures):
            source_paper = futures[future]
            try:
                paper_data, result, error = future.result()
            except Exception as e:
                paper_data = source_paper
                result = None
                error = str(e)

            completed_count += 1
            arxiv_id = paper_data.get("arxiv_id", "unknown")
            if result and update_recommendation_result(
                paper_data["id"],
                result.get("recommendation_score", 0),
                result.get("recommendation_reason", ""),
                interest_hash,
            ):
                success_count += 1
                logger.info(f"[{completed_count}/{total}] 🎯 {arxiv_id} | 推荐 {result['recommendation_score']}/100")
            else:
                fail_count += 1
                logger.warning(f"[{completed_count}/{total}] ❌ recommendation {arxiv_id}: {error or 'update failed'}")

            if progress_callback:
                progress_callback({
                    "current": completed_count,
                    "total": total,
                    "status": "running",
                    "success": success_count,
                    "fail": fail_count,
                    "arxiv_id": arxiv_id,
                    "recommendation_score": result.get("recommendation_score", 0) if result else 0,
                    "message": f"[{completed_count}/{total}] {arxiv_id}"
                })

    if progress_callback:
        progress_callback({
            "current": total,
            "total": total,
            "status": "completed",
            "success": success_count,
            "fail": fail_count,
            "message": f"推荐评分完成：{success_count} 篇成功，{fail_count} 篇失败"
        })
    return success_count


def recommend_pending_papers(limit=200, date=None, concurrency=None, progress_callback=None, ingest_mode=None):
    """按当前研究兴趣为缺失或过期的论文补齐个性化推荐分。"""
    cfg = get_personalization_config()
    research_interests = cfg.get("research_interests", "")
    interest_hash = get_research_interest_hash(research_interests)
    if not research_interests or not interest_hash:
        if progress_callback:
            progress_callback({"current": 0, "total": 0, "status": "completed", "message": "未设置研究兴趣，跳过推荐评分"})
        return 0
    papers = get_papers_for_recommendation(
        limit=limit,
        date=date,
        interest_hash=interest_hash,
        ingest_mode=ingest_mode,
    )
    return recommend_papers(papers, research_interests, interest_hash, concurrency=concurrency, progress_callback=progress_callback)


def analyze_pending_papers(limit=50, concurrency=None, progress_callback=None, ingest_mode=None):
    """
    批量分析未处理的论文：从数据库获取指定数量后调用 analyze_papers()。

    Args:
        limit (int): 最大处理论文数量，默认 50
        concurrency (int | None): 并发线程数，None 时使用 config.py 中的默认值
        progress_callback (callable | None): 进度回调函数，接收 dict 参数

    Returns:
        int: 成功新增分析的论文数量
    """
    papers = get_unanalyzed_papers(limit=limit, ingest_mode=ingest_mode)
    return analyze_papers(papers, concurrency=concurrency, progress_callback=progress_callback)

