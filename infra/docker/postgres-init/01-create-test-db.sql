-- Runs once on first start of an empty data volume.
-- The main database comes from POSTGRES_DB; this adds a separate one for tests.
CREATE DATABASE claimlens_test;
