"""Append-only, hash-chained audit log.

Every kernel hop appends an entry; each entry hash covers its content plus the previous
entry's hash, so tampering or deletion is detectable by `verify_chain` (LLM-Native paper:
event sourcing/provenance as first-class infrastructure; ACE: audit execution traces).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def entry_hash(prev_hash: str, seq: int, ts: str, event: str, request_id: str, principal: str, data: dict) -> str:
    payload = canonical(
        {"seq": seq, "ts": ts, "event": event, "request_id": request_id, "principal": principal, "data": data}
    )
    return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    ts: str
    event: str
    request_id: str
    principal: str
    data: dict
    prev_hash: str
    entry_hash: str

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "event": self.event,
            "request_id": self.request_id,
            "principal": self.principal,
            "data": self.data,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }


def verify_chain(records: Iterable[AuditRecord]) -> dict:
    """Recompute the chain; returns {ok, checked, first_bad_seq, reason}."""
    prev = GENESIS
    expected_seq = 1
    for rec in records:
        if rec.seq != expected_seq:
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": rec.seq,
                    "reason": f"sequence gap: expected {expected_seq}, got {rec.seq}"}
        if rec.prev_hash != prev:
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": rec.seq,
                    "reason": "prev_hash does not link to previous entry"}
        want = entry_hash(rec.prev_hash, rec.seq, rec.ts, rec.event, rec.request_id, rec.principal, rec.data)
        if want != rec.entry_hash:
            return {"ok": False, "checked": expected_seq - 1, "first_bad_seq": rec.seq,
                    "reason": "entry_hash mismatch (content tampered)"}
        prev = rec.entry_hash
        expected_seq += 1
    return {"ok": True, "checked": expected_seq - 1, "first_bad_seq": None, "reason": "chain intact"}


class InMemoryAuditSink:
    """Single-process sink for tests and degraded (no-Postgres) mode."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def append(self, event: str, request_id: str, principal: str, data: dict) -> AuditRecord:
        with self._lock:
            ts = datetime.now(UTC).isoformat()
            prev = self._records[-1].entry_hash if self._records else GENESIS
            rec = AuditRecord(
                seq=len(self._records) + 1,
                ts=ts,
                event=event,
                request_id=request_id,
                principal=principal,
                data=data,
                prev_hash=prev,
                entry_hash=entry_hash(prev, len(self._records) + 1, ts, event, request_id, principal, data),
            )
            self._records.append(rec)
            return rec

    def all(self) -> list[AuditRecord]:
        with self._lock:
            return list(self._records)

    def for_request(self, request_id: str) -> list[AuditRecord]:
        return [r for r in self.all() if r.request_id == request_id]


class PostgresAuditSink:
    """Append-only audit log in Postgres. seq assignment and hash chaining happen inside an
    advisory-locked transaction so concurrent appenders cannot fork the chain."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._conn is None:
            import psycopg

            self._conn = psycopg.connect(self._dsn)
            with self._conn.transaction():
                self._conn.execute(_SCHEMA_SQL)
        return self._conn

    def append(self, event: str, request_id: str, principal: str, data: dict) -> AuditRecord:

        with self._lock:
            conn = self._connect()
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (4201,))
                row = conn.execute(
                    "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq = (row[0] + 1) if row else 1
                prev = row[1] if row else GENESIS
                ts = datetime.now(UTC).isoformat()
                h = entry_hash(prev, seq, ts, event, request_id, principal, data)
                conn.execute(
                    "INSERT INTO audit_log (seq, ts, event, request_id, principal, data, prev_hash, entry_hash)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (seq, ts, event, request_id, principal, json.dumps(data), prev, h),
                )
            conn.commit()
            return AuditRecord(seq, ts, event, request_id, principal, data, prev, h)

    def all(self) -> list[AuditRecord]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT seq, ts, event, request_id, principal, data, prev_hash, entry_hash"
                " FROM audit_log ORDER BY seq"
            ).fetchall()
        return [
            AuditRecord(r[0], r[1].isoformat() if isinstance(r[1], datetime) else str(r[1]),
                        r[2], r[3], r[4], r[5], r[6], r[7])
            for r in rows
        ]

    def for_request(self, request_id: str) -> list[AuditRecord]:
        return [r for r in self.all() if r.request_id == request_id]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        BIGINT PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL,
    event      TEXT NOT NULL,
    request_id TEXT NOT NULL,
    principal  TEXT NOT NULL,
    data       JSONB NOT NULL,
    prev_hash  TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_request_id_idx ON audit_log (request_id);
"""


class AuditLog:
    """Facade used across the kernel so callers never touch sinks directly."""

    def __init__(self, sink: InMemoryAuditSink | PostgresAuditSink) -> None:
        self.sink = sink
        self.backend = "postgres" if isinstance(sink, PostgresAuditSink) else "memory"

    def append(self, event: str, request_id: str, principal: str, data: dict) -> AuditRecord:
        return self.sink.append(event, request_id, principal, data)

    def all(self) -> list[AuditRecord]:
        return self.sink.all()

    def for_request(self, request_id: str) -> list[AuditRecord]:
        return self.sink.for_request(request_id)

    def verify(self) -> dict:
        return verify_chain(self.all())
