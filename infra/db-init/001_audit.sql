-- llm-core M0 schema: append-only, hash-chained audit log.
-- (kernel.audit.PostgresAuditSink also creates this idempotently on connect.)
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
