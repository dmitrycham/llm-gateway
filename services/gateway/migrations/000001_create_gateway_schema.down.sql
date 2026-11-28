ALTER DEFAULT PRIVILEGES IN SCHEMA gateway
    REVOKE USAGE, SELECT ON SEQUENCES FROM gateway_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA gateway
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM gateway_service;
-- No CASCADE: later down migrations must have removed every object first.
DROP SCHEMA gateway;
