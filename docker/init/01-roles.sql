-- Database and roles for the benchmark workbench.
--
-- Run once by the human against the externally managed container app_postgres
-- (see docker/README.md). Project code never runs this file and never execs
-- into the container. Idempotent: re-running changes nothing that exists, and
-- existing roles keep their passwords.
--
--   docker exec -i app_postgres psql -U postgres -v ON_ERROR_STOP=1 \
--     -v owner_password='...' -v read_password='...' < docker/init/01-roles.sql
--
-- Passwords are never stored in this file. Put the same values in ~/.pgpass
-- of user deploy:
--   127.0.0.1:5432:benchdata:bench_owner:...
--   127.0.0.1:5432:benchdata:bench_read:...
--
-- A second database, benchmeta, owned by bench_owner, also exists in the
-- container. It is used from phase 2 and is not created by this file.

\if :{?owner_password}
\else
  \warn 'owner_password is not set: pass -v owner_password=... -v read_password=...'
  \quit
\endif
\if :{?read_password}
\else
  \warn 'read_password is not set: pass -v owner_password=... -v read_password=...'
  \quit
\endif

-- Roles: plain login roles, no superuser, no CREATEDB, no CREATEROLE.
SELECT format('CREATE ROLE bench_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bench_owner') \gexec

SELECT format('CREATE ROLE bench_read LOGIN PASSWORD %L', :'read_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bench_read') \gexec

-- Database owned by bench_owner, so rebuilds can drop and create schemas.
SELECT 'CREATE DATABASE benchdata OWNER bench_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'benchdata') \gexec

ALTER DATABASE benchdata OWNER TO bench_owner;
GRANT CONNECT ON DATABASE benchdata TO bench_read;

\connect benchdata

-- bench_read is read-only. Rebuild drops and recreates schema mybank, so its
-- SELECT access comes from default privileges on everything bench_owner
-- creates. They are deliberately schema-less: IN SCHEMA defaults are destroyed
-- by DROP SCHEMA on every rebuild. USAGE on the schema itself is granted by
-- the rebuild (GRANT USAGE ON SCHEMA mybank TO bench_read) after CREATE SCHEMA.
ALTER DEFAULT PRIVILEGES FOR ROLE bench_owner GRANT SELECT ON TABLES TO bench_read;
ALTER DEFAULT PRIVILEGES FOR ROLE bench_owner GRANT SELECT ON SEQUENCES TO bench_read;
