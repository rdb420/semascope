# Security

Report vulnerabilities privately through GitHub security advisories on this
repository. Do not open a public issue for a security problem.

Design commitments: no code generation from user input, no state change over
GET, no service port published except the reverse proxy, secrets only from the
environment or Docker secrets, outbound HTTP through one client with
private-range blocking and size caps, `defusedxml` for XML, archive limits
for containers.
