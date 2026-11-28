-- The ai schema: documents, chunks, embeddings and the indexing queue (spec §8.1).
-- Tables come with the features that need them. Always schema-qualify names:
-- migrate connects with search_path=public, where its version table lives.
CREATE SCHEMA ai;

GRANT USAGE ON SCHEMA ai TO ai_service, ai_worker;
-- Default privileges cover every table and sequence that migrator creates here later.
-- Service roles read and write rows but own nothing (spec §8.4).
ALTER DEFAULT PRIVILEGES IN SCHEMA ai
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ai_service, ai_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai
    GRANT USAGE, SELECT ON SEQUENCES TO ai_service, ai_worker;
