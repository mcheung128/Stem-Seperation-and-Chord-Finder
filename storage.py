from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import secrets
import sqlite3
import threading


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.db_path = base_dir / "app.db"
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'separation',
                    title TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    job_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL,
                    error TEXT,
                    settings_json TEXT NOT NULL,
                    result_files_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    logs_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tracks)").fetchall()}
            if "kind" not in columns:
                connection.execute("ALTER TABLE tracks ADD COLUMN kind TEXT NOT NULL DEFAULT 'separation'")

    def create_user(self, email: str, password: str) -> dict:
        email_normalized = email.strip().lower()
        password_hash = self._hash_password(password)
        now = _utcnow()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email_normalized, password_hash, now),
            )
            user_id = cursor.lastrowid
        return {"id": user_id, "email": email_normalized}

    def authenticate_user(self, email: str, password: str) -> dict | None:
        email_normalized = email.strip().lower()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, email, password_hash FROM users WHERE email = ?",
                (email_normalized,),
            ).fetchone()
        if row is None or not self._verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "email": row["email"]}

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, user_id, _utcnow()),
            )
        return token

    def get_user_by_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.email
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ?
                """,
                (token,),
            ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "email": row["email"]}

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def create_track(
        self,
        user_id: int,
        kind: str,
        title: str,
        source_filename: str,
        source_path: str,
        job_id: str,
        settings: dict,
    ) -> int:
        now = _utcnow()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tracks (
                    user_id, kind, title, source_filename, source_path, job_id, status, stage, progress,
                    error, settings_json, result_files_json, analysis_json, logs_text, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    kind,
                    title,
                    source_filename,
                    source_path,
                    job_id,
                    "queued",
                    "queued",
                    0.0,
                    None,
                    json.dumps(settings),
                    json.dumps({}),
                    json.dumps({}),
                    "",
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_track_from_job(self, track_id: int, job) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE tracks
                SET status = ?, stage = ?, progress = ?, error = ?, settings_json = ?, result_files_json = ?,
                    analysis_json = ?, logs_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    job.status,
                    job.stage,
                    job.progress,
                    job.error,
                    json.dumps(job.settings),
                    json.dumps(job.result_files),
                    json.dumps(job.analysis),
                    "\n\n".join(job.logs),
                    _utcnow(),
                    track_id,
                ),
            )

    def get_track_for_user(self, user_id: int, track_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tracks WHERE id = ? AND user_id = ?",
                (track_id, user_id),
            ).fetchone()
        return self._row_to_track(row) if row else None

    def list_tracks_for_user(self, user_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tracks WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def get_track_by_job_id(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tracks WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def delete_track_for_user(self, user_id: int, track_id: int) -> dict | None:
        track = self.get_track_for_user(user_id, track_id)
        if track is None:
            return None
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM tracks WHERE id = ? AND user_id = ?", (track_id, user_id))
        return track

    def _row_to_track(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "kind": row["kind"],
            "title": row["title"],
            "source_filename": row["source_filename"],
            "source_path": row["source_path"],
            "job_id": row["job_id"],
            "status": row["status"],
            "stage": row["stage"],
            "progress": row["progress"],
            "error": row["error"],
            "settings": json.loads(row["settings_json"]),
            "result_files": json.loads(row["result_files_json"]),
            "analysis": json.loads(row["analysis_json"]),
            "logs": row["logs_text"].split("\n\n") if row["logs_text"] else [],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240000)
        return f"{salt.hex()}:{digest.hex()}"

    def _verify_password(self, password: str, stored: str) -> bool:
        salt_hex, digest_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240000)
        return secrets.compare_digest(digest.hex(), digest_hex)
