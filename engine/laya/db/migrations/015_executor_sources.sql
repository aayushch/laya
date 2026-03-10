-- Add source_type to distinguish ingestion vs executor workflows
-- and webhook_path for executor sources to know their n8n webhook endpoint.
ALTER TABLE sources ADD COLUMN source_type TEXT NOT NULL DEFAULT 'ingestion';
ALTER TABLE sources ADD COLUMN webhook_path TEXT;
