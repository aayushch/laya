-- Models/keys a provider refused outright (#25): 404 retired/unknown model,
-- 401 rejected key, 403 model not allowed for this key. The refusal repeats on
-- every call until the user changes settings, so llm_call skips the call and the
-- queue holds events (processing_status='held') instead of burning their retries.
-- A table, not module state: /health reads it and it survives restarts.
CREATE TABLE IF NOT EXISTS model_availability (
    model       TEXT NOT NULL,             -- configured id, e.g. gemini/gemini-2.0-flash
    role        TEXT NOT NULL,             -- llm_call role that hit it (router, stager, ...)
    space_id    TEXT NOT NULL DEFAULT '',  -- API keys are per space; '' = no space
    kind        TEXT NOT NULL,             -- not_found | auth | permission
    reason      TEXT NOT NULL,             -- provider error, truncated to 300 chars
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model, role, space_id)
);
