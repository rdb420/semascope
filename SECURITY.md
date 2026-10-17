# Security

Report vulnerabilities privately through GitHub security advisories on this
repository. Do not open a public issue for a security problem.

Design commitments: no code generation from user input, no state change over
GET, no service port published except the reverse proxy (the optional
observability profile adds Grafana and Flower on loopback only), secrets only
from the environment or Docker secrets, outbound HTTP to any URL a source,
user or document supplies through one client with private-range blocking
(checked and pinned per connection), timeouts and size caps, timeouts and
size caps on the clients for the operator-configured extraction and model
services (Tika, MinerU, the embedding and entity providers), `defusedxml` for
XML, archive limits for containers. How each is done, and what stays the
operator's job, is in [docs/security.md](docs/security.md).
