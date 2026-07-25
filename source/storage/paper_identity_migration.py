"""Migration from arXiv-only paper identity to generic library papers."""


def migrate_generic_paper_identity(conn):
    """Rebuild papers while preserving IDs referenced by all child tables."""
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("ALTER TABLE papers RENAME TO papers_arxiv_v2")
    conn.execute("""
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_key TEXT UNIQUE NOT NULL,
            arxiv_id TEXT UNIQUE,
            source_type TEXT NOT NULL DEFAULT 'arxiv',
            source_id TEXT,
            ingest_mode TEXT NOT NULL DEFAULT 'feed',
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            abstract TEXT NOT NULL,
            categories TEXT NOT NULL,
            primary_category TEXT,
            url TEXT,
            pdf_url TEXT,
            venue TEXT,
            published_date TEXT,
            updated_date TEXT,
            pdf_local_path TEXT,
            pdf_sha256 TEXT,
            pdf_size_bytes INTEGER,
            hidden INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (source_type IN ('arxiv', 'openreview', 'doi', 'web', 'upload')),
            CHECK (ingest_mode IN ('feed', 'manual'))
        )
    """)
    conn.execute("""
        INSERT INTO papers (
            id, paper_key, arxiv_id, source_type, source_id, ingest_mode,
            title, authors, abstract, categories, primary_category, url,
            pdf_url, published_date, updated_date, hidden, created_at
        )
        SELECT
            id, arxiv_id, arxiv_id, 'arxiv', arxiv_id, 'feed',
            title, authors, abstract, categories, primary_category, url,
            pdf_url, published_date, updated_date, COALESCE(hidden, 0), created_at
        FROM papers_arxiv_v2
    """)
    conn.execute("DROP TABLE papers_arxiv_v2")
    conn.execute("CREATE INDEX idx_papers_arxiv_id ON papers(arxiv_id)")
    conn.execute("CREATE INDEX idx_papers_published ON papers(published_date)")
    conn.execute("CREATE INDEX idx_papers_category ON papers(primary_category)")
    conn.execute("CREATE INDEX idx_papers_source_type ON papers(source_type)")
    conn.execute("CREATE INDEX idx_papers_ingest_mode ON papers(ingest_mode)")
    conn.execute("""
        CREATE UNIQUE INDEX uq_papers_source_identity
        ON papers(source_type, source_id)
        WHERE source_id IS NOT NULL AND source_id <> ''
    """)
    conn.execute("""
        CREATE UNIQUE INDEX uq_papers_pdf_sha256
        ON papers(pdf_sha256)
        WHERE pdf_sha256 IS NOT NULL AND pdf_sha256 <> ''
    """)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"paper identity migration broke foreign keys: {violations[:5]}")
