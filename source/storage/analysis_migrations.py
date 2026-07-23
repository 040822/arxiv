"""Schema migrations for analysis records."""

import logging

logger = logging.getLogger(__name__)


def _is_empty_value(field, value):
    if value is None or value == "":
        return True
    return field == "tags" and value == "[]"


def migrate_analysis_unique(conn):
    """Merge duplicate analysis rows and enforce the papers-to-analysis 1:1 rule."""
    columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(analysis)").fetchall()
        if row["name"] not in {"id", "paper_id"}
    ]
    duplicate_paper_ids = [
        int(row["paper_id"])
        for row in conn.execute(
            """
            SELECT paper_id
            FROM analysis
            GROUP BY paper_id
            HAVING COUNT(*) > 1
            ORDER BY paper_id
            """
        ).fetchall()
    ]
    deleted_rows = 0
    conflict_count = 0
    for paper_id in duplicate_paper_ids:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM analysis WHERE paper_id = ? ORDER BY id",
                (paper_id,),
            ).fetchall()
        ]
        canonical = rows[0]
        updates = {}
        for duplicate in rows[1:]:
            for field in columns:
                current = updates.get(field, canonical.get(field))
                candidate = duplicate.get(field)
                if _is_empty_value(field, current) and not _is_empty_value(field, candidate):
                    updates[field] = candidate
                elif (
                    not _is_empty_value(field, current)
                    and not _is_empty_value(field, candidate)
                    and current != candidate
                ):
                    conflict_count += 1
        if updates:
            assignments = ", ".join(f"{field} = ?" for field in updates)
            conn.execute(
                f"UPDATE analysis SET {assignments} WHERE id = ?",
                (*updates.values(), canonical["id"]),
            )
        duplicate_ids = [row["id"] for row in rows[1:]]
        conn.executemany(
            "DELETE FROM analysis WHERE id = ?",
            [(row_id,) for row_id in duplicate_ids],
        )
        deleted_rows += len(duplicate_ids)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_paper_id ON analysis(paper_id)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_analysis_paper_id")
    if duplicate_paper_ids:
        logger.warning(
            "Merged %s duplicate analysis groups; deleted %s rows; kept %s conflicting values",
            len(duplicate_paper_ids),
            deleted_rows,
            conflict_count,
        )
