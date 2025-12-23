-- Migration: Convert to Bundle-Only Architecture
-- This migration removes the PKL/REQUIREMENTS asset types and simplifies to bundles only
--
-- CAUTION: This is a destructive migration that drops existing PKL/REQUIREMENTS data
-- Only run this in development/staging environments during prototyping phase

BEGIN;

-- ============================================================================
-- STEP 1: Backup existing data (optional, for rollback)
-- ============================================================================

-- Uncomment if you want to preserve old data
-- CREATE TABLE assets_backup AS SELECT * FROM assets WHERE asset_type IN ('PKL', 'REQUIREMENTS');
-- CREATE TABLE asset_versions_backup AS
--     SELECT av.* FROM asset_versions av
--     JOIN assets a ON av.asset_id = a.id
--     WHERE a.asset_type IN ('PKL', 'REQUIREMENTS');

-- ============================================================================
-- STEP 2: Drop old asset type references
-- ============================================================================

-- Remove workers' references to old asset types
ALTER TABLE workers DROP COLUMN IF EXISTS pkl_asset_id CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS requirements_asset_id CASCADE;

-- Remove legacy pkl fields from workers (if they exist)
ALTER TABLE workers DROP COLUMN IF EXISTS pkl_file_path CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS pkl_file_hash CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS pkl_file_size CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS pkl_version CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS requirements_file_path CASCADE;
ALTER TABLE workers DROP COLUMN IF EXISTS requirements_file_hash CASCADE;

-- Do the same for reputers table if it exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'reputers') THEN
        ALTER TABLE reputers DROP COLUMN IF EXISTS model_asset_id CASCADE;
        ALTER TABLE reputers DROP COLUMN IF EXISTS config_asset_id CASCADE;
        ALTER TABLE reputers DROP COLUMN IF EXISTS requirements_asset_id CASCADE;
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Delete old asset type data
-- ============================================================================

-- Delete asset_versions for PKL and REQUIREMENTS assets
DELETE FROM asset_versions
WHERE asset_id IN (
    SELECT id FROM assets WHERE asset_type IN ('PKL', 'REQUIREMENTS')
);

-- Delete PKL and REQUIREMENTS assets
DELETE FROM assets WHERE asset_type IN ('PKL', 'REQUIREMENTS');

-- ============================================================================
-- STEP 4: Update asset_type enum to BUNDLE only
-- ============================================================================

-- Drop and recreate the enum with only BUNDLE
DROP TYPE IF EXISTS asset_type CASCADE;
CREATE TYPE asset_type AS ENUM ('BUNDLE');

-- Restore the column with new enum type (default to BUNDLE)
ALTER TABLE assets ADD COLUMN asset_type_new asset_type NOT NULL DEFAULT 'BUNDLE';
ALTER TABLE assets DROP COLUMN asset_type CASCADE;
ALTER TABLE assets RENAME COLUMN asset_type_new TO asset_type;

-- ============================================================================
-- STEP 5: Add bundle metadata column to asset_versions
-- ============================================================================

ALTER TABLE asset_versions ADD COLUMN IF NOT EXISTS bundle_metadata JSONB;

COMMENT ON COLUMN asset_versions.bundle_metadata IS
'Parsed model.yaml contents for bundle assets. Includes name, version, entrypoint, etc.';

-- ============================================================================
-- STEP 6: Add bundle reference to workers
-- ============================================================================

-- Add bundle_asset_id (nullable for now during migration)
ALTER TABLE workers ADD COLUMN IF NOT EXISTS bundle_asset_id INTEGER REFERENCES assets(id);

-- Add build pipeline columns
ALTER TABLE workers ADD COLUMN IF NOT EXISTS build_state VARCHAR(50) DEFAULT 'pending';
ALTER TABLE workers ADD COLUMN IF NOT EXISTS image_reference TEXT;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS build_logs TEXT;
ALTER TABLE workers ADD COLUMN IF NOT EXISTS build_error TEXT;

