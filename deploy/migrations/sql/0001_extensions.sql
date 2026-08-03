-- 0001 — extensions
--
-- Migrations are never edited once merged (GIT-WORKFLOW.md rule 9). Fix forward
-- with a new file.

create extension if not exists timescaledb;

-- Digest is used to hash station bearer tokens and invite tokens (D-017, D-020),
-- and to hash observation bodies for the idempotency check (D-015).
create extension if not exists pgcrypto;
