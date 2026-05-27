import logging
import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from database import (
    init_db, get_papers_with_analysis, get_all_tags,
    get_paper_count, get_analyzed_count, get_unanalyzed_count,
    search_papers, get_daily_stats,
    browse_papers, get_all_categories, get_all_dates,
    get_paper_by_arxiv_id, get_analysis_by_paper_id,
    update_analysis, hide_paper, unhide_paper, delete_paper,
    start_task_log, finish_task_log, get_task_logs, get_task_stats,
    get_running_tasks, clear_task_logs
)
from fetcher import fetch_latest_papers
from analyzer import analyze_pending_papers
from markdown_gen import generate_all_markdown
from settings import (
    load_settings, save_settings, get_provider_presets, get_all_providers,
    add_provider, remove_provider, switch_provider, update_provider,
    get_prompts, save_prompts, get_concurrency,
    get_admin_password, set_admin_password, verify_admin_password, has_admin_password
)
from config import WEB_HOST, WEB_PORT, SCHEDULE_HOUR, SCHEDULE_MINUTE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.context_processor
def inject_now():
    return {"now": datetime.now().strftime("%Y-%m-%d %H:%M")}


@app.context_processor
def utility_processor():
    def _remove_param(key):
        args = request.args.copy()
        args.pop(key, None)
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    def _build_query():
        args = request.args.copy()
        args.pop("page", None)
        return "&".join(f"{k}={v}" for k, v in args.items() if v)

    return {"_remove_param": _remove_param, "_build_query": _build_query}


scheduler = BackgroundScheduler()


def daily_pipeline():
    log_id = start_task_log("daily_pipeline", "每日定时任务启动")
    try:
        new_papers = fetch_latest_papers()
        logger.info(f"Fetched {len(new_papers)} new papers.")
        concurrency = get_concurrency()
        analyzed_count = analyze_pending_papers(limit=100, concurrency=concurrency)
        logger.info(f"Analyzed {analyzed_count} papers.")
        readme_path, daily_path = generate_all_markdown()
        logger.info(f"Markdown generated: {readme_path}, {daily_path}")
        finish_task_log(log_id, "success",
            f"完成：抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇",
            f"new_papers={len(new_papers)}, analyzed={analyzed_count}, concurrency={concurrency}")
    except Exception as e:
        logger.error(f"Daily pipeline error: {e}")
        finish_task_log(log_id, "error", f"失败：{e}")


@app.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page = 20

    if min_rating is not None:
        min_rating = max(0, min(5, min_rating))

    papers = get_papers_with_analysis(
        date=date, tag=tag, min_rating=min_rating,
        limit=per_page, offset=(page - 1) * per_page
    )

    total_papers = get_paper_count()
    analyzed_papers = get_analyzed_count()
    tags_with_counts = get_all_tags()

    return render_template(
        "index.html",
        papers=papers,
        page=page,
        tag=tag,
        date=date,
        min_rating=min_rating,
        total_papers=total_papers,
        analyzed_papers=analyzed_papers,
        tags=tags_with_counts,
    )


@app.route("/paper/<arxiv_id>")
def paper_detail(arxiv_id):
    paper = get_paper_by_arxiv_id(arxiv_id)
    if not paper:
        return "Paper not found", 404

    import json
    if paper.get("authors") and isinstance(paper["authors"], str):
        paper["authors"] = json.loads(paper["authors"])
    if paper.get("categories") and isinstance(paper["categories"], str):
        paper["categories"] = json.loads(paper["categories"])

    analysis = get_analysis_by_paper_id(paper["id"])

    return render_template("paper.html", paper=paper, analysis=analysis)


@app.route("/search")
def search():
    keyword = request.args.get("q", "").strip()
    papers = []
    if keyword:
        papers = search_papers(keyword, limit=50)
    return render_template("search.html", papers=papers, keyword=keyword)


