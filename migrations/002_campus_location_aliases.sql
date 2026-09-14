CREATE TABLE IF NOT EXISTS campus_location_aliases (
    id VARCHAR(36) PRIMARY KEY,
    campus VARCHAR(120),
    alias VARCHAR(120) NOT NULL UNIQUE,
    canonical_name VARCHAR(240) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_campus_location_aliases_campus
    ON campus_location_aliases (campus);
CREATE INDEX IF NOT EXISTS ix_campus_location_aliases_canonical_name
    ON campus_location_aliases (canonical_name);
