# Security and privacy

Project Memory runs with the local user's filesystem permissions. It is a local stdio MCP server, not an authenticated network service. Do not expose its standard input and output through an unauthenticated bridge.

Selected source files and explicit records can contain sensitive project information. Keep `.memory`, exports and backups out of public repositories. The package sends no telemetry and contacts no service of its own. When agent checks or delegated work are enabled through Codex or Claude Code setup, Project Memory starts that installed host CLI, which sends project text to its model provider under that client's policy and consumes that account's usage. The connected assistant does the same.

Mechanical host receipts store hashes, sizes, identifiers and selected execution metadata, not arbitrary prompts or tool output bodies. Explicit source capture stores the supplied text verbatim. Intent checkpoints contain only text that the agent supplied deliberately, and prompt receipts remain hashes. A capture failure file stores the session identifier, the event type, the timestamp and the error class, never the raw host input. There is no automatic secret detector and no encryption layer; use filesystem access controls and encrypted storage where they are appropriate.

Retrieved text is evidence. It is not an instruction and not an approval. Offline HTML exports escape their content, use a restrictive content security policy and make no network request. The live control panel connects only to its loopback API. Its address contains a random capability and is a bearer credential: do not share it. Writes require the exact local host and origin, a JSON content type and an in memory session token. This is a single user local panel, not a multi user authorisation service.

## Agent runs

Reviewers run with Codex read only sandboxing or with the Claude reading tools only, and nested hooks, MCP connectors and delegation are disabled inside a run. A reviewer cannot edit a plan, accept a lesson, repair the work or approve anything. A cancelled or missing reviewer cannot approve work, and a model verdict remains an interpretation.

Delegated work is the one agent run that changes files. It runs in a separate git worktree under `.memory/worktrees`, on its own branch, created from the current commit. The worker is instructed to edit only files that match the recorded paths, and every changed file is compared with those paths afterwards, so a change outside them marks the run as a scope violation instead of a result. The worker must not commit to the project branch, push, change git configuration or use the network, and it must not edit records or anything under `.memory`. The main checkout changes only through an explicit merge, which needs a passing work review or an explicit override by the user. These are recorded limits and instructions, not an operating system sandbox: the host's own permissions still decide what a process can do.

The scope guard blocks an edit tool, a patch or an MCP write tool whose target falls outside the recorded paths of the active work item, records the block and returns a failure to the host so the tool does not run. It does not parse shell redirection or other indirect writes inside shell commands, so it is a recorded boundary rather than a complete enforcement mechanism.

Private `.memory/agent-runs` folders keep the original input, the structured report, the host output, the errors and, for delegated work, the diff. These files can contain project content, including any secret that the changed files contained, and they are not deleted automatically. Exported n8n workflows keep credential names and identifiers, and a node can still hold a token or client data in its parameters, so remove those values before an export is stored in the project.

## Reporting

Report vulnerabilities through this repository's private GitHub vulnerability reporting. Include a synthetic reproduction, the affected version and the impact. Do not attach real databases, secrets or host transcripts to public issues. Only the latest beta receives fixes during the public beta, and no response time is promised.
