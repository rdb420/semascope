# External dashboards

Semascope's **Overview** page answers the common questions (how much, of
what kind, about whom, since when). For anything else, export tables and
point a dashboard tool at them ([ADR-0012](adr/0012-never-delivered-capabilities.md),
parity row ANL-17). These are recipes, not shipped integrations: nothing
below runs inside the stack.

An export carries what its token's user may read, no more; give the
dashboard's export a user with the reader role on the collections it should
show. Named entities of a collection that pseudonymises are exported by key,
without their names. The columns are listed under [Tables](interop.md#tables).

## 1. Export on a schedule

```bash
# every night: documents and their concepts and entities, as Parquet
oss export documents --collection demo --format parquet --limit 10000 \
  --out /srv/dashboards/documents.parquet
oss export entities --collection demo --format parquet --limit 10000 \
  --out /srv/dashboards/entities.parquet
```

`oss` reads `SEMASCOPE_API_TOKEN` and `SEMASCOPE_API_URL` from the
environment or the env file, so a cron job or a systemd timer can run it. An
export stops at 10,000 documents; split a larger collection by search (a
date range, a facet value) and export each part.

## 2. DuckDB

DuckDB reads the files as they are:

```sql
-- documents per language and month of modification
SELECT language, date_trunc('month', modified_at) AS month, count(*) AS documents
FROM 'documents.parquet'
GROUP BY ALL ORDER BY month;

-- the people named in the most documents
SELECT label, count(DISTINCT document_id) AS documents
FROM 'entities.parquet'
WHERE kind = 'entity' AND class = 'Person'
GROUP BY label ORDER BY documents DESC LIMIT 20;

-- documents per concept, with their titles
SELECT e.label AS concept, d.title, d.page_url
FROM 'entities.parquet' e JOIN 'documents.parquet' d ON d.id = e.document_id
WHERE e.kind = 'concept';
```

To keep a history, append each night's export to a table instead:
`CREATE TABLE IF NOT EXISTS documents AS FROM 'documents.parquet' LIMIT 0;
INSERT INTO documents FROM 'documents.parquet';` with an export date column
added in the `INSERT`.

## 3. Apache Superset

Superset connects to DuckDB through the `duckdb-engine` SQLAlchemy driver.
Load the exports into a database file Superset can read
(`duckdb /srv/dashboards/semascope.duckdb "CREATE OR REPLACE TABLE documents AS FROM 'documents.parquet'"`),
add a database with the URI `duckdb:////srv/dashboards/semascope.duckdb`
(read-only), and build datasets on `documents` and `entities`. A CSV export
can also be uploaded through Superset's own CSV upload, if it is enabled.

## 4. Grafana

Grafana has no built-in Parquet source. Two ways that keep Semascope out of
the query path:

- load the Parquet files into PostgreSQL (for example with DuckDB's
  `postgres` extension: `ATTACH 'dbname=dashboards' AS pg (TYPE postgres);
  CREATE OR REPLACE TABLE pg.documents AS FROM 'documents.parquet';`) and
  use Grafana's PostgreSQL data source with a read-only role;
- or install a DuckDB data source plugin and point it at the database file
  from step 3.

Grafana's own dashboards in `compose.observe.yaml` watch the stack
(requests, queues, latency); keep content dashboards in a separate Grafana
organisation or folder so their viewers do not need access to operations.

## 5. RDF instead of tables

For a triplestore or a graph tool, export the collection's graph
(`oss export graph --collection demo --format nquads`) or documents with
their provenance (`oss export search ... --format nquads`) and load the
N-Quads file; each document, or the collection, is one named graph. See
[RDF exports](interop.md#rdf-exports).
