# Workspace and agent evaluation, 14 September 2026

This evaluation compares beta 4 at `b65b7ae7a5aba18a8fddc9e84f62247e2d089ccf` with the beta 5 development implementation. The baseline suite passed 149 tests. Passing tests was insufficient: the baseline accepted Done for a deliberately incomplete parser. The updated system rejected that same unsupported completion, and both real host reviewers accepted the repaired implementation without removing its exception.

The [aggregate measurements](verification-workspace-agents-2026-09-14.json) retain 15 reviewer attempts across ten fixture directories, including two startup failures and two cancellations. Host logs, databases, inputs and outputs remain in private `results/*/.memory` directories. The public report contains synthetic-case aggregates, not customer transcripts. Tests and reviews were run during development; early failures used earlier revisions of the implementation. This is a local beta evaluation, not a production or competitor benchmark.

## Quality and completion

The invented contract has four criteria: strict UTF-8 succeeds, invalid UTF-8 is rejected, explicitly tagged Latin-1 remains supported, and production effects are reported as unmeasured. The broken implementation passes its two tests while ignoring the legacy flag. Its recorded good/complete outcome deliberately overstates the evidence. The repair implements the conditional Latin-1 path and adds its test. Both successful original paths remain covered.

| Case | Observed result |
| --- | --- |
| Beta 4, two passing tests and missing legacy behaviour | The plan accepts Done. |
| Beta 5, unchanged broken behaviour | The independent result is Changes required; the domain layer rejects Done. |
| Actual Codex outcome reviewer | It identifies the missing exception, then passes all four criteria after repair. |
| Actual Claude outcome reviewer | It independently identifies the missing exception, then passes all four criteria after repair. |
| Intent reviewer, next action proposes deleting the exception | It identifies the conflict with the unchanged objective and scope. |
| Codex native Stop | It requests exactly one reviewer and records the reviewer result. The host reports unsupported completion. |
| Claude native Stop after repair of hook feedback | Two Stop events produce one request and one block receipt. The reviewer identifies the missing legacy behaviour. |
| Actual stdio MCP outcome write | It commits the outcome, returns a queued check ID, and launches a separate Claude reviewer that finds the defect. |

These observations support correctness on the stated fixture. They do not establish general reviewer accuracy or a daily improvement percentage. Reviewer proposals remain proposals; no accepted lessons are created by these checks.

## Interruptions and recovery

Two real Codex `turn/interrupt` calls generated Interrupt receipts and cancelled their associated reviewers. The workers stopped after approximately 0.5 and 1.5 seconds; provider usage was unavailable. The final interrupted host turn took 55.6 seconds overall, including its earlier work. An explicit recovery review preserved the cancelled attempt, inspected the existing artifacts and reported the missing legacy path and unproven interrupted completion. It did not execute the implementation again.

These runs verify reviewer cancellation and inspection after host interruption. They do not independently prove exactly-once handling of an interrupted external write. The earlier [continuation and recovery evaluation](autonomy-2026-09-14.md) remains separate evidence. Claude has no equivalent Interrupt hook; this iteration verifies its Stop dispatch and standalone reviewer, not native Claude interruption cancellation.

## Failures and their causes

Two initial Codex reviewers failed before inference because the launcher tried to disable a nonexistent MCP entry, then quoted a configuration key in a way the CLI interpreted literally. The launcher now disables only observed connector entries using supported keys.

A subsequent Codex run exposed colliding Stop receipts after a block. Event identity now includes the final-message hash and block state. A durable intervention receipt prevents repeated blocks for the same check and turn. The initial problematic run lasted 209.5 seconds; the comparable corrected native run lasted 110.6 seconds. Model behaviour and tool counts varied, so this is an observed pair, not an isolated performance estimate.

