ALTER TABLE sync_report_runs ADD COLUMN failure_category VARCHAR;

CREATE TABLE sync_retry_audits (
    id VARCHAR PRIMARY KEY,
    source_sync_run_id VARCHAR NOT NULL REFERENCES sync_runs(id),
    retry_sync_run_id VARCHAR REFERENCES sync_runs(id),
    outcome VARCHAR NOT NULL CHECK (outcome IN ('running', 'succeeded', 'failed')),
    requested_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE TABLE sync_retry_audit_reports (
    retry_audit_id VARCHAR NOT NULL,
    report_name VARCHAR NOT NULL,
    PRIMARY KEY (retry_audit_id, report_name)
);
