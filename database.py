import sqlite3
import json
import os
import logging
from datetime import datetime
from config import DB_PATH, DB_DIR

logger = logging.getLogger(__name__)


def get_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            abstract TEXT NOT NULL,
            categories TEXT NOT NULL,
            primary_category TEXT,
            url TEXT,
            pdf_url TEXT,
            published_date TEXT,
            updated_date TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id INTEGER NOT NULL,
            tags TEXT,
            summary_cn TEXT,
            summary_en TEXT,
            rating INTEGER DEFAULT 0,
            value_comment TEXT,
            qa_analysis TEXT,
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_category ON papers(primary_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_paper_id ON analysis(paper_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_rating ON analysis(rating)")

    cursor.execute("PRAGMA table_info(analysis)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "qa_analysis" not in columns:
        cursor.execute("ALTER TABLE analysis ADD COLUMN qa_analysis TEXT")

    cursor.execute("PRAGMA table_info(papers)")
    paper_columns = [row["name"] for row in cursor.fetchall()]
    if "hidden" not in paper_columns:
        cursor.execute("ALTER TABLE papers ADD COLUMN hidden INTEGER DEFAULT 0")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            message TEXT,
            detail TEXT,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            duration_sec REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_name ON task_logs(task_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_status ON task_logs(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_started ON task_logs(started_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL,
            paper_count INTEGER DEFAULT 0,
            analyzed_count INTEGER DEFAULT 0,
            avg_rating REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date)")

    conn.commit()
    conn.close()


def paper_exists(arxiv_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def insert_paper(paper_data):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO papers (arxiv_id, title, authors, abstract, categories,
                              primary_category, url, pdf_url, published_date, updated_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_data["arxiv_id"],
            paper_data["title"],
            json.dumps(paper_data["authors"], ensure_ascii=False),
            paper_data["abstract"],
            json.dumps(paper_data["categories"], ensure_ascii=False),
            paper_data["primary_category"],
            paper_data["url"],
            paper_data["pdf_url"],
            paper_data["published_date"],
            paper_data["updated_date"],
        ))
        conn.commit()
        paper_id = cursor.lastrowid
        conn.close()
        return paper_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def insert_analysis(paper_id, analysis_data):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM analysis WHERE paper_id = ?", (paper_id,))
    if cursor.fetchone():
        conn.close()
        logger.debug(f"Analysis already exists for paper_id={paper_id}, skipping.")
        return None

    cursor.execute("""
        INSERT INTO analysis (paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        paper_id,
        json.dumps(analysis_data["tags"], ensure_ascii=False),
        analysis_data["summary_cn"],
        analysis_data["summary_en"],
        analysis_data["rating"],
        analysis_data["value_comment"],
        analysis_data.get("qa_analysis", ""),
    ))
    conn.commit()
    analysis_id = cursor.lastrowid
    conn.close()
    return analysis_id


def get_paper_by_arxiv_id(arxiv_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_papers_with_analysis(date=None, tag=None, min_rating=None, limit=100, offset=0, count_total=False):
    conn = get_connection()
    cursor = conn.cursor()

    base_query = """
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE (p.hidden IS NULL OR p.hidden = 0)
    """
    params = []

    if date:
        base_query += " AND p.published_date = ?"
        params.append(date)

    if min_rating is not None:
        base_query += " AND a.rating >= ?"
        params.append(min_rating)

    if tag:
        base_query += " AND a.tags LIKE ?"
        params.append(f"%{tag}%")

    total = 0
    if count_total:
        count_sql = "SELECT COUNT(*) " + base_query
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

    query = "SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis, a.analyzed_at " + base_query
    query += " ORDER BY p.published_date DESC, a.rating DESC"
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)

    if count_total:
        return results, total
    return results


def browse_papers(date=None, tag=None, min_rating=None, max_rating=None,
                  category=None, has_analysis=None, limit=20, offset=0):
    conn = get_connection()
    cursor = conn.cursor()

    base_query = """
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE (p.hidden IS NULL OR p.hidden = 0)
    """
    params = []

    if date:
        base_query += " AND p.published_date = ?"
        params.append(date)

    if category:
        base_query += " AND p.primary_category = ?"
        params.append(category)

    if min_rating is not None:
        base_query += " AND a.rating >= ?"
        params.append(min_rating)

    if max_rating is not None:
        base_query += " AND a.rating <= ?"
        params.append(max_rating)

    if tag:
        base_query += " AND a.tags LIKE ?"
        params.append(f"%{tag}%")

    if has_analysis == "yes":
        base_query += " AND a.id IS NOT NULL"
    elif has_analysis == "no":
        base_query += " AND a.id IS NULL"

    count_query = "SELECT COUNT(*) as cnt " + base_query
    cursor.execute(count_query, params)
    total = cursor.fetchone()["cnt"]

    data_query = """
        SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis, a.analyzed_at
    """ + base_query + " ORDER BY p.published_date DESC, a.rating DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(data_query, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)

    return results, total


def get_all_categories():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT primary_category, COUNT(*) as cnt
        FROM papers
        GROUP BY primary_category
        ORDER BY cnt DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [(row["primary_category"], row["cnt"]) for row in rows]


def get_all_dates():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT published_date, COUNT(*) as cnt
        FROM papers
        GROUP BY published_date
        ORDER BY published_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [(row["published_date"], row["cnt"]) for row in rows]


def get_all_tags():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT tags FROM analysis WHERE tags IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    tag_counts = {}
    for row in rows:
        tags = json.loads(row["tags"])
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(tag_counts.items(), key=lambda x: -x[1])


def get_paper_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM papers")
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_analyzed_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM analysis")
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_unanalyzed_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE a.id IS NULL
    """)
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_unanalyzed_papers(limit=100):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.* FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE a.id IS NULL
        ORDER BY p.published_date DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        results.append(r)
    return results


def search_papers(keyword, limit=50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.*, a.tags, a.summary_cn, a.summary_en, a.rating, a.value_comment, a.qa_analysis
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE (p.hidden IS NULL OR p.hidden = 0)
        AND (p.title LIKE ? OR p.abstract LIKE ? OR a.summary_cn LIKE ? OR a.tags LIKE ? OR a.qa_analysis LIKE ?)
        ORDER BY a.rating DESC, p.published_date DESC
        LIMIT ?
    """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit))
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        results.append(r)
    return results


def get_daily_stats(date):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END) as analyzed,
               AVG(a.rating) as avg_rating
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
    """, (date,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_analysis_by_paper_id(paper_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM analysis WHERE paper_id = ?", (paper_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        r = dict(row)
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        return r
    return None


def update_analysis(paper_id, data):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM analysis WHERE paper_id = ?", (paper_id,))
    exists = cursor.fetchone()

    if exists:
        sets = []
        params = []
        if "rating" in data:
            sets.append("rating = ?")
            params.append(int(data["rating"]))
        if "tags" in data:
            sets.append("tags = ?")
            params.append(json.dumps(data["tags"], ensure_ascii=False))
        if "summary_cn" in data:
            sets.append("summary_cn = ?")
            params.append(data["summary_cn"])
        if "value_comment" in data:
            sets.append("value_comment = ?")
            params.append(data["value_comment"])
        if "qa_analysis" in data:
            sets.append("qa_analysis = ?")
            params.append(data["qa_analysis"])

        if sets:
            params.append(paper_id)
            cursor.execute(f"UPDATE analysis SET {', '.join(sets)} WHERE paper_id = ?", params)
    else:
        cursor.execute("""
            INSERT INTO analysis (paper_id, tags, summary_cn, summary_en, rating, value_comment, qa_analysis)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            paper_id,
            json.dumps(data.get("tags", []), ensure_ascii=False),
            data.get("summary_cn", ""),
            data.get("summary_en", ""),
            int(data.get("rating", 0)),
            data.get("value_comment", ""),
            data.get("qa_analysis", ""),
        ))

    conn.commit()
    conn.close()
    return True


def hide_paper(arxiv_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE papers SET hidden = 1 WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def unhide_paper(arxiv_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE papers SET hidden = 0 WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_paper(arxiv_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def batch_delete_papers(arxiv_ids):
    if not arxiv_ids:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join(["?"] * len(arxiv_ids))
    cursor.execute(f"DELETE FROM papers WHERE arxiv_id IN ({placeholders})", arxiv_ids)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected


def start_task_log(task_name, message=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO task_logs (task_name, status, message, started_at) VALUES (?, 'running', ?, ?)",
        (task_name, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    log_id = cursor.lastrowid
    conn.close()
    return log_id


def finish_task_log(log_id, status, message="", detail=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT started_at FROM task_logs WHERE id = ?", (log_id,))
    row = cursor.fetchone()
    duration = 0
    if row and row["started_at"]:
        started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
        duration = (datetime.now() - started).total_seconds()

    cursor.execute(
        "UPDATE task_logs SET status = ?, message = ?, detail = ?, finished_at = ?, duration_sec = ? WHERE id = ?",
        (status, message, detail, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), round(duration, 1), log_id)
    )
    conn.commit()
    conn.close()


def get_task_logs(task_name=None, limit=50, offset=0):
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM task_logs WHERE 1=1"
    params = []

    if task_name:
        query += " AND task_name = ?"
        params.append(task_name)

    count_query = query.replace("SELECT *", "SELECT COUNT(*) as cnt")
    cursor.execute(count_query, params)
    total = cursor.fetchone()["cnt"]

    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows], total


def get_task_stats():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT task_name,
               COUNT(*) as total_runs,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_runs,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_runs,
               SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
               MAX(started_at) as last_run,
               AVG(duration_sec) as avg_duration
        FROM task_logs
        GROUP BY task_name
        ORDER BY last_run DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_running_tasks():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM task_logs WHERE status = 'running' ORDER BY started_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def clear_task_logs(keep_days=30):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM task_logs WHERE started_at < datetime('now', ?)",
        (f"-{keep_days} days",)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def save_report(report_date, content, paper_count, analyzed_count, avg_rating):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reports (report_date, content, paper_count, analyzed_count, avg_rating)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET
            content=excluded.content,
            paper_count=excluded.paper_count,
            analyzed_count=excluded.analyzed_count,
            avg_rating=excluded.avg_rating,
            created_at=CURRENT_TIMESTAMP
    """, (report_date, content, paper_count, analyzed_count, avg_rating))
    conn.commit()
    conn.close()


def get_reports(limit=50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports ORDER BY report_date DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_report_by_date(report_date):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE report_date = ?", (report_date,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_report_dates():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT report_date, paper_count, analyzed_count, avg_rating FROM reports ORDER BY report_date DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def generate_report_content(date):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT p.*, a.tags, a.summary_cn, a.rating, a.value_comment
        FROM papers p
        LEFT JOIN analysis a ON p.id = a.paper_id
        WHERE p.published_date = ?
        ORDER BY a.rating DESC, p.arxiv_id
    """, (date,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return None, 0, 0, 0

    papers = []
    for row in rows:
        r = dict(row)
        if r.get("authors") and isinstance(r["authors"], str):
            r["authors"] = json.loads(r["authors"])
        if r.get("categories") and isinstance(r["categories"], str):
            r["categories"] = json.loads(r["categories"])
        if r.get("tags") and isinstance(r["tags"], str):
            r["tags"] = json.loads(r["tags"])
        papers.append(r)

    total = len(papers)
    analyzed = sum(1 for p in papers if p.get("rating") and p["rating"] > 0)
    ratings = [p["rating"] for p in papers if p.get("rating") and p["rating"] > 0]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0

    tag_counts = {}
    category_counts = {}
    for p in papers:
        if p.get("tags") and isinstance(p["tags"], list):
            for t in p["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if p.get("categories") and isinstance(p["categories"], list):
            for c in p["categories"]:
                category_counts[c] = category_counts.get(c, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:15]
    top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:10]
    high_rated = [p for p in papers if p.get("rating") and p["rating"] >= 4]
    high_rated.sort(key=lambda x: -x["rating"])

    html = f'<div class="report-summary">'
    html += f'<div class="report-stats">'
    html += f'<div class="report-stat"><span class="report-stat-val">{total}</span><span class="report-stat-label">论文总数</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{analyzed}</span><span class="report-stat-label">已分析</span></div>'
    html += f'<div class="report-stat"><span class="report-stat-val">{avg_rating}</span><span class="report-stat-label">平均评级</span></div>'
    html += f'</div></div>'

    if top_categories:
        html += '<div class="report-section"><h3>📂 分类分布</h3><div class="report-tags">'
        for cat, cnt in top_categories:
            html += f'<span class="tag-badge">{cat} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    if top_tags:
        html += '<div class="report-section"><h3>🏷️ 热门标签</h3><div class="report-tags">'
        for tag, cnt in top_tags:
            html += f'<span class="tag-badge">{tag} <span class="tag-count">{cnt}</span></span>'
        html += '</div></div>'

    if high_rated:
        html += '<div class="report-section"><h3>⭐ 高分论文 (4★+)</h3><div class="report-papers">'
        for p in high_rated:
            stars = '★' * p['rating'] + '☆' * (5 - p['rating'])
            authors = ', '.join(p['authors'][:3]) if isinstance(p.get('authors'), list) else str(p.get('authors', ''))
            cats = ' '.join(f'<span class="category-tag">{c}</span>' for c in (p.get('categories') or [])[:3])
            html += f'''<div class="report-paper">
                <div class="report-paper-title"><a href="/paper/{p['arxiv_id']}">{p['title']}</a></div>
                <div class="report-paper-meta"><span class="rating">{stars}</span> {cats}</div>
                <div class="report-paper-authors">{authors}</div>
                {f'<div class="report-paper-comment">{p["value_comment"]}</div>' if p.get('value_comment') else ''}
            </div>'''
        html += '</div></div>'

    html += '<div class="report-section"><h3>📋 全部论文</h3><div class="report-papers">'
    for p in papers:
        stars = ''
        if p.get('rating') and p['rating'] > 0:
            stars = f'<span class="rating">{"★" * p["rating"]}{"☆" * (5 - p["rating"])}</span>'
        cats = ' '.join(f'<span class="category-tag">{c}</span>' for c in (p.get('categories') or [])[:3])
        authors = ', '.join(p['authors'][:3]) if isinstance(p.get('authors'), list) else str(p.get('authors', ''))
        tags_html = ''
        if p.get('tags') and isinstance(p['tags'], list):
            tags_html = ' '.join(f'<span class="tag-small">{t}</span>' for t in p['tags'][:5])
        summary = f'<div class="report-paper-summary">{p["summary_cn"]}</div>' if p.get('summary_cn') else ''
        html += f'''<div class="report-paper">
            <div class="report-paper-title"><a href="/paper/{p['arxiv_id']}">{p['title']}</a> {stars}</div>
            <div class="report-paper-meta">{cats}</div>
            <div class="report-paper-authors">{authors}</div>
            {'<div class="report-paper-tags">' + tags_html + '</div>' if tags_html else ''}
            {summary}
        </div>'''
    html += '</div></div>'

    return html, total, analyzed, avg_rating
