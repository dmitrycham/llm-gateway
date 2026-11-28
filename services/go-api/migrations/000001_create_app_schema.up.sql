-- The app schema: Go API data (spec §8.1).
-- Tables come with the features that need them. Always schema-qualify names:
-- migrate connects with search_path=public, where its version table lives.
CREATE SCHEMA app;

GRANT USAGE ON SCHEMA app TO app_service;
-- Default privileges cover every table and sequence that migrator creates here later.
-- Service roles read and write rows but own nothing (spec §8.4).
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO app_service;
