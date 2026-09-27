from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .utils import json_list, normalize_chapter_index, normalize_plate


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class LibraryDatabase:
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")
            self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS comics (
                id TEXT PRIMARY KEY,
                source_id TEXT UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                nickname TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                works_json TEXT NOT NULL DEFAULT '[]',
                actors_json TEXT NOT NULL DEFAULT '[]',
                note TEXT NOT NULL DEFAULT '',
                root_path TEXT,
                cover_path TEXT,
                storage_kind TEXT NOT NULL DEFAULT 'local',
                cover_privacy_enabled INTEGER NOT NULL DEFAULT 1,
                cover_privacy_direction TEXT NOT NULL DEFAULT 'below',
                cover_privacy_start INTEGER NOT NULL DEFAULT 58,
                page_count INTEGER NOT NULL DEFAULT 0,
                progress_page INTEGER NOT NULL DEFAULT 0,
                source_status TEXT NOT NULL DEFAULT 'local',
                source_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_read_at TEXT,
                read_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS collections (
                id TEXT PRIMARY KEY,
                parent_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comic_collections (
                comic_id TEXT NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
                collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                PRIMARY KEY (comic_id, collection_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS online_metadata_cache (
                album_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                works_json TEXT NOT NULL DEFAULT '[]',
                actors_json TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                source_updated_at TEXT NOT NULL DEFAULT '',
                page_count INTEGER NOT NULL DEFAULT 0,
                first_photo_id INTEGER,
                episode_manifest_json TEXT NOT NULL DEFAULT '[]',
                details_complete INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS library_series (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'custom',
                auto_update_enabled INTEGER NOT NULL DEFAULT 1,
                last_checked_at TEXT,
                last_update_found_at TEXT,
                update_items_json TEXT NOT NULL DEFAULT '[]',
                update_error TEXT
            );
            CREATE TABLE IF NOT EXISTS library_series_members (
                series_id TEXT NOT NULL REFERENCES library_series(id) ON DELETE CASCADE,
                comic_id TEXT NOT NULL UNIQUE REFERENCES comics(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                PRIMARY KEY (series_id, comic_id)
            );
            CREATE INDEX IF NOT EXISTS idx_comics_source ON comics(source_id);
            CREATE INDEX IF NOT EXISTS idx_comics_updated ON comics(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_library_series_members_order
              ON library_series_members(series_id, position);
            """
        )
        existing_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(comics)").fetchall()
        }
        extra_columns = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "source_updated_at": "TEXT NOT NULL DEFAULT ''",
            "chapters_json": "TEXT NOT NULL DEFAULT '[]'",
            "series_id": "TEXT",
            "series_title": "TEXT NOT NULL DEFAULT ''",
            "chapter_index": "REAL NOT NULL DEFAULT 1",
            "storage_kind": "TEXT NOT NULL DEFAULT 'local'",
            "cover_privacy_enabled": "INTEGER NOT NULL DEFAULT 1",
            "cover_privacy_direction": "TEXT NOT NULL DEFAULT 'below'",
            "cover_privacy_start": "INTEGER NOT NULL DEFAULT 58",
            "read_count": "INTEGER NOT NULL DEFAULT 0",
            "works_json": "TEXT NOT NULL DEFAULT '[]'",
            "actors_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        storage_kind_was_added = "storage_kind" not in existing_columns
        for name, definition in extra_columns.items():
            if name not in existing_columns:
                self.connection.execute(f"ALTER TABLE comics ADD COLUMN {name} {definition}")
        if storage_kind_was_added:
            self.connection.execute(
                "UPDATE comics SET storage_kind = 'remote' WHERE root_path IS NULL AND source_id IS NOT NULL"
            )
        existing_series_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(library_series)").fetchall()
        }
        extra_series_columns = {
            "kind": "TEXT NOT NULL DEFAULT 'custom'",
            "auto_update_enabled": "INTEGER NOT NULL DEFAULT 1",
            "last_checked_at": "TEXT",
            "last_update_found_at": "TEXT",
            "update_items_json": "TEXT NOT NULL DEFAULT '[]'",
            "update_error": "TEXT",
        }
        kind_was_added = "kind" not in existing_series_columns
        for name, definition in extra_series_columns.items():
            if name not in existing_series_columns:
                self.connection.execute(f"ALTER TABLE library_series ADD COLUMN {name} {definition}")
        if kind_was_added:
            # Older releases did not persist provenance. Existing groups whose
            # every member points at the same JM album are native source series.
            self.connection.execute(
                """
                UPDATE library_series
                SET kind = 'source'
                WHERE id IN (
                  SELECT m.series_id
                  FROM library_series_members m
                  JOIN comics c ON c.id = m.comic_id
                  GROUP BY m.series_id
                  HAVING count(*) >= 2
                     AND count(c.series_id) = count(*)
                     AND count(DISTINCT c.series_id) = 1
                )
                """
            )
        existing_metadata_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(online_metadata_cache)").fetchall()
        }
        extra_metadata_columns = {
            "first_photo_id": "INTEGER",
            "episode_manifest_json": "TEXT NOT NULL DEFAULT '[]'",
            "works_json": "TEXT NOT NULL DEFAULT '[]'",
            "actors_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in extra_metadata_columns.items():
            if name not in existing_metadata_columns:
                self.connection.execute(f"ALTER TABLE online_metadata_cache ADD COLUMN {name} {definition}")
        self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    @staticmethod
    def _comic(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        title = row["title"] or ""
        nickname = row["nickname"] or ""
        source_id = row["source_id"]
        return {
            "id": row["id"],
            "sourceId": source_id,
            "title": title,
            "nickname": nickname,
            "displayName": nickname.strip() or title or (f"JM{source_id}" if source_id else "未命名本子"),
            "authors": json_list(row["authors_json"]),
            "tags": json_list(row["tags_json"]),
            "works": json_list(row["works_json"]),
            "actors": json_list(row["actors_json"]),
            "note": row["note"] or "",
            "description": row["description"] or "",
            "publishedAt": row["published_at"] or "",
            "updatedAtSource": row["source_updated_at"] or "",
            "chapters": json_list(row["chapters_json"]),
            "seriesId": row["series_id"],
            "seriesTitle": row["series_title"] or "",
            "chapterIndex": row["chapter_index"] or 1,
            "rootPath": row["root_path"],
            "coverPath": row["cover_path"],
            "storageKind": row["storage_kind"] if row["storage_kind"] in {"local", "download", "cache", "remote"} else "local",
            "coverPrivacyEnabled": bool(row["cover_privacy_enabled"]),
            "coverPrivacyDirection": row["cover_privacy_direction"] if row["cover_privacy_direction"] in {"above", "below"} else "below",
            "coverPrivacyStart": max(15, min(85, int(row["cover_privacy_start"] or 58))),
            "pageCount": row["page_count"] or 0,
            "progressPage": row["progress_page"] or 0,
            "sourceStatus": row["source_status"],
            "sourceError": row["source_error"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "lastReadAt": row["last_read_at"],
            "readCount": int(row["read_count"] or 0),
            "collections": json_list(row["collection_ids_json"]),
        }

    def _comic_select(self) -> str:
        return """
            SELECT c.*,
              COALESCE((SELECT json_group_array(collection_id)
                        FROM comic_collections cc WHERE cc.comic_id = c.id), '[]') AS collection_ids_json
            FROM comics c
        """

    def list_comics(self, search: str = "", collection_id: str | None = None) -> list[dict[str, Any]]:
        clauses = ["(? = '' OR lower(c.title || ' ' || c.nickname || ' ' || c.authors_json || ' ' || c.tags_json || ' ' || c.works_json || ' ' || c.actors_json || ' ' || coalesce(c.source_id, '')) LIKE ?)"]
        params: list[Any] = [search.strip(), f"%{search.strip().casefold()}%"]
        if collection_id == "unfiled":
            clauses.append("NOT EXISTS (SELECT 1 FROM comic_collections cc0 WHERE cc0.comic_id = c.id)")
        elif collection_id:
            clauses.append("EXISTS (SELECT 1 FROM comic_collections cc1 WHERE cc1.comic_id = c.id AND cc1.collection_id = ?)")
            params.append(collection_id)
        sql = self._comic_select() + f" WHERE {' AND '.join(clauses)} ORDER BY CASE WHEN c.last_read_at IS NULL THEN 1 ELSE 0 END, c.last_read_at DESC, c.updated_at DESC"
        with self.lock:
            return [self._comic(row) for row in self.connection.execute(sql, params).fetchall()]  # type: ignore[misc]

    def get_comic(self, comic_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(self._comic_select() + " WHERE c.id = ?", (comic_id,)).fetchone()
            return self._comic(row)

    def get_comic_by_source(self, source_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT id FROM comics WHERE source_id = ?", (str(source_id),)).fetchone()
        return self.get_comic(row["id"]) if row else None

    def get_next_source_series_member(self, comic_id: str) -> dict[str, Any] | None:
        """Return the next chapter only when the comic belongs to a native JM source series."""
        with self.lock:
            row = self.connection.execute(
                """
                SELECT following.comic_id
                FROM library_series_members current
                JOIN library_series series
                  ON series.id = current.series_id AND series.kind = 'source'
                JOIN library_series_members following
                  ON following.series_id = current.series_id
                 AND following.position > current.position
                WHERE current.comic_id = ?
                ORDER BY following.position
                LIMIT 1
                """,
                (str(comic_id),),
            ).fetchone()
        return self.get_comic(row["comic_id"]) if row else None

    def upsert_comic(self, data: dict[str, Any]) -> dict[str, Any]:
        # Parallel download jobs can finish the same source chapter together.
        with self.lock:
            return self._upsert_comic_locked(data)

    def _upsert_comic_locked(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_comic(data.get("id", "")) if data.get("id") else None
        if existing is None and data.get("sourceId"):
            existing = self.get_comic_by_source(str(data["sourceId"]))
        comic_id = existing["id"] if existing else str(uuid.uuid4())
        timestamp = now_iso()

        def value(key: str, default: Any) -> Any:
            return data[key] if key in data and data[key] is not None else (existing.get(key, default) if existing else default)

        source_id = value("sourceId", "")
        series_id = value("seriesId", "")
        privacy_direction = str(value("coverPrivacyDirection", "below") or "below").casefold()
        if privacy_direction not in {"above", "below"}:
            privacy_direction = "below"
        try:
            privacy_start = int(value("coverPrivacyStart", 58))
        except (TypeError, ValueError):
            privacy_start = 58
        root_path = value("rootPath", None)
        default_storage_kind = "remote" if source_id and not root_path else "local"
        storage_kind = str(value("storageKind", default_storage_kind) or "").casefold()
        if storage_kind not in {"local", "download", "cache", "remote"}:
            storage_kind = default_storage_kind
        fields = {
            "id": comic_id,
            "sourceId": str(source_id) if source_id not in (None, "") else None,
            "title": value("title", ""),
            "nickname": value("nickname", ""),
            "authors": value("authors", []),
            "tags": value("tags", []),
            "works": value("works", []),
            "actors": value("actors", []),
            "note": value("note", ""),
            "description": value("description", ""),
            "publishedAt": value("publishedAt", ""),
            "updatedAtSource": value("updatedAtSource", ""),
            "chapters": value("chapters", []),
            "seriesId": str(series_id) if series_id not in (None, "") else None,
            "seriesTitle": value("seriesTitle", ""),
            "chapterIndex": normalize_chapter_index(value("chapterIndex", 1) or 1),
            "rootPath": root_path,
            "coverPath": value("coverPath", None),
            "storageKind": storage_kind,
            "coverPrivacyEnabled": bool(value("coverPrivacyEnabled", True)),
            "coverPrivacyDirection": privacy_direction,
            "coverPrivacyStart": max(15, min(85, privacy_start)),
            "pageCount": int(value("pageCount", 0) or 0),
            "progressPage": int(value("progressPage", 0) or 0),
            "sourceStatus": value("sourceStatus", "local"),
            "sourceError": value("sourceError", None),
            "createdAt": existing["createdAt"] if existing else timestamp,
            "lastReadAt": value("lastReadAt", None),
            "readCount": max(0, int(value("readCount", 0) or 0)),
        }
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO comics (
                  id, source_id, title, nickname, authors_json, tags_json, works_json, actors_json, note, description,
                  published_at, source_updated_at, chapters_json, series_id, series_title, chapter_index,
                  root_path, cover_path, storage_kind, cover_privacy_enabled, cover_privacy_direction, cover_privacy_start, page_count,
                  progress_page, source_status, source_error, created_at, updated_at, last_read_at, read_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source_id=excluded.source_id, title=excluded.title, nickname=excluded.nickname,
                  authors_json=excluded.authors_json, tags_json=excluded.tags_json,
                  works_json=excluded.works_json, actors_json=excluded.actors_json, note=excluded.note,
                  description=excluded.description, published_at=excluded.published_at,
                  source_updated_at=excluded.source_updated_at, chapters_json=excluded.chapters_json,
                  series_id=excluded.series_id, series_title=excluded.series_title,
                  chapter_index=excluded.chapter_index,
                  root_path=excluded.root_path, cover_path=excluded.cover_path,
                  storage_kind=excluded.storage_kind,
                  cover_privacy_enabled=excluded.cover_privacy_enabled,
                  cover_privacy_direction=excluded.cover_privacy_direction,
                  cover_privacy_start=excluded.cover_privacy_start,
                  page_count=excluded.page_count,
                  progress_page=excluded.progress_page, source_status=excluded.source_status,
                  source_error=excluded.source_error, updated_at=excluded.updated_at, last_read_at=excluded.last_read_at,
                  read_count=excluded.read_count
                """,
                (
                    fields["id"], fields["sourceId"], fields["title"], fields["nickname"],
                    json.dumps(fields["authors"], ensure_ascii=False), json.dumps(fields["tags"], ensure_ascii=False),
                    json.dumps(fields["works"], ensure_ascii=False), json.dumps(fields["actors"], ensure_ascii=False),
                    fields["note"], fields["description"], fields["publishedAt"], fields["updatedAtSource"],
                    json.dumps(fields["chapters"], ensure_ascii=False), fields["seriesId"], fields["seriesTitle"],
                    fields["chapterIndex"], fields["rootPath"], fields["coverPath"], fields["storageKind"],
                    int(fields["coverPrivacyEnabled"]), fields["coverPrivacyDirection"], fields["coverPrivacyStart"],
                    fields["pageCount"], fields["progressPage"], fields["sourceStatus"], fields["sourceError"],
                    fields["createdAt"], timestamp, fields["lastReadAt"], fields["readCount"],
                ),
            )
            self.connection.commit()
        comic = self.get_comic(comic_id)
        assert comic is not None
        return comic

    def update_comic(self, comic_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        comic = self.get_comic(comic_id)
        return self.upsert_comic({**comic, **patch, "id": comic_id}) if comic else None

    def record_comic_open(self, comic_id: str) -> dict[str, Any] | None:
        """Count reader sessions atomically instead of inflating the count on every page turn."""
        with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE comics
                SET read_count = read_count + 1, last_read_at = ?, updated_at = updated_at
                WHERE id = ?
                """,
                (now_iso(), comic_id),
            )
            self.connection.commit()
        return self.get_comic(comic_id) if cursor.rowcount else None

    def delete_comic(self, comic_id: str) -> bool:
        return self.delete_comics([comic_id]) > 0

    def delete_comics(self, comic_ids: list[str], *, require_all: bool = False) -> int:
        clean_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        if not clean_ids:
            return 0
        placeholders = ",".join("?" for _ in clean_ids)
        with self.lock:
            with self.connection:
                if require_all:
                    found = self.connection.execute(
                        f"SELECT count(*) AS value FROM comics WHERE id IN ({placeholders})",
                        clean_ids,
                    ).fetchone()
                    if int(found["value"]) != len(clean_ids):
                        raise RuntimeError("部分书架记录已经变化，已停止删除，请刷新后重试")
                cursor = self.connection.execute(
                    f"DELETE FROM comics WHERE id IN ({placeholders})",
                    clean_ids,
                )
                self.connection.execute(
                    """
                    DELETE FROM library_series
                    WHERE id IN (
                      SELECT s.id FROM library_series s
                      LEFT JOIN library_series_members m ON m.series_id = s.id
                      GROUP BY s.id
                      HAVING count(m.comic_id) < CASE WHEN s.kind = 'source' THEN 1 ELSE 2 END
                    )
                    """
                )
            return cursor.rowcount

    def list_library_series(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM library_series ORDER BY created_at DESC"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                member_rows = self.connection.execute(
                    """
                    SELECT comic_id FROM library_series_members
                    WHERE series_id = ? ORDER BY position
                    """,
                    (row["id"],),
                ).fetchall()
                members = [self.get_comic(member["comic_id"]) for member in member_rows]
                members = [member for member in members if member is not None]
                kind = row["kind"] if row["kind"] in {"custom", "source"} else "custom"
                if len(members) < 2 and kind != "source":
                    continue
                first = members[0]
                read_members = [member for member in members if member.get("lastReadAt")]
                resume = max(read_members, key=lambda member: member["lastReadAt"]) if read_members else first
                source_series_ids = {
                    str(member["seriesId"])
                    for member in members
                    if member.get("seriesId")
                }
                source_series_id = (
                    next(iter(source_series_ids))
                    if len(source_series_ids) == 1 and all(member.get("seriesId") for member in members)
                    else None
                )
                known_source_ids = {
                    str(member["sourceId"])
                    for member in members
                    if member.get("sourceId")
                }
                pending_updates = [
                    item for item in json_list(row["update_items_json"])
                    if isinstance(item, dict)
                    and str(item.get("sourceId") or "") not in known_source_ids
                ]
                result.append({
                    "id": row["id"],
                    "kind": kind,
                    "isSourceSeries": kind == "source",
                    "displayAsSeries": len(members) >= 2,
                    "displayName": first["seriesTitle"].strip() or first["displayName"],
                    "coverComicId": first["id"],
                    "memberIds": [member["id"] for member in members],
                    "members": members,
                    "resumeComicId": resume["id"],
                    "resumePage": resume["progressPage"],
                    "resumeLastReadAt": resume["lastReadAt"],
                    "createdAt": row["created_at"],
                    "sourceSeriesId": source_series_id,
                    "updateSupported": bool(source_series_id),
                    "autoUpdateEnabled": bool(row["auto_update_enabled"]) and bool(source_series_id),
                    "lastCheckedAt": row["last_checked_at"],
                    "lastUpdateFoundAt": row["last_update_found_at"],
                    "updateItems": pending_updates,
                    "updateAvailableCount": len(pending_updates),
                    "updateError": row["update_error"],
                })
            return result

    def get_library_series(self, series_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_library_series() if item["id"] == series_id), None)

    def create_library_series(self, comic_ids: list[str], *, kind: str = "custom") -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        clean_kind = kind if kind in {"custom", "source"} else "custom"
        minimum_members = 1 if clean_kind == "source" else 2
        if len(ordered_ids) < minimum_members:
            raise ValueError("创建系列至少需要选择两本")
        placeholders = ",".join("?" for _ in ordered_ids)
        with self.lock:
            existing = self.connection.execute(
                f"SELECT id FROM comics WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            if len(existing) != len(ordered_ids):
                raise ValueError("选择中包含不存在的本子")
            conflict = self.connection.execute(
                f"SELECT comic_id FROM library_series_members WHERE comic_id IN ({placeholders}) LIMIT 1",
                ordered_ids,
            ).fetchone()
            if conflict:
                raise ValueError("所选本子中已有属于其他系列的条目")
            series_id = str(uuid.uuid4())
            with self.connection:
                self.connection.execute(
                    "INSERT INTO library_series (id, created_at, kind) VALUES (?, ?, ?)",
                    (series_id, now_iso(), clean_kind),
                )
                self.connection.executemany(
                    "INSERT INTO library_series_members (series_id, comic_id, position) VALUES (?, ?, ?)",
                    [(series_id, comic_id, position) for position, comic_id in enumerate(ordered_ids)],
                )
        return next(item for item in self.list_library_series() if item["id"] == series_id)

    def ensure_library_series(self, comic_ids: list[str], *, kind: str = "custom") -> dict[str, Any] | None:
        ordered_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        clean_kind = kind if kind in {"custom", "source"} else "custom"
        minimum_members = 1 if clean_kind == "source" else 2
        if len(ordered_ids) < minimum_members:
            return None
        placeholders = ",".join("?" for _ in ordered_ids)
        with self.lock:
            existing = self.connection.execute(
                f"SELECT id FROM comics WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            if len(existing) != len(ordered_ids):
                raise ValueError("自动建立系列时包含不存在的本子")
            memberships = self.connection.execute(
                f"SELECT DISTINCT series_id FROM library_series_members WHERE comic_id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            series_ids = [row["series_id"] for row in memberships]
            if len(series_ids) > 1:
                return None
            if not series_ids:
                return self.create_library_series(ordered_ids, kind=clean_kind)

            series_id = series_ids[0]
            current_count = self.connection.execute(
                "SELECT count(*) AS value FROM library_series_members WHERE series_id = ?",
                (series_id,),
            ).fetchone()["value"]
            with self.connection:
                self.connection.executemany(
                    "INSERT OR IGNORE INTO library_series_members (series_id, comic_id, position) VALUES (?, ?, ?)",
                    [(series_id, comic_id, current_count + index) for index, comic_id in enumerate(ordered_ids)],
                )
                rows = self.connection.execute(
                    """
                    SELECT m.comic_id, m.position, c.chapter_index
                    FROM library_series_members m
                    JOIN comics c ON c.id = m.comic_id
                    WHERE m.series_id = ?
                    ORDER BY c.chapter_index, m.position
                    """,
                    (series_id,),
                ).fetchall()
                self.connection.executemany(
                    "UPDATE library_series_members SET position = ? WHERE series_id = ? AND comic_id = ?",
                    [(position, series_id, row["comic_id"]) for position, row in enumerate(rows)],
                )
        return next((item for item in self.list_library_series() if item["id"] == series_id), None)

    def ensure_source_series(self, source_series_id: str) -> dict[str, Any] | None:
        clean_source_id = str(source_series_id or "").strip()
        if not clean_source_id:
            return None
        with self.lock:
            rows = self.connection.execute(
                "SELECT id FROM comics WHERE series_id = ? ORDER BY chapter_index, created_at",
                (clean_source_id,),
            ).fetchall()
        return self.ensure_library_series([row["id"] for row in rows], kind="source")

    def ensure_source_subscriptions(self) -> None:
        """Backfill update tracking for source albums that currently have one chapter."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT DISTINCT series_id FROM comics WHERE series_id IS NOT NULL AND trim(series_id) != ''"
            ).fetchall()
        for row in rows:
            self.ensure_source_series(str(row["series_id"]))

    def record_library_series_check(
        self,
        series_id: str,
        update_items: list[dict[str, Any]] | None = None,
        error: str | None = None,
        checked_at: str | None = None,
    ) -> dict[str, Any] | None:
        timestamp = checked_at or now_iso()
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT id, update_items_json FROM library_series WHERE id = ?",
                (series_id,),
            ).fetchone()
            if not existing:
                return None
            clean_items = update_items if update_items is not None else json_list(existing["update_items_json"])
            found_count = len(clean_items) if update_items is not None else 0
            self.connection.execute(
                """
                UPDATE library_series
                SET last_checked_at = ?,
                    last_update_found_at = CASE WHEN ? > 0 THEN ? ELSE last_update_found_at END,
                    update_items_json = ?,
                    update_error = ?
                WHERE id = ?
                """,
                (
                    timestamp,
                    found_count,
                    timestamp,
                    json.dumps(clean_items, ensure_ascii=False),
                    str(error) if error else None,
                    series_id,
                ),
            )
        return self.get_library_series(series_id)

    def delete_library_series(self, series_id: str) -> bool:
        with self.lock:
            cursor = self.connection.execute("DELETE FROM library_series WHERE id = ?", (series_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def list_collections(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT c.*, (
                  SELECT count(DISTINCT CASE
                    WHEN lsm.series_id IS NOT NULL THEN 'series:' || lsm.series_id
                    ELSE 'comic:' || cc.comic_id
                  END)
                  FROM comic_collections cc
                  LEFT JOIN library_series_members lsm ON lsm.comic_id = cc.comic_id
                  WHERE cc.collection_id = c.id
                ) AS comic_count
                FROM collections c ORDER BY c.sort_order, c.name COLLATE NOCASE
                """
            ).fetchall()
        return [
            {"id": row["id"], "parentId": row["parent_id"], "name": row["name"], "sortOrder": row["sort_order"], "comicCount": row["comic_count"]}
            for row in rows
        ]

    def create_collection(self, name: str, parent_id: str | None = None) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("收藏夹名称不能为空")
        collection_id = str(uuid.uuid4())
        with self.lock:
            row = self.connection.execute("SELECT COALESCE(MAX(sort_order), -1) AS value FROM collections WHERE parent_id IS ?", (parent_id,)).fetchone()
            self.connection.execute(
                "INSERT INTO collections (id, parent_id, name, sort_order, created_at) VALUES (?, ?, ?, ?, ?)",
                (collection_id, parent_id, clean_name, int(row["value"]) + 1, now_iso()),
            )
            self.connection.commit()
        return next(item for item in self.list_collections() if item["id"] == collection_id)

    def update_collection(self, collection_id: str, name: str | None = None, parent_id: str | None = None, parent_supplied: bool = False) -> dict[str, Any] | None:
        with self.lock:
            existing = self.connection.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if not existing:
                return None
            clean_name = existing["name"] if name is None else name.strip()
            if not clean_name:
                raise ValueError("收藏夹名称不能为空")
            new_parent = parent_id if parent_supplied else existing["parent_id"]
            if new_parent == collection_id:
                raise ValueError("收藏夹不能成为自己的子目录")
            if new_parent:
                descendants = self.connection.execute(
                    """
                    WITH RECURSIVE tree(id) AS (
                      SELECT id FROM collections WHERE parent_id = ?
                      UNION ALL SELECT c.id FROM collections c JOIN tree t ON c.parent_id = t.id
                    ) SELECT 1 FROM tree WHERE id = ? LIMIT 1
                    """,
                    (collection_id, new_parent),
                ).fetchone()
                if descendants:
                    raise ValueError("不能把目录移动到自己的子目录中")
            self.connection.execute("UPDATE collections SET name = ?, parent_id = ? WHERE id = ?", (clean_name, new_parent, collection_id))
            self.connection.commit()
        return next(item for item in self.list_collections() if item["id"] == collection_id)

    def delete_collection(self, collection_id: str) -> bool:
        with self.lock:
            cursor = self.connection.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            self.connection.commit()
            return cursor.rowcount > 0

    def set_comic_collections(self, comic_id: str, collection_ids: list[str]) -> dict[str, Any] | None:
        if not self.get_comic(comic_id):
            return None
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM comic_collections WHERE comic_id = ?", (comic_id,))
            self.connection.executemany(
                "INSERT OR IGNORE INTO comic_collections (comic_id, collection_id) VALUES (?, ?)",
                [(comic_id, collection_id) for collection_id in dict.fromkeys(collection_ids)],
            )
        return self.get_comic(comic_id)

    def add_comics_to_collection(self, collection_id: str, comic_ids: list[str]) -> dict[str, Any]:
        ordered_ids = list(dict.fromkeys(str(comic_id) for comic_id in comic_ids if comic_id))
        if not ordered_ids:
            raise ValueError("没有可加入目录的本子")
        if len(ordered_ids) > 2000:
            raise ValueError("一次最多归类 2000 本")
        placeholders = ",".join("?" for _ in ordered_ids)
        with self.lock, self.connection:
            collection = self.connection.execute(
                "SELECT id, name FROM collections WHERE id = ?",
                (collection_id,),
            ).fetchone()
            if not collection:
                raise ValueError("收藏目录不存在")
            existing_comics = self.connection.execute(
                f"SELECT id FROM comics WHERE id IN ({placeholders})",
                ordered_ids,
            ).fetchall()
            if len(existing_comics) != len(ordered_ids):
                raise ValueError("拖拽内容包含不存在的本子")
            existing_links = self.connection.execute(
                f"SELECT comic_id FROM comic_collections WHERE collection_id = ? AND comic_id IN ({placeholders})",
                [collection_id, *ordered_ids],
            ).fetchall()
            linked_ids = {row["comic_id"] for row in existing_links}
            self.connection.executemany(
                "INSERT OR IGNORE INTO comic_collections (comic_id, collection_id) VALUES (?, ?)",
                [(comic_id, collection_id) for comic_id in ordered_ids],
            )
        return {
            "ok": True,
            "collectionId": collection_id,
            "collectionName": collection["name"],
            "comicIds": ordered_ids,
            "addedCount": len(ordered_ids) - len(linked_ids),
        }

    def get_setting(self, key: str, fallback: str = "") -> str:
        with self.lock:
            row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else fallback

    @staticmethod
    def _online_metadata_row(row: sqlite3.Row) -> dict[str, Any]:
        first_photo_id = row["first_photo_id"]
        return {
            "albumId": str(row["album_id"]),
            "title": row["title"] or "",
            "authors": json_list(row["authors_json"]),
            "tags": json_list(row["tags_json"]),
            "works": json_list(row["works_json"]),
            "actors": json_list(row["actors_json"]),
            "description": row["description"] or "",
            "publishedAt": row["published_at"] or "",
            "updatedAtSource": row["source_updated_at"] or "",
            "pageCount": max(0, int(row["page_count"] or 0)),
            "firstPhotoId": str(first_photo_id) if first_photo_id else "",
            "episodeManifest": json_list(row["episode_manifest_json"]),
            "detailsComplete": bool(row["details_complete"]),
            "fetchedAt": row["fetched_at"] or "",
        }

    def get_online_metadata(self, album_ids: list[Any]) -> list[dict[str, Any]]:
        ordered_ids = list(dict.fromkeys(
            source_id for value in album_ids if (source_id := normalize_plate(value))
        ))
        if not ordered_ids:
            return []
        placeholders = ",".join("?" for _ in ordered_ids)
        with self.lock:
            rows = self.connection.execute(
                f"SELECT * FROM online_metadata_cache WHERE album_id IN ({placeholders})",
                [int(source_id) for source_id in ordered_ids],
            ).fetchall()
        by_id = {str(row["album_id"]): self._online_metadata_row(row) for row in rows}
        return [by_id[source_id] for source_id in ordered_ids if source_id in by_id]

    def clear_online_metadata(self) -> int:
        """Drop only the disposable JMonline metadata index."""
        with self.lock, self.connection:
            row = self.connection.execute("SELECT COUNT(*) AS total FROM online_metadata_cache").fetchone()
            self.connection.execute("DELETE FROM online_metadata_cache")
        return int(row["total"] if row else 0)

    @staticmethod
    def _metadata_string_list(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return list(dict.fromkeys(
            clean for item in value if (clean := str(item).strip())
        ))

    @staticmethod
    def _metadata_episode_manifest(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, (list, tuple)):
            return []
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for position, raw in enumerate(value, start=1):
            if not isinstance(raw, dict):
                continue
            source_id = normalize_plate(raw.get("sourceId") or raw.get("photoId"))
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            prepared.append({
                "sourceId": source_id,
                "title": str(raw.get("title") or "").strip(),
                "chapterIndex": normalize_chapter_index(raw.get("chapterIndex"), position),
            })
        return prepared

    def upsert_online_metadata_many(self, items: list[dict[str, Any]]) -> int:
        prepared: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            source_id = normalize_plate(item.get("albumId") or item.get("sourceId"))
            if source_id:
                prepared[source_id] = item
        if not prepared:
            return 0

        existing = {
            item["albumId"]: item
            for item in self.get_online_metadata(list(prepared))
        }
        changed = 0
        with self.lock, self.connection:
            for source_id, incoming in prepared.items():
                current = existing.get(source_id, {})

                def text_value(key: str) -> str:
                    clean = str(incoming.get(key) or "").strip()
                    return clean or str(current.get(key) or "")

                authors = self._metadata_string_list(incoming.get("authors")) or list(current.get("authors") or [])
                tags = self._metadata_string_list(incoming.get("tags")) or list(current.get("tags") or [])
                works = self._metadata_string_list(incoming.get("works")) or list(current.get("works") or [])
                actors = self._metadata_string_list(incoming.get("actors")) or list(current.get("actors") or [])
                try:
                    incoming_pages = max(0, int(incoming.get("pageCount") or 0))
                except (TypeError, ValueError):
                    incoming_pages = 0
                page_count = incoming_pages or max(0, int(current.get("pageCount") or 0))
                first_photo_id = normalize_plate(incoming.get("firstPhotoId")) or str(current.get("firstPhotoId") or "")
                episode_manifest = self._metadata_episode_manifest(incoming.get("episodeManifest"))
                if not episode_manifest:
                    episode_manifest = self._metadata_episode_manifest(current.get("episodeManifest"))
                if not first_photo_id and episode_manifest:
                    first_photo_id = str(episode_manifest[0]["sourceId"])
                details_complete = bool(current.get("detailsComplete")) or bool(incoming.get("detailsComplete"))
                fetched_at = str(incoming.get("fetchedAt") or "").strip()
                if not fetched_at:
                    fetched_at = str(current.get("fetchedAt") or "") or now_iso()
                merged = {
                    "albumId": source_id,
                    "title": text_value("title"),
                    "authors": authors,
                    "tags": tags,
                    "works": works,
                    "actors": actors,
                    "description": text_value("description"),
                    "publishedAt": text_value("publishedAt"),
                    "updatedAtSource": text_value("updatedAtSource"),
                    "pageCount": page_count,
                    "firstPhotoId": first_photo_id,
                    "episodeManifest": episode_manifest,
                    "detailsComplete": details_complete,
                    "fetchedAt": fetched_at,
                }
                if current and all(current.get(key) == value for key, value in merged.items()):
                    continue
                self.connection.execute(
                    """
                    INSERT INTO online_metadata_cache (
                      album_id, title, authors_json, tags_json, works_json, actors_json, description,
                      published_at, source_updated_at, page_count, first_photo_id,
                      episode_manifest_json, details_complete, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(album_id) DO UPDATE SET
                      title=excluded.title,
                      authors_json=excluded.authors_json,
                      tags_json=excluded.tags_json,
                      works_json=excluded.works_json,
                      actors_json=excluded.actors_json,
                      description=excluded.description,
                      published_at=excluded.published_at,
                      source_updated_at=excluded.source_updated_at,
                      page_count=excluded.page_count,
                      first_photo_id=excluded.first_photo_id,
                      episode_manifest_json=excluded.episode_manifest_json,
                      details_complete=excluded.details_complete,
                      fetched_at=excluded.fetched_at
                    """,
                    (
                        int(source_id), merged["title"],
                        json.dumps(authors, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(works, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(actors, ensure_ascii=False, separators=(",", ":")),
                        merged["description"], merged["publishedAt"], merged["updatedAtSource"],
                        page_count, int(first_photo_id) if first_photo_id else None,
                        json.dumps(episode_manifest, ensure_ascii=False, separators=(",", ":")),
                        int(details_complete), fetched_at,
                    ),
                )
                changed += 1
        return changed

    def upsert_online_metadata(self, item: dict[str, Any]) -> dict[str, Any] | None:
        source_id = normalize_plate(item.get("albumId") or item.get("sourceId"))
        if not source_id:
            return None
        self.upsert_online_metadata_many([item])
        stored = self.get_online_metadata([source_id])
        return stored[0] if stored else None

    def set_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            self.connection.commit()
