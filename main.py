"""
CLI 命令行入口模块

本模块是论文数据库系统的命令行接口，提供以下子命令：
- fetch: 仅从 arXiv 抓取新论文
- analyze: 仅对未分析的论文进行 AI 分析
- generate: 仅生成 Markdown 报告
- run: 执行完整流程（抓取 + 分析 + 生成报告）

使用方法：
    python main.py              # 执行完整流程（默认）
    python main.py fetch        # 仅抓取论文
    python main.py analyze      # 仅分析论文
    python main.py generate     # 仅生成报告
    python main.py run          # 执行完整流程

日志配置：
    - 日志级别: INFO
    - 日志格式: 时间 [级别] 模块名: 消息
    - 输出目标: 标准输出
"""

import sys
import logging
from datetime import datetime

# 配置日志系统
# 设置日志级别为 INFO，格式包含时间戳、级别、模块名和消息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_full_pipeline():
    """
    执行完整的论文处理流程
    
    按顺序执行以下步骤：
    1. 初始化数据库（创建表、迁移结构）
    2. 从 arXiv 抓取最新论文
    3. 使用 AI 分析未处理的论文
    4. 生成 Markdown 报告（README + 每日报告）
    
    每个步骤都会记录日志，包括开始和完成状态。
    """
    # 延迟导入，避免循环依赖
    from database import init_db
    from fetcher import fetch_latest_papers
    from analyzer import analyze_pending_papers
    from markdown_gen import generate_all_markdown

    # 流程开始日志
    logger.info("=" * 60)
    logger.info("AI Paper Database - Full Pipeline")
    logger.info("=" * 60)

    # 步骤 1: 初始化数据库
    logger.info("[1/4] Initializing database...")
    init_db()

    # 步骤 2: 抓取最新论文
    logger.info("[2/4] Fetching latest papers from arXiv...")
    new_papers = fetch_latest_papers()
    logger.info(f"Fetched {len(new_papers)} new papers.")

    # 步骤 3: AI 分析论文（默认最多 100 篇）
    logger.info("[3/4] Analyzing papers with AI...")
    analyzed_count = analyze_pending_papers(limit=100)
    logger.info(f"Analyzed {analyzed_count} papers.")

    # 步骤 4: 生成 Markdown 报告
    logger.info("[4/4] Generating Markdown reports...")
    readme_path, daily_path = generate_all_markdown()
    logger.info(f"README: {readme_path}")
    logger.info(f"Daily report: {daily_path}")

    # 流程完成日志
    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info("=" * 60)


def run_fetch_only():
    """
    仅执行论文抓取操作
    
    从 arXiv 抓取最新论文并存入数据库，不进行 AI 分析和报告生成。
    适用于需要快速更新论文库的场景。
    """
    from database import init_db
    from fetcher import fetch_latest_papers

    logger.info("Fetching papers only...")
    init_db()
    new_papers = fetch_latest_papers()
    logger.info(f"Fetched {len(new_papers)} new papers.")


def run_analyze_only():
    """
    仅执行 AI 分析操作
    
    对数据库中未分析的论文进行 AI 分析，不抓取新论文和生成报告。
    适用于已有未分析论文需要处理的场景。
    """
    from database import init_db
    from analyzer import analyze_pending_papers

    logger.info("Analyzing pending papers only...")
    init_db()
    analyzed_count = analyze_pending_papers(limit=100)
    logger.info(f"Analyzed {analyzed_count} papers.")


def run_generate_only():
    """
    仅执行报告生成操作
    
    基于现有数据库数据生成 Markdown 报告，不抓取新论文和进行分析。
    适用于数据已更新需要重新生成报告的场景。
    """
    from database import init_db
    from markdown_gen import generate_all_markdown

    logger.info("Generating Markdown only...")
    init_db()
    readme_path, daily_path = generate_all_markdown()
    logger.info(f"README: {readme_path}")
    logger.info(f"Daily report: {daily_path}")


def main():
    """
    主函数：解析命令行参数并执行相应操作
    
    支持的子命令：
    - fetch: 仅抓取新论文
    - analyze: 仅分析论文
    - generate: 仅生成报告
    - run: 执行完整流程
    
    如果没有提供参数，默认执行完整流程。
    如果参数无效，显示使用帮助。
    """
    if len(sys.argv) > 1:
        # 获取子命令（转换为小写以支持大小写不敏感）
        cmd = sys.argv[1].lower()
        
        # 根据子命令执行相应操作
        if cmd == "fetch":
            run_fetch_only()
        elif cmd == "analyze":
            run_analyze_only()
        elif cmd == "generate":
            run_generate_only()
        elif cmd == "run":
            run_full_pipeline()
        else:
            # 无效命令，显示帮助信息
            print("Usage: python main.py [fetch|analyze|generate|run]")
            print("  fetch    - Only fetch new papers from arXiv")
            print("  analyze  - Only analyze unanalyzed papers")
            print("  generate - Only generate Markdown reports")
            print("  run      - Full pipeline (fetch + analyze + generate)")
    else:
        # 无参数时执行完整流程
        run_full_pipeline()


# 程序入口点
if __name__ == "__main__":
    main()