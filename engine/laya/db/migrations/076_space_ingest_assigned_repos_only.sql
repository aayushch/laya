-- Per-space switch: when on, the space's GitHub/Bitbucket ingestion sources poll only its assigned repos, and other spaces' sources skip the ones it claims (rules in laya/space_repos.py).
ALTER TABLE spaces ADD COLUMN ingest_assigned_repos_only INTEGER NOT NULL DEFAULT 0;
