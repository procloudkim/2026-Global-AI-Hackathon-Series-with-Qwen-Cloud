"""Dependency-free product console for the Librarian demo."""
from __future__ import annotations


def render_demo_home() -> str:
    """Return the self-contained demo page without invoking a provider."""
    return _DEMO_HTML


_DEMO_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Librarian · Evidence-bound memory</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17231f;
      --muted: #61706a;
      --paper: #f3efe6;
      --surface: #fffdf7;
      --line: #d8d3c7;
      --accent: #c85f2f;
      --accent-dark: #86371c;
      --safe: #227454;
      --warn: #9b5d13;
      --shadow: 0 16px 48px rgba(23, 35, 31, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at 15% 0%, rgba(200, 95, 47, 0.12), transparent 34rem),
        var(--paper);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, textarea, select { font: inherit; }
    a { color: inherit; }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 24px 0 18px;
      border-bottom: 1px solid rgba(23, 35, 31, 0.16);
    }
    .brand { display: flex; align-items: center; gap: 12px; font-weight: 800; letter-spacing: -0.02em; }
    .mark {
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border: 2px solid var(--ink);
      border-radius: 12px 12px 4px 4px;
      background: var(--surface);
      box-shadow: 4px 4px 0 var(--accent);
    }
    .top-links { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 14px; }
    .top-links a { text-decoration: none; padding: 8px 10px; border-radius: 10px; }
    .top-links a:hover { background: rgba(255, 255, 255, 0.65); color: var(--ink); }
    .hero { padding: 64px 0 42px; display: grid; grid-template-columns: 1.45fr 0.55fr; gap: 48px; }
    .eyebrow { margin: 0 0 12px; color: var(--accent-dark); font-size: 13px; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }
    h1 { max-width: 820px; margin: 0; font-family: Georgia, "Times New Roman", serif; font-size: clamp(44px, 7vw, 78px); line-height: 0.98; letter-spacing: -0.045em; }
    .hero-copy { max-width: 690px; margin: 24px 0 0; color: var(--muted); font-size: 19px; line-height: 1.65; }
    .hero-note {
      align-self: end;
      padding: 20px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255, 253, 247, 0.78);
    }
    .hero-note strong { display: block; margin-bottom: 7px; }
    .hero-note p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.55; }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 24px; }
    .badge { padding: 7px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface); font-size: 12px; font-weight: 700; }
    .badge.safe { color: var(--safe); border-color: rgba(34, 116, 84, 0.35); }
    .layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr); gap: 24px; padding-bottom: 72px; }
    .panel { border: 1px solid var(--line); border-radius: 22px; background: var(--surface); box-shadow: var(--shadow); overflow: hidden; }
    .panel-head { display: flex; justify-content: space-between; align-items: start; gap: 16px; padding: 24px 26px 18px; border-bottom: 1px solid var(--line); }
    .panel-head h2 { margin: 0 0 6px; font-size: 22px; letter-spacing: -0.025em; }
    .panel-head p { margin: 0; color: var(--muted); font-size: 14px; line-height: 1.5; }
    .step { padding: 24px 26px; border-bottom: 1px solid var(--line); }
    .step:last-child { border-bottom: 0; }
    .step-title { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
    .step-number { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 9px; background: var(--ink); color: white; font-size: 13px; font-weight: 800; }
    .step-title h3 { margin: 0; font-size: 17px; }
    label { display: block; margin: 14px 0 6px; color: var(--muted); font-size: 12px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      color: var(--ink);
      padding: 11px 12px;
      outline: none;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }
    textarea { min-height: 96px; resize: vertical; line-height: 1.5; }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(200, 95, 47, 0.12); }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-top: 14px; }
    button {
      border: 0;
      border-radius: 11px;
      padding: 10px 14px;
      cursor: pointer;
      font-weight: 800;
      transition: transform 100ms ease, opacity 100ms ease;
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { cursor: wait; opacity: 0.55; transform: none; }
    .primary { background: var(--ink); color: white; }
    .provider { background: var(--accent); color: white; }
    .secondary { border: 1px solid var(--line); background: white; color: var(--ink); }
    .cost { color: var(--warn); font-size: 12px; font-weight: 700; }
    details.advanced { margin-top: 14px; border: 1px dashed var(--line); border-radius: 12px; padding: 10px 12px; }
    details.advanced summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 800; }
    .result { min-height: 520px; }
    .result-body { padding: 22px; }
    .status-line { display: flex; align-items: center; gap: 9px; margin-bottom: 16px; color: var(--muted); font-size: 13px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--line); }
    .dot.ok { background: var(--safe); box-shadow: 0 0 0 4px rgba(34, 116, 84, 0.12); }
    .dot.error { background: #b6312c; box-shadow: 0 0 0 4px rgba(182, 49, 44, 0.12); }
    .answer { margin: 0 0 16px; padding: 18px; border-left: 4px solid var(--accent); border-radius: 4px 14px 14px 4px; background: #fff7ef; font-family: Georgia, "Times New Roman", serif; font-size: 24px; line-height: 1.35; }
    .section-label { margin: 20px 0 9px; color: var(--muted); font-size: 11px; font-weight: 900; letter-spacing: 0.1em; text-transform: uppercase; }
    .cards { display: grid; gap: 9px; }
    .claim-card { padding: 13px 14px; border: 1px solid var(--line); border-radius: 12px; background: white; }
    .claim-top { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
    .claim-value { font-weight: 900; overflow-wrap: anywhere; }
    .status-pill { padding: 4px 7px; border-radius: 999px; background: #ece9df; color: var(--muted); font-size: 10px; font-weight: 900; text-transform: uppercase; }
    .status-pill.active, .status-pill.resolved { background: #dff3e9; color: var(--safe); }
    .status-pill.superseded { background: #eee8e2; color: #7d6252; text-decoration: line-through; }
    .status-pill.disputed, .status-pill.unresolved { background: #fff0d6; color: var(--warn); }
    .claim-meta { margin-top: 7px; color: var(--muted); font-size: 11px; line-height: 1.45; overflow-wrap: anywhere; }
    .meta-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .metric { padding: 10px; border-radius: 11px; background: #f4f1e9; }
    .metric span { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; }
    .metric strong { display: block; margin-top: 4px; font-size: 14px; overflow-wrap: anywhere; }
    .empty { padding: 40px 18px; text-align: center; color: var(--muted); line-height: 1.6; }
    .raw { margin-top: 18px; border-top: 1px solid var(--line); padding-top: 14px; }
    .raw summary { cursor: pointer; color: var(--muted); font-size: 12px; font-weight: 800; }
    pre { max-height: 300px; overflow: auto; padding: 12px; border-radius: 10px; background: #151c19; color: #e6eee9; white-space: pre-wrap; word-break: break-word; font-size: 11px; line-height: 1.45; }
    .utility { display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 880px) {
      .hero, .layout { grid-template-columns: 1fr; }
      .hero { padding-top: 42px; }
      .hero-note { align-self: auto; }
      .result { min-height: 0; }
    }
    @media (max-width: 580px) {
      .shell { width: min(100% - 20px, 1180px); }
      header { align-items: flex-start; }
      .top-links { flex-direction: column; align-items: flex-end; gap: 2px; }
      .hero { gap: 24px; }
      h1 { font-size: 44px; }
      .grid-2, .meta-grid { grid-template-columns: 1fr; }
      .panel-head, .step { padding-left: 18px; padding-right: 18px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand"><span class="mark" aria-hidden="true">L</span><span>Librarian</span></div>
      <nav class="top-links" aria-label="Product links">
        <a href="/docs">API docs</a>
        <a href="/health">Process health</a>
      </nav>
    </header>

    <section class="hero">
      <div>
        <p class="eyebrow">Track 1 · Evidence-bound MemoryAgent</p>
        <h1>Memory that can explain why it changed.</h1>
        <p class="hero-copy">Librarian keeps sources immutable, replaces stale claims without erasing their history, and answers from a bounded context with citations.</p>
        <div class="badges">
          <span class="badge safe">No Qwen call on page load</span>
          <span class="badge">Append-only history</span>
          <span class="badge">Bitemporal query</span>
          <span class="badge">REST + MCP</span>
        </div>
      </div>
      <aside class="hero-note">
        <strong>Cost boundary</strong>
        <p>Loading examples, reading stats, and explaining stored memory are local and provider-free. Ingest and query call the configured Qwen endpoint; lint may call it for conflict judgment. Every such action is marked before you run it.</p>
      </aside>
    </section>

    <main class="layout">
      <section class="panel" aria-labelledby="workflow-title">
        <div class="panel-head">
          <div><h2 id="workflow-title">Guided memory workflow</h2><p>Use the sample buttons to fill the forms. Nothing runs until you choose an action.</p></div>
          <span class="badge" id="run_badge">Preparing unique namespace</span>
        </div>

        <div class="step">
          <div class="step-title"><span class="step-number">1</span><h3>Add a source or correction</h3></div>
          <label for="source_id">Source ID</label>
          <input id="source_id" value="" autocomplete="off" />
          <label for="ingest_text">Source text</label>
          <textarea id="ingest_text">Preparing an isolated example…</textarea>
          <div class="actions">
            <button class="secondary" id="sample-a" type="button">Load original</button>
            <button class="secondary" id="sample-b" type="button">Load correction</button>
            <button class="provider" id="ingest" type="button">Ingest source</button>
            <span class="cost">Calls Qwen · usage returned</span>
          </div>
        </div>

        <div class="step">
          <div class="step-title"><span class="step-number">2</span><h3>Ask for the current or historical fact</h3></div>
          <label for="question">Question</label>
          <textarea id="question">Preparing an isolated question…</textarea>
          <div class="grid-2">
            <div><label for="top_k">Maximum pages</label><select id="top_k"><option>1</option><option>3</option><option selected>5</option><option>10</option></select></div>
            <div><label for="as_of">Compatibility as_of</label><input id="as_of" placeholder="2026-07-15T00:00:00Z" /></div>
          </div>
          <details class="advanced">
            <summary>Advanced bitemporal cutoffs</summary>
            <div class="grid-2">
              <div><label for="valid_at">Valid at</label><input id="valid_at" placeholder="When it was true" /></div>
              <div><label for="known_at">Known at</label><input id="known_at" placeholder="When it became known" /></div>
            </div>
          </details>
          <div class="actions">
            <button class="provider" id="query" type="button">Query memory</button>
            <span class="cost">Calls Qwen · usage returned</span>
          </div>
        </div>

        <div class="step">
          <div class="step-title"><span class="step-number">3</span><h3>Explain the stored lifecycle</h3></div>
          <label for="memory_key">Canonical memory key</label>
          <input id="memory_key" value="" autocomplete="off" />
          <div class="actions">
            <button class="primary" id="explain" type="button">Explain memory</button>
            <span class="badge safe">Free · local ledger read</span>
          </div>
        </div>

        <div class="step">
          <div class="step-title"><span class="step-number">4</span><h3>Inspect and maintain</h3></div>
          <div class="utility">
            <button class="secondary" id="stats" type="button">Read local stats</button>
            <button class="secondary" id="lint" type="button">Audit memory</button>
          </div>
          <p class="cost">Stats is provider-free. Lint may call Qwen when a conflict needs judgment.</p>
        </div>
      </section>

      <aside class="panel result" aria-labelledby="result-title">
        <div class="panel-head"><div><h2 id="result-title">Evidence view</h2><p>Answers, lifecycle states, citations, and raw receipts stay separate.</p></div></div>
        <div class="result-body" id="result" aria-live="polite">
          <div class="status-line"><span class="dot"></span><span>Ready. Load a sample or inspect an existing memory key.</span></div>
          <div class="empty">The current answer will appear here without hiding its supporting claims or transition history.</div>
          <details class="raw"><summary>Raw JSON receipt</summary><pre id="raw">{}</pre></details>
        </div>
      </aside>
    </main>
  </div>

  <script>
    const result = document.getElementById("result");
    const runToken = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().slice(0, 8)
      : (String(Date.now()).slice(-6) + Math.random().toString(16).slice(2, 4));
    const runScope = "judge-demo-" + runToken;
    const samples = {
      a: {
        id: "judge-source-a-" + runToken,
        text: "In " + runScope + ", librarian's quota is 100 units per minute."
      },
      b: {
        id: "judge-source-b-" + runToken,
        text: "This record explicitly replaces judge-source-a-" + runToken + ". In " + runScope + ", librarian's quota is 1000 units per minute."
      }
    };

    function value(id) { return document.getElementById(id).value.trim(); }
    function node(tag, className, text) {
      const el = document.createElement(tag);
      if (className) el.className = className;
      if (text !== undefined) el.textContent = String(text);
      return el;
    }
    function statusPill(status) { return node("span", "status-pill " + String(status || ""), status || "unknown"); }
    function setSample(sample) {
      document.getElementById("source_id").value = sample.id;
      document.getElementById("ingest_text").value = sample.text;
    }
    function initializeRun() {
      document.getElementById("run_badge").textContent = "Unique namespace · " + runToken;
      document.getElementById("question").value = "What is librarian's current quota in " + runScope + "?";
      document.getElementById("memory_key").value = runScope + "::librarian::quota";
      setSample(samples.a);
    }
    function hasStandaloneNumber(valueText, expected) {
      return new RegExp("(^|\\D)" + expected + "(\\D|$)").test(String(valueText || ""));
    }

    async function request(label, url, options = {}) {
      setBusy(true);
      renderStatus("Working: " + label, "");
      try {
        const response = await fetch(url, options);
        const text = await response.text();
        let payload;
        try { payload = JSON.parse(text); } catch { payload = {detail: text || "Non-JSON response"}; }
        if (!response.ok) {
          const detail = typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail || {status: response.status});
          throw new Error(detail);
        }
        renderPayload(label, payload);
        return payload;
      } catch (error) {
        renderError(label, error instanceof Error ? error.message : String(error));
        return null;
      } finally {
        setBusy(false);
      }
    }

    function setBusy(busy) {
      for (const button of document.querySelectorAll("button")) button.disabled = busy;
    }
    function resetResult() {
      result.replaceChildren();
    }
    function renderStatus(message, kind) {
      resetResult();
      const line = node("div", "status-line");
      line.append(node("span", "dot " + kind), node("span", "", message));
      result.append(line);
    }
    function sectionLabel(text) { result.append(node("div", "section-label", text)); }
    function renderError(label, message) {
      renderStatus(label + " failed", "error");
      result.append(node("div", "claim-card", message));
      appendRaw({status: "error", detail: message});
    }
    function appendRaw(payload) {
      const details = node("details", "raw");
      details.append(node("summary", "", "Raw JSON receipt"));
      details.append(node("pre", "", JSON.stringify(payload, null, 2)));
      result.append(details);
    }
    function renderPayload(label, payload) {
      renderStatus(label + " complete", "ok");
      if (Array.isArray(payload.claim_ids)) renderIngest(payload);
      if (payload.answer !== undefined) renderAnswer(payload);
      if (Array.isArray(payload.current_claims)) renderExplanation(payload);
      if (Array.isArray(payload.transitions) && payload.transitions.length) renderDecisions(payload.transitions, "Transitions");
      if (payload.store || payload.ledger) renderStats(payload);
      if (Array.isArray(payload.findings)) renderFindings(payload);
      appendRaw(payload);
    }
    function renderIngest(payload) {
      sectionLabel("Ingest receipt");
      const page = payload.page || {};
      result.append(node("div", "claim-card", "Stored " + (payload.claim_ids || []).length + " claim(s) on " + (page.slug || "unknown page") + "."));
      const metrics = node("div", "meta-grid");
      metrics.append(metric("Route", payload.route_tier || "—"));
      metrics.append(metric("Model", payload.model || "—"));
      metrics.append(metric("Tokens", payload.tokens?.total ?? "—"));
      result.append(metrics);
      if ((payload.claim_ids || []).length) {
        result.append(node("div", "claim-meta", "Claims: " + payload.claim_ids.join(", ")));
      }
    }
    function renderAnswer(payload) {
      result.append(node("div", "answer", payload.answer || "No supported answer."));
      sectionLabel("Verified facts");
      const cards = node("div", "cards");
      for (const fact of payload.facts || []) {
        const card = node("div", "claim-card");
        card.append(node("div", "claim-value", fact.value || fact.normalized_value || "—"));
        card.append(node("div", "claim-meta", fact.key || "Unkeyed fact"));
        cards.append(card);
      }
      if (!cards.children.length) cards.append(node("div", "claim-card", "No facts survived validation."));
      result.append(cards);
      sectionLabel("Answer proof");
      const metrics = node("div", "meta-grid");
      metrics.append(metric("Confidence", payload.confidence ?? "—"));
      metrics.append(metric("Abstained", payload.abstained ? "yes" : "no"));
      metrics.append(metric("Tokens", payload.tokens?.total ?? "—"));
      result.append(metrics);
      const evidence = (payload.evidence_source_ids || []).join(", ") || "No source receipt";
      result.append(node("div", "claim-meta", "Sources: " + evidence));
      const citations = payload.citations || [];
      if (citations.length || (payload.evidence_claim_ids || []).length) {
        sectionLabel("Citations");
        const citationCards = node("div", "cards");
        for (const citation of citations) citationCards.append(node("div", "claim-card", citation));
        if ((payload.evidence_claim_ids || []).length) {
          citationCards.append(node("div", "claim-card", "Claim IDs: " + payload.evidence_claim_ids.join(", ")));
        }
        result.append(citationCards);
      }
      const hasCurrent = (payload.facts || []).some(item => hasStandaloneNumber(item.value || item.normalized_value, 1000));
      if (hasCurrent) {
        const stale = hasStandaloneNumber(payload.answer, 100);
        result.append(node("div", "claim-card", stale
          ? "Answer consistency failed: standalone stale value 100 appears."
          : "Answer consistency passed: 1000 is selected. Lifecycle proof is checked separately below."));
      }
    }
    function renderExplanation(payload) {
      sectionLabel("Resolution · " + payload.resolution_status);
      const cards = node("div", "cards");
      for (const claim of payload.current_claims) cards.append(claimCard(claim));
      if (!cards.children.length) cards.append(node("div", "claim-card", "No claim exists for this canonical key."));
      result.append(cards);
      if ((payload.decisions || []).length) renderDecisions(payload.decisions, "Why it changed");
      renderHistory(payload.history || []);
      renderReplacementProof(payload);
      sectionLabel("Proof boundary");
      const integrity = payload.integrity || {};
      const boundary = "Read-only ledger projection · provider calls: " + (payload.proof_boundary?.provider_calls ?? "unknown")
        + " · integrity: " + (integrity.status || "unknown");
      result.append(node("div", "claim-card", boundary));
      if ((integrity.recovery_required || []).length) {
        result.append(node("div", "claim-card", "Recovery required before interpretation: " + integrity.recovery_required.join(", ")));
      }
    }
    function renderHistory(history) {
      sectionLabel("Revision history");
      const cards = node("div", "cards");
      for (const revision of history) {
        const card = claimCard(revision);
        card.append(node("div", "claim-meta", "Revision " + revision.ordinal + " · " + revision.change_kind + " · " + revision.recorded_at));
        card.append(node("div", "claim-meta", revision.reason || "No recorded reason"));
        cards.append(card);
      }
      if (!cards.children.length) cards.append(node("div", "claim-card", "No revision receipt is available."));
      result.append(cards);
    }
    function renderReplacementProof(payload) {
      if (payload.key !== runScope + "::librarian::quota") return;
      const claims = payload.canonical_claims || [];
      const active = claims.find(claim => claim.status === "active"
        && hasStandaloneNumber(claim.value || claim.normalized_value, 1000)
        && (claim.source_ids || []).includes(samples.b.id));
      const stale = claims.find(claim => claim.status === "superseded"
        && hasStandaloneNumber(claim.value || claim.normalized_value, 100)
        && (claim.source_ids || []).includes(samples.a.id));
      const transition = (payload.decisions || []).find(item => active && stale
        && item.claim_id === stale.claim_id
        && item.trigger_claim_id === active.claim_id
        && (item.evidence_source_ids || []).includes(samples.b.id));
      const passed = payload.resolution_status === "resolved" && active && stale && transition;
      sectionLabel("Replacement proof");
      result.append(node("div", "claim-card", passed
        ? "Passed: source B made 1000 active, superseded source A's 100, and left a linked transition receipt."
        : "Not yet proven: ingest original, then correction, then explain this unique demo key."));
    }
    function claimCard(claim) {
      const card = node("div", "claim-card");
      const top = node("div", "claim-top");
      top.append(node("div", "claim-value", claim.value || claim.normalized_value || "—"), statusPill(claim.status));
      card.append(top);
      card.append(node("div", "claim-meta", "Sources: " + ((claim.source_ids || []).join(", ") || "none")));
      card.append(node("div", "claim-meta", "Claim: " + (claim.claim_id || "unknown")));
      return card;
    }
    function renderDecisions(decisions, title) {
      sectionLabel(title);
      const cards = node("div", "cards");
      for (const decision of decisions) {
        const card = node("div", "claim-card");
        const top = node("div", "claim-top");
        const change = (decision.from_status || "created") + " → " + (decision.to_status || "unknown");
        top.append(node("div", "claim-value", change), statusPill(decision.to_status));
        card.append(top);
        card.append(node("div", "claim-meta", decision.rule || decision.rationale || "Recorded transition"));
        card.append(node("div", "claim-meta", "Evidence: " + ((decision.evidence_source_ids || []).join(", ") || "none")));
        cards.append(card);
      }
      result.append(cards);
    }
    function renderStats(payload) {
      sectionLabel("Local state");
      const metrics = node("div", "meta-grid");
      const store = payload.store || {};
      metrics.append(metric("Wiki pages", store.wiki_pages ?? payload.wiki_pages ?? "—"));
      metrics.append(metric("Projection", store.projection_consistent === false ? "drift" : "consistent"));
      metrics.append(metric("Revisions", store.claim_history?.revision_count ?? payload.claim_history?.revision_count ?? "—"));
      result.append(metrics);
    }
    function renderFindings(payload) {
      sectionLabel("Audit findings");
      const cards = node("div", "cards");
      for (const finding of payload.findings) cards.append(node("div", "claim-card", finding.message || finding.type));
      if (!cards.children.length) cards.append(node("div", "claim-card", "No findings."));
      result.append(cards);
    }
    function metric(label, valueText) {
      const box = node("div", "metric");
      box.append(node("span", "", label), node("strong", "", valueText));
      return box;
    }
    function temporalInputs() {
      const cutoffs = {
        as_of: value("as_of") || null,
        valid_at: value("valid_at") || null,
        known_at: value("known_at") || null
      };
      if (cutoffs.as_of && (cutoffs.valid_at || cutoffs.known_at)) {
        throw new Error("Use as_of by itself, or use valid_at and known_at together.");
      }
      if (Boolean(cutoffs.valid_at) !== Boolean(cutoffs.known_at)) {
        throw new Error("valid_at and known_at must be provided together.");
      }
      return cutoffs;
    }

    document.getElementById("sample-a").addEventListener("click", () => setSample(samples.a));
    document.getElementById("sample-b").addEventListener("click", () => setSample(samples.b));
    document.getElementById("ingest").addEventListener("click", () => request("Ingest", "/ingest", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({source_id: value("source_id"), text: value("ingest_text")})
    }));
    document.getElementById("query").addEventListener("click", () => {
      try {
        const cutoffs = temporalInputs();
        request("Query", "/query", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            question: value("question"), top_k: Number(value("top_k")), ...cutoffs
          })
        });
      } catch (error) {
        renderError("Query", error instanceof Error ? error.message : String(error));
      }
    });
    document.getElementById("explain").addEventListener("click", () => {
      try {
        const cutoffs = temporalInputs();
        let url = "/memory/explain?key=" + encodeURIComponent(value("memory_key"));
        for (const [name, cutoff] of Object.entries(cutoffs)) {
          if (cutoff) url += "&" + name + "=" + encodeURIComponent(cutoff);
        }
        request("Explain", url);
      } catch (error) {
        renderError("Explain", error instanceof Error ? error.message : String(error));
      }
    });
    document.getElementById("stats").addEventListener("click", () => request("Stats", "/stats"));
    document.getElementById("lint").addEventListener("click", () => request("Audit", "/lint", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({apply_archive: false})
    }));
    initializeRun();
  </script>
</body>
</html>
"""
