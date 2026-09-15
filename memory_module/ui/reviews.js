async function showChecks(parent, work) {
  const section = document.createElement("details");
  section.className = "agent-checks";
  section.open = expandedCheckPanels.has(work.id);
  section.addEventListener("toggle", () => {
    if (section.open) expandedCheckPanels.add(work.id);
    else expandedCheckPanels.delete(work.id);
  });
  const h = document.createElement("summary");
  h.textContent = "Agent checks";
  section.append(h);
  parent.append(section);
  if (!reviewHost) {
    const p = document.createElement("p");
    p.textContent =
      "Agent checks are not configured. Connect Codex or Claude through project setup.";
    section.append(p);
    return;
  }
  const controls = document.createElement("div");
  controls.className = "editor-actions";
  const role = document.createElement("select");
  role.setAttribute("aria-label", "Check type");
  for (const [value, title] of [
    ["outcome", "Check the result"],
    ["intent", "Check intent and scope"],
    ["recovery", "Inspect recovery evidence"],
  ]) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = title;
    role.append(o);
  }
  const start = document.createElement("button");
  start.textContent = "Run check";
  const timeLimit = document.createElement("select");
  timeLimit.setAttribute("aria-label", "Review time limit");
  for (const [seconds, label] of [
    [300, "Up to 5 minutes"],
    [900, "Up to 15 minutes"],
  ]) {
    const option = document.createElement("option");
    option.value = seconds;
    option.textContent = label;
    timeLimit.append(option);
  }
  controls.append(role, timeLimit, start);
  section.append(controls);
  let requestKey = crypto.randomUUID();
  start.addEventListener("click", async () => {
    start.disabled = true;
    try {
      await post("reviews", {
        episode_id: work.id,
        role: role.value,
        max_seconds: Number(timeLimit.value),
        request_key: requestKey,
        retry: true,
      });
      healthTag = null;
      await poll();
    } catch (error) {
      const p = document.createElement("p");
      p.className = "form-error";
      p.textContent = error.message;
      section.append(p);
    } finally {
      start.disabled = false;
    }
  });
  const result = await api("reviews", { episode: work.id, limit: 10 });
  h.textContent =
    "Agent checks · " +
    (result.current
      ? labels(result.current.state)
      : result.runs.length
        ? labels(result.runs[0].state)
        : "Not run");
  if (!result.runs.length) {
    const p = document.createElement("p");
    p.textContent = "No agent check has run for this action.";
    section.append(p);
  }
  const renderRuns = (result) => {
    for (const run of result.runs) {
      const article = document.createElement("article");
      article.className = "check-result";
      article.dataset.state = run.state;
      const status = document.createElement("span");
      status.className = "check-status";
      status.textContent =
        labels(run.role) +
        " · " +
        labels(
          result.current?.id === run.id ? result.current.state : run.state,
        ) +
        " · " +
        run.host;
      article.append(status);
      if (result.current?.id === run.id && result.current.state === "stale") {
        const previous = document.createElement("p");
        previous.textContent = "Recorded check: " + labels(run.state) + ". Current evidence has changed.";
        article.append(previous);
      }
      const p = document.createElement("p");
      p.textContent =
        run.error ||
        run.report?.summary ||
        (["queued", "running", "cancelling"].includes(run.state)
          ? "The agent is checking the recorded evidence."
          : "No report was recorded for this check.");
      article.append(p);
      if (["queued", "running", "cancelling"].includes(run.state)) {
        start.disabled = true;
        const cancel = document.createElement("button");
        cancel.textContent = "Cancel check";
        cancel.disabled = run.state === "cancelling";
        cancel.addEventListener("click", async () => {
          cancel.disabled = true;
          try {
            await post("reviews", { cancel: run.id });
            healthTag = null;
            await poll();
          } catch (error) {
            p.textContent = error.message;
            cancel.disabled = false;
          }
        });
        article.append(cancel);
      }
      if (run.report) {
        const details = document.createElement("details");
        details.open = expandedChecks.has(run.id);
        details.addEventListener("toggle", () => {
          if (details.open) expandedChecks.add(run.id);
          else expandedChecks.delete(run.id);
        });
        const summary = document.createElement("summary");
        summary.textContent = ["pass", "changes_required", "uncertain"].includes(run.state)
          ? "Checks and evidence" : "Retained report · This run did not approve the work";
        details.append(summary);
        const list = document.createElement("ol");
        for (const check of run.report.checks) {
          const li = document.createElement("li");
          li.textContent =
            (run.conditions?.[check.criterion] || check.criterion) + " — " + check.result + ". " + check.evidence;
          list.append(li);
        }
        details.append(list);
        if (run.report.constraint_checks?.length) {
          const heading = document.createElement("h4");
          heading.textContent = "Project constraints";
          details.append(heading);
          const mapping = document.createElement("ul");
          for (const check of run.report.constraint_checks) {
            const li = document.createElement("li");
            li.textContent = `${run.conditions?.[check.constraint] || check.constraint} · ${labels(check.applicability)} · ${labels(check.result)}. ${check.reason} ${check.evidence}`;
            mapping.append(li);
          }
          details.append(mapping);
        }
        for (const finding of run.report.findings) {
          const p = document.createElement("p");
          p.textContent = finding;
          details.append(p);
        }
        for (const proposal of run.report.lesson_proposals) {
          const p = document.createElement("p");
          p.textContent =
            "Proposed lesson: " +
            proposal.proposal +
            " Basis: " +
            proposal.basis +
            " Conditions: " +
            proposal.conditions +
            " Exceptions: " +
            proposal.exceptions;
          details.append(p);
        }
        article.append(details);
      }
      if (run.metrics) {
        const metrics = document.createElement("p");
        metrics.className = "check-metrics";
        metrics.textContent =
          ((run.metrics.duration_ms || 0) / 1000).toFixed(1) +
          " seconds · " +
          run.metrics.input_characters +
          " input characters · Provider usage " +
          (run.metrics.provider_usage ? "recorded" : "unavailable");
        article.append(metrics);
        if (run.metrics.phase) {
          const activity = document.createElement("p");
          activity.className = "check-metrics";
          const m = run.metrics;
          activity.textContent = `${labels(m.phase)} · ${m.completed_inspections} inspections completed · ${m.failed_inspections} inspection errors · ${m.output_bytes} output bytes · ${m.host_error_events} host errors`;
          if (m.last_activity_at) activity.textContent += ` · Last output ${new Date(m.last_activity_at).toLocaleTimeString()}`;
          else activity.textContent += " · No child output observed";
          if (["running", "cancelling"].includes(run.state)) activity.textContent += ` · ${m.remaining_seconds}s remaining`;
          else if (m.termination_reason) activity.textContent += ` · ${labels(m.termination_reason)}`;
          article.append(activity);
        }
      }
      section.append(article);
    }
  };
  renderRuns(result);
  if (result.more) {
    let offset = result.next_offset;
    const more = document.createElement("button");
    more.textContent = "Load earlier checks";
    section.append(more);
    more.addEventListener("click", async () => {
      more.disabled = true;
      try {
        const page = await api("reviews", {
          episode: work.id,
          limit: 10,
          offset,
        });
        renderRuns(page);
        offset = page.next_offset;
        more.hidden = !page.more;
        section.append(more);
      } catch (error) {
        const p = document.createElement("p");
        p.textContent = error.message;
        section.append(p);
      } finally {
        more.disabled = false;
      }
    });
  }
}
