-- =============================================================================
-- JAS Database Schema — Supabase / PostgreSQL with pgvector
-- =============================================================================
-- Run this migration in the Supabase SQL Editor (or via psql) to bootstrap
-- the three core tables used by the Job Application System.
--
-- Embedding dimension: 768 (Google text-embedding-004)
-- =============================================================================

-- 0. Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";    -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";      -- pgvector  VECTOR type

-- 0b. Drop existing tables if rebuilding (safely drops empty tables to apply new schema)
DROP TABLE IF EXISTS applications_tracked CASCADE;
DROP TABLE IF EXISTS jobs_found CASCADE;
DROP TABLE IF EXISTS user_profile CASCADE;


-- =============================================================================
-- 1. user_profile
-- =============================================================================
-- Stores the single user's profile, parsed resume text, and its embedding.
-- The resume_embedding column supports hot-swap: clear old → store new.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_profile (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity
    full_name       TEXT        NOT NULL DEFAULT '',
    email           TEXT        NOT NULL DEFAULT '',
    phone           TEXT        NOT NULL DEFAULT '',
    linkedin_url    TEXT        NOT NULL DEFAULT '',

    -- Resume content
    resume_text     TEXT        NOT NULL DEFAULT '',
    resume_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    resume_embedding VECTOR(768),                     -- text-embedding-004

    -- Parsed / enriched fields (JSONB for flexibility - kept for legacy compatibility)
    skills          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    experience      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    education       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    certifications  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    summary         TEXT        NOT NULL DEFAULT '',

    -- Preferences
    target_roles    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    preferred_locations JSONB   NOT NULL DEFAULT '[]'::jsonb,
    min_salary      INTEGER,

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one profile row is expected; this unique partial index enforces it.
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_profile_singleton
    ON user_profile ((true));

-- HNSW index for fast cosine-similarity search on the resume embedding.
CREATE INDEX IF NOT EXISTS idx_user_profile_embedding
    ON user_profile
    USING hnsw (resume_embedding vector_cosine_ops);


-- =============================================================================
-- 2. jobs_found
-- =============================================================================
-- Every job posting discovered by the ingestion pipeline lands here.
-- url_hash provides idempotent deduplication across pipeline runs.
-- =============================================================================

CREATE TABLE IF NOT EXISTS jobs_found (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Deduplication
    url_hash        TEXT        NOT NULL UNIQUE,       -- SHA-256 of the URL

    -- Core fields
    title           TEXT        NOT NULL DEFAULT '',
    company         TEXT        NOT NULL DEFAULT '',
    location        TEXT        NOT NULL DEFAULT '',
    url             TEXT        NOT NULL DEFAULT '',
    platform        TEXT        NOT NULL DEFAULT '',    -- e.g. "linkedin", "wellfound"
    jd_text         TEXT        NOT NULL DEFAULT '',

    -- Enrichment
    salary_range    TEXT        NOT NULL DEFAULT '',
    job_type        TEXT        NOT NULL DEFAULT '',    -- full-time, contract …
    experience_level TEXT       NOT NULL DEFAULT '',

    -- AI-generated & Score
    jd_embedding    VECTOR(768),                       -- text-embedding-004
    cosine_score    FLOAT,                             -- cosine similarity vs resume
    llm_score       INTEGER,                           -- Gemini suitability score
    llm_reasoning   TEXT        NOT NULL DEFAULT '',
    tailored_bullets JSONB      NOT NULL DEFAULT '[]'::jsonb,
    tailored_resume_path TEXT,
    cover_letter_path TEXT,
    ai_summary      TEXT        NOT NULL DEFAULT '',

    -- Workflow status
    status          TEXT        NOT NULL DEFAULT 'new'
                        CHECK (status IN (
                            'new',
                            'FILTERED_MATH',
                            'FILTERED_LLM',
                            'PENDING_USER',
                            'APPLIED_AUTO',
                            'APPLIED_MANUAL',
                            'SKIPPED'
                        )),

    -- Timestamps
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast lookup by status for the daily digest & pending-jobs queries.
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs_found (status);

-- Date-range queries (daily stats, recent jobs).
CREATE INDEX IF NOT EXISTS idx_jobs_discovered
    ON jobs_found (discovered_at DESC);

-- HNSW index for semantic search across job embeddings.
CREATE INDEX IF NOT EXISTS idx_jobs_embedding
    ON jobs_found
    USING hnsw (jd_embedding vector_cosine_ops);


-- =============================================================================
-- 3. applications_tracked
-- =============================================================================
-- Tracks every concrete application action the user (or automation) takes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS applications_tracked (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK back to the job
    job_id          UUID        NOT NULL
                        REFERENCES jobs_found(id) ON DELETE CASCADE,

    -- Application details
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending',           -- queued for submission
                            'submitted',         -- application sent
                            'acknowledged',      -- confirmation received
                            'interviewing',      -- in interview loop
                            'offer_received',    -- offer extended
                            'accepted',          -- offer accepted
                            'rejected',          -- rejection received
                            'withdrawn'          -- user withdrew
                        )),

    -- Generated artefacts
    cover_letter_path TEXT      NOT NULL DEFAULT '',
    resume_path       TEXT      NOT NULL DEFAULT '',
    notes             TEXT      NOT NULL DEFAULT '',

    -- Timestamps
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_status_change TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_applications_job
    ON applications_tracked (job_id);

CREATE INDEX IF NOT EXISTS idx_applications_status
    ON applications_tracked (status);


-- =============================================================================
-- 4. Auto-update triggers for updated_at columns
-- =============================================================================

CREATE OR REPLACE FUNCTION jas_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- user_profile
DROP TRIGGER IF EXISTS trg_user_profile_updated ON user_profile;
CREATE TRIGGER trg_user_profile_updated
    BEFORE UPDATE ON user_profile
    FOR EACH ROW EXECUTE FUNCTION jas_set_updated_at();

-- jobs_found
DROP TRIGGER IF EXISTS trg_jobs_found_updated ON jobs_found;
CREATE TRIGGER trg_jobs_found_updated
    BEFORE UPDATE ON jobs_found
    FOR EACH ROW EXECUTE FUNCTION jas_set_updated_at();

-- applications_tracked
DROP TRIGGER IF EXISTS trg_applications_tracked_updated ON applications_tracked;
CREATE TRIGGER trg_applications_tracked_updated
    BEFORE UPDATE ON applications_tracked
    FOR EACH ROW EXECUTE FUNCTION jas_set_updated_at();
