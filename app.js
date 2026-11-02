"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
};
const append = (parent, ...children) => {
  parent.append(...children);
  return parent;
};
const button = (text, fn, cls) => {
  const n = el("button", text, cls);
  n.type = "button";
  n.addEventListener("click", () => fn(n));
  return n;
};
const link = (text, url) => {
  const n = el("a", text);
  try {
    const parsed = new URL(url, location.href);
    if (["http:", "https:", "codex:"].includes(parsed.protocol))
      n.href = parsed.href;
  } catch {}
  n.target = "_blank";
  n.rel = "noopener noreferrer";
  return n;
};
const input = (type, value = "") => {
  const n = el("input");
  n.type = type;
  n.value = value;
  return n;
};
const field = (name, control, cls) => {
  const n = el("label", name, cls);
  control.setAttribute("aria-label", name);
  n.append(control);
  return n;
};
const details = (name, ...children) => {
  const n = el("details");
  n.append(el("summary", name), ...children);
  return n;
};
const feedback = () => {
  const n = el("p");
  n.setAttribute("role", "status");
  return n;
};
const labels = {
  corrections: "Corrections requested",
  checks: "Checks required",
  decisions: "Awaiting decision",
  resolved: "Resolved",
};
let data,
  db = new URL(location.href).searchParams.get("database"),
  tab = location.hash.slice(1) || "findings";
const findSelection = new Set(),
  reviewSelection = new Set();
const localKey = (name) =>
  "auditorium.brenda." + (data?.workspace.id || "") + "." + name;
const getLocal = (key, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(localKey(key))) ?? fallback;
  } catch {
    return fallback;
  }
};
const setLocal = (key, value) =>
  localStorage.setItem(localKey(key), JSON.stringify(value));
let reviewBatch = null,
  reviewFilter = "attention",
  reviewFinding = null,
  reviewQuery = "",
  findingQuery = "",
  findingFilter = "available";
const queue = (bid) => data.queue.filter((i) => i.batch_id === bid);
const refs = (items) =>
  items.map((i) => ({
    finding_id: i.id,
    check_id: i.check?.id ?? null,
    decision_id: i.decision?.id ?? null,
  }));
const selected = (bid) => queue(bid).filter((i) => reviewSelection.has(i.id));
const date = (value) =>
  value
    ? new Date(
        value.includes("T") ? value : value.replace(" ", "T") + "Z",
      ).toLocaleString()
    : "Not observed";
const needsAttention = (i) =>
  !i.resolved &&
  (i.category !== "decisions" || !i.can_group_accept || i.severity === "high");
