# Postgres for ClaimLens jobs (and, later, LangGraph checkpoints).
FROM postgres:16-alpine

# Scripts in this folder run once, the first time the data volume is created.
COPY infra/docker/postgres-init/ /docker-entrypoint-initdb.d/

EXPOSE 5432

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" || exit 1
