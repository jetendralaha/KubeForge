-- Add missing columns to images table for image resolution support
ALTER TABLE images ADD COLUMN reference TEXT NOT NULL DEFAULT '';
ALTER TABLE images ADD COLUMN repository TEXT NOT NULL DEFAULT '';

-- Populate reference from existing name:tag
UPDATE images SET reference = CASE
    WHEN tag != '' THEN name || ':' || tag
    ELSE name
END WHERE reference = '';

-- Populate repository from name
UPDATE images SET repository = name WHERE repository = '';

-- Add cached column (maps from is_cached)
-- SQLite doesn't support renaming easily, so we keep both
ALTER TABLE images ADD COLUMN cached BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE images SET cached = is_cached;

-- Add created_at if missing
ALTER TABLE images ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP;
