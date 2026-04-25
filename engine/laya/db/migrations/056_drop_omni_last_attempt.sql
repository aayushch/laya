-- Remove omni_last_attempt table. The pre-LLM watermark it provided caused a
-- deadlock: the scheduler threshold counted cards since the last *successful*
-- snapshot, but _resynthesize_space used the more-recent attempt watermark,
-- finding zero new cards and skipping the LLM call entirely. Now both the
-- scheduler and resynthesis use omni_snapshots.generated_at as the single
-- source of truth.
DROP TABLE IF EXISTS omni_last_attempt;
