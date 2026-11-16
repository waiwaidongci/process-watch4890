-- Investigation cover registration. Applied after the ORM bootstrap; the ORM
-- metadata creates the same tables for a fresh database.
CREATE INDEX IF NOT EXISTS cover_cause_run_idx ON cover_causes (run_id);
CREATE INDEX IF NOT EXISTS cover_conflict_run_idx ON cover_conflicts (run_id);
CREATE INDEX IF NOT EXISTS cover_search_run_idx ON cover_searches (run_id);
