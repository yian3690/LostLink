CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id varchar(36) PRIMARY KEY,
    line_user_id varchar(64) UNIQUE NOT NULL,
    display_name varchar(120),
    role varchar(20) NOT NULL DEFAULT 'student',
    consented_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_reports (
    id varchar(36) PRIMARY KEY,
    user_id varchar(36) REFERENCES users(id),
    kind varchar(10) NOT NULL CHECK (kind IN ('lost', 'found')),
    status varchar(24) NOT NULL DEFAULT 'open',
    description text NOT NULL DEFAULT '',
    category varchar(80),
    brand varchar(80),
    color varchar(80),
    distinctive_features jsonb NOT NULL DEFAULT '[]',
    campus varchar(120),
    location varchar(240),
    occurred_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_reports_lookup
    ON item_reports(kind, status, campus, category);

CREATE TABLE IF NOT EXISTS item_images (
    id varchar(36) PRIMARY KEY,
    report_id varchar(36) NOT NULL REFERENCES item_reports(id) ON DELETE CASCADE,
    object_path varchar(500) NOT NULL,
    thumbnail_path varchar(500),
    mime_type varchar(80) NOT NULL DEFAULT 'image/jpeg',
    scan_status varchar(20) NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_embeddings (
    id varchar(36) PRIMARY KEY,
    report_id varchar(36) UNIQUE NOT NULL REFERENCES item_reports(id) ON DELETE CASCADE,
    e5_text vector(768),
    siglip_text vector(768),
    siglip_image vector(768),
    e5_model varchar(200),
    siglip_model varchar(200),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_embeddings_e5_hnsw
    ON item_embeddings USING hnsw (e5_text vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_embeddings_siglip_text_hnsw
    ON item_embeddings USING hnsw (siglip_text vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_embeddings_siglip_image_hnsw
    ON item_embeddings USING hnsw (siglip_image vector_cosine_ops);

CREATE TABLE IF NOT EXISTS match_candidates (
    id varchar(36) PRIMARY KEY,
    lost_report_id varchar(36) NOT NULL REFERENCES item_reports(id),
    found_report_id varchar(36) NOT NULL REFERENCES item_reports(id),
    score double precision NOT NULL,
    decision varchar(20) NOT NULL DEFAULT 'waiting',
    score_breakdown jsonb NOT NULL DEFAULT '{}',
    reasons jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lost_report_id, found_report_id)
);

CREATE INDEX IF NOT EXISTS ix_matches_lost_score
    ON match_candidates(lost_report_id, score DESC);
CREATE INDEX IF NOT EXISTS ix_matches_found_score
    ON match_candidates(found_report_id, score DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id varchar(36) PRIMARY KEY,
    match_id varchar(36) REFERENCES match_candidates(id),
    user_id varchar(36) REFERENCES users(id),
    channel varchar(20) NOT NULL DEFAULT 'line',
    status varchar(20) NOT NULL DEFAULT 'pending',
    payload jsonb NOT NULL DEFAULT '{}',
    attempts integer NOT NULL DEFAULT 0,
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS claims (
    id varchar(36) PRIMARY KEY,
    match_id varchar(36) NOT NULL REFERENCES match_candidates(id),
    claimant_user_id varchar(36) REFERENCES users(id),
    private_evidence text NOT NULL DEFAULT '',
    status varchar(24) NOT NULL DEFAULT 'pending',
    reviewed_by varchar(36) REFERENCES users(id),
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id varchar(36) PRIMARY KEY,
    actor_user_id varchar(36) REFERENCES users(id),
    action varchar(120) NOT NULL,
    entity_type varchar(80) NOT NULL,
    entity_id varchar(36),
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

