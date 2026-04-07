-- Add trace and omni model columns to spaces for per-space model overrides
ALTER TABLE spaces ADD COLUMN trace_model TEXT;
ALTER TABLE spaces ADD COLUMN omni_model TEXT;
