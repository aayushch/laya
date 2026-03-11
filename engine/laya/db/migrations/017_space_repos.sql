-- Per-space repository assignments: links spaces to repos from repos.json
CREATE TABLE IF NOT EXISTS space_repos (
    space_id  TEXT NOT NULL REFERENCES spaces(space_id) ON DELETE CASCADE,
    repo_name TEXT NOT NULL,
    position  INTEGER DEFAULT 0,
    PRIMARY KEY (space_id, repo_name)
);
