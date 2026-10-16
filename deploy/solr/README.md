# Solr 10 configsets

Phase 1 adds three configsets here, written from scratch for Solr 10:

| Collection | Unit | Purpose |
|---|---|---|
| `documents` | one document | metadata, full text, facets, pack tags with `pack_version` |
| `chunks` | one chunk | `DenseVectorField` (cosine, quantised), chunk text, document id |
| `entities` | one label of one concept or individual, per pack version | the Tagger handler dictionary |

Rules: managed schema with schemaless off; nothing that Solr 10 removed
(`<lib>`, `ManagedSynonymFilterFactory`, language response writers,
`ExternalFileField`).

## Authentication

Solr runs with basic authentication (`blockUnknown: true`, one user,
`semascope`, with the admin role). Compose mounts `init-security.sh` into the
Solr image's `/docker-entrypoint-initdb.d`. On every start it waits for Solr,
then, if ZooKeeper has no `security.json` with an `authentication` section,
renders `security.json.tmpl` with a salted SHA-256 hash of `SOLR_PASSWORD`
(from `.env`, passed in as `SEMASCOPE_SOLR_PASSWORD`) and uploads it. It never
replaces an existing `security.json`, so it is safe to run repeatedly, and an
existing install gets authentication on its next `./oss up`.

The Solr health check passes only when the `semascope` user gets in and an
anonymous request is refused, so the application services, which depend on a
healthy Solr, never start against an open Solr. With `SOLR_PASSWORD` empty the
health check fails rather than run without authentication.

To change the password later, update it in Solr first and then in `.env`:

```bash
docker compose exec solr sh -c 'curl -s -u "semascope:$SEMASCOPE_SOLR_PASSWORD" \
  -H "Content-Type: application/json" \
  -d "{\"set-user\": {\"semascope\": \"NEW-PASSWORD\"}}" \
  http://localhost:8983/solr/admin/authentication'
# then set SOLR_PASSWORD=NEW-PASSWORD in .env and run ./oss up
```

The embedded ZooKeeper (port 9983) is reachable only inside the compose
network and has open ACLs; do not publish it.
