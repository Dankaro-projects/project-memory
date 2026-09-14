# Security and privacy

Project Memory runs with the local user's filesystem permissions. It is a local stdio MCP server, not an authenticated network service. Do not expose its stdin/stdout through an unauthenticated bridge.

Selected source files and explicit records may contain sensitive project information. Keep `.memory`, exports and backups out of public repositories. The package sends no telemetry. When agent checks are enabled through Codex or Claude setup, it starts that installed host CLI to review selected memory evidence and relevant project files. These model requests use the host account and consume its usage. The connected assistant and reviewer may send project text to their model providers under those clients' policies.

Mechanical host receipts store hashes, sizes, identifiers and selected execution metadata, not arbitrary prompts or tool-output bodies. Explicit source capture stores the supplied text verbatim. There is no automatic secret detector or encryption layer; use filesystem access controls and encrypted storage where appropriate.

Retrieved text is evidence, not executable instruction or approval. Offline HTML exports escape content, use a restrictive content security policy and make no network requests. The live workspace connects only to its loopback API. Its private URL is a bearer capability; do not share it. Writes require the exact local Host and Origin, JSON content type and an in-memory CSRF token. This is a single-user local workspace, not a multi-user authorisation service.

Reviewers run with Codex read-only sandboxing or Claude Read/Glob/Grep tools, with nested hooks, MCP connectors and delegation disabled. A cancelled or missing reviewer cannot approve work. Private `.memory/agent-runs` folders preserve the original input, structured report, host output and errors for diagnosis; these files can contain project content and are not automatically deleted. A model verdict remains an interpretation, and lesson acceptance remains explicit.

Report vulnerabilities through this repository's private GitHub vulnerability reporting. Include a synthetic reproduction, affected version and impact. Do not attach real databases, secrets or host transcripts to public issues. Only the latest beta receives fixes during the initial public beta; no response-time SLA is promised.