Claude exposed a different cause: Stop `additionalContext` also continues the conversation. Returning “still running” feedback repeatedly produced seven Stop events. The corrected run produced two. Its native host duration fell from 37.9 to 19.8 seconds, while its independent reviewer continued asynchronously. This matches the documented [Claude Stop control semantics](https://code.claude.com/docs/en/hooks#stop-decision-control); a non-blocking feedback field is still a continuation trigger.

Real Chrome editing exposed an older-plan compatibility defect: omitting an optional dependency list versus explicitly saving an empty list incorrectly replaced governing evidence and invalidated a pass. The comparison now treats those representations equivalently. The fixture retains the mistaken update and its correction. A subsequent browser save reaches Done with the same passing check. Changing the parser without a memory write then moves it to Review and blocks dependent writing; restoring the checked file restores the valid result. The historical check is never rewritten.

The evaluation also retained a malformed MCP evidence request, which was rejected before writing. Its corrected request succeeded. Browser test development initially read asynchronous UI state before rendering finished; those test waits were corrected. Neither failure is counted as a successful product check. The first CI run also exposed a packaging-test path comparison on macOS: `/var` and `/private/var` can refer to the same installed interpreter. The assertion now compares executable identity and still checks that the launcher runs the installed module.

A later review of this full development session exposed a scale issue that the small fixtures missed. Its initial prompt contained 2,505 characters, but it then read a 255,713-byte evidence file containing the full capture history. The review timed out after 120.1 seconds, with no provider usage total returned. The implementation now keeps full semantic records in review context, includes mechanical counts, reported failures and unconfirmed calls, and links to the intact receipt archive for individual reads. This removes no receipts or accepted criteria. The comparable rerun keeps the full 247,076-byte receipt archive while reducing its semantic context file to 9,894 bytes, but it also reaches the two-minute deadline without a verdict. Provider token totals are unavailable for both interrupted runs; these character and byte counts do not establish token savings. The deadline now allows five minutes so a large verification task can finish without removing criteria. Full-session runs remain separate from the original 15 fixture attempts.

## Cost and responsiveness

| Independent reviewer | Broken case | Repaired case | Aggregate input tokens, including cache |
| --- | ---: | ---: | ---: |
| Codex | 51.3 s | 57.9 s | 35,596 / 35,889 |
| Claude | 31.0 s | 40.6 s | 17,631 / 40,951 |

Codex's `input_tokens` already includes its cached portion. For Claude, the table adds input, cache-creation and cache-read fields once; nested iteration details are not added again. These counts aggregate multiple provider requests. They are not the maximum size of an individual request. Initial packets contained about 5.4K characters, which is not a token measurement.

Actual full-input notifications from the corrected native Codex host reached **21,578 tokens**. The earlier looping run reached 46,810. The below-10K target remains unmet. The adapter does not control all native instructions, tool definitions and conversation history. No feature or criterion was removed to claim a token pass.

On the two-card local fixture, five board reads had a median of **13.08 ms**, and five revision checks including artifact hashing had a median of **9.97 ms**. A separate automated Chrome run reached the interactive action control in **107 ms** after navigation. These measurements cover small warm local fixtures, not large repositories, cold host startup or an end-to-end model task. Browser measurements came from Chrome 152.0.7977.83 on macOS.

Daily repeated research, human corrections and maintenance effort remain **unmeasured**. The implementation avoids automatic retries and repeated model polling, but that is a design property, not measured daily savings. Review artifacts accumulate locally; retention and large-project hashing cost remain limitations. Runtime dependencies remain zero.

## Browser and packaging verification

Real Chrome extension interaction verifies sprint creation, a writing action with an explained code dependency, decision and review inspection, successful Done, and live artifact drift. The automated HTTP/browser case verifies action and sprint creation, comments, rejected unsupported Done, preserved drafts after concurrent edits, explicit reload, revised-plan saving, sprint filtering and mobile form width. It reports no JavaScript errors or external requests. Existing offline rendering, filters, source navigation, pagination, correction wording, board views and content-security checks also pass.

The final Python suite passes 167 tests. The wheel was installed into a fresh environment outside the checkout. Checks verified its persistent launcher, all three packaged agent instructions, actual MCP handshake/read, offline HTML, live HTTP, authenticated plan write, backup and uninstall with preserved data. The MCPB also starts an actual bundled server in a fresh project. Distribution checks reject private databases, transcripts and unexpected runtime files. This does not prove installation in every desktop host.

## Reproduce the cases

Use a source checkout with `uv sync` and authenticated Codex/Claude CLIs. Each seed requires a new directory; it refuses to overwrite existing work. Live checks consume the configured host account's usage.

```sh
uv run python -m unittest discover -s tests -q
uv run python -m examples.agent_check_case --project results/reproduce-codex --step seed
uv run python -m examples.agent_check_case --project results/reproduce-codex --step check --host codex
uv run project-memory review --project results/reproduce-codex --wait CHECK_ID
uv run python -m examples.agent_check_case --project results/reproduce-codex --step finish
uv run python -m examples.agent_check_case --project results/reproduce-codex --step repair
uv run python -m examples.agent_check_case --project results/reproduce-codex --step check --host codex
uv run project-memory review --project results/reproduce-codex --wait NEW_CHECK_ID
uv run python -m examples.agent_check_case --project results/reproduce-codex --step finish
```

Repeat with a fresh directory and `--host claude`. For intent, use `--step drift` after seeding, then `--step check --role intent`. The result and full immutable history are available with `--step status`. The host hook cases use actual clients:

```sh
uv run python -m examples.agent_hook_case --project results/reproduce-hook
uv run python -m examples.agent_hook_case --project results/reproduce-interrupt --interrupt
uv run python -m examples.claude_agent_hook_case --project results/reproduce-claude-hook
```

After the interrupt case reaches Cancelled, explicitly request recovery with `project-memory review --project results/reproduce-interrupt --episode EPISODE_ID --role recovery --retry`. The host result and `.memory/case.json` supply the IDs.

For the baseline, extract commit `b65b7ae7a5aba18a8fddc9e84f62247e2d089ccf` into a separate directory. Run the current `examples/agent_check_case.py` by absolute path, with that extracted code on `PYTHONPATH` and outside the current checkout, first with `--step seed`, then `--step finish`. Verify that the imported package is beta 4. This isolates the implementation while reusing the same fixture contract. Historical startup and hook-loop failures require their original in-development code; they are retained observations, not expected failures of the final version.

Install Playwright as a development tool and run `node tests/workspace_browser.cjs` and `node tests/browser_check.cjs`; CI installs a pinned version. `MEMORY_PYTHON`, `MEMORY_PLAYWRIGHT` and `MEMORY_CHROMIUM` select local executables. Build with `uv build`, then run `python scripts/check_artifacts.py dist` and `python scripts/installed_smoke.py dist`. The aggregate generator `examples.agent_evidence` reads the named private fixtures from this evaluation; it does not fabricate missing logs.

The existing development database was connected to the new workspace after a backup. Before/after hashes match for all 11 episodes, 54 events, 26 sources and 50 evidence links. Host discovery reports all nine Codex hooks enabled and trusted, and a separate MCP process passes its handshake. Existing live tasks still need a fresh session for their updated tool definitions.

## Unresolved limits

Reviewers can miss a requirement or report a false conflict. The beta has no human override for a failed outcome check. It hashes the whole non-ignored project conservatively, so unrelated code changes can require rechecking completed work. One active review is allowed per project; competing requests return a visible conflict instead of a queue. Native agent execution was verified on macOS, not on Linux or Windows. Large histories, long-running reviews beyond five minutes, retention cost and daily correction rates need further measurement.

The workspace supports planning, following work and requesting reviews. It does not run unattended implementation from a card. It cannot infer every missing decision from prose or prove that an unrecorded success criterion was considered. The public package remains beta 4 until a separate beta 5 release is published.