-- Add wallet secret reference
ALTER TABLE workers ADD COLUMN IF NOT EXISTS wallet_secret_name VARCHAR(255);

-- Add indexes for new columns
CREATE INDEX IF NOT EXISTS idx_workers_bundle_asset_id ON workers(bundle_asset_id);
CREATE INDEX IF NOT EXISTS idx_workers_build_state ON workers(build_state);
CREATE INDEX IF NOT EXISTS idx_workers_wallet_secret ON workers(wallet_secret_name);

COMMENT ON COLUMN workers.bundle_asset_id IS 'Reference to the bundle asset containing code, artifacts, and config';
COMMENT ON COLUMN workers.build_state IS 'Build pipeline state: pending, building, completed, failed';
COMMENT ON COLUMN workers.image_reference IS 'Full container image reference after successful build';
COMMENT ON COLUMN workers.wallet_secret_name IS 'Name of Kubernetes secret containing wallet credentials';

-- ============================================================================
-- STEP 7: Update reputers table similarly (if exists)
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'reputers') THEN
        ALTER TABLE reputers ADD COLUMN IF NOT EXISTS bundle_asset_id INTEGER REFERENCES assets(id);
        ALTER TABLE reputers ADD COLUMN IF NOT EXISTS build_state VARCHAR(50) DEFAULT 'pending';
        ALTER TABLE reputers ADD COLUMN IF NOT EXISTS image_reference TEXT;
        ALTER TABLE reputers ADD COLUMN IF NOT EXISTS wallet_secret_name VARCHAR(255);

        CREATE INDEX IF NOT EXISTS idx_reputers_bundle_asset_id ON reputers(bundle_asset_id);
    END IF;
END $$;

-- ============================================================================
-- STEP 8: Update existing indexes (cleanup)
-- ============================================================================

-- Remove old indexes that no longer exist
DROP INDEX IF EXISTS idx_workers_pkl_asset_id;
DROP INDEX IF EXISTS idx_workers_requirements_asset_id;
DROP INDEX IF EXISTS idx_reputers_model_asset_id;
DROP INDEX IF EXISTS idx_reputers_config_asset_id;
DROP INDEX IF EXISTS idx_reputers_requirements_asset_id;

-- ============================================================================
-- STEP 9: Add constraints for data integrity
-- ============================================================================

-- Workers must have a bundle (make NOT NULL after existing data migrated)
-- Uncomment this after all existing workers have been migrated or deleted:
-- ALTER TABLE workers ALTER COLUMN bundle_asset_id SET NOT NULL;

-- Build state must be valid value
ALTER TABLE workers ADD CONSTRAINT check_workers_build_state
    CHECK (build_state IN ('pending', 'building', 'completed', 'failed'));

-- If image_reference is set, build_state should be completed
-- (This is a soft constraint, enforced by application logic)

-- ============================================================================
-- STEP 10: Update activity log triggers (if needed)
-- ============================================================================

-- Update activity log to track bundle-related events
-- This depends on your existing activity log setup
-- Add new activity types: BUNDLE_UPLOADED, BUNDLE_DEPLOYED, etc.

-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================

-- Verify asset_type enum
-- SELECT unnest(enum_range(NULL::asset_type));
-- Expected: Only 'BUNDLE'

-- Verify workers structure
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'workers' AND column_name LIKE '%asset%';
-- Expected: Only bundle_asset_id

-- Count remaining assets
-- SELECT asset_type, COUNT(*) FROM assets GROUP BY asset_type;
-- Expected: Only BUNDLE rows (or 0 if no bundles uploaded yet)

COMMIT;

-- ============================================================================
-- ROLLBACK (if needed)
-- ============================================================================

-- To rollback this migration, you would need to:
-- 1. Restore from assets_backup and asset_versions_backup tables
-- 2. Recreate the old asset_type enum
-- 3. Restore old columns on workers/reputers
-- 4. Drop new columns (bundle_asset_id, build_state, etc.)
--
-- Only possible if you created backup tables at the start!
