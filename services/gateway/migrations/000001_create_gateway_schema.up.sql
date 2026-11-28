-- The gateway schema: budgets, usage and generation results (ADR-003, ADR-009).
-- Tables come with the features that need them. Always schema-qualify names:
-- migrate connects with search_path=public, where its version table lives.
CREATE SCHEMA gateway;

GRANT USAGE ON SCHEMA gateway TO gateway_service;
-- Default privileges cover every table and sequence that migrator creates here later.
-- Service roles read and write rows but own nothing (spec §8.4).
ALTER DEFAULT PRIVILEGES IN SCHEMA gateway
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gateway_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA gateway
    GRANT USAGE, SELECT ON SEQUENCES TO gateway_service;
