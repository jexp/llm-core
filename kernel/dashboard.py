"""Single-file live dashboard served at GET /ui (no build tooling, vanilla JS).

The page itself is static; all data calls require a principal key entered in the page —
same authn as every other client.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>llm-core — live kernel view</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; background: #0d1117; color: #c9d1d9; }
  header { display: flex; gap: 16px; align-items: center; padding: 12px 20px; background: #161b22; border-bottom: 1px solid #30363d; }
  h1 { font-size: 15px; margin: 0; }
  #badge { font-size: 12px; padding: 3px 8px; border-radius: 10px; background: #238636; }
  #badge.bad { background: #da3633; }
  main { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; padding: 16px 20px; }
  section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #8b949e; margin: 0 0 8px; }
  input, textarea, select { width: 100%; box-sizing: border-box; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 6px 8px; font: inherit; }
  textarea { min-height: 110px; }
  button { background: #238636; color: #fff; border: 0; border-radius: 6px; padding: 6px 14px; font: inherit; cursor: pointer; }
  #feed { max-height: 70vh; overflow-y: auto; font-size: 12px; }
  .row { display: flex; gap: 10px; padding: 3px 4px; border-bottom: 1px solid #21262d; white-space: nowrap; }
  .seq { color: #8b949e; width: 40px; text-align: right; }
  .ts { color: #8b949e; }
  .ev { width: 90px; font-weight: 600; }
  .RECEIVED { color: #58a6ff; } .DECIDED { color: #d2a8ff; } .DECISION { color: #d2a8ff; }
  .PLAN { color: #ffa657; } .OP_RESULT { color: #7ee787; } .COMPLETED { color: #7ee787; }
  .ESCALATED { color: #ff7b72; }
  .who { color: #79c0ff; width: 110px; }
  .d { overflow: hidden; text-overflow: ellipsis; color: #8b949e; }
  #resp { margin-top: 8px; white-space: pre-wrap; font-size: 11px; max-height: 300px; overflow: auto; }
  label { font-size: 11px; color: #8b949e; display: block; margin: 8px 0 3px; }
</style>
</head>
<body>
<header>
  <h1>llm-core kernel — live view</h1>
  <span id="badge">…</span>
  <span style="flex:1"></span>
  <input id="key" style="width:220px" placeholder="principal key" value="dev-auditor-key">
</header>
<main>
  <section>
    <h2>audit chain (live)</h2>
    <div id="feed"></div>
  </section>
  <section>
    <h2>send a request</h2>
    <label>mode</label>
    <select id="mode">
      <option value="nl">natural language (→ router/T1)</option>
      <option value="t0">structured op (→ T0)</option>
    </select>
    <div id="nlbox"><label>text</label>
      <textarea id="text">what do you have under $20 for camping?</textarea></div>
    <div id="t0box" style="display:none"><label>payload {op, args}</label>
      <textarea id="payload">{"op": "catalog.list", "args": {"max_price": 20}}</textarea></div>
    <label>request key (needs customer role)</label>
    <input id="rkey" value="dev-smoke-key">
    <div style="margin-top:10px"><button onclick="send()">send</button></div>
    <div id="resp"></div>
  </section>
</main>
<script>
let lastSeq = 0;
const $ = (id) => document.getElementById(id);
const auth = () => ({ "Authorization": "Bearer " + $("key").value });

async function poll() {
  try {
    const v = await (await fetch("/v1/audit/verify", { headers: auth() })).json();
    const b = $("badge");
    b.textContent = v.ok ? "chain OK · " + v.checked + " entries · " + v.backend : "CHAIN BROKEN @" + v.first_bad_seq;
    b.className = v.ok ? "" : "bad";
    const r = await (await fetch("/v1/audit", { headers: auth() })).json();
    for (const rec of r.records) {
      if (rec.seq <= lastSeq) continue;
      lastSeq = rec.seq;
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `<span class="seq">${rec.seq}</span><span class="ts">${rec.ts.slice(11, 19)}</span>` +
        `<span class="ev ${rec.event}">${rec.event}</span><span class="who">${rec.principal}</span>` +
        `<span class="d">${escapeHtml(summary(rec.data))}</span>`;
      $("feed").prepend(row);
    }
  } catch (e) { $("badge").textContent = "error: " + e.message; $("badge").className = "bad"; }
}

function summary(d) {
  for (const k of ["tier", "decision", "op", "reason", "backend"])
    if (k in d) return k + "=" + String(d[k]) + (d.degraded ? " (degraded)" : "");
  return JSON.stringify(d).slice(0, 70);
}
const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function send() {
  const body = $("mode").value === "nl"
    ? { payload: { text: $("text").value } }
    : { payload: JSON.parse($("payload").value) };
  const r = await fetch("/v1/request", { method: "POST",
    headers: { "Content-Type": "application/json", "Authorization": "Bearer " + $("rkey").value },
    body: JSON.stringify(body) });
  $("resp").textContent = JSON.stringify(await r.json(), null, 2);
}

$("mode").onchange = () => {
  $("nlbox").style.display = $("mode").value === "nl" ? "" : "none";
  $("t0box").style.display = $("mode").value === "t0" ? "" : "none";
};
poll();
setInterval(poll, 1500);
</script>
</body>
</html>
"""