function focusCurrent() {
  const heading = document.querySelector(".review-pane h3");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
    heading.scrollIntoView({ block: "start" });
  }
}
function remember() {
  setLocal("position", {
    reviewBatch,
    reviewFilter,
    reviewFinding,
    reviewQuery,
  });
}
function adopt(next) {
  const changed = data?.workspace.id !== next.workspace.id;
  data = next;
  db = data.database;
  const url = new URL(location.href);
  url.searchParams.set("database", db);
  history.replaceState(null, "", url);
  if (changed) {
    const p = getLocal("position", {});
    reviewBatch = p.reviewBatch ?? null;
    reviewFilter = p.reviewFilter || "attention";
    reviewFinding = p.reviewFinding ?? null;
    reviewQuery = p.reviewQuery || "";
    findSelection.clear();
    reviewSelection.clear();
  }
  $("database-path").textContent = db;
  $("version").textContent = data.version;
  $("file-path").value = db;
}
async function read() {
  const response = await fetch(
    "/api/state?" + new URLSearchParams({ database: db }),
    { cache: "no-store" },
  );
  const result = await response.json();
  if (!response.ok) throw Error(result.error);
  adopt(result);
}
async function request(payload) {
  const pinned = db;
  const response = await fetch("/api/action", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Auditorium-Database": encodeURIComponent(db),
    },
    body: JSON.stringify({ database_id: data.workspace.id, ...payload }),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error);
  if (
    result.state &&
    (db === pinned || ["load", "create"].includes(payload.action))
  )
    adopt(result.state);
  return result;
}
async function perform(payload, status, b, after = () => render()) {
  status.className = "";
  status.textContent =
    payload.action === "sync"
      ? "Syncing PRs…"
      : payload.action === "submit"
        ? "Preparing branch and worktree…"
        : payload.action === "handoff"
          ? "Preparing prompt…"
          : "Saving…";
  if (b) b.disabled = true;
  try {
    const result = await request(payload);
    status.textContent = "Saved";
    status.className = "success";
    after(result);
  } catch (e) {
    status.textContent = e.message;
    status.className = "error";
  } finally {
    if (b?.isConnected) b.disabled = false;
  }
}
function switchTab(name) {
  tab = name;
  location.hash = name;
  render();
}
function render() {
  if (!data) return;
  for (const b of document.querySelectorAll("[data-tab]"))
    b.setAttribute("aria-current", b.dataset.tab === tab ? "page" : "false");
  const root = $("content");
  root.replaceChildren();
  (
    ({
      findings: renderFindings,
      batches: renderBatches,
      review: renderReview,
      handoffs: renderHandoffs,
      about: renderAbout,
    })[tab] || renderFindings
  )(root);
}
function findingHeading(i) {
  const n = el("div");
  n.append(el("h3", i.id + " · " + i.title), el("p", i.path, "meta"));
  return n;
}
function selectionBox(checked, fn, label) {
  const n = input("checkbox");
  n.checked = checked;
  n.setAttribute("aria-label", label);
  n.addEventListener("change", () => fn(n.checked));
  return n;
}
function renderFindings(root) {
  root.append(
    el("h2", "Findings"),
    el(
      "p",
      "Choose useful work, then prepare batches. Mixed targets create separate batches automatically.",
      "muted",
    ),
  );
  const bar = el("div", undefined, "toolbar"),
    query = input("text", findingQuery),
    filter = el("select");
  for (const [v, t] of [
    ["available", "Available"],
    ["all", "All findings"],
    ["rejected", "Rejected"],
  ]) {
    const o = el("option", t);
    o.value = v;
    filter.append(o);
  }
  filter.value = findingFilter;
  const message = feedback(),
    create = button(
      `Prepare selected (${findSelection.size})`,
      (b) =>
        perform(
          { action: "new_batch", finding_ids: [...findSelection] },
          message,
          b,
          () => {
            findSelection.clear();
            switchTab("batches");
          },
        ),
      "primary",
    );
  create.disabled = !findSelection.size;
  bar.append(
    field("Filter findings", query, "grow"),
    field("Show", filter),
    create,
  );
  root.append(bar, message);
  const list = el("div");
  root.append(list);
  const paint = () => {
    list.replaceChildren();
    const findings = data.findings.filter(
      (f) =>
        (findingFilter === "all" ||
          (findingFilter === "available" && data.available.includes(f.id)) ||
          (findingFilter === "rejected" && f.status === "rejected")) &&
        [f.id, f.title, f.quotation, f.path]
          .join(" ")
          .toLowerCase()
          .includes(findingQuery.toLowerCase()),
    );
    for (const f of findings) {
      const card = el("article", undefined, "card"),
        head = el("div", undefined, "row"),
        text = findingHeading(f),
        pick = selectionBox(
          findSelection.has(f.id),
          (yes) => {
            yes ? findSelection.add(f.id) : findSelection.delete(f.id);
            create.textContent = `Prepare selected (${findSelection.size})`;
            create.disabled = !findSelection.size;
          },
          "Select " + f.id,
        );
      pick.disabled = !data.available.includes(f.id);
      head.append(pick, text);
      card.append(head, el("p", f.quotation, "quote"));
      if (f.reason) card.append(el("p", f.reason, "muted"));
      const foot = el("div", undefined, "toolbar"),
        target = el("select"),
        status = feedback();
      for (const t of ["", "main", "develop"]) {
        const o = el("option", t || "Choose target");
        o.value = t;
        target.append(o);
      }
      target.value = f.target || "";
      target.disabled = data.batch_findings.some((m) => m.finding_id === f.id);
      target.addEventListener("change", () =>
        perform(
          { action: "target", finding_id: f.id, target: target.value },
          status,
        ),
      );
      foot.append(link(f.page_url, f.page_url), field("PR target", target));
      if (data.available.includes(f.id))
        foot.append(
          button(
            "Reject finding",
            (b) => perform({ action: "reject", finding_id: f.id }, status, b),
            "quiet",
          ),
        );
      if (f.status === "rejected")
        foot.append(
          button(
            "Restore finding",
            (b) => perform({ action: "restore", finding_id: f.id }, status, b),
            "quiet",
          ),
        );
      card.append(foot, status);
      list.append(card);
    }
    if (!findings.length)
      list.append(
        el(
          "p",
          "No findings match. Discover findings to populate this fresh database.",
          "empty",
        ),
      );
  };
  query.addEventListener("input", () => {
    findingQuery = query.value;
    paint();
  });
  filter.addEventListener("change", () => {
    findingFilter = filter.value;
    paint();
  });
  paint();
  root.append(
    details(
      `Coverage · ${data.coverage.length} sources`,
      ...data.coverage.map((c) =>
        append(
          el("div", undefined, "list-item"),
          el("strong", c.path),
          el("p", c.basis, "meta"),
          el("p", c.notes),
        ),
      ),
    ),
  );
}
function renderBatches(root) {
  root.append(el("h2", "Batches in preparation"));
  const batches = data.batches.filter((b) => !b.submitted_at);
  if (!batches.length)
    root.append(
      el("p", "Select available findings to prepare a batch.", "empty"),
    );
  for (const b of batches) {
    const card = el("article", undefined, "card"),
      status = feedback(),
      items = queue(b.id);
    card.append(
      el("h3", `Batch #${b.id} · ${b.target}`),
      el("p", `${items.length} findings · ${b.title}`, "muted"),
    );
    for (const i of items) {
      const notes = el("textarea");
      notes.value = getLocal(`instructions.${b.id}.${i.id}`, i.instructions);
      notes.rows = 2;
      const itemStatus = feedback();
      notes.addEventListener("input", () =>
        setLocal(`instructions.${b.id}.${i.id}`, notes.value),
      );
      notes.addEventListener("blur", () =>
        perform(
          {
            action: "instructions",
            batch_id: b.id,
            finding_id: i.id,
            notes: notes.value,
          },
          itemStatus,
        ),
      );
      card.append(
        details(
          i.id + " · " + i.title,
          el("p", i.quotation, "quote"),
          el("p", i.proposed, "muted"),
          field("Fix instructions for " + i.id, notes),
          button(
            "Remove from draft",
            (btn) =>
              perform(
                { action: "remove_member", batch_id: b.id, finding_id: i.id },
                itemStatus,
                btn,
              ),
            "quiet",
          ),
          itemStatus,
        ),
      );
    }
    card.append(
      el(
        "p",
        "Submission creates a branch and worktree. Copy the handoff and start the agent in a session you choose.",
        "muted",
      ),
      button(
        "Prepare worktree & handoff",
        async (btn) => {
          try {
            for (const i of items)
              await request({
                action: "instructions",
                batch_id: b.id,
                finding_id: i.id,
                notes: getLocal(`instructions.${b.id}.${i.id}`, i.instructions),
              });
          } catch (e) {
            status.textContent = e.message;
            status.className = "error";
            return;
          }
          await perform(
            { action: "submit", batch_id: b.id },
            status,
            btn,
            (result) => {
              render();
              showHandoff(result.handoff_id);
            },
          );
        },
        "primary",
      ),
      status,
    );
    root.append(card);
  }
}
function prSummary(pr) {
  const box = el("div", undefined, "list-item"),
    m = pr.metadata ? JSON.parse(pr.metadata) : null;
  box.append(link(m?.title || pr.url, pr.url));
  if (m) {
    box.append(
      el("span", m.merged_at ? "Merged" : m.draft ? "Draft" : m.state, "badge"),
      el(
        "p",
        `${m.comments + m.review_comments} comments · ${m.commits} commits · ${m.changed_files} files · +${m.additions} −${m.deletions}`,
        "stats",
      ),
      el(
        "p",
        `${m.head_branch} → ${m.base_branch} · ${m.head_sha.slice(0, 7)} · Observed ${date(pr.observed_at)}`,
        "meta",
      ),
    );
  } else
    box.append(
      el(
        "p",
        "PR metadata has not been observed. Sync PRs to load it.",
        "muted",
      ),
    );
  if (pr.sync_error) box.append(el("p", pr.sync_error, "error"));
  return box;
}
function renderReview(root) {
  root.append(
    el("h2", "Review"),
    el(
      "p",
      "Resolve uncertainty, inspect proposed changes, and decide. Agent recommendations and your decisions are separate.",
      "muted",
    ),
  );
  const batches = data.batches.filter((b) => b.submitted_at);
  if (!batches.length) {
    root.append(
      el(
        "p",
        "Prepared work appears here. Existing audits remain in their pinned version.",
        "empty",
      ),
    );
    return;
  }
  if (!reviewBatch) {
    for (const b of batches) {
      const items = queue(b.id),
        done = items.filter((i) => i.resolved).length;
      root.append(
        button(
          "",
          () => {
            reviewBatch = b.id;
            reviewFinding = null;
            remember();
            render();
          },
          "batch-choice",
        ),
      );
      const n = root.lastChild;
      n.append(
        append(
          el("div"),
          el("h3", `Batch #${b.id} · ${b.title}`),
          el("p", `${items.length} findings · ${b.target}`, "meta"),
        ),
        append(
          el("div"),
          el("strong", `${done}/${items.length} resolved`),
          el(
            "p",
            `${items.filter((i) => i.category === "corrections").length} corrections · ${items.filter((i) => i.category === "checks").length} checks · ${items.filter((i) => i.category === "decisions").length} decisions`,
            "meta",
          ),
        ),
      );
    }
    return;
  }
  const batch = batches.find((b) => b.id === reviewBatch);
  if (!batch) {
    reviewBatch = null;
    renderReview(root);
    return;
  }
  const items = queue(batch.id);
  if (
    reviewFilter === "attention" &&
    !items.some(needsAttention) &&
    items.some((i) => !i.resolved)
  )
    reviewFilter = "decisions";
  const prs = data.pull_requests.filter((p) => p.batch_id === batch.id),
    status = feedback();
  root.append(
    button(
      "← All batches",
      () => {
        reviewBatch = null;
        remember();
        render();
      },
      "quiet",
    ),
  );
  const top = el("div", undefined, "card"),
    heading = el("div", undefined, "split-heading");
  heading.append(
    el("h3", `Batch #${batch.id} · ${batch.title}`),
    el(
      "strong",
      `${items.filter((i) => i.resolved).length}/${items.length} resolved`,
    ),
  );
  top.append(heading, ...prs.map(prSummary));
  const controls = el("div", undefined, "actions");
  controls.append(
    button("Sync PRs", (b) =>
      perform({ action: "sync", batch_id: batch.id }, status, b),
    ),
    button("Prepare validation…", () => scopeDialog(batch)),
    button("Prepare corrections…", () =>
      handoff(
        "follow_up",
        batch.id,
        items.filter((i) => i.category === "corrections").map((i) => i.id),
        status,
      ),
    ),
  );
  controls.lastChild.disabled = !items.some(
    (i) => i.category === "corrections",
  );
  const done = items.length && items.every((i) => i.resolved);
  if (done) {
    controls.append(
      button(
        "Authorize publication",
        (b) => perform({ action: "authorize", batch_id: batch.id }, status, b),
        "primary",
      ),
    );
    top.append(
      el(
        "p",
        "Review complete. Authorization syncs PRs once and records your decision. Merge separately on GitHub.",
        "muted",
      ),
    );
  }
  const auth = data.publication_authorizations
    .filter((a) => a.batch_id === batch.id)
    .at(-1);
  if (auth) {
    const current = Object.fromEntries(
      prs
        .filter((p) => p.metadata)
        .map((p) => [p.url, JSON.parse(p.metadata).head_sha]),
    );
    const decisions = items.map((i) => i.decision?.id);
    const valid =
      done &&
      JSON.stringify(current) ===
        JSON.stringify(JSON.parse(auth.observed_heads)) &&
      JSON.stringify(decisions) === JSON.stringify(JSON.parse(auth.decisions));
    top.append(
      el(
        "p",
        valid
          ? "Publication authorized · " + date(auth.created_at)
          : "Previous publication authorization needs renewal.",
        "notice",
      ),
    );
  }
  top.append(controls, status);
  const prUrl = input("url"),
    addStatus = feedback();
  top.append(
    details(
      "Batch tools & worktree",
      el("p", `${batch.branch}\n${batch.worktree}`, "quote meta"),
      field("Add PR URL", prUrl),
      button("Add PR", (b) =>
        perform(
          { action: "add_pr", batch_id: batch.id, url: prUrl.value },
          addStatus,
          b,
        ),
      ),
      addStatus,
    ),
  );
  root.append(top);
  const shell = el("div", undefined, "review-shell"),
    side = el("aside", undefined, "queue"),
    pane = el("section", undefined, "review-pane");
  shell.append(side, pane);
  root.append(shell);
  const filter = el("select");
  for (const [v, t] of [
    ["attention", "Needs attention"],
    ["corrections", labels.corrections],
    ["checks", labels.checks],
    ["decisions", labels.decisions],
    ["resolved", labels.resolved],
    ["all", "All findings"],
  ]) {
    const o = el("option", t);
    o.value = v;
    filter.append(o);
  }
  filter.value = reviewFilter;
  const search = input("text", reviewQuery);
  side.append(field("Review queue", filter), field("Filter findings", search));
  const selectedStatus = feedback(),
    groupButton = button(
      "Accept selected",
      (b) => {
        const chosen = selected(batch.id);
        perform(
          {
            action: "decide",
            batch_id: batch.id,
            entries: refs(chosen),
            disposition: "accept",
          },
          selectedStatus,
          b,
          () => {
            reviewSelection.clear();
            render();
          },
        );
      },
      "primary",
    ),
    selectedPrompt = button("Validate selected…", () =>
      handoff(
        "validation",
        batch.id,
        selected(batch.id).map((i) => i.id),
        selectedStatus,
      ),
    );
  side.append(
    append(el("div", undefined, "actions"), groupButton, selectedPrompt),
    selectedStatus,
    button(
      "Select all passing results",
      () => {
        for (const i of items.filter((i) => i.can_group_accept && !i.resolved))
          reviewSelection.add(i.id);
        paint();
      },
      "quiet",
    ),
    button(
      "Clear selection",
      () => {
        reviewSelection.clear();
        paint();
      },
      "quiet",
    ),
  );
  const listing = el("div");
  side.append(listing);
  const visible = () =>
    items.filter(
      (i) =>
        (reviewFilter === "all" || reviewFilter === "attention"
          ? reviewFilter === "all" || needsAttention(i)
          : reviewFilter === i.category) &&
        [i.id, i.title, i.path, i.decision?.notes]
          .join(" ")
          .toLowerCase()
          .includes(reviewQuery.toLowerCase()),
    );
  const paint = () => {
    listing.replaceChildren();
    const showing = visible();
    if (!showing.some((i) => i.id === reviewFinding))
      reviewFinding = showing[0]?.id || null;
    remember();
    for (const i of showing) {
      const row = el("div", undefined, "queue-item"),
        pick = selectionBox(
          reviewSelection.has(i.id),
          (yes) => {
            yes ? reviewSelection.add(i.id) : reviewSelection.delete(i.id);
            updateGroup();
          },
          "Select " + i.id,
        );
      const b = button(
        "",
        () => {
          reviewFinding = i.id;
          remember();
          paint();
          focusCurrent();
        },
        i.id === reviewFinding ? "active" : "",
      );
      b.append(
        el("strong", i.id + " · " + i.title),
        el(
          "small",
          i.label +
            (getLocal(`draft.${batch.id}.${i.id}`, null) ? " · Draft" : ""),
        ),
      );
      row.append(pick, b);
      listing.append(row);
    }
    if (!showing.length)
      listing.append(el("p", "No findings in this queue.", "empty"));
    pane.replaceChildren();
    const item = items.find((i) => i.id === reviewFinding);
    if (item)
      renderFinding(pane, batch, item, prs, () => {
        const list = visible(),
          idx = list.findIndex((i) => i.id === item.id);
        reviewFinding = list[idx + 1]?.id ?? list[idx - 1]?.id ?? null;
        remember();
      });
    updateGroup();
  };
  const updateGroup = () => {
    const chosen = selected(batch.id);
    groupButton.textContent = `Accept selected (${chosen.length})`;
    groupButton.disabled =
      !chosen.length || chosen.some((i) => !i.can_group_accept);
    selectedPrompt.disabled = !chosen.length;
  };
  filter.addEventListener("change", () => {
    reviewFilter = filter.value;
    remember();
    paint();
  });
  search.addEventListener("input", () => {
    reviewQuery = search.value;
    remember();
    paint();
  });
  paint();
}
function renderFinding(pane, batch, i, prs, next) {
  const card = el("article", undefined, "card");
  card.append(
    findingHeading(i),
    el("span", i.label, "badge " + (i.resolved ? "good" : "warn")),
    el("p", i.reason || i.title),
    link(i.page_url, i.page_url),
  );
  const comparison = el("div", undefined, "two");
  comparison.append(
    append(
      el("div", undefined, "passage"),
      el("h4", "Original content"),
      el("p", i.quotation, "quote"),
    ),
    append(
      el("div", undefined, "passage"),
      el(
        "h4",
        i.check
          ? "Proposed passage · " + i.check.revision.slice(0, 7)
          : "Expected change",
      ),
      el(
        "p",
        i.check
          ? i.check.proposed || "Passage removed; inspect the PR diff."
          : i.instructions || i.proposed,
        "quote",
      ),
    ),
  );
  card.append(comparison);
  if (i.instructions)
    card.append(
      details("Scope & instructions", el("p", i.instructions, "quote")),
    );
  if (i.check) {
    const c = i.check,
      check = el("section", undefined, "card"),
      content = el("div", undefined, "check-content"),
      notes = el("div");
    notes.append(
      el("h4", "Check & evidence"),
      el(
        "strong",
        `${c.reporter} recommends ${c.recommendation.replaceAll("_", " ")}${c.outcome === "blocked" ? " · Check blocked" : ""}`,
      ),
      el("p", c.summary, "quote"),
      el("p", i.freshness_note, i.freshness === "current" ? "meta" : "notice"),
    );
    if (i.decision && ["needs_work", "revert"].includes(i.decision.action))
      notes.append(
        el(
          "p",
          i.decision.notes === c.summary
            ? "Your decision: " + i.decision.action.replaceAll("_", " ")
            : "Your request: " + i.decision.notes,
          "notice",
        ),
      );
    if (i.freshness === "unknown") {
      const syncStatus = feedback();
      notes.append(
        button("Sync PRs", (b) =>
          perform({ action: "sync", batch_id: batch.id }, syncStatus, b, () => {
            render();
            focusCurrent();
          }),
        ),
        syncStatus,
      );
    }
    const pr = prs.find((p) => p.id === c.pr_id);
    if (pr)
      notes.append(
        append(
          el("p", undefined, "meta"),
          link(
            "Commit " + c.revision.slice(0, 7),
            pr.url.replace(/\/pull\/\d+$/, "/commit/" + c.revision),
          ),
          el("span", " · " + date(c.created_at)),
        ),
      );
    if (c.target_url && c.target_url !== i.page_url)
      notes.append(
        details("Checked page reference", link(c.target_url, c.target_url)),
      );
    const images = data.evidence.filter((e) => e.check_id === c.id),
      gallery = el("div", undefined, "evidence");
    for (const img of images) {
      const url =
          "/api/image?" + new URLSearchParams({ database: db, id: img.id }),
        figure = el("figure"),
        a = link("", url),
        image = el("img");
      image.src = url;
      image.alt = img.label;
      image.loading = "lazy";
      a.append(image);
      figure.append(
        a,
        el("figcaption", img.label + " · Scroll preview or open full image ↗"),
      );
      gallery.append(figure);
    }
    if (!images.length) {
      notes.append(el("p", "No screenshot attached.", "meta"));
      content.className = "";
    }
    content.append(notes);
    if (images.length) content.append(gallery);
    check.append(content);
    card.append(check);
  } else
    card.append(
      el(
        "p",
        "No check recorded. Prepare validation, record your own check, or make an explained disposition.",
        "notice",
      ),
    );
  const decision = el("section", undefined, "decision"),
    status = feedback(),
    actions = el("div", undefined, "actions"),
    draftKey = `draft.${batch.id}.${i.id}`,
    draft = getLocal(draftKey, null),
    editor = el("div");
  decision.append(el("h4", "Your decision"), actions, editor, status);
  const submit = (disposition, notes, b) =>
    perform(
      {
        action: "decide",
        batch_id: batch.id,
        entries: refs([i]),
        disposition,
        notes,
      },
      status,
      b,
      () => {
        localStorage.removeItem(localKey(draftKey));
        next();
        render();
        focusCurrent();
      },
    );
  function form(disposition, focus = true) {
    const saved = getLocal(draftKey, null),
      box = el("form", undefined, "decision-form"),
      notes = el("textarea");
    notes.rows = 3;
    notes.value =
      saved?.disposition === disposition
        ? saved.notes
        : disposition === "needs_work"
          ? i.decision?.notes ||
            (["needs_work", "blocked"].includes(i.check?.outcome) ||
            i.check?.recommendation === "needs_work"
              ? i.check.summary
              : "")
          : i.decision?.notes || "";
    const label =
      disposition === "needs_work"
        ? "What needs to change?"
        : disposition === "accept"
          ? "Why do you accept this result despite the evidence gap?"
          : "Reason or instructions";
    const saveDraft = () => {
      setLocal(draftKey, { disposition, notes: notes.value });
    };
    notes.addEventListener("input", saveDraft);
    saveDraft();
    box.append(
      field(label, notes),
      el(
        "p",
        "Draft saved in this browser. You can navigate away and return.",
        "meta",
      ),
    );
    const save = button(
      disposition === "needs_work"
        ? "Submit needs work & next"
        : "Submit decision & next",
      (b) => submit(disposition, notes.value, b),
      "primary",
    );
    box.append(
      append(
        el("div", undefined, "actions"),
        save,
        button("Discard draft", () => {
          localStorage.removeItem(localKey(draftKey));
          editor.replaceChildren();
        }),
      ),
    );
    box.addEventListener("submit", (e) => {
      e.preventDefault();
      save.click();
    });
    editor.replaceChildren(box);
    if (focus) notes.focus();
  }
  actions.append(
    button(
      "Accept & next",
      (b) => {
        if (i.can_accept) submit("accept", "", b);
        else form("accept");
      },
      i.can_accept ? "primary" : "",
    ),
    button("Needs work…", () => form("needs_work")),
    button("Revert…", () => form("revert")),
    button(
      "Re-review…",
      () => handoff("validation", batch.id, [i.id], status),
      "quiet",
    ),
  );
  const more = el("select");
  for (const [v, t] of [
    ["", "Other disposition…"],
    ["deferred", "Defer to another batch"],
    ["withdrawn", "Withdraw finding"],
    ["resolved_by_revert", "Resolved by revert"],
    ["clear", "Clear decision"],
  ]) {
    const o = el("option", t);
    o.value = v;
    more.append(o);
  }
  more.addEventListener("change", () => {
    if (more.value === "clear") submit("clear", "", more);
    else if (more.value) form(more.value);
  });
  actions.append(more);
  if (draft) form(draft.disposition, false);
  card.append(decision);
  const patches = prs
    .map((p) => ({ pr: p, patch: JSON.parse(p.patches || "{}")[i.path] }))
    .filter((p) => p.patch);
  card.append(
    details(
      "PR diff · " + i.path,
      ...patches.map((p) =>
        append(
          el("div"),
          link(p.pr.url, p.pr.url + "/files"),
          el("pre", p.patch, "diff"),
        ),
      ),
    ),
  );
  const history = data.checks.filter(
    (c) =>
      c.batch_id === batch.id && c.finding_id === i.id && c.id !== i.check?.id,
  );
  if (history.length)
    card.append(
      details(
        `${history.length} earlier checks`,
        ...history.map((c) => el("p", c.outcome + " · " + c.summary, "quote")),
      ),
    );
  card.append(
    ownCheck(batch, i, prs, status),
    button(
      "Next finding →",
      () => {
        next();
        render();
        focusCurrent();
      },
      "quiet",
    ),
  );
  pane.append(card);
}
function ownCheck(batch, i, prs) {
  const key = `own.${batch.id}.${i.id}`,
    draft = getLocal(key, null),
    form = el("form"),
    status = feedback(),
    pr = el("select");
  for (const p of prs) {
    const o = el("option", "#" + p.url.split("/").pop());
    o.value = p.id;
    pr.append(o);
  }
  if (draft) pr.value = draft.pr;
  const revision = input(
      "text",
      draft?.revision ??
        JSON.parse(prs.find((p) => p.id === Number(pr.value))?.metadata || "{}")
          .head_sha ??
        i.check?.revision ??
        "",
    ),
    outcome = el("select");
  for (const t of ["pass", "needs_work", "blocked"]) {
    const o = el("option", t);
    o.value = t;
    outcome.append(o);
  }
  if (draft) outcome.value = draft.outcome;
  const summary = el("textarea"),
    proposed = el("textarea"),
    paths = el("textarea"),
    target = input("url", draft?.target ?? i.page_url);
  summary.value = draft?.summary ?? "";
  proposed.value = draft?.proposed ?? i.check?.proposed ?? i.proposed;
  paths.value =
    draft?.paths ??
    (Object.keys(JSON.parse(i.check?.source_blobs || "{}")).join("\n") ||
      i.path);
  const accept = selectionBox(
    draft?.accept ?? true,
    () => {},
    "Accept this passing check",
  );
  const remember = () =>
    setLocal(key, {
      pr: pr.value,
      revision: revision.value,
      outcome: outcome.value,
      summary: summary.value,
      proposed: proposed.value,
      paths: paths.value,
      target: target.value,
      accept: accept.checked,
    });
  pr.addEventListener("change", () => {
    revision.value =
      JSON.parse(prs.find((p) => p.id === Number(pr.value))?.metadata || "{}")
        .head_sha || "";
    remember();
  });
  form.addEventListener("input", remember);
  form.addEventListener("change", remember);
  form.append(
    field("PR", pr),
    field("Commit checked (full SHA)", revision),
    field("Outcome", outcome),
    field("Supporting source paths · one per line", paths),
    field("Proposed passage", proposed),
    field("Result or substantive concern", summary),
    field("Page checked", target),
    append(el("label", "Accept this passing check", "check"), accept),
  );
  form.append(
    button("Save my check", (b) => {
      const check = {
        finding_id: i.id,
        pr_id: Number(pr.value),
        revision: revision.value,
        source_paths: paths.value.split("\n").filter(Boolean),
        proposed: proposed.value,
        outcome: outcome.value,
        recommendation: outcome.value === "pass" ? "accept" : "needs_work",
        summary: summary.value,
        target_url: target.value,
        screenshots: [],
      };
      perform(
        {
          action: "own_check",
          batch_id: batch.id,
          check,
          accept: accept.checked && outcome.value === "pass",
          notes: summary.value,
        },
        status,
        b,
        () => {
          localStorage.removeItem(localKey(key));
          render();
        },
      );
    }),
    button(
      "Discard my check draft",
      () => {
        localStorage.removeItem(localKey(key));
        render();
      },
      "quiet",
    ),
    el(
      "p",
      "Draft saved in this browser. Saving a passing check can record acceptance in the same step.",
      "meta",
    ),
    status,
  );
  const d = details("Record my own check", form);
  d.open = !!draft;
  return d;
}
function scopeDialog(batch) {
  const dialog = el("dialog"),
    status = feedback(),
    items = queue(batch.id),
    scope = el("select");
  for (const [v, t] of [
    ["checks", "Checks required"],
    ["remaining", "All unresolved findings"],
    ["selected", "Selected findings"],
    ["all", "Whole batch"],
  ]) {
    const o = el("option", t);
    o.value = v;
    scope.append(o);
  }
  const summary = el("p", undefined, "prompt-scope");
  const ids = () =>
    items
      .filter(
        (i) =>
          scope.value === "all" ||
          (scope.value === "remaining" && !i.resolved) ||
          (scope.value === "checks" &&
            i.category === "checks" &&
            i.freshness !== "unknown") ||
          (scope.value === "selected" && reviewSelection.has(i.id)),
      )
      .map((i) => i.id);
  const update = () =>
    (summary.textContent = `${ids().length} findings: ${ids().join(", ") || "none"}${scope.value === "checks" && items.some((i) => i.freshness === "unknown") ? " · Sync PRs before preparing checks for findings with older metadata." : ""}`);
  scope.addEventListener("change", update);
  update();
  dialog.append(
    el("h2", "Prepare validation"),
    field("Scope", scope),
    summary,
    el(
      "p",
      "This prepares a copyable prompt. Start the agent in a session you choose.",
      "muted",
    ),
    button(
      "Prepare prompt",
      async (b) => {
        if (!ids().length) {
          status.textContent = "Choose a nonempty scope.";
          return;
        }
        await perform(
          {
            action: "handoff",
            kind: "validation",
            batch_id: batch.id,
            finding_ids: ids(),
          },
          status,
          b,
          (r) => {
            dialog.close();
            render();
            showHandoff(r.handoff_id);
          },
        );
      },
      "primary",
    ),
    button("Cancel", () => dialog.close()),
    status,
  );
  openDialog(dialog);
}
async function handoff(kind, bid, ids, status) {
  await perform(
    { action: "handoff", kind, batch_id: bid, finding_ids: ids },
    status,
    null,
    (r) => {
      render();
      showHandoff(r.handoff_id);
    },
  );
}
function openDialog(dialog) {
  $("dialogs").append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
}
function showHandoff(id) {
  const h = data.handoffs.find((h) => h.id === id);
  if (!h) return;
  const dialog = el("dialog"),
    area = el("textarea", undefined, "prompt"),
    status = feedback();
  area.value = h.prompt;
  area.readOnly = true;
  area.setAttribute("aria-label", "Handoff prompt");
  dialog.append(
    append(
      el("div", undefined, "split-heading"),
      el("h2", `Handoff #${h.id} · ${h.kind.replaceAll("_", " ")}`),
      button("Close", () => dialog.close()),
    ),
    el(
      "p",
      "Handoff prepared. Copy the prompt and start the agent in a session you choose. No agent starts here.",
      "notice",
    ),
    el("p", "SQLite: " + db, "meta"),
    area,
    button(
      "Copy prompt",
      async () => {
        try {
          await navigator.clipboard.writeText(area.value);
          status.textContent =
            "Copied · start the agent in your chosen session";
        } catch {
          area.focus();
          area.select();
          status.textContent = "Select and copy the prompt.";
        }
      },
      "primary",
    ),
    status,
  );
  openDialog(dialog);
}
function renderHandoffs(root) {
  root.append(
    el("h2", "Handoffs"),
    el(
      "p",
      "Prepared prompts and received results. Execution stays in the session you choose.",
      "muted",
    ),
  );
  if (!data.handoffs.length)
    root.append(el("p", "No handoffs prepared.", "empty"));
  for (const h of [...data.handoffs].reverse()) {
    const members = data.handoff_findings.filter((m) => m.handoff_id === h.id),
      received = new Set(
        data.checks
          .filter((c) => c.handoff_id === h.id)
          .map((c) => c.finding_id),
      ).size,
      card = el("article", undefined, "card"),
      status = feedback();
    card.append(
      el(
        "h3",
        `#${h.id} · ${h.kind.replaceAll("_", " ")}${h.batch_id ? " · Batch #" + h.batch_id : ""}`,
      ),
      el(
        "p",
        h.kind === "validation"
          ? `${received}/${members.length} findings reported`
          : h.received_at
            ? "Results received"
            : "Handoff prepared",
        "badge",
      ),
      el("p", date(h.created_at), "meta"),
    );
    if (!h.dismissed_at)
      card.append(
        button("Copy / view prompt", () => showHandoff(h.id)),
        button(
          "Dismiss card",
          (b) =>
            perform(
              {
                action: "handoff_details",
                handoff_id: h.id,
                session_url: h.session_url,
                dismiss: true,
              },
              status,
              b,
            ),
          "quiet",
        ),
      );
    else
      card.append(
        details(
          "Handoff receipt",
          button("View prompt", () => showHandoff(h.id)),
        ),
      );
    if (h.session_url)
      card.append(link("Open operator-provided session ↗", h.session_url));
    const session = input("url", h.session_url || "");
    card.append(
      details(
        "Session reference (optional)",
        field("Session URL", session),
        button("Save session link", (b) =>
          perform(
            {
              action: "handoff_details",
              handoff_id: h.id,
              session_url: session.value,
              dismiss: !!h.dismissed_at,
            },
            status,
            b,
          ),
        ),
      ),
      status,
    );
    root.append(card);
  }
}
function renderAbout(root) {
  root.append(
    el("h2", "Data model"),
    el(
      "p",
      `Actual SQLite schema · ${data.version} · ${data.workspace.id}`,
      "muted",
    ),
  );
  for (const s of data.schema)
    root.append(details(s.type + " · " + s.name, el("pre", s.sql, "diff")));
  root.append(
    el("h2", "Glossary & terms"),
    el(
      "p",
      "Handoff prepared: a prompt exists. Results received: reports arrived. Neither asserts that an agent is running.",
    ),
    el(
      "p",
      "Source scope current: declared documentation blobs match the observed PR head. This does not verify runtime behavior or deployment.",
    ),
    el(
      "p",
      "Resolved: the operator accepted, deferred, withdrew, or confirmed resolution by revert. Publication authorization records a separate approval; merging happens on GitHub.",
    ),
  );
}
$("discover").addEventListener("click", () => {
  const dialog = el("dialog"),
    focus = el("textarea"),
    status = feedback();
  dialog.append(
    el("h2", "Discover findings"),
    field("Research focus (optional)", focus),
    el(
      "p",
      "Discovery populates this fresh database. Existing audits remain in their pinned application.",
      "muted",
    ),
    button(
      "Prepare discovery prompt",
      (b) =>
        perform(
          { action: "handoff", kind: "discovery", focus: focus.value },
          status,
          b,
          (r) => {
            dialog.close();
            render();
            showHandoff(r.handoff_id);
          },
        ),
      "primary",
    ),
    button("Cancel", () => dialog.close()),
    status,
  );
  openDialog(dialog);
});
$("reload").addEventListener("click", async () => {
  try {
    await read();
    render();
    $("global-feedback").textContent =
      "SQLite reloaded. GitHub observations change through Sync PRs.";
  } catch (e) {
    $("global-feedback").textContent = e.message;
  }
});
$("files").addEventListener("click", () => {
  $("file-controls").hidden = !$("file-controls").hidden;
});
$("browse").addEventListener("click", () =>
  perform(
    { action: "browse", operation: "load" },
    $("file-feedback"),
    $("browse"),
    (r) => {
      if (r.path) $("file-path").value = r.path;
    },
  ),
);
$("load").addEventListener("click", () =>
  perform(
    { action: "load", path: $("file-path").value },
    $("file-feedback"),
    $("load"),
    () => {
      $("file-controls").hidden = true;
      render();
    },
  ),
);
$("new").addEventListener("click", async () => {
  try {
    const choice = await request({ action: "browse", operation: "create" });
    if (choice.path)
      await perform(
        { action: "create", path: choice.path },
        $("file-feedback"),
        $("new"),
        () => {
          $("file-controls").hidden = true;
          render();
        },
      );
  } catch (e) {
    $("file-feedback").textContent = e.message;
  }
});
for (const b of document.querySelectorAll("[data-tab]"))
  b.addEventListener("click", () => switchTab(b.dataset.tab));
window.addEventListener("hashchange", () => {
  tab = location.hash.slice(1) || "findings";
  render();
});
(async () => {
  try {
    if (!db) db = (await (await fetch("/api/bootstrap")).json()).database;
    await read();
    render();
  } catch (e) {
    $("global-feedback").textContent = e.message;
  }
})();

setInterval(async () => {
  if (!data || document.hidden) return;
  const pinned = db;
  try {
    const response = await fetch(
      "/api/state?" + new URLSearchParams({ database: pinned }),
      { cache: "no-store" },
    );
    if (!response.ok) return;
    const next = await response.json();
    if (pinned !== db || JSON.stringify(next) === JSON.stringify(data)) return;
    if (
      document.activeElement?.matches("input,textarea,select") ||
      document.querySelector("dialog[open]")
    ) {
      $("reload").textContent = "Reload · new results";
      return;
    }
    const y = scrollY;
    adopt(next);
    render();
    window.scrollTo(0, y);
    $("reload").textContent = "Reload";
  } catch {}
}, 5000);
