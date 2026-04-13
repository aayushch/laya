-- Track whether a workspace session was spawned for code or research.
-- Used by resume_with_answer to apply the correct permission/sandbox mode:
-- 'code' sessions get full write access (acceptEdits/full-auto),
-- 'research' sessions get scoped write access (research dir only + web search).
ALTER TABLE workspace_sessions ADD COLUMN session_type TEXT DEFAULT 'code';
