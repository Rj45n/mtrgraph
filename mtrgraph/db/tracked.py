"""S3 tracked objects — only what mtrgraph PUT itself, never user data."""
from __future__ import annotations

from datetime import datetime, timezone


def track_s3_object(conn, *, schedule_id: int, endpoint: str, bucket: str,
                     key: str, size_bytes: int | None) -> int:
    cur = conn.execute(
        """INSERT INTO s3_tracked_objects(schedule_id, endpoint, bucket, key,
                                            size_bytes, created_at)
           VALUES(?,?,?,?,?,?)""",
        (schedule_id, endpoint, bucket, key, size_bytes,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def list_tracked_alive(conn, schedule_id: int) -> list:
    return conn.execute(
        """SELECT * FROM s3_tracked_objects
           WHERE schedule_id=? AND deleted_at IS NULL
           ORDER BY id""",
        (schedule_id,),
    ).fetchall()


def count_tracked_alive(conn, schedule_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM s3_tracked_objects WHERE schedule_id=? AND deleted_at IS NULL",
        (schedule_id,),
    ).fetchone()[0]


def mark_tracked_deleted(conn, tracked_id: int) -> None:
    conn.execute(
        "UPDATE s3_tracked_objects SET deleted_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), tracked_id),
    )


def get_tracked_by_key(conn, schedule_id: int, key: str):
    return conn.execute(
        """SELECT * FROM s3_tracked_objects
           WHERE schedule_id=? AND key=? AND deleted_at IS NULL""",
        (schedule_id, key),
    ).fetchone()


def list_tracked_all(conn, schedule_id: int) -> list:
    return conn.execute(
        "SELECT * FROM s3_tracked_objects WHERE schedule_id=? ORDER BY id DESC",
        (schedule_id,),
    ).fetchall()
