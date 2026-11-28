#!/usr/bin/env bash
# Runs once, when the postgres container starts on an empty data volume
# (docker-entrypoint-initdb.d). Creates the roles of spec §8.4 and ADR-008 and the
# extensions that need a superuser. Schemas, tables and grants come from migrations,
# which run as migrator, the owner of every object.
set -euo pipefail

# Passwords come from the environment via \getenv, not -v: process arguments are
# world-readable on the host, the environment is not. :'name' quotes them in SQL.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" <<'SQL'
\getenv migrator_password MIGRATOR_PASSWORD
\getenv app_service_password APP_SERVICE_PASSWORD
\getenv ai_service_password AI_SERVICE_PASSWORD
\getenv ai_worker_password AI_WORKER_PASSWORD
\getenv gateway_service_password GATEWAY_SERVICE_PASSWORD
CREATE ROLE migrator LOGIN PASSWORD :'migrator_password';
-- Service roles own nothing: they get row access through migration grants only.
CREATE ROLE app_service LOGIN PASSWORD :'app_service_password';
CREATE ROLE ai_service LOGIN PASSWORD :'ai_service_password';
CREATE ROLE ai_worker LOGIN PASSWORD :'ai_worker_password';
-- The gateway schema holds budgets, usage and generation results (ADR-003, ADR-009).
CREATE ROLE gateway_service LOGIN PASSWORD :'gateway_service_password';

-- The database owner may create schemas, so migrator can run CREATE SCHEMA.
ALTER DATABASE :"db" OWNER TO migrator;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db"
  TO app_service, ai_service, ai_worker, gateway_service;

-- vector: ai.chunk_embeddings (spec §8.2); citext: app.users.email (spec §8.1).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
SQL
