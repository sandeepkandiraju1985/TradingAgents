(function () {
  "use strict";
  const LWC = window.LightweightCharts;
  const $ = (id) => document.getElementById(id);
  const S = {
    data: null, wl: null, perf: {}, range: 252, mode: "cash", amount: 10000, showS: true, showB: true,
    sym: null, stock: null, type: "candle", srange: 126, booted: false, interval: "1d", setup: null
  };
  const BENCH_NAMES = { "^NSEI": "NIFTY 50", "^BSESN": "SENSEX", "^GSPC": "S&P 500", "^NDX": "NASDAQ 100" };
  const CUR = { INR: "₹", USD: "$" };

  /* ---------- helpers ---------- */
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; }
  const SVGNS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs) { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
  function curOf(sym) { const s = (sym || "").toUpperCase(); return (s.endsWith(".NS") || s.endsWith(".BO") || ["^NSEI", "^BSESN", "^NSEBANK"].includes(s)) ? "INR" : "USD"; }
  function money(v, cur, dp) { if (v === null || v === undefined || isNaN(v)) return "–"; dp = dp === undefined ? 2 : dp; return (CUR[cur] || "") + Number(v).toLocaleString(cur === "INR" ? "en-IN" : "en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp }); }
  function pct(x, dp) { if (x === null || x === undefined || isNaN(x)) return "–"; dp = dp === undefined ? 1 : dp; return (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(dp) + "%"; }
  function dir(x) { return x >= 0 ? "up" : "down"; }
  function when(ts) { return new Date(ts * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
  function dayOf(iso) { return new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }); }
  function localISO(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  function tstr(t) { if (typeof t === "string") return t; if (typeof t === "number") return new Date(t * 1000).toISOString().slice(0, 10); return t.year + "-" + String(t.month).padStart(2, "0") + "-" + String(t.day).padStart(2, "0"); }
  function dur(sec) { if (sec === null || sec === undefined) return "–"; const m = Math.floor(sec / 60), s = Math.round(sec % 60); return m ? m + "m " + s + "s" : s + "s"; }
  function short(sym) { return (sym || "").replace(/\.(NS|BO)$/, ""); }
  function benchName(sym) { return BENCH_NAMES[sym] || sym; }
  function busy(btn, on, text) { if (on) { btn.dataset.label = btn.textContent; btn.textContent = text; btn.disabled = true; } else { btn.textContent = btn.dataset.label || btn.textContent; btn.disabled = false; } }

  async function api(path, opts) {
    const o = Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {});
    const r = await fetch(path, o);
    let body = null;
    try { body = await r.json(); } catch (e) { /* empty body */ }
    if (!r.ok) {
      const d = body && body.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(function (x) { return x.msg; }).join("; ") : "Request failed (" + r.status + ")");
    }
    return body;
  }
  let toastTimer;
  function toast(msg, bad) { const t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, bad ? 8000 : 4000); }
  function seg(id, onPick) {
    const g = $(id);
    g.addEventListener("click", function (e) {
      const b = e.target.closest("button"); if (!b) return;
      g.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
      onPick(b.dataset.v);
    });
  }
  function symButton(sym, label) { const b = el("button", "symbtn", label || short(sym)); b.type = "button"; b.title = "Open " + sym + " on the chart"; b.addEventListener("click", function () { loadStock(sym, true); }); return b; }
  function spark(vals, color, H, fill) {
    const W = 240, lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), span = (hi - lo) || 1;
    const pts = vals.map(function (v, i) { return (i / Math.max(1, vals.length - 1) * W).toFixed(1) + "," + (H - 2 - (v - lo) / span * (H - 4)).toFixed(1); });
    const s = sv("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", "aria-hidden": "true" });
    if (fill) s.appendChild(sv("polygon", { points: "0," + H + " " + pts.join(" ") + " " + W + "," + H, fill: color, "fill-opacity": "0.10" }));
    s.appendChild(sv("polyline", { points: pts.join(" "), fill: "none", stroke: color, "stroke-width": "2", "vector-effect": "non-scaling-stroke" }));
    return s;
  }
  function sma(vals, n) { const out = []; let t = 0; for (let i = 0; i < vals.length; i++) { t += vals[i].value; if (i >= n) t -= vals[i - n].value; if (i >= n - 1) out.push({ time: vals[i].time, value: t / n }); } return out; }

  /* ---------- theme ---------- */
  function C() { const s = getComputedStyle(document.documentElement); const g = function (k) { return s.getPropertyValue("--" + k).trim(); }; return { surface: g("surface"), ink: g("ink"), muted: g("muted"), faint: g("faint"), line: g("line"), grid: g("grid"), s1: g("s1"), s2: g("s2"), up: g("up"), down: g("down"), accent: g("accent") }; }
  function hexA(hex, a) { const h = (hex || "#888888").replace("#", ""); const full = h.length === 3 ? h.split("").map(function (x) { return x + x; }).join("") : h; const n = parseInt(full, 16); return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")"; }
  function chartOpts(c) {
    return {
      autoSize: true,
      layout: { background: { type: "solid", color: c.surface }, textColor: c.muted, fontFamily: "Segoe UI, Inter, system-ui, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.line }, timeScale: { borderColor: c.line, rightOffset: 2 },
      crosshair: { mode: LWC.CrosshairMode.Normal, vertLine: { color: c.faint, labelBackgroundColor: c.ink }, horzLine: { color: c.faint, labelBackgroundColor: c.ink } }
    };
  }
  function chartMessage(boxId, text) {
    const wrap = $(boxId).parentNode; let m = wrap.querySelector(".msgbox");
    if (!text) { if (m) m.remove(); return; }
    if (!m) { m = el("div", "msgbox"); m.style.position = "absolute"; m.style.inset = "0"; m.style.background = "var(--surface)"; wrap.appendChild(m); }
    m.textContent = text;
  }

  /* ---------- market strip ---------- */
  async function loadStrip() {
    const box = $("quotes");
    try {
      const list = await api("/api/strip");
      box.textContent = "";
      list.forEach(function (q) {
        const d = el("div", "quote");
        d.appendChild(el("div", "name", q.name));
        if (q.error) { d.appendChild(el("div", "px muted", "–")); d.appendChild(el("div", "chg muted", "No data")); box.appendChild(d); return; }
        d.appendChild(el("div", "px num", Number(q.last).toLocaleString(q.symbol === "^NSEI" || q.symbol === "^BSESN" || q.symbol === "INR=X" ? "en-IN" : "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
        d.appendChild(el("div", "chg num " + dir(q.change_pct), pct(q.change_pct, 2)));
        d.appendChild(spark(q.spark, q.change_pct >= 0 ? "var(--up)" : "var(--down)", 26, false));
        box.appendChild(d);
      });
    } catch (e) {
      box.textContent = "";
      box.appendChild(el("p", "empty", "Market quotes are unavailable right now: " + e.message));
      box.style.padding = "4px 16px";
    }
  }

  /* ---------- state ---------- */
  let pollTimer;
  async function refresh() {
    clearTimeout(pollTimer);
    try {
      S.data = await api("/api/state");
      renderAll();
      if (!S.booted) boot2();
      const list = S.data.analyses;
      if (S.avId === null && list.length) {
        const pick = list.find(function (a) { return a.status === "running"; }) || list[0];
        selectAnalysis(pick.id, false);
      } else if (S.av) {
        const row = list.find(function (a) { return a.id === S.avId; });
        if (row && row.status !== S.av.status) loadAgentView();
      }
    } catch (e) {
      toast("Couldn't reach the console: " + e.message, true);
    }
    const running = S.data && S.data.analyses.some(function (a) { return a.status === "queued" || a.status === "running"; });
    pollTimer = setTimeout(refresh, running ? 5000 : 30000);
  }
  function renderAll() {
    renderAnalyses(); renderScan(); renderPositions(); renderClosed(); renderQueue(); renderExits();
    renderLedger(); renderSettings(); renderEvents(); renderWatchlists();
  }

  /* ---------- analyses ---------- */
  function renderAnalyses() {
    const body = $("analysesBody"); body.textContent = "";
    const list = S.data.analyses;
    if (!list.length) { const tr = el("tr"), td = el("td", "empty", "No analyses yet. Run one above, or flag stocks with Scan now."); td.colSpan = 4; tr.appendChild(td); body.appendChild(tr); }
    list.forEach(function (a) {
      const tr = el("tr");
      tr.setAttribute("aria-current", String(a.id === S.avId));
      tr.title = "Show this analysis in the agent view";
      tr.addEventListener("click", function () { selectAnalysis(a.id, true); });
      const td1 = el("td"); td1.appendChild(el("strong", "", short(a.symbol))); td1.appendChild(el("small", "", dayOf(a.trade_date))); tr.appendChild(td1);
      const td2 = el("td");
      if (a.status === "done") td2.appendChild(el("span", "rating " + String(a.rating || "review").toLowerCase(), a.rating || "REVIEW"));
      else if (a.status === "running") {
        td2.appendChild(el("span", "rating running", "Running " + dur(S.data.now - a.started)));
        if (a.progress && a.progress.current) td2.appendChild(el("span", "stage", a.progress.current));
      }
      else if (a.status === "queued") td2.appendChild(el("span", "rating queued", "Queued"));
      else if (a.status === "cancelled") td2.appendChild(el("span", "rating cancelled", "Cancelled"));
      else { td2.appendChild(el("span", "rating failed", "Failed")); td2.appendChild(el("small", "", (a.error || "").slice(0, 160))); }
      tr.appendChild(td2);
      const st = a.stats || {};
      const td3 = el("td", "r num", a.status === "done" ? dur(st.seconds) + " · " + (st.llm_calls ?? "–") : "");
      if (a.status === "done" && st.tokens_in !== undefined) td3.appendChild(el("small", "", Math.round((st.tokens_in + st.tokens_out) / 1000) + "k tokens"));
      tr.appendChild(td3);
      const td4 = el("td", "r");
      if (a.report_path) { const l = el("a", "linkbtn", "Report"); l.href = "/api/analyses/" + a.id + "/report"; l.target = "_blank"; l.rel = "noopener"; td4.appendChild(l); }
      tr.appendChild(td4);
      body.appendChild(tr);
    });
    const done = list.filter(function (a) { return a.status === "done" && a.stats; }).slice(0, 10);
    $("effStats").textContent = done.length ? (done.length === 1 ? "Last run: " : "Last " + done.length + " runs, average: ") + dur(done.reduce(function (t, a) { return t + (a.stats.seconds || 0); }, 0) / done.length) + ", " +
      Math.round(done.reduce(function (t, a) { return t + (a.stats.llm_calls || 0); }, 0) / done.length) + " LLM calls, " +
      Math.round(done.reduce(function (t, a) { return t + (a.stats.tokens_in || 0) + (a.stats.tokens_out || 0); }, 0) / done.length / 1000) + "k tokens per run." : "";
  }
  function prefill(sym) {
    $("aSym").value = sym;
    $("analyze").scrollIntoView({ block: "start" });
    $("aRun").focus();
    toast("Check the analysts and date, then press Run analysis.");
  }
  $("aDate").value = localISO(new Date());
  $("analyzeForm").addEventListener("submit", async function (e) {
    e.preventDefault();
    const btn = $("aRun");
    const analysts = Array.from($("aAnalysts").querySelectorAll("input:checked")).map(function (i) { return i.value; });
    busy(btn, true, "Starting…");
    try {
      const a = await api("/api/analyses", { method: "POST", body: JSON.stringify({ symbol: $("aSym").value, trade_date: $("aDate").value, analysts: analysts }) });
      toast("Analysis of " + a.symbol + " queued. Watch the agents work in the agent view.");
      await refresh();
      selectAnalysis(a.id, true);
    } catch (err) { toast(err.message, true); }
    busy(btn, false);
  });

  /* ---------- agent view ---------- */
  S.avId = null; S.av = null; S.avSig = ""; S.avOpen = {}; S.avStopping = {};
  let avTimer;
  const ANALYST_KEYS = ["market", "social", "news", "fundamentals"];
  const SPEAKER = { Bull: "bull", Bear: "bear", Aggressive: "aggressive", Conservative: "conservative", Neutral: "neutral" };

  // Agents write Markdown. Render the common parts as DOM nodes; never as HTML.
  function inline(parent, text) {
    text.split(/(\*\*[^*]+\*\*)/).forEach(function (part) {
      if (!part) return;
      if (/^\*\*[^*]+\*\*$/.test(part)) parent.appendChild(el("strong", "", part.slice(2, -2)));
      else parent.appendChild(document.createTextNode(part));
    });
  }
  function md(text) {
    const root = el("div", "md");
    let list = null, table = null, para = null;
    (text || "").split(/\r?\n/).forEach(function (raw) {
      const line = raw.replace(/\s+$/, "");
      let m;
      if (/^\s*\|.*\|$/.test(line)) {
        list = para = null;
        if (/^\s*\|(\s*:?-+:?\s*\|)+$/.test(line)) return;   // header separator row
        if (!table) { const wrap = el("div", "tblwrap"); table = el("table"); wrap.appendChild(table); root.appendChild(wrap); }
        const tr = el("tr"), head = table.rows.length === 0;
        line.trim().replace(/^\||\|$/g, "").split("|").forEach(function (c) { const cell = el(head ? "th" : "td"); inline(cell, c.trim()); tr.appendChild(cell); });
        table.appendChild(tr);
        return;
      }
      table = null;
      if (!line.trim() || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { list = para = null; return; }
      if ((m = line.match(/^\s*#{1,6}\s+(.*)$/))) { const h = el("h4"); inline(h, m[1]); root.appendChild(h); list = para = null; return; }
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/), numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || numbered) {
        const tag = numbered ? "OL" : "UL";
        if (!list || list.tagName !== tag) { list = el(tag.toLowerCase()); root.appendChild(list); }
        const li = el("li"); inline(li, (bullet || numbered)[1]); list.appendChild(li); para = null;
        return;
      }
      list = null;
      if (!para) { para = el("p"); root.appendChild(para); } else para.appendChild(document.createElement("br"));
      inline(para, line.trim());
    });
    return root;
  }

  function selectAnalysis(id, scroll) {
    if (S.avId !== id) { S.avSig = ""; S.avOpen = {}; }
    S.avId = id;
    loadAgentView();
    if (S.data) renderAnalyses();
    if (scroll) $("agents").scrollIntoView({ block: "start" });
  }
  async function loadAgentView() {
    clearTimeout(avTimer);
    if (S.avId === null) return;
    const id = S.avId;
    try {
      const a = await api("/api/analyses/" + id);
      if (S.avId !== id) return;
      S.av = a;
      renderAgentView();
      if (a.status === "running" || a.status === "queued") avTimer = setTimeout(loadAgentView, 4000);
    } catch (e) {
      $("avEmpty").hidden = false; $("avEmpty").textContent = "Couldn't load this analysis: " + e.message;
    }
  }
  function stepTime(s) {
    if (s.status === "done") return s.seconds !== undefined ? dur(s.seconds) : "done";
    return s.status === "working" ? "working…" : s.status;
  }
  function openStep(key) {
    S.avOpen[key] = true;
    const d = $("avSteps").querySelector('details[data-key="' + key + '"]');
    if (d) { d.open = true; d.scrollIntoView({ block: "start" }); }
  }
  async function decide(p, verb) {
    try {
      if (verb === "approve") { const o = await api("/api/proposals/" + p.id + "/approve", { method: "POST", body: "{}" }); toast("Approved. Dry-run " + o.side + " of " + o.qty + " " + o.symbol + " recorded at " + money(o.price, o.currency) + "."); }
      else { await api("/api/proposals/" + p.id + "/reject", { method: "POST", body: "{}" }); toast("Rejected."); }
    } catch (e) { toast(e.message, true); }
    S.avSig = ""; await refresh(); loadAgentView();
  }
  function renderAgentView() {
    const a = S.av; if (!a) return;
    $("avEmpty").hidden = true;
    $("avTitle").textContent = short(a.symbol) + " · " + dayOf(a.trade_date) + " · " + a.analysts.length + (a.analysts.length === 1 ? " analyst" : " analysts");
    const st = $("avStatus"); st.textContent = "";
    const label = a.status === "done" ? (a.rating || "REVIEW") : a.status === "running" ? "Running " + dur(a.now - a.started) : a.status.charAt(0).toUpperCase() + a.status.slice(1);
    st.appendChild(el("span", "rating " + (a.status === "done" ? String(a.rating || "review").toLowerCase() : a.status), label));
    if (a.status === "done" && a.stats) st.appendChild(el("span", "muted", " " + dur(a.stats.seconds) + " · " + (a.stats.llm_calls ?? "–") + " LLM calls"));

    const act = $("avActions"); act.textContent = "";
    if (a.status === "running" || a.status === "queued") {
      const stopping = S.avStopping[a.id];
      const c = el("button", "btn small no", stopping ? "Stopping…" : "Cancel"); c.type = "button"; c.disabled = !!stopping;
      c.title = a.status === "running" ? "Stops when the agent that is working now finishes" : "Removes it from the queue";
      c.addEventListener("click", async function () {
        c.disabled = true;
        try {
          await api("/api/analyses/" + a.id + "/cancel", { method: "POST", body: "{}" });
          if (a.status === "running") { S.avStopping[a.id] = true; toast("Stopping after the agent that is working now finishes."); } else toast("Cancelled.");
        } catch (e) { toast(e.message, true); }
        S.avSig = ""; refresh(); loadAgentView();
      });
      act.appendChild(c);
    }
    if (a.report_path) { const r = el("a", "btn small", "Full report"); r.href = "/api/analyses/" + a.id + "/report"; r.target = "_blank"; r.rel = "noopener"; act.appendChild(r); }

    const sig = JSON.stringify([a.status, a.progress, a.proposals.map(function (p) { return p.status; }), a.error]);
    if (sig === S.avSig) return;   // nothing new: keep the page (and the reader's place) as it is
    S.avSig = sig;

    const P = a.progress, pipe = $("avPipe"), steps = $("avSteps"), dec = $("avDecision");
    pipe.textContent = ""; steps.textContent = ""; dec.textContent = "";
    if (a.status === "failed" || a.status === "cancelled") {
      const box = el("div", "decision"); box.appendChild(el("span", "bigrating rating " + a.status, a.status === "failed" ? "Failed" : "Cancelled"));
      const right = el("div"); right.appendChild(el("p", "", a.error || "")); box.appendChild(right); dec.appendChild(box);
    }
    if (!P) { if (a.status === "queued") pipe.appendChild(el("li", "muted", "Waiting for the analysis ahead of it to finish.")); return; }

    P.steps.forEach(function (s, i) {
      if (i > 0 && ANALYST_KEYS.indexOf(s.key) < 0) { const sep = el("li", "pipe-sep", "›"); sep.setAttribute("aria-hidden", "true"); pipe.appendChild(sep); }
      const li = el("li", "pipe " + s.status), dot = el("span", "dot");
      li.appendChild(dot); li.appendChild(document.createTextNode(s.label)); li.appendChild(el("small", "", stepTime(s)));
      if (s.status !== "skipped") { li.tabIndex = 0; li.addEventListener("click", function () { openStep(s.key); }); li.addEventListener("keydown", function (e) { if (e.key === "Enter") openStep(s.key); }); }
      pipe.appendChild(li);
    });

    const pm = P.steps.find(function (s) { return s.key === "portfolio_manager"; });
    if (a.status === "done") {
      const box = el("div", "decision");
      box.appendChild(el("span", "bigrating rating " + String(a.rating || "review").toLowerCase(), a.rating || "REVIEW"));
      const right = el("div");
      right.appendChild(el("div", "who", "Portfolio manager's decision"));
      const text = pm && pm.text ? pm.text : (a.summary || "");
      right.appendChild(md(text.length > 900 ? text.slice(0, 900).replace(/\s+\S*$/, "") + " …" : text));
      const p = a.proposals[0];
      const row = el("div", "actions");
      if (p && p.status === "pending") {
        row.appendChild(el("span", "", "Proposed: "));
        row.lastChild.appendChild(el("span", "side " + p.side.toLowerCase(), p.side));
        row.appendChild(el("span", "num", p.qty + " " + short(p.symbol) + " @ " + money(p.price, p.currency)));
        const ok = el("button", "btn ok small", "Approve"), no = el("button", "btn no small", "Reject");
        ok.type = no.type = "button";
        ok.addEventListener("click", function () { ok.disabled = no.disabled = true; decide(p, "approve"); });
        no.addEventListener("click", function () { ok.disabled = no.disabled = true; decide(p, "reject"); });
        row.appendChild(ok); row.appendChild(no);
      } else if (p) {
        row.appendChild(el("span", "muted", p.side + " " + p.qty + " " + short(p.symbol) + ": " + p.status + "."));
      } else {
        row.appendChild(el("span", "muted", "No trade proposed for this rating."));
      }
      right.appendChild(row);
      box.appendChild(right);
      dec.appendChild(box);
    }

    P.steps.forEach(function (s) {
      if (s.status === "skipped") return;
      const d = el("details", "avstep"); d.dataset.key = s.key;
      d.open = !!S.avOpen[s.key];
      d.addEventListener("toggle", function () { S.avOpen[s.key] = d.open; });
      const sum = el("summary");
      sum.appendChild(el("span", "sdot " + s.status));
      sum.appendChild(document.createTextNode(s.label));
      sum.appendChild(el("small", "", stepTime(s)));
      d.appendChild(sum);
      const body = el("div", "avbody");
      if (s.turns && s.turns.length) {
        s.turns.forEach(function (t) { const b = el("div", "turn " + (SPEAKER[t.speaker] || "neutral")); b.appendChild(el("div", "who", t.speaker)); b.appendChild(md(t.text)); body.appendChild(b); });
      } else if (s.text) {
        body.appendChild(md(s.text));
      } else {
        body.appendChild(el("p", "empty", s.status === "working" ? "Working on it. The output appears here when this agent finishes." : "Waits for the agents before it."));
      }
      d.appendChild(body);
      steps.appendChild(d);
    });
  }

  /* ---------- scan ---------- */
  function renderScan() {
    const s = S.data.settings.last_scan;
    $("scanStatus").textContent = s ? "Last scan " + when(s.ts) + " · " + s.count + " screened · " + s.flagged + " flagged" : "Not run yet";
    const body = $("scanBody"); body.textContent = "";
    const rows = S.data.scan_results.slice().sort(function (a, b) { return (b.flags ? b.flags.length : -1) - (a.flags ? a.flags.length : -1); });
    if (!rows.length) { const tr = el("tr"), td = el("td", "empty", "Press Scan now to screen every watchlist."); td.colSpan = 6; tr.appendChild(td); body.appendChild(tr); return; }
    rows.forEach(function (r) {
      const tr = el("tr"), td1 = el("td"); td1.appendChild(symButton(r.symbol)); tr.appendChild(td1);
      if (r.error) { const td = el("td", "muted", r.error); td.colSpan = 5; tr.appendChild(td); body.appendChild(tr); return; }
      tr.appendChild(el("td", "r num", money(r.last, r.currency)));
      tr.appendChild(el("td", "r num " + dir(r.change_pct), pct(r.change_pct, 2)));
      const su = r.setup || {}, tdS = el("td");
      if (su.status) {
        tdS.appendChild(el("span", "vpchip " + su.status, SETUP_LABEL[su.status] || su.status));
        const detail = su.status === "entry" ? "at " + money(su.entry, r.currency) + (su.reward_risk ? " · R/R " + su.reward_risk.toFixed(1) : "") + (su.proposal_id ? " · proposed" : "")
          : su.status === "waiting" && su.distance_pct !== null && su.distance_pct !== undefined ? su.distance_pct.toFixed(1) + "% above POC " + money(su.poc, r.currency)
          : su.status === "testing" ? "POC " + money(su.poc, r.currency) : "";
        if (detail) tdS.appendChild(el("small", "", detail));
      }
      tr.appendChild(tdS);
      tr.appendChild(el("td", r.flags.length ? "" : "muted", r.flags.length ? r.flags.join(" · ") : "No signal"));
      const td5 = el("td", "r");
      if (r.flags.length) { const b = el("button", "btn small", "Analyze"); b.type = "button"; b.addEventListener("click", function () { prefill(r.symbol); }); td5.appendChild(b); }
      tr.appendChild(td5); body.appendChild(tr);
    });
  }
  $("scanBtn").addEventListener("click", async function () {
    const btn = this; busy(btn, true, "Scanning…");
    try {
      const out = await api("/api/scan", { method: "POST", body: "{}" });
      toast("Screened " + out.summary.count + " stocks: " + out.summary.flagged + " flagged, " + out.summary.exits + " auto-exits.");
      await refresh();
    } catch (e) { toast(e.message, true); }
    busy(btn, false);
  });

  /* ---------- positions, closed trades ---------- */
  function renderPositions() {
    const body = $("posBody"); body.textContent = "";
    const list = S.data.positions, st = S.data.settings;
    if (!list.length) { const tr = el("tr"), td = el("td", "empty", "No open positions. Approve a Buy proposal to open one."); td.colSpan = 6; tr.appendChild(td); body.appendChild(tr); }
    list.forEach(function (p) {
      const tr = el("tr"), td1 = el("td"); td1.appendChild(symButton(p.symbol)); td1.appendChild(el("small", "", "Since " + when(p.opened))); tr.appendChild(td1);
      tr.appendChild(el("td", "r num", String(p.qty)));
      tr.appendChild(el("td", "r num", money(p.avg_price, p.currency)));
      tr.appendChild(el("td", "r num", money(p.last, p.currency)));
      tr.appendChild(el("td", "r num " + (p.change_pct === null ? "" : dir(p.change_pct)), pct(p.change_pct, 2)));
      tr.appendChild(el("td", "r num", money(p.stop_price, p.currency)));
      body.appendChild(tr);
    });
    $("exitNote").textContent = "Positions are checked every 10 minutes while the console is open, and with every scan." +
      (st.last_exit_check ? " Last check: " + when(st.last_exit_check) + "." : "");
    $("ruleChip").textContent = "Rule: loss over " + st.stop_loss_pct + "%";
  }
  $("exitBtn").addEventListener("click", async function () {
    const btn = this; busy(btn, true, "Checking…");
    try {
      const out = await api("/api/check-exits", { method: "POST", body: "{}" });
      toast(out.exits.length ? out.exits.length + " auto-exit(s) recorded." : "No position is past the auto-exit limit.");
      await refresh();
    } catch (e) { toast(e.message, true); }
    busy(btn, false);
  });
  function renderClosed() {
    const box = $("carousel"); box.textContent = "";
    if (!S.data.closed.length) { box.appendChild(el("p", "empty", "No closed trades yet.")); return; }
    S.data.closed.forEach(function (t) {
      const c = el("article", "prev");
      const h = el("h3"); h.appendChild(symButton(t.symbol)); c.appendChild(h);
      const auto = (t.reason || "").indexOf("Auto-exit") === 0;
      c.appendChild(el("span", "reason " + (auto ? "stop" : "sig"), auto ? "Auto-exit" : t.reason || "Sold"));
      [["Opened", when(t.opened)], ["Closed", when(t.closed)], ["Entry", money(t.entry, t.currency)], ["Exit", money(t.exit, t.currency)]].forEach(function (kv) {
        const r = el("div", "kv"); r.appendChild(el("span", "", kv[0])); r.appendChild(el("b", "", kv[1])); c.appendChild(r);
      });
      const rr = el("div", "ret"); rr.appendChild(el("span", "muted", "Return")); rr.appendChild(el("b", dir(t.return_pct), pct(t.return_pct))); c.appendChild(rr);
      box.appendChild(c);
    });
  }
  $("prevL").addEventListener("click", function () { $("carousel").scrollBy({ left: -270, behavior: "smooth" }); });
  $("prevR").addEventListener("click", function () { $("carousel").scrollBy({ left: 270, behavior: "smooth" }); });

  /* ---------- approvals, exits, ledger, settings, events ---------- */
  function renderQueue() {
    const q = $("queue"); q.textContent = "";
    const pending = S.data.proposals.filter(function (p) { return p.status === "pending"; });
    $("pendingChip").textContent = pending.length + " waiting";
    const nav = $("navCount"); nav.textContent = pending.length || "";
    if (!pending.length) q.appendChild(el("p", "empty", "Nothing to approve. Proposals appear here when an analysis rates a stock Buy or Sell."));
    pending.forEach(function (p) {
      const o = el("article", "order"), h = el("div", "head"), l = el("span");
      l.appendChild(el("span", "side " + p.side.toLowerCase(), p.side)); l.appendChild(symButton(p.symbol, p.symbol));
      h.appendChild(l); h.appendChild(el("span", "num", p.qty + " @ " + money(p.price, p.currency)));
      o.appendChild(h);
      o.appendChild(el("p", "", p.source === "volume_profile" ? "Volume profile setup. " + (p.note || "") : "Rated " + (p.rating || "–") + ". " + (p.note || "")));
      const a = el("div", "actions"), ok = el("button", "btn ok small", "Approve"), no = el("button", "btn no small", "Reject");
      ok.type = no.type = "button";
      ok.addEventListener("click", async function () {
        ok.disabled = no.disabled = true;
        try { const order = await api("/api/proposals/" + p.id + "/approve", { method: "POST", body: "{}" }); toast("Approved. Dry-run " + order.side + " of " + order.qty + " " + order.symbol + " recorded at " + money(order.price, order.currency) + "."); }
        catch (e) { toast(e.message, true); }
        refresh();
      });
      no.addEventListener("click", async function () {
        ok.disabled = no.disabled = true;
        try { await api("/api/proposals/" + p.id + "/reject", { method: "POST", body: "{}" }); toast("Rejected."); } catch (e) { toast(e.message, true); }
        refresh();
      });
      a.appendChild(ok); a.appendChild(no);
      if (p.analysis_id) { const r = el("a", "linkbtn", "Read the report"); r.href = "/api/analyses/" + p.analysis_id + "/report"; r.target = "_blank"; r.rel = "noopener"; a.appendChild(r); }
      o.appendChild(a); q.appendChild(o);
    });
    const decided = S.data.proposals.filter(function (p) { return p.status !== "pending"; }).slice(0, 5);
    if (decided.length) {
      q.appendChild(el("h3", "", "Recently decided")).style.margin = "12px 0 4px";
      decided.forEach(function (p) { q.appendChild(el("p", "muted", p.side + " " + p.qty + " " + p.symbol + ": " + p.status + (p.decided ? ", " + when(p.decided) : ""))).style.margin = "2px 0"; });
    }
  }
  function renderExits() {
    const box = $("exitlog"); box.textContent = "";
    const list = S.data.events.filter(function (e) { return e.kind === "auto_exit"; });
    if (!list.length) { box.appendChild(el("p", "empty", "No auto-exits yet. A position is sold when it falls more than " + S.data.settings.stop_loss_pct + "% below its average entry.")); return; }
    list.forEach(function (ev) {
      const o = el("article", "order"), h = el("div", "head"), l = el("span");
      l.appendChild(el("span", "side sell", "SELL")); l.appendChild(symButton(ev.symbol, ev.symbol));
      h.appendChild(l); h.appendChild(el("span", "num down", ev.data ? pct(ev.data.loss_pct) : ""));
      o.appendChild(h); o.appendChild(el("p", "", ev.message + " " + when(ev.ts) + "."));
      box.appendChild(o);
    });
  }
  function renderLedger() {
    const body = $("ledgerBody"); body.textContent = "";
    if (!S.data.orders.length) { const tr = el("tr"), td = el("td", "empty", "No orders yet."); td.colSpan = 7; tr.appendChild(td); body.appendChild(tr); return; }
    S.data.orders.forEach(function (o) {
      const tr = el("tr");
      tr.appendChild(el("td", "num", when(o.ts)));
      const ts = el("td"); ts.appendChild(symButton(o.symbol)); tr.appendChild(ts);
      const sd = el("td"); sd.appendChild(el("span", "side " + o.side.toLowerCase(), o.side)); tr.appendChild(sd);
      tr.appendChild(el("td", "r num", String(o.qty)));
      tr.appendChild(el("td", "r num", money(o.price, o.currency)));
      tr.appendChild(el("td", "muted", o.reason || ""));
      tr.appendChild(el("td", "", o.mode === "dry-run" ? "Dry-run" : o.mode));
      body.appendChild(tr);
    });
  }
  function renderSettings() {
    const s = S.data.settings;
    $("hourlyTop").checked = !!s.hourly_scan;
    const form = $("settingsForm");
    if (form.contains(document.activeElement) && S.settingsFilled) return;
    $("sStop").value = s.stop_loss_pct; $("sInr").value = s.trade_budget.INR; $("sUsd").value = s.trade_budget.USD; $("hourlySet").checked = !!s.hourly_scan;
    S.settingsFilled = true;
  }
  async function saveHourly(on) {
    try { await api("/api/settings", { method: "PATCH", body: JSON.stringify({ hourly_scan: on }) }); toast(on ? "Hourly scan on: Mon to Fri, 9:15 to 15:30 IST, while the console is open." : "Hourly scan off."); }
    catch (e) { toast(e.message, true); }
    refresh();
  }
  $("hourlyTop").addEventListener("change", function () { saveHourly(this.checked); });
  $("settingsForm").addEventListener("submit", async function (e) {
    e.preventDefault();
    try {
      await api("/api/settings", { method: "PATCH", body: JSON.stringify({ stop_loss_pct: +$("sStop").value, hourly_scan: $("hourlySet").checked, trade_budget: { INR: +$("sInr").value, USD: +$("sUsd").value } }) });
      toast("Settings saved."); S.settingsFilled = false; refresh();
      if (S.sym) loadStock(S.sym, false);
    } catch (err) { toast(err.message, true); }
  });
  function renderEvents() {
    const ul = $("events"); ul.textContent = "";
    if (!S.data.events.length) { ul.appendChild(el("li", "muted", "Nothing has happened yet.")); return; }
    S.data.events.forEach(function (ev) { const li = el("li"); li.appendChild(el("time", "", when(ev.ts))); li.appendChild(el("span", ev.kind === "error" ? "down" : "", ev.message)); ul.appendChild(li); });
  }

  /* ---------- watchlist performance ---------- */
  let perf = null, sSeries = null, bSeries = null;
  function ewSeries(P, start) {
    const out = [];
    for (let i = start; i < P.dates.length; i++) {
      let t = 0; for (const m of P.members) t += P.series[m][i] / P.series[m][start];
      out.push(t / P.members.length);
    }
    return out;
  }
  function fmtVal(v) { const P = S.perf[S.wl]; return S.mode === "pct" ? pct(v) : money(v, P && !P.error ? P.currency : "INR", 0); }
  async function loadPerf(wid, show) {
    if (!S.perf[wid]) {
      if (show) $("perfChart").classList.add("loading");
      try { S.perf[wid] = await api("/api/watchlists/" + wid + "/performance"); }
      catch (e) { S.perf[wid] = { error: e.message }; }
      $("perfChart").classList.remove("loading");
    }
    if (show || wid === S.wl) renderPerf();
    renderWatchlists(true);
  }
  function renderPerf() {
    const P = S.perf[S.wl], wl = S.data && S.data.watchlists.find(function (w) { return w.id === S.wl; });
    if (!perf || !wl) return;
    $("keySName").textContent = wl.name; $("keyBName").textContent = benchName(wl.benchmark);
    if (!P) { chartMessage("perfChart", "Loading prices…"); return; }
    if (P.error) { chartMessage("perfChart", "Couldn't load prices for this watchlist: " + P.error); $("keySRet").textContent = ""; $("keyBRet").textContent = ""; drawYearly(); return; }
    chartMessage("perfChart", null);
    const cur = CUR[P.currency] || ""; $("amountCur").textContent = cur; $("modeCash").textContent = cur;
    const n = P.dates.length, start = S.range > 0 ? Math.max(0, n - 1 - S.range) : 0;
    const ew = ewSeries(P, start);
    const sData = [], bData = [];
    for (let i = start; i < n; i++) {
      const s = ew[i - start], b = P.benchmark[i] / P.benchmark[start];
      sData.push({ time: P.dates[i], value: S.mode === "pct" ? (s - 1) * 100 : S.amount * s });
      bData.push({ time: P.dates[i], value: S.mode === "pct" ? (b - 1) * 100 : S.amount * b });
    }
    const sRet = (ew[ew.length - 1] - 1) * 100, bRet = (P.benchmark[n - 1] / P.benchmark[start] - 1) * 100;
    $("keySRet").textContent = pct(sRet); $("keySRet").className = dir(sRet);
    $("keyBRet").textContent = pct(bRet); $("keyBRet").className = dir(bRet);
    const pf = { type: "custom", formatter: fmtVal, minMove: 0.01 };
    sSeries.setData(sData); bSeries.setData(bData);
    sSeries.applyOptions({ priceFormat: pf, visible: S.showS }); bSeries.applyOptions({ priceFormat: pf, visible: S.showB });
    perf.timeScale().fitContent();
    $("perfNote").textContent = "Equal amounts in each of " + P.members.length + " stocks, bought on " + dayOf(P.dates[start]) + "." +
      (P.missing.length ? " No prices for: " + P.missing.join(", ") + "." : "") + " Drag to pan, scroll to zoom. Past returns don't predict future returns.";
    drawYearly();
  }
  function initPerf() {
    const box = $("perfChart");
    const c = C();
    perf = LWC.createChart(box, chartOpts(c));
    sSeries = perf.addAreaSeries({ lineWidth: 2, lineColor: c.s1, topColor: hexA(c.s1, 0.28), bottomColor: hexA(c.s1, 0.02), priceLineVisible: false });
    bSeries = perf.addLineSeries({ lineWidth: 2, color: c.s2, priceLineVisible: false });
    const tip = $("perfTip");
    perf.subscribeCrosshairMove(function (p) {
      if (!p.time || !p.point || p.point.x < 0 || p.point.y < 0) { tip.hidden = true; return; }
      const wl = S.data.watchlists.find(function (w) { return w.id === S.wl; });
      const a = p.seriesData.get(sSeries), b = p.seriesData.get(bSeries);
      tip.textContent = ""; tip.appendChild(el("div", "d", dayOf(tstr(p.time))));
      [[a, wl ? wl.name : "Watchlist", "--s1", S.showS], [b, wl ? benchName(wl.benchmark) : "Benchmark", "--s2", S.showB]].forEach(function (row) {
        if (!row[0] || !row[3]) return;
        const r = el("div", "row"), lab = el("span"), sw = el("i"); sw.style.background = "var(" + row[2] + ")";
        lab.appendChild(sw); lab.appendChild(document.createTextNode(row[1])); r.appendChild(lab); r.appendChild(el("b", "", fmtVal(row[0].value))); tip.appendChild(r);
      });
      tip.hidden = false;
      const w = box.clientWidth, tw = tip.offsetWidth;
      tip.style.left = (p.point.x + 16 + tw > w - 60 ? Math.max(0, p.point.x - tw - 16) : p.point.x + 16) + "px";
    });
  }
  function yearly(P) {
    const out = []; let prev = 0; const n = P.dates.length;
    for (let i = 0; i < n; i++) {
      const y = P.dates[i].slice(0, 4), last = i === n - 1 || P.dates[i + 1].slice(0, 4) !== y;
      if (!last) continue;
      let t = 0; for (const m of P.members) t += P.series[m][i] / P.series[m][prev];
      out.push({ year: y, s: (t / P.members.length - 1) * 100, b: (P.benchmark[i] / P.benchmark[prev] - 1) * 100, ytd: i === n - 1, from: prev === 0 ? P.dates[0] : null });
      prev = i;
    }
    return out;
  }
  function drawYearly() {
    const svg = $("yearly"), wrap = $("yearWrap"), tip = $("yearTip"), P = S.perf[S.wl];
    svg.textContent = ""; $("ytable").textContent = "";
    if (!P || P.error || !P.members.length) return;
    const wl = S.data.watchlists.find(function (w) { return w.id === S.wl; });
    const w = Math.max(280, wrap.clientWidth), h = 150, padL = 40, padB = 20, padT = 8, plotH = h - padB - padT;
    const rows = yearly(P), vals = [];
    rows.forEach(function (r) { if (S.showS) vals.push(r.s); if (S.showB) vals.push(r.b); });
    if (!vals.length) return;
    let max = Math.max(5, Math.max.apply(null, vals)) * 1.08, min = Math.min(0, Math.min.apply(null, vals)) * 1.08;
    const y = function (v) { return padT + (max - v) / (max - min) * plotH; };
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    [max / 1.08, 0, min < 0 ? min / 1.08 : null].forEach(function (t) {
      if (t === null) return;
      svg.appendChild(sv("line", { x1: padL, x2: w, y1: y(t), y2: y(t), class: t === 0 ? "zero" : "gl" }));
      const tx = sv("text", { x: padL - 6, y: y(t) + 3.5, "text-anchor": "end" }); tx.textContent = Math.round(t) + "%"; svg.appendChild(tx);
    });
    const band = (w - padL) / rows.length, bw = Math.max(4, Math.min(16, (band - 10) / 2));
    const sName = wl ? wl.name : "Watchlist", bName = wl ? benchName(wl.benchmark) : "Benchmark";
    rows.forEach(function (r, i) {
      const cx = padL + i * band + band / 2;
      const hit = sv("rect", { x: padL + i * band + 1, y: padT, width: band - 2, height: plotH, rx: 4, class: "hit", tabindex: 0 });
      hit.setAttribute("aria-label", r.year + ": " + sName + " " + pct(r.s) + ", " + bName + " " + pct(r.b));
      svg.appendChild(hit);
      const bars = [];
      if (S.showS) bars.push([r.s, "var(--s1)"]); if (S.showB) bars.push([r.b, "var(--s2)"]);
      bars.forEach(function (bar, k) {
        const x = bars.length === 2 ? (k === 0 ? cx - bw - 1 : cx + 1) : cx - bw / 2;
        const top = Math.min(y(bar[0]), y(0)), hh = Math.max(1, Math.abs(y(bar[0]) - y(0)));
        svg.appendChild(sv("rect", { x: x, y: top, width: bw, height: hh, rx: 2, fill: bar[1], "pointer-events": "none" }));
      });
      const lab = sv("text", { x: cx, y: h - 5, "text-anchor": "middle" }); lab.textContent = r.ytd ? r.year + " YTD" : r.year; svg.appendChild(lab);
      const show = function () {
        tip.textContent = ""; tip.appendChild(el("div", "d", r.from ? r.year + ", from " + dayOf(r.from) : r.ytd ? r.year + ", year to date" : r.year));
        [[r.s, sName, "--s1"], [r.b, bName, "--s2"]].forEach(function (row) { const rr = el("div", "row"), lb = el("span"), sw = el("i"); sw.style.background = "var(" + row[2] + ")"; lb.appendChild(sw); lb.appendChild(document.createTextNode(row[1])); rr.appendChild(lb); rr.appendChild(el("b", "", pct(row[0]))); tip.appendChild(rr); });
        tip.hidden = false; const tw = tip.offsetWidth; tip.style.left = Math.min(Math.max(0, cx - tw / 2), w - tw) + "px"; tip.style.top = "-6px";
      };
      hit.addEventListener("pointerenter", show); hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", function () { tip.hidden = true; }); hit.addEventListener("blur", function () { tip.hidden = true; });
    });
    const tbl = el("table"), head = el("tr");
    ["Year", sName, bName].forEach(function (t) { head.appendChild(el("th", "", t)); }); tbl.appendChild(head);
    rows.forEach(function (r) { const tr = el("tr"); tr.appendChild(el("td", "", r.ytd ? r.year + " YTD" : r.year)); tr.appendChild(el("td", "num " + dir(r.s), pct(r.s))); tr.appendChild(el("td", "num " + dir(r.b), pct(r.b))); tbl.appendChild(tr); });
    $("ytable").appendChild(tbl);
  }
  $("ydetails").addEventListener("toggle", function () { $("ytable").hidden = !this.open; });
  seg("rangeSeg", function (v) { S.range = +v; renderPerf(); });
  seg("modeSeg", function (v) { S.mode = v; renderPerf(); });
  $("amount").addEventListener("input", function () { const v = +this.value; if (v >= 100) { S.amount = v; renderPerf(); } });
  [["keyS", "showS"], ["keyB", "showB"]].forEach(function (k) {
    $(k[0]).addEventListener("click", function () { S[k[1]] = !S[k[1]]; this.setAttribute("aria-pressed", String(S[k[1]])); renderPerf(); });
  });
  $("wlSelect").addEventListener("change", function () { S.wl = +this.value; renderWatchlists(true); loadPerf(S.wl, true); });

  /* ---------- watchlist cards ---------- */
  function renderWatchlists(force) {
    const grid = $("wlGrid");
    if (!force && grid.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
    const lists = S.data.watchlists, sel = $("wlSelect");
    if (!lists.some(function (w) { return w.id === S.wl; })) S.wl = lists.length ? lists[0].id : null;
    sel.textContent = "";
    lists.forEach(function (w) { const o = el("option", "", w.name); o.value = w.id; sel.appendChild(o); });
    if (S.wl !== null) sel.value = String(S.wl);
    grid.textContent = "";
    if (!lists.length) grid.appendChild(el("p", "empty", "No watchlists. Create one to chart and scan it."));
    lists.forEach(function (w) {
      const P = S.perf[w.id], c = el("article", "wl"); c.setAttribute("aria-current", String(w.id === S.wl));
      const top = el("div", "top"); top.appendChild(el("span", "chip", "vs " + benchName(w.benchmark)));
      const show = el("button", "linkbtn", w.id === S.wl ? "On the chart" : "Show on chart"); show.type = "button";
      show.addEventListener("click", function () { S.wl = w.id; renderWatchlists(true); loadPerf(w.id, true); $("analyze").scrollIntoView({ block: "start" }); });
      top.appendChild(show); c.appendChild(top);
      c.appendChild(el("h3", "", w.name));
      if (P && !P.error && P.members.length) {
        const n = P.dates.length, start = Math.max(0, n - 1 - 1260), ew = ewSeries(P, start).filter(function (_, i) { return i % 5 === 0; });
        const sp = spark(ew, "var(--s1)", 44, true); sp.setAttribute("class", "spark"); c.appendChild(sp);
        const stats = el("div", "stats");
        [["Return, 1 year", P.r1], ["Return, 5 years", P.r5]].forEach(function (x) { const t = el("div", "stat"); t.appendChild(el("small", "", x[0])); t.appendChild(el("b", x[1] === null ? "muted" : dir(x[1]), x[1] === null ? "Not enough data" : pct(x[1] * 100))); stats.appendChild(t); });
        c.appendChild(stats);
      } else {
        c.appendChild(el("p", "empty", P && P.error ? "Prices unavailable: " + P.error : "Loading returns…"));
      }
      const syms = el("div", "syms");
      w.symbols.forEach(function (s) {
        const chip = el("span", "sym"); chip.appendChild(symButton(s, short(s)));
        const x = el("button", "", "×"); x.type = "button"; x.setAttribute("aria-label", "Remove " + s + " from " + w.name);
        x.addEventListener("click", function () { saveSymbols(w, w.symbols.filter(function (z) { return z !== s; })); });
        chip.appendChild(x); syms.appendChild(chip);
      });
      c.appendChild(syms);
      const add = el("form", "addrow"), inp = el("input"); inp.placeholder = "Add a symbol, e.g. ITC.NS"; inp.setAttribute("aria-label", "Add a symbol to " + w.name);
      const ab = el("button", "btn small", "Add"); ab.type = "submit";
      add.appendChild(inp); add.appendChild(ab);
      add.addEventListener("submit", function (e) { e.preventDefault(); const v = inp.value.trim().toUpperCase(); if (v) saveSymbols(w, w.symbols.concat([v])); });
      c.appendChild(add);
      const del = el("button", "linkbtn", "Delete watchlist"); del.type = "button"; del.style.color = "var(--down)"; del.style.alignSelf = "start";
      del.addEventListener("click", async function () {
        if (!del.dataset.armed) { del.dataset.armed = "1"; del.textContent = "Click again to delete " + w.name; setTimeout(function () { delete del.dataset.armed; del.textContent = "Delete watchlist"; }, 4000); return; }
        try { await api("/api/watchlists/" + w.id, { method: "DELETE" }); delete S.perf[w.id]; toast("Deleted " + w.name + "."); await refresh(); if (S.wl !== null) loadPerf(S.wl, true); } catch (e) { toast(e.message, true); }
      });
      c.appendChild(del);
      grid.appendChild(c);
    });
  }
  async function saveSymbols(w, symbols) {
    try {
      await api("/api/watchlists/" + w.id, { method: "PATCH", body: JSON.stringify({ symbols: symbols }) });
      delete S.perf[w.id];
      await refresh();
      renderWatchlists(true);
      loadPerf(w.id, w.id === S.wl);
    } catch (e) { toast(e.message, true); }
  }
  $("newWlBtn").addEventListener("click", function () { $("newWl").hidden = false; $("nwName").focus(); });
  $("nwCancel").addEventListener("click", function () { $("newWl").hidden = true; });
  $("newWl").addEventListener("submit", async function (e) {
    e.preventDefault();
    const syms = $("nwSyms").value.split(",").map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);
    try {
      const w = await api("/api/watchlists", { method: "POST", body: JSON.stringify({ name: $("nwName").value, benchmark: $("nwBench").value, symbols: syms }) });
      $("newWl").hidden = true; $("newWl").reset();
      S.wl = w.id; await refresh(); renderWatchlists(true);
      if (w.symbols.length) loadPerf(w.id, true);
      toast("Created " + w.name + ".");
    } catch (err) { toast(err.message, true); }
  });

  /* ---------- stock chart ---------- */
  let stk = null, candle, line, area, vol, s20, s50, plines = [];
  function primary() { return S.type === "candle" ? candle : S.type === "line" ? line : area; }
  function initStock() {
    stk = LWC.createChart($("stockChart"), chartOpts(C()));
    candle = stk.addCandlestickSeries({ borderVisible: false, priceLineVisible: false });
    line = stk.addLineSeries({ lineWidth: 2, priceLineVisible: false, visible: false });
    area = stk.addAreaSeries({ lineWidth: 2, priceLineVisible: false, visible: false });
    vol = stk.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    stk.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    s20 = stk.addLineSeries({ lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    s50 = stk.addLineSeries({ lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
    stk.subscribeCrosshairMove(function (p) {
      if (!S.stock) return;
      let bar = null;
      if (p.time) { const ts = tstr(p.time); bar = S.stock.bars.find(function (b) { return b.time === ts; }) || null; }
      showOhlc(bar || S.stock.bars[S.stock.bars.length - 1]);
    });
    stockColors();
    stk.timeScale().subscribeVisibleLogicalRangeChange(function () { requestAnimationFrame(drawVP); });
    if (window.ResizeObserver) new ResizeObserver(function () { requestAnimationFrame(drawVP); }).observe($("stockChart"));
  }
  function stockColors() {
    const c = C();
    stk.applyOptions(chartOpts(c));
    candle.applyOptions({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down });
    line.applyOptions({ color: c.ink });
    area.applyOptions({ lineColor: c.ink, topColor: hexA(c.ink, 0.16), bottomColor: hexA(c.ink, 0.01) });
    s20.applyOptions({ color: c.s1 }); s50.applyOptions({ color: c.s2 });
  }
  function showOhlc(b) {
    const o = $("ohlc"), cur = S.stock.currency; o.textContent = "";
    o.appendChild(document.createTextNode(barTime(b.time) + "  "));
    [["O", b.open], ["H", b.high], ["L", b.low], ["C", b.close]].forEach(function (kv) { o.appendChild(document.createTextNode(kv[0] + " ")); o.appendChild(el("b", "", money(kv[1], cur))); o.appendChild(document.createTextNode("  ")); });
  }
  function barTime(t) {
    if (typeof t !== "number") return dayOf(t);
    // Intraday times are the exchange's clock written as UTC (see market.py).
    return new Date(t * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  }
  function intraday() { return S.interval !== "1d"; }
  function visibleBars() { return intraday() ? Math.round(S.srange * 13) : S.srange; }   // ~13 half-hours per NSE day
  async function loadStock(sym, scroll) {
    if (S.sym !== sym) S.setup = null;
    S.sym = sym;
    $("stkSym").textContent = short(sym); $("stkName").textContent = sym; $("stkPx").textContent = ""; $("stkChg").textContent = "";
    if (scroll) $("charts").scrollIntoView({ block: "start" });
    if (!stk) return;
    $("stockChart").classList.add("loading");
    try {
      const d = await api("/api/bars/" + encodeURIComponent(sym) + "?period=2y&interval=" + S.interval);
      if (S.sym !== sym) return;
      S.stock = d; chartMessage("stockChart", null); renderStock();
      if ($("optVP").checked) loadSetup(sym);
    } catch (e) {
      S.stock = null; chartMessage("stockChart", e.message); $("ohlc").textContent = ""; $("ruleState").textContent = "";
    }
    $("stockChart").classList.remove("loading");
  }
  function renderStock() {
    const d = S.stock; if (!d) return;
    const c = C(), bars = d.bars, n = bars.length, last = bars[n - 1], prev = bars[n - 2] || last, ch = (last.close / prev.close - 1) * 100;
    $("stkPx").textContent = money(last.close, d.currency);
    $("stkChg").textContent = pct(ch, 2) + (intraday() ? " last 30 min" : " on the day"); $("stkChg").className = "num " + dir(ch);
    $("stkAnalyze").hidden = false;
    candle.setData(bars.map(function (b) { return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }; }));
    const closes = bars.map(function (b) { return { time: b.time, value: b.close }; });
    line.setData(closes); area.setData(closes);
    vol.setData(bars.map(function (b) { return { time: b.time, value: b.volume, color: hexA(b.close >= b.open ? c.up : c.down, 0.35) }; }));
    s20.setData(sma(closes, 20)); s50.setData(sma(closes, 50));
    const pf = { type: "custom", formatter: function (v) { return money(v, d.currency); }, minMove: 0.01 };
    [candle, line, area, s20, s50].forEach(function (s) { s.applyOptions({ priceFormat: pf }); });
    showOhlc(last);
    renderStockView();
    stk.applyOptions({ timeScale: { timeVisible: intraday(), secondsVisible: false } });
    stk.timeScale().setVisibleRange({ from: bars[Math.max(0, n - visibleBars())].time, to: last.time });
    const pos = d.position;
    $("ruleState").textContent = pos
      ? "Holding " + pos.qty + " at " + money(pos.avg_price, d.currency) + " (" + pct((last.close / pos.avg_price - 1) * 100) + "). Auto-exit if the price falls below " + money(pos.stop_price, d.currency) + "."
      : "No open position in " + short(d.symbol) + ".";
  }
  function snapToBar(bars, iso) { let hit = null; for (const b of bars) { if (b.time <= iso) hit = b.time; else break; } return hit; }
  function renderStockView() {
    const d = S.stock; if (!d) return;
    const c = C(), sig = $("optSig").checked;
    candle.applyOptions({ visible: S.type === "candle" }); line.applyOptions({ visible: S.type === "line" }); area.applyOptions({ visible: S.type === "area" });
    s20.applyOptions({ visible: $("optSma20").checked }); s50.applyOptions({ visible: $("optSma50").checked }); vol.applyOptions({ visible: $("optVol").checked });
    [candle, line, area].forEach(function (s) { s.setMarkers([]); });
    plines.forEach(function (pl) { pl[0].removePriceLine(pl[1]); }); plines = [];
    const p = primary();
    let marks = [];
    if (sig && !intraday()) marks = d.orders.slice().reverse().map(function (o) {
      const t = snapToBar(d.bars, localISO(new Date(o.ts * 1000))); if (!t) return null;
      const auto = (o.reason || "").indexOf("Auto-exit") === 0;
      return o.side === "BUY"
        ? { time: t, position: "belowBar", color: c.up, shape: "arrowUp", text: "BUY " + o.qty }
        : { time: t, position: "aboveBar", color: c.down, shape: "arrowDown", text: auto ? "Auto-exit" : "SELL " + o.qty };
    }).filter(Boolean);
    const st = S.setup, vpOn = $("optVP").checked && st && st.symbol === d.symbol && st.poc;
    if (vpOn) {
      const line = function (price, color, style, title) { plines.push([p, p.createPriceLine({ price: price, color: color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title: title })]); };
      line(st.poc, c.accent, LWC.LineStyle.Solid, "POC");
      line(st.target, c.up, LWC.LineStyle.Dashed, "Target");
      line(st.stop, c.down, LWC.LineStyle.Dashed, "Setup stop");
      if (st.status === "entry") line(st.entry, c.ink, LWC.LineStyle.Dotted, "Entry");
      if (intraday()) {
        const times = new Set(d.bars.map(function (b) { return b.time; }));
        if (times.has(st.swing_low.time)) marks.push({ time: st.swing_low.time, position: "belowBar", color: c.down, shape: "circle", text: "Swing low" });
        if (times.has(st.swing_high.time)) marks.push({ time: st.swing_high.time, position: "aboveBar", color: c.up, shape: "circle", text: "Swing high" });
        if (st.status === "entry" && times.has(st.signal_time)) marks.push({ time: st.signal_time, position: "belowBar", color: c.up, shape: "arrowUp", text: "Entry" });
      }
    }
    marks.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    p.setMarkers(marks);
    requestAnimationFrame(drawVP);
    if (!sig) return;
    if (d.position) {
      plines.push([p, p.createPriceLine({ price: d.position.avg_price, color: c.faint, lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: true, title: "Entry" })]);
      plines.push([p, p.createPriceLine({ price: d.position.stop_price, color: c.down, lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: "Auto-exit −" + d.stop_loss_pct + "%" })]);
    }
  }
  const SETUP_LABEL = { no_trend: "No uptrend", no_swing: "No swing yet", waiting: "Waiting for pullback", testing: "Testing POC", entry: "Entry signal", invalid: "Setup failed", error: "No data" };
  async function loadSetup(sym) {
    S.setup = null; renderSetupBadge();
    let st;
    try { st = await api("/api/setup/" + encodeURIComponent(sym)); }
    catch (e) { st = { symbol: sym, status: "error", message: e.message }; }
    if (S.sym !== sym) return;
    S.setup = st; renderSetupBadge(); renderStockView();
  }
  function renderSetupBadge() {
    const box = $("stkSetup"); box.textContent = "";
    box.hidden = !$("optVP").checked || !S.sym;
    if (box.hidden) return;
    const st = S.setup;
    if (!st) { box.appendChild(el("span", "muted", "Checking the volume profile setup…")); return; }
    box.appendChild(el("span", "vpchip " + st.status, SETUP_LABEL[st.status] || st.status));
    box.appendChild(el("span", "", st.message || ""));
    if (st.poc) {
      const cur = st.currency || (S.stock && S.stock.currency);
      box.appendChild(el("span", "levels", "POC " + money(st.poc, cur) + " · stop " + money(st.stop, cur) + " · target " + money(st.target, cur) +
        (st.reward_risk ? " · reward/risk " + st.reward_risk.toFixed(1) : "")));
      if (!intraday()) { const b = el("button", "linkbtn", "See the swing on 30-min"); b.type = "button"; b.addEventListener("click", function () { $("ivSeg").querySelector('[data-v="30m"]').click(); }); box.appendChild(b); }
    }
  }
  function drawVP() {
    const cv = $("vpCanvas"), box = $("stockChart");
    const w = box.clientWidth, h = box.clientHeight, dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); cv.style.width = w + "px"; cv.style.height = h + "px";
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    const st = S.setup;
    if (!stk || !S.stock || !st || !st.profile || !$("optVP").checked || st.symbol !== S.stock.symbol) return;
    const p = primary(), c = C(), paneW = w - stk.priceScale("right").width();
    const maxVol = Math.max.apply(null, st.profile.map(function (b) { return b.volume; })) || 1, maxLen = paneW * 0.22;
    st.profile.forEach(function (b) {
      const y1 = p.priceToCoordinate(b.high), y2 = p.priceToCoordinate(b.low);
      if (y1 === null || y2 === null) return;
      const len = b.volume / maxVol * maxLen, poc = st.poc >= b.low && st.poc < b.high;
      ctx.fillStyle = hexA(poc ? c.accent : c.s1, poc ? 0.6 : 0.22);
      ctx.fillRect(paneW - len, Math.min(y1, y2) + 0.5, len, Math.max(1, Math.abs(y2 - y1) - 1));
    });
  }
  seg("typeSeg", function (v) { S.type = v; renderStockView(); });
  seg("srangeSeg", function (v) { S.srange = +v; if (S.stock) { const b = S.stock.bars; stk.timeScale().setVisibleRange({ from: b[Math.max(0, b.length - visibleBars())].time, to: b[b.length - 1].time }); } });
  seg("ivSeg", function (v) { S.interval = v; if (S.sym) loadStock(S.sym, false); });
  $("optVP").addEventListener("change", function () { if (this.checked && S.sym && !S.setup) loadSetup(S.sym); renderSetupBadge(); renderStockView(); });
  ["optSma20", "optSma50", "optVol", "optSig"].forEach(function (id) { $(id).addEventListener("change", renderStockView); });
  $("stkAnalyze").addEventListener("click", function () { if (S.sym) prefill(S.sym); });
  $("search").addEventListener("submit", function (e) { e.preventDefault(); const v = $("q").value.trim().toUpperCase(); if (v) loadStock(v, true); });

  /* ---------- theme changes ---------- */
  function retheme() {
    if (!LWC) return;
    const c = C();
    perf.applyOptions(chartOpts(c));
    sSeries.applyOptions({ lineColor: c.s1, topColor: hexA(c.s1, 0.28), bottomColor: hexA(c.s1, 0.02) }); bSeries.applyOptions({ color: c.s2 });
    stockColors(); renderStock(); requestAnimationFrame(drawVP);
  }
  try { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme); } catch (e) { /* old browser */ }
  new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if (window.ResizeObserver) new ResizeObserver(function () { drawYearly(); }).observe($("yearWrap"));

  /* ---------- boot ---------- */
  function boot2() {
    S.booted = true;
    const lists = S.data.watchlists;
    if (lists.length && lists[0].symbols.length && !S.sym) loadStock(lists[0].symbols[0], false);
    (async function () {
      if (S.wl !== null) await loadPerf(S.wl, true);
      for (const w of lists) if (w.id !== S.wl) await loadPerf(w.id, false);
    })();
  }
  if (!LWC) {
    ["perfChart", "stockChart"].forEach(function (id) { chartMessage(id, "The chart library didn't load. Reload the page."); });
  } else {
    initPerf(); initStock();
  }
  loadStrip(); setInterval(loadStrip, 5 * 60 * 1000);
  refresh();
})();
