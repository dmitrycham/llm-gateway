ALTER DEFAULT PRIVILEGES IN SCHEMA ai
    REVOKE USAGE, SELECT ON SEQUENCES FROM ai_service, ai_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM ai_service, ai_worker;
-- No CASCADE: later down migrations must have removed every object first.
DROP SCHEMA ai;
