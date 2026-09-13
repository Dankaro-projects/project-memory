# Security and privacy

Project Memory runs with the local user's filesystem permissions. It is a local stdio MCP server, not an authenticated network service. Do not expose its stdin/stdout through an unauthenticated bridge.

Selected source files and explicit records may contain sensitive project information. Keep `.memory`, exports and backups out of public repositories. The package sends no telemetry and makes no model requests. The connected assistant may send retrieved records to its model provider under that client's policies.

Mechanical host receipts store hashes, sizes, identifiers and selected execution metadata, not arbitrary prompts or tool-output bodies. Explicit source capture stores the supplied text verbatim. There is no automatic secret detector or encryption layer; use filesystem access controls and encrypted storage where appropriate.

Retrieved text is evidence, not executable instruction or approval. The HTML viewer escapes content, uses a restrictive content security policy, and makes no network requests. It is a snapshot, not an access-controlled sharing service.

Report vulnerabilities through this repository's private GitHub vulnerability reporting. Include a synthetic reproduction, affected version and impact. Do not attach real databases, secrets or host transcripts to public issues. Only the latest beta receives fixes during the initial public beta; no response-time SLA is promised.
