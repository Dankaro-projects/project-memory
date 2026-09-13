# Evidence and limits

The public beta preserves a working private prototype and adds installation, lifecycle management and approved project revisions. Measurements below distinguish deterministic checks, real host execution and untested product hypotheses. A passing suite alone does not establish day-to-day productivity.

## Reproducible beta checks

Run the commands in the README in a fresh checkout. Each example output directory must be new. Outputs stay under the ignored `results` directory.

| Check | Observed locally on 13 September 2026 | Meaning |
|---|---|---|
| Python regression and lifecycle suite | 101 tests passed | Covers immutable history, boundaries, retries, stale evidence, revision approval, interrupted setup recovery and preservation of existing settings. |
| Fresh wheel installation outside the checkout | Passed | CLI, repeated capture, a separate MCP process, packaged viewer, backup and removal operate without the source directory. |
| Scripted document/retrieval case | 7/7 checks passed | An oversized lesson remains discoverable, exceptions survive expansion, changed files flag decisions, unchanged captures reuse a version and original text is reconstructed. |
| Complete source expansion at a 2,500-character reply limit | 2 calls; 4,338 returned characters in this run | Path length affects envelope size. These are reply characters, not model tokens. |
| Actual parser failure and recovery | Exit 1, then exit 0 | The example retains both outcomes and one unresolved action. It does not infer a semantic lesson from an exit code. |
| Chrome 152 HTML execution | Passed, zero network requests and JavaScript errors | Covers navigation, filters, paging, escaped content, source freshness, correction ordering and mobile width. |

`tests/test_product.py` injects a write interruption before and after the hook configuration write, retries setup and checks for duplicate hooks and lost unrelated settings. It also aborts approval inside a transaction and verifies that neither the revision nor its table creation leaks through a partial commit.

The wheel has zero declared runtime dependencies. Build tools, the optional Playwright browser check and live Codex verification are development dependencies. Package sizes and hashes are emitted by `scripts/check_artifacts.py`; release assets provide the exact published bytes.

## Installed Codex beta boundary

The installed wheel ran in a separate virtual environment and project directory with Codex 0.153.4 on macOS. The setup command persisted project trust and obtained nine exact hashes from the actual host. Fresh sessions observed all nine lifecycle events, including `SessionEnd` on ordinary CLI shutdown. The verification client no longer supplies temporary project trust. App-server unsubscribe does not itself emit `SessionEnd` on this host.

An interruption case wrote one marker, interrupted the tool, then resumed the same task through a new host process. The marker remained exactly once; an unconfirmed execution receipt and unknown process completion remained visible. Seven recovery MCP calls completed without a reported error. Compaction preserved all four checked facts, including the legacy exception and unmeasured production outcome. No adapter calls were recursively captured as external work.

A separate document task passed all twelve quality and integration checks. It used five MCP calls, including one rejected relative document path followed by a successful absolute-path retry. Stating the absolute-path requirement in the tool schema did not eliminate this model correction. Raw model outputs and host transcripts remain local; the reviewed [aggregate report](verification-0.5.0b1.json) contains counts and provider usage.

| Comparable synthetic case | Before fixes | After fixes | Quality |
|---|---:|---:|---|
| Document task: aggregate provider input | 116,481 | 114,403 | 12/12 checks in both; one path correction in each. |
| Interruption and recovery: aggregate provider input | 259,684 | 289,216 | The side effect occurs once and unknown execution remains visible in both. |

These are single runs of the same harness and model, not a controlled productivity study. Path lengths and model choices vary. The integration fixes improve capture correctness; the recovery rerun uses more tokens. Neither result establishes a general saving. Human corrections and maintenance minutes were not measured.

To reproduce the live boundary after installing the release, create an empty test directory, set `TEST_PROJECT` to its absolute path, and run these commands from the checkout. Each output directory must be new. The harness uses the signed-in Codex account and consumes model usage.

```sh
project-memory setup --project "$TEST_PROJECT" --db "$TEST_PROJECT/memory.sqlite" --client codex --trust
python -m examples.codex_interrupt --project "$TEST_PROJECT" --output results/live-interruption
python -m examples.codex_documents --project "$TEST_PROJECT" --output results/live-documents
python -m examples.codex_lifecycle --project "$TEST_PROJECT" --output results/live-lifecycle
codex exec -C "$TEST_PROJECT" --skip-git-repo-check 'Reply only: The session can close.'
project-memory doctor --project "$TEST_PROJECT"
python -m examples.context_audit results/live-interruption
```

The context audit exits with status 1 when the target is not met. That failure is evidence, not a reason to remove conditions or skip difficult tasks.

## Prior prototype measurements

The following are historical results from the private prototype, not a new public-beta trial. The private databases and host transcripts are intentionally excluded from this repository.

Three synthetic paired Codex tasks preserved ten quality checks and reduced aggregate provider input from 93,435 to 74,634 tokens, a 20.1% reduction. Repeated reads of current research fell from two to zero; the necessary stale-source refresh remained one in each condition. A different MCP-first approach increased input from 93,895 to 163,369 tokens because it added model turns. Neither experiment establishes general savings.

A later document workflow passed twelve integration and answer checks, with five MCP calls: four succeeded, one exceeded the allowed reply limit and was corrected. Its complete inputs ranged from 14,556 to 17,721 tokens. There was no matched control. An early result parser missed that error because it relied on a missing host flag; the evaluator was corrected to inspect returned error content. Errors must remain in the reported denominator.

The earlier host verification observed all nine Codex lifecycle events, a real interrupted command, and recovery through a new host process. The command's marker remained exactly once. Process completion remained unknown where the evidence could not establish it. Fault injection before SQLite capture blocked an action; a post-capture failure preserved its side effect and exposed an unconfirmed receipt. These observations motivate the public harnesses, not a claim that every host version behaves identically.

## Public packaging defects found and corrected

The initial Windows run exposed a real freshness defect: a captured `file:///D:/...` URI was not converted back to a Windows path, so changed or unreadable documents appeared current. The implementation now uses the standard library's platform-aware URL-to-path conversion. The same existing cases are rerun on Windows rather than skipped.

The first bundle manifest used an unsupported platform key. The official MCPB validator rejected it; the manifest now uses the documented `platform_overrides` field and passes validation.

Fresh host execution exposed two further defects. Setup had trusted hook hashes while leaving project trust effective only within its temporary host process. Setup now writes persistent trust before obtaining the hashes and verifies the saved setting. The renamed adapter also recorded its own MCP calls; capture now excludes both the legacy and public adapter namespaces. Comparable cases were rerun after both fixes. The browser report now lists optional project-view checks only when their fixture actually ran.

## What remains unmeasured

Daily completion rates, human corrections, net maintenance time, long-term drift and competing products have not been measured in a representative public beta. No guarantee of autonomous learning or competitor superiority follows from the current evidence. The assistant can overlook evidence, write a poor interpretation or fail to record a dependency. Textual freshness cannot establish whether an unchanged vision is still the right vision.

Below 10K tokens in every complete model input remains an unmet target. Mandatory context, tool definitions and accumulated host history can exceed it before memory retrieval. Do not remove features, exceptions or evidence to meet the target. Measure the complete input as reported by the provider; label character-based estimates separately.

A useful beta trial includes difficult work and failures as well as successes. For each comparable task, record completion, acceptance checks, provider input tokens, repeated research, corrections and minutes spent maintaining memory. Record unknown measurements as unknown. No participant content or telemetry is collected automatically.
