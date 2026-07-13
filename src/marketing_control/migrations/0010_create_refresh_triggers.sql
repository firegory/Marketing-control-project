CREATE TABLE refresh_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    startup_refresh_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE startup_refresh_outcomes (
    account_id VARCHAR NOT NULL,
    local_date DATE NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('attempted', 'succeeded', 'failed')),
    attempted_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    failure_detail VARCHAR,
    PRIMARY KEY (account_id, local_date)
);
