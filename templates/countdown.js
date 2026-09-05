(() => {
  const target = document.body.dataset.nextRun;
  const output = document.querySelector("[data-countdown]");
  if (target && output) {
    const tick = () => {
      const seconds = Math.max(0, Math.floor((new Date(target).getTime() - Date.now()) / 1000));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      output.textContent = `${hours}h ${minutes}m ${seconds % 60}s`;
    };
    tick();
    setInterval(tick, 1000);
  }

  for (const time of document.querySelectorAll("[data-local-time]")) {
    const date = new Date(time.dateTime);
    if (!Number.isNaN(date.getTime())) {
      time.textContent = new Intl.DateTimeFormat(undefined, {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
      }).format(date);
    }
  }

  const copyButton = document.querySelector("[data-copy-prompt]");
  const transcript = document.querySelector("[data-prompt-transcript]");
  if (!copyButton || !transcript) return;

  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(transcript.textContent || "");
      copyButton.textContent = "copied";
    } catch {
      copyButton.textContent = "copy failed";
    }
    setTimeout(() => { copyButton.textContent = "copy raw trace"; }, 1500);
  });

  const eventsRoot = document.querySelector("[data-trace-events]");
  const search = document.querySelector("[data-trace-search]");
  const filters = [...document.querySelectorAll("[data-trace-filter]")];
  const summary = document.querySelector("[data-trace-summary]");
  if (!eventsRoot || !search || !filters.length || !summary) return;

  let events;
  try {
    events = JSON.parse(transcript.textContent || "[]");
  } catch {
    summary.textContent = "The redacted trace could not be parsed.";
    return;
  }
  if (!Array.isArray(events)) return;

  const group = event => {
    if (event.kind === "system_prompt") return "system";
    if (event.kind === "pi_input") return "input";
    if (event.kind === "pi_output") return "output";
    if (event.kind === "tool") return "tool";
    return "other";
  };
  const label = event => {
    const role = event.role ? `${event.role} · ` : "";
    if (event.kind === "system_prompt") return `${role}system prompt`;
    if (event.kind === "tool") return `${role}tool · ${event.name || "unknown"}`;
    if (event.kind === "pi_output") return `${role}${event.artifact ? "publication submitted" : "review submitted"}`;
    if (event.kind === "pi_input") {
      try {
        const message = JSON.parse(event.message);
        return `${role}${message.task || "prompt"}`;
      } catch { return `${role}prompt`; }
    }
    return `${role}${event.kind || "event"}`;
  };

  const counts = events.reduce((all, event) => {
    const key = group(event);
    all[key] = (all[key] || 0) + 1;
    return all;
  }, {});
  summary.textContent = `${events.length} events · ${counts.system || 0} prompts · ${counts.input || 0} inputs · ${counts.output || 0} outputs · ${counts.tool || 0} tool calls`;

  for (const [index, event] of events.entries()) {
    const card = document.createElement("details");
    const eventGroup = group(event);
    card.className = "trace-event";
    card.dataset.traceGroup = eventGroup;
    card.open = eventGroup === "output";

    const heading = document.createElement("summary");
    const badge = document.createElement("span");
    badge.className = "trace-kind";
    badge.textContent = eventGroup;
    const title = document.createElement("span");
    title.textContent = `${String(index + 1).padStart(2, "0")} · ${label(event)}`;
    heading.append(badge, title);

    const body = document.createElement("pre");
    body.textContent = JSON.stringify(event, null, 2);
    card.append(heading, body);
    eventsRoot.append(card);
  }

  let selected = "all";
  const applyFilters = () => {
    const query = search.value.trim().toLocaleLowerCase();
    let shown = 0;
    for (const card of eventsRoot.querySelectorAll("[data-trace-group]")) {
      const matchesGroup = selected === "all" || card.dataset.traceGroup === selected;
      const matchesQuery = !query || card.textContent.toLocaleLowerCase().includes(query);
      card.hidden = !matchesGroup || !matchesQuery;
      if (!card.hidden) shown += 1;
    }
    summary.dataset.filtered = `${shown} shown`;
    summary.textContent = `${shown} of ${events.length} events shown`;
  };
  for (const filter of filters) {
    filter.addEventListener("click", () => {
      selected = filter.dataset.traceFilter || "all";
      for (const item of filters) item.setAttribute("aria-pressed", String(item === filter));
      applyFilters();
    });
  }
  search.addEventListener("input", applyFilters);
})();