@app.route("/browse")
def browse():
    page = request.args.get("page", 1, type=int)
    per_page = 20

    date = request.args.get("date", None)
    tag = request.args.get("tag", None)
    category = request.args.get("category", None)
    min_rating = request.args.get("min_rating", None, type=int)
    max_rating = request.args.get("max_rating", None, type=int)
    has_analysis = request.args.get("has_analysis", None)

    if min_rating is not None:
        min_rating = max(0, min(5, min_rating))
    if max_rating is not None:
        max_rating = max(0, min(5, max_rating))

    papers, total = browse_papers(
        date=date, tag=tag, category=category,
        min_rating=min_rating, max_rating=max_rating,
        has_analysis=has_analysis,
        limit=per_page, offset=(page - 1) * per_page
    )

    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    tags_with_counts = get_all_tags()
    categories_with_counts = get_all_categories()
    dates_with_counts = get_all_dates()

    return render_template(
        "browse.html",
        papers=papers, page=page, total=total, total_pages=total_pages,
        date=date, tag=tag, category=category,
        min_rating=min_rating, max_rating=max_rating,
        has_analysis=has_analysis,
        tags=tags_with_counts,
        categories=categories_with_counts,
        dates=dates_with_counts,
    )


@app.route("/api/papers")
def api_papers():
    page = request.args.get("page", 1, type=int)
    tag = request.args.get("tag", None)
    date = request.args.get("date", None)
    min_rating = request.args.get("min_rating", None, type=int)
    per_page = 20

    papers = get_papers_with_analysis(
        date=date, tag=tag, min_rating=min_rating,
        limit=per_page, offset=(page - 1) * per_page
    )
    return jsonify(papers)


@app.route("/api/tags")
def api_tags():
    tags = get_all_tags()
    return jsonify(tags)


@app.route("/api/stats")
def api_stats():
    return jsonify({
        "total_papers": get_paper_count(),
        "analyzed_papers": get_analyzed_count(),
        "unanalyzed_papers": get_unanalyzed_count(),
        "concurrency": get_concurrency(),
    })


