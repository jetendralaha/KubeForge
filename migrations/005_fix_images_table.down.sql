-- Revert images table changes
-- SQLite doesn't support DROP COLUMN in older versions, so this is best-effort
ALTER TABLE images DROP COLUMN reference;
ALTER TABLE images DROP COLUMN repository;
ALTER TABLE images DROP COLUMN cached;
ALTER TABLE images DROP COLUMN created_at;
