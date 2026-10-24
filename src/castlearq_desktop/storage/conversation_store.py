# Copyright 2026 Nicolas Bruna
# SPDX-License-Identifier: Apache-2.0
"""Conversation persistence boundary for CastleArq Desktop.

Implemented with the standard library ``sqlite3`` module. The database file is
created lazily on the first successful save; constructing a store or loading a
missing conversation never creates it.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional

__all__ = ["ConversationStore", "ConversationStoreError"]


class ConversationStoreError(Exception):
    """Raised when a storage operation cannot be completed safely."""


class ConversationStore:
    def __init__(self, database_path: Optional[str] = None) -> None:
        self._database_path = database_path

    @property
    def database_path(self) -> Optional[str]:
        return self._database_path

    def _connect(self) -> sqlite3.Connection:
        if not self._database_path:
            raise ConversationStoreError("database path is not configured")
        parent = os.path.dirname(os.path.abspath(self._database_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                model_id TEXT,
                artifact_filename TEXT,
                title TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(conversation_id, turn_index),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "title" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT")

    def _validate_id(self, conversation_id: Any) -> str:
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversation_id must be a non-empty string")
        return conversation_id

    def _normalize_messages(self, messages: Any) -> list[dict]:
        if not isinstance(messages, (list, tuple)):
            raise ValueError("messages must be a list or tuple")
        normalized = []
        for idx, item in enumerate(messages):
            if not isinstance(item, dict):
                raise ValueError("each message must be a dict")
            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str) or not role.strip():
                raise ValueError("message role must be a non-empty string")
            if not isinstance(content, str):
                raise ValueError("message content must be a string")
            normalized.append({"role": role, "content": content, "turn_index": idx})
        return normalized

    def save_conversation(self, conversation_id: str, messages: Any, model_id: str | None = None, artifact_filename: str | None = None) -> None:
        cid = self._validate_id(conversation_id)
        normalized = self._normalize_messages(messages)
        try:
            conn = self._connect()
        except Exception as exc:
            raise ConversationStoreError(str(exc)) from exc
        try:
            with conn:
                self._init_schema(conn)
                conn.execute(
                    "INSERT INTO conversations (id, model_id, artifact_filename) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET model_id=excluded.model_id, artifact_filename=excluded.artifact_filename, updated_at=datetime('now')",
                    (cid, model_id, artifact_filename),
                )
                conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
                for msg in normalized:
                    conn.execute(
                        "INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (?,?,?,?)",
                        (cid, msg["turn_index"], msg["role"], msg["content"]),
                    )
        except sqlite3.Error as exc:
            raise ConversationStoreError("failed to save conversation") from exc
        finally:
            conn.close()

    def list_conversations(self) -> list[dict]:
        if not self._database_path or not os.path.exists(self._database_path):
            return []
        try:
            conn = self._connect()
        except Exception as exc:
            raise ConversationStoreError(str(exc)) from exc
        try:
            self._init_schema(conn)
            rows = conn.execute(
                """SELECT c.id, c.model_id, c.artifact_filename, c.title, c.created_at, c.updated_at,
                          (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                   FROM conversations c ORDER BY c.updated_at DESC, c.id DESC"""
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "model_id": r[1],
                    "artifact_filename": r[2],
                    "title": r[3],
                    "created_at": r[4],
                    "updated_at": r[5],
                    "message_count": r[6],
                }
                for r in rows
            ]
        except sqlite3.Error as exc:
            raise ConversationStoreError("failed to list conversations") from exc
        finally:
            conn.close()

    def load_conversation(self, conversation_id: str) -> list[dict]:
        cid = self._validate_id(conversation_id)
        if not self._database_path or not os.path.exists(self._database_path):
            return []
        try:
            conn = self._connect()
        except Exception as exc:
            raise ConversationStoreError(str(exc)) from exc
        try:
            self._init_schema(conn)
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY turn_index ASC, id ASC",
                (cid,),
            ).fetchall()
            return [{"role": r[0], "content": r[1]} for r in rows]
        except sqlite3.Error as exc:
            raise ConversationStoreError("failed to load conversation") from exc
        finally:
            conn.close()

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        cid = self._validate_id(conversation_id)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        normalized_title = title.strip()
        if not self._database_path or not os.path.exists(self._database_path):
            return False
        try:
            conn = self._connect()
        except Exception as exc:
            raise ConversationStoreError(str(exc)) from exc
        try:
            with conn:
                self._init_schema(conn)
                cur = conn.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    (normalized_title, cid),
                )
                return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise ConversationStoreError("failed to rename conversation") from exc
        finally:
            conn.close()

    def delete_conversation(self, conversation_id: str) -> bool:
        cid = self._validate_id(conversation_id)
        if not self._database_path or not os.path.exists(self._database_path):
            return False
        try:
            conn = self._connect()
        except Exception as exc:
            raise ConversationStoreError(str(exc)) from exc
        try:
            with conn:
                self._init_schema(conn)
                cur = conn.execute(
                    "DELETE FROM conversations WHERE id = ?", (cid,)
                )
                return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise ConversationStoreError("failed to delete conversation") from exc
        finally:
            conn.close()