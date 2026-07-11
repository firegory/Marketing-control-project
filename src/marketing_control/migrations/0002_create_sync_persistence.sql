CREATE TABLE sync_runs (
    id VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    requested_start_date DATE NOT NULL,
    requested_end_date DATE NOT NULL,
    completed_start_date DATE,
    completed_end_date DATE,
    failure_detail VARCHAR,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    CHECK (requested_start_date <= requested_end_date),
    CHECK (
        (completed_start_date IS NULL AND completed_end_date IS NULL)
        OR (
            completed_start_date IS NOT NULL
            AND completed_end_date IS NOT NULL
            AND completed_start_date <= completed_end_date
        )
    )
);

CREATE TABLE report_coverage (
    report_name VARCHAR NOT NULL,
    covered_start_date DATE NOT NULL,
    covered_end_date DATE NOT NULL,
    sync_run_id VARCHAR REFERENCES sync_runs(id),
    recorded_at TIMESTAMP NOT NULL,
    PRIMARY KEY (report_name, covered_start_date, covered_end_date),
    CHECK (covered_start_date <= covered_end_date)
);

CREATE TABLE history_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kind VARCHAR NOT NULL CHECK (kind IN ('initial', 'backfill')),
    requested_start_date DATE NOT NULL,
    requested_end_date DATE NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CHECK (requested_start_date <= requested_end_date)
);
