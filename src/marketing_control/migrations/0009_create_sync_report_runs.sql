CREATE TABLE sync_report_runs (
    sync_run_id VARCHAR NOT NULL,
    report_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    total_units INTEGER NOT NULL CHECK (total_units >= 0),
    completed_units INTEGER NOT NULL DEFAULT 0 CHECK (completed_units >= 0 AND completed_units <= total_units),
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    failure_detail VARCHAR,
    PRIMARY KEY (sync_run_id, report_name)
);

CREATE TABLE sync_run_locks (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sync_run_id VARCHAR NOT NULL
);