@app.route("/api/settings/concurrency", methods=["POST"])
def api_save_concurrency():
    try:
        data = request.get_json()
        val = int(data.get("concurrency", 5))
        val = max(1, min(20, val))
        settings = load_settings()
        settings["concurrency"] = val
        if save_settings(settings):
            return jsonify({"status": "ok", "message": f"并发数已设置为 {val}"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    log_id = start_task_log("fetch", "手动抓取论文")
    try:
        new_papers = fetch_latest_papers()
        unanalyzed = get_unanalyzed_count()
        msg = f"抓取完成：{len(new_papers)} 篇新论文" + (f"，{unanalyzed} 篇待分析" if unanalyzed else "")
        finish_task_log(log_id, "success", msg, f"new_papers={len(new_papers)}, unanalyzed={unanalyzed}")
        return jsonify({"status": "ok", "count": len(new_papers), "unanalyzed": unanalyzed, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    log_id = start_task_log("analyze", "手动AI分析")
    try:
        limit = request.args.get("limit", 50, type=int)
        concurrency = get_concurrency()
        count = analyze_pending_papers(limit=limit, concurrency=concurrency)
        remaining = get_unanalyzed_count()
        msg = f"分析完成：{count} 篇已分析（并发数 {concurrency}）" + (f"，{remaining} 篇剩余" if remaining else "，全部完成！")
        finish_task_log(log_id, "success", msg, f"analyzed={count}, remaining={remaining}, concurrency={concurrency}")
        return jsonify({"status": "ok", "count": count, "remaining": remaining, "concurrency": concurrency, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def api_generate():
    log_id = start_task_log("generate", "手动生成报告")
    try:
        readme_path, daily_path = generate_all_markdown()
        finish_task_log(log_id, "success", "报告生成成功", f"readme={readme_path}, daily={daily_path}")
        return jsonify({"status": "ok", "readme": readme_path, "daily": daily_path, "message": "报告生成成功"})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    log_id = start_task_log("run", "一键执行全部")
    try:
        new_papers = fetch_latest_papers()
        concurrency = get_concurrency()
        analyzed_count = analyze_pending_papers(limit=100, concurrency=concurrency)
        readme_path, daily_path = generate_all_markdown()
        msg = f"完成！抓取 {len(new_papers)} 篇，分析 {analyzed_count} 篇（并发数 {concurrency}）"
        finish_task_log(log_id, "success", msg, f"fetched={len(new_papers)}, analyzed={analyzed_count}, concurrency={concurrency}")
        return jsonify({"status": "ok", "fetched": len(new_papers), "analyzed": analyzed_count, "concurrency": concurrency, "readme": readme_path, "daily": daily_path, "message": msg})
    except Exception as e:
        finish_task_log(log_id, "error", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/providers/presets", methods=["GET"])
def api_provider_presets():
    return jsonify(get_provider_presets())


@app.route("/api/providers", methods=["GET"])
def api_list_providers():
    settings = load_settings()
    providers = settings.get("providers", {})
    active = settings.get("active_provider", "")
    result = {}
    for k, v in providers.items():
        item = v.copy()
        if item.get("api_key"):
            key = item["api_key"]
            item["api_key_masked"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
        else:
            item["api_key_masked"] = ""
        item["is_active"] = (k == active)
        result[k] = item
    return jsonify({"active_provider": active, "providers": result})


@app.route("/api/providers", methods=["POST"])
def api_add_provider():
    try:
        data = request.get_json()
        key = data.get("key", "").strip()
        if not key:
            return jsonify({"status": "error", "message": "供应商ID不能为空"}), 400
        config = {
            "name": data.get("name", key),
            "api_key": data.get("api_key", ""),
            "base_url": data.get("base_url", ""),
            "model": data.get("model", ""),
            "temperature": float(data.get("temperature", 0.3)),
            "max_tokens": int(data.get("max_tokens", 1000)),
        }
        if add_provider(key, config):
            return jsonify({"status": "ok", "message": f"供应商 {config['name']} 已添加"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/providers/<key>", methods=["PUT"])
def api_update_provider(key):
    try:
        data = request.get_json()
        config = {}
        if "name" in data:
            config["name"] = data["name"]
        if "api_key" in data and data["api_key"]:
            config["api_key"] = data["api_key"]
        if "base_url" in data:
            config["base_url"] = data["base_url"]
        if "model" in data:
            config["model"] = data["model"]
        if "temperature" in data:
            config["temperature"] = float(data["temperature"])
        if "max_tokens" in data:
            config["max_tokens"] = int(data["max_tokens"])
        if update_provider(key, config):
            return jsonify({"status": "ok", "message": "已更新"})
        return jsonify({"status": "error", "message": "更新失败，供应商不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/providers/<key>", methods=["DELETE"])
def api_delete_provider(key):
    if remove_provider(key):
        return jsonify({"status": "ok", "message": "已删除"})
    return jsonify({"status": "error", "message": "删除失败"}), 400


@app.route("/api/providers/<key>/activate", methods=["POST"])
def api_activate_provider(key):
    if switch_provider(key):
        return jsonify({"status": "ok", "message": f"已切换到 {key}"})
    return jsonify({"status": "error", "message": "切换失败，供应商不存在"}), 404


@app.route("/api/test_connection", methods=["POST"])
def api_test_connection():
    try:
        from openai import OpenAI
        from settings import get_ai_config
        cfg = get_ai_config()
        if not cfg["api_key"]:
            return jsonify({"status": "error", "message": "请先配置 API Key"}), 400
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Hello, reply with 'ok' only."}],
            max_tokens=10,
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"status": "ok", "message": f"连接成功！模型回复: {reply}"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"连接失败: {str(e)}"}), 500


@app.route("/api/prompts", methods=["GET"])
def api_get_prompts():
    return jsonify(get_prompts())


@app.route("/api/prompts", methods=["POST"])
def api_save_prompts():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400
        prompts = {
            "system_prompt": data.get("system_prompt", ""),
            "user_prompt": data.get("user_prompt", ""),
        }
        if save_prompts(prompts):
            return jsonify({"status": "ok", "message": "Prompt 已保存"})
        return jsonify({"status": "error", "message": "保存失败"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/db/info", methods=["GET"])
def api_db_info():
    import os
    from config import DB_PATH, OUTPUT_DIR, DAILY_DIR
    from database import get_all_tags, get_all_categories

    total = get_paper_count()
    analyzed = get_analyzed_count()
    unanalyzed = get_unanalyzed_count()
    tags = get_all_tags()
    categories = get_all_categories()

    dates = get_all_dates()
    date_range = ""
    if dates:
        date_range = f"{dates[-1][0]} ~ {dates[0][0]}"

    db_size = "N/A"
    if os.path.exists(DB_PATH):
        size_bytes = os.path.getsize(DB_PATH)
        if size_bytes > 1024 * 1024:
            db_size = f"{size_bytes / 1024 / 1024:.1f} MB"
        else:
            db_size = f"{size_bytes / 1024:.1f} KB"

    avg_rating = None
    if analyzed > 0:
        conn = None
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT AVG(rating) FROM analysis")
            row = cursor.fetchone()
            if row and row[0]:
                avg_rating = f"{row[0]:.1f}"
        except:
            pass
        finally:
            if conn:
                conn.close()

    return jsonify({
        "total_papers": total,
        "analyzed_papers": analyzed,
        "unanalyzed_papers": unanalyzed,
        "total_tags": len(tags),
        "total_categories": len(categories),
        "date_range": date_range,
        "db_size": db_size,
        "avg_rating": avg_rating,
        "db_path": DB_PATH,
        "output_dir": OUTPUT_DIR,
        "daily_dir": DAILY_DIR,
    })


@app.route("/api/admin/password", methods=["POST"])
def api_set_admin_password():
    try:
        data = request.get_json()
        current = data.get("current_password", "")
        new_pwd = data.get("new_password", "")

        if has_admin_password():
            if not verify_admin_password(current):
                return jsonify({"status": "error", "message": "当前密码错误"}), 403

        if not new_pwd:
            return jsonify({"status": "error", "message": "新密码不能为空"}), 400

        set_admin_password(new_pwd)
        return jsonify({"status": "ok", "message": "管理密码已设置"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/password", methods=["DELETE"])
def api_clear_admin_password():
    try:
        set_admin_password("")
        return jsonify({"status": "ok", "message": "管理密码已清除"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/analysis", methods=["PUT"])
def api_update_paper_analysis(arxiv_id):
    try:
        paper = get_paper_by_arxiv_id(arxiv_id)
        if not paper:
            return jsonify({"status": "error", "message": "论文不存在"}), 404

        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "无效的请求数据"}), 400

        update_analysis(paper["id"], data)
        return jsonify({"status": "ok", "message": "已更新"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/hide", methods=["POST"])
def api_hide_paper(arxiv_id):
    try:
        if hide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/unhide", methods=["POST"])
def api_unhide_paper(arxiv_id):
    try:
        if unhide_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已取消隐藏"})
        return jsonify({"status": "error", "message": "操作失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>", methods=["DELETE"])
def api_delete_paper(arxiv_id):
    try:
        if delete_paper(arxiv_id):
            return jsonify({"status": "ok", "message": "论文已删除"})
        return jsonify({"status": "error", "message": "删除失败"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/paper/<arxiv_id>/reanalyze", methods=["POST"])
def api_reanalyze_paper(arxiv_id):
    try:
        from analyzer import analyze_paper
        from database import get_paper_by_arxiv_id, update_analysis
        paper = get_paper_by_arxiv_id(arxiv_id)
        if not paper:
            return jsonify({"status": "error", "message": "论文不存在"}), 404

        paper_data = dict(paper)
        import json
        if paper_data.get("authors") and isinstance(paper_data["authors"], str):
            paper_data["authors"] = json.loads(paper_data["authors"])
        if paper_data.get("categories") and isinstance(paper_data["categories"], str):
            paper_data["categories"] = json.loads(paper_data["categories"])

        result_data, result, error = analyze_paper(paper_data)
        if result:
            update_analysis(paper["id"], result)
            return jsonify({"status": "ok", "message": "重新分析完成", "rating": result.get("rating")})
        return jsonify({"status": "error", "message": f"分析失败: {error}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/tasks")
def tasks_page():
    return render_template("tasks.html")


@app.route("/api/tasks/stats", methods=["GET"])
def api_task_stats():
    stats = get_task_stats()
    running = get_running_tasks()
    return jsonify({"stats": stats, "running": running})


@app.route("/api/tasks/logs", methods=["GET"])
def api_task_logs():
    task_name = request.args.get("task", None)
    page = request.args.get("page", 1, type=int)
    per_page = 30
    logs, total = get_task_logs(task_name=task_name, limit=per_page, offset=(page - 1) * per_page)
    return jsonify({"logs": logs, "total": total, "page": page, "per_page": per_page})


@app.route("/api/tasks/clear", methods=["POST"])
def api_clear_logs():
    keep_days = request.args.get("keep_days", 30, type=int)
    deleted = clear_task_logs(keep_days=keep_days)
    return jsonify({"status": "ok", "message": f"已清理 {deleted} 条 {keep_days} 天前的日志"})


@app.route("/api/tasks/scheduled", methods=["GET"])
def api_scheduled_tasks():
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name or job.id,
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "未调度",
            "trigger": str(job.trigger),
        })
    return jsonify(jobs)


def create_app():
    init_db()

    scheduler.add_job(
        daily_pipeline,
        "cron",
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        id="daily_pipeline",
        name="每日定时任务",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started: daily at {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}")

    return app


if __name__ == "__main__":
    app = create_app()
    logger.info(f"Starting web server at http://{WEB_HOST}:{WEB_PORT}")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
