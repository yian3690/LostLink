ALTER TABLE item_reports
    ADD COLUMN IF NOT EXISTS feature_confidences JSONB NOT NULL DEFAULT '{}'::jsonb;
