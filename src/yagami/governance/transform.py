from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..storage.db import get_db, now_ms


class TransformationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EntityMatch:
    start: int
    end: int
    entity_type: str
    value: str
    priority: int


@dataclass
class TransformationSession:
    request_id: str
    project_id: str
    mode: str
    mapping: dict[str, str] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    messages_transformed: int = 0

    @property
    def active(self) -> bool:
        return bool(self.mapping) or self.messages_transformed > 0

    def summary(self) -> dict:
        return {
            "mode": self.mode,
            "messages_transformed": self.messages_transformed,
            "entity_counts": dict(sorted(self.entity_counts.items())),
            "placeholders": sorted(self.mapping),
        }


_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("API_KEY", re.compile(r"\b(?:sk|ghp|gho|github_pat)-?[A-Za-z0-9_]{20,}\b"), 100),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), 100),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        100,
    ),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), 90),
    ("CREDIT_CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), 80),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), 70),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), 60),
    (
        "MRN",
        re.compile(r"\b(?:MRN|medical record(?: number)?)\s*[:#-]?\s*[A-Z0-9-]{5,20}\b", re.I),
        50,
    ),
)


def generate_transform_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _parse_key(encoded: str) -> bytes:
    try:
        value = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TransformationError("YAGAMI_TRANSFORM_KEY must be URL-safe base64") from exc
    if len(value) != 32:
        raise TransformationError("YAGAMI_TRANSFORM_KEY must decode to exactly 32 bytes")
    return value


def _entity_matches(text: str) -> list[EntityMatch]:
    candidates: list[EntityMatch] = []
    for entity_type, pattern, priority in _PATTERNS:
        for match in pattern.finditer(text):
            candidates.append(
                EntityMatch(
                    start=match.start(),
                    end=match.end(),
                    entity_type=entity_type,
                    value=match.group(0),
                    priority=priority,
                )
            )
    selected: list[EntityMatch] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, -item.priority, -item.end)):
        if any(
            candidate.start < existing.end and existing.start < candidate.end
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def detect_entity_types(text: str) -> list[str]:
    return [match.entity_type for match in _entity_matches(text)]


class PrivacyTransformer:
    def __init__(self, *, key: str, ttl_seconds: int = 3600) -> None:
        self._key = _parse_key(key) if key else None
        self._hash_key = (
            hashlib.sha256(b"yagami-value-hash-v1:" + self._key).digest()
            if self._key is not None
            else None
        )
        self._aesgcm = AESGCM(self._key) if self._key is not None else None
        self.ttl_seconds = ttl_seconds

    @property
    def tokenization_available(self) -> bool:
        return self._aesgcm is not None

    async def transform_text(
        self,
        text: str,
        *,
        session: TransformationSession,
    ) -> str:
        matches = _entity_matches(text)
        if not matches:
            return text
        session.messages_transformed += 1
        pieces: list[str] = []
        cursor = 0
        for match in matches:
            pieces.append(text[cursor : match.start])
            count = session.entity_counts.get(match.entity_type, 0) + 1
            session.entity_counts[match.entity_type] = count
            if session.mode == "redact":
                placeholder = f"[REDACTED_{match.entity_type}]"
            elif session.mode == "tokenize":
                if self._aesgcm is None:
                    raise TransformationError(
                        "tokenize policy requires YAGAMI_TRANSFORM_KEY; generate one with "
                        "yagami-keygen"
                    )
                placeholder = f"[YGM_{match.entity_type}_{count}]"
                session.mapping[placeholder] = match.value
                await self._store_token(
                    request_id=session.request_id,
                    project_id=session.project_id,
                    placeholder=placeholder,
                    entity_type=match.entity_type,
                    value=match.value,
                )
            else:
                raise TransformationError(f"unsupported transformation mode {session.mode!r}")
            pieces.append(placeholder)
            cursor = match.end
        pieces.append(text[cursor:])
        return "".join(pieces)

    async def _store_token(
        self,
        *,
        request_id: str,
        project_id: str,
        placeholder: str,
        entity_type: str,
        value: str,
    ) -> None:
        if self._aesgcm is None or self._hash_key is None:
            raise TransformationError("tokenization key is not configured")
        nonce = os.urandom(12)
        aad = f"{request_id}:{project_id}:{placeholder}".encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, value.encode("utf-8"), aad)
        created_at = now_ms()
        await get_db().execute(
            "INSERT INTO privacy_tokens(request_id, project_id, placeholder, entity_type,"
            " nonce, ciphertext, value_hash, created_at, expires_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                project_id,
                placeholder,
                entity_type,
                nonce,
                ciphertext,
                hmac.new(self._hash_key, value.encode("utf-8"), hashlib.sha256).hexdigest(),
                created_at,
                created_at + self.ttl_seconds * 1000,
            ),
        )
        await get_db().commit()

    def rehydrate(self, text: str, session: TransformationSession) -> str:
        result = text
        for placeholder in sorted(session.mapping, key=len, reverse=True):
            result = result.replace(placeholder, session.mapping[placeholder])
        return result

    async def rehydrate_from_vault(
        self,
        text: str,
        *,
        request_id: str,
        project_id: str,
        delete: bool = True,
    ) -> str:
        if self._aesgcm is None:
            raise TransformationError("rehydration requires YAGAMI_TRANSFORM_KEY")
        db = get_db()
        async with db.execute(
            "SELECT placeholder, nonce, ciphertext, expires_at FROM privacy_tokens"
            " WHERE request_id=? AND project_id=? ORDER BY id",
            (request_id, project_id),
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            raise TransformationError("tokenization session not found or already deleted")
        if any(int(row["expires_at"]) < now_ms() for row in rows):
            await db.execute(
                "DELETE FROM privacy_tokens WHERE request_id=? AND project_id=?",
                (request_id, project_id),
            )
            await db.commit()
            raise TransformationError("tokenization session expired")
        result = text
        for row in rows:
            placeholder = str(row["placeholder"])
            aad = f"{request_id}:{project_id}:{placeholder}".encode("utf-8")
            try:
                value = self._aesgcm.decrypt(row["nonce"], row["ciphertext"], aad).decode("utf-8")
            except Exception as exc:  # noqa: BLE001 - authenticated decryption has one safe outcome
                raise TransformationError("token vault authentication failed") from exc
            result = result.replace(placeholder, value)
        if delete:
            await db.execute(
                "DELETE FROM privacy_tokens WHERE request_id=? AND project_id=?",
                (request_id, project_id),
            )
            await db.commit()
        return result

    async def delete_session(self, session: TransformationSession) -> None:
        if session.mode == "tokenize":
            await get_db().execute(
                "DELETE FROM privacy_tokens WHERE request_id=? AND project_id=?",
                (session.request_id, session.project_id),
            )
            await get_db().commit()
        session.mapping.clear()

    async def cleanup_expired(self) -> int:
        cursor = await get_db().execute(
            "DELETE FROM privacy_tokens WHERE expires_at < ?", (now_ms(),)
        )
        await get_db().commit()
        return max(cursor.rowcount, 0)
