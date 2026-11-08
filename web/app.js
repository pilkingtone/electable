/* Electable front end. Reads data/forecast.json, data/history.json, data/calibration.json.
 * URL hash = panel tokens + chamber, e.g. #seats-races-history-senate (like electit's scheme; _ also accepted). */
(function () {
  "use strict";

  const CHAMBERS = ["senate", "house", "governor"];
  const VIEWS = [...CHAMBERS, "news"];
  const PANELS = ["seats", "races", "polls", "tipping", "history", "errors", "electit"];
  const DEFAULT_PANELS = ["seats", "races", "polls", "tipping", "history", "errors", "electit"];
  const STATE_NAMES = {AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",CT:"Connecticut",DE:"Delaware",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming"};
  const CHAMBER_LABEL = {senate: "Senate", house: "House", governor: "Governors"};
  const UNIT = {senate: "seats", house: "seats", governor: "governorships"};

  const state = {dashIdx: {}, results: null, news: null, newsSource: "", historyDays: 30, electitSet: null, chamber: "senate", panels: new Set(DEFAULT_PANELS), forecast: null, history: [], calibration: null};
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const pct = (p) => p == null ? "—" : p > 0.99 ? ">99%" : p < 0.01 ? "<1%" : Math.round(p * 100) + "%";
  const margin = (m) => m == null ? "—" : (m >= 0 ? "D+" : "R+") + Math.abs(m).toFixed(1);
  const fmtDate = (s) => new Date(s + "T12:00:00").toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});
  const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"}); };
  const resultOf = (r) => (state.results && state.results.races[r.id]) || null;
  const raceLabel = (r) => r.district != null ? `${r.state}-${String(r.district).padStart(2, "0")}` : r.state;
  const raceById = (id) => {
    for (const ch of CHAMBERS) {
      const c = state.forecast.chambers[ch];
      const r = c && c.races && c.races.find((x) => x.id === id);
      if (r) return r;
    }
    return null;
  };

  /* ---------------- routing ---------------- */
  // "#original-..." shows the forecast as published on Election Day (data/archive/).
  const ARCHIVE = /(^#|[_-])original([_-]|$)/.test(location.hash);
  function readHash() {
    const toks = location.hash.replace(/^#/, "").split(/[_-]/).filter(Boolean);
    const ch = toks.find((t) => VIEWS.includes(t));
    const ps = toks.filter((t) => PANELS.includes(t));
    if (ch) state.chamber = ch;
    if (ps.length) state.panels = new Set(ps);
  }
  function writeHash() {
    if (state.chamber === "news") { history.replaceState(null, "", "#news"); return; }
    const toks = (ARCHIVE ? ["original"] : []).concat(PANELS.filter((p) => state.panels.has(p)), state.chamber);
    history.replaceState(null, "", "#" + toks.join("-"));
  }

  /* ---------------- tooltip ---------------- */
  const tip = document.getElementById("tooltip");
  function showTip(evt, rows) {
    tip.replaceChildren();
    rows.forEach(([value, label, color]) => {
      const row = document.createElement("div");
      row.className = "row";
      if (color) {
        const sw = document.createElement("span");
        sw.className = "swatch"; sw.style.background = color;
        row.appendChild(sw);
      }
      const v = document.createElement("span"); v.className = "tv"; v.textContent = value;
      row.appendChild(v);
      if (label) { const l = document.createElement("span"); l.className = "tl"; l.textContent = label; row.appendChild(l); }
      tip.appendChild(row);
    });
    tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  function focusTip(el, rows) {
    const b = el.getBoundingClientRect();
    showTip({clientX: b.right, clientY: b.top}, rows);
  }

  /* ---------------- helpers ---------------- */
  const width = (el) => Math.max(320, el.clientWidth || 640);
  function svgIn(el, h) {
    el.replaceChildren();
    const w = width(el);
    return d3.select(el).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
  }
  function message(el, text) {
    el.replaceChildren();
    const p = document.createElement("p"); p.className = "sub"; p.textContent = text; el.appendChild(p);
  }
  const partyColor = (m) => m >= 0 ? css("--dem") : css("--rep");

  /* ---------------- header + tiles ---------------- */
  function renderMeta() {
    const f = state.forecast, el = document.getElementById("meta");
    el.replaceChildren();
    const days = Math.ceil((new Date(f.election_date + "T00:00:00") - new Date()) / 864e5);
    const a = document.createElement("div");
    a.append(state.results ? (ARCHIVE ? "Published " : f.model_version ? `Forecast (model ${f.model_version}) on data from ` : "Final forecast ") : "Updated ");
    // generated_at is the build time in UTC; show it in the visitor's own time zone.
    const built = f.generated_at ? new Date(f.generated_at) : null;
    const s = document.createElement("strong");
    s.textContent = built && !isNaN(built)
      ? built.toLocaleString(undefined, {month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"})
      : fmtDate(f.as_of);
    if (built && !isNaN(built)) { const t = document.createElement("time"); t.dateTime = f.generated_at; t.appendChild(s); a.appendChild(t); }
    else a.appendChild(s);
    const b = document.createElement("div");
    b.textContent = state.results ? `Results updated ${fmtTime(state.results.generated_at)}`
      : days > 0 ? `${days} day${days === 1 ? "" : "s"} until Nov 3, 2026` : "Election day: Nov 3, 2026";
    el.append(a, b);
    if (f.national) {
      const c = document.createElement("div");
      c.textContent = `National environment ${margin(f.national.mean)} (±${f.national.sd.toFixed(1)})`;
      el.appendChild(c);
    }
    document.getElementById("demo-banner").hidden = !f.demo;
  }

  function tile(parent, {label, value, note, key, lead}) {
    const t = document.createElement("div");
    t.className = "tile" + (lead ? " lead-" + lead : "");
    const l = document.createElement("div"); l.className = "label";
    if (key) { const k = document.createElement("span"); k.className = "key"; k.style.background = key; l.appendChild(k); }
    l.append(label);
    const v = document.createElement("div"); v.className = "value"; v.textContent = value;
    t.append(l, v);
    if (note) { const n = document.createElement("div"); n.className = "note"; n.textContent = note; t.appendChild(n); }
    parent.appendChild(t);
  }

  function renderTiles(c) {
    const el = document.getElementById("tiles"); el.replaceChildren();
    const ch = state.chamber, s = c.dem_seats;
    const pD = c.p_dem_control, pR = 1 - pD;
    const what = ch === "governor" ? "a majority of governorships" : `the ${CHAMBER_LABEL[ch]}`;
    const F = state.results ? "Forecast: " : "";
    tile(el, {label: `${F}Democrats win ${what}`, value: pct(pD), key: css("--dem"), lead: pD >= pR ? "dem" : null,
              note: `Needs ${c.threshold} ${UNIT[ch]}`});
    tile(el, {label: `${F}Republicans ${ch === "governor" ? "keep a majority" : "control"}`, value: pct(pR), key: css("--rep"),
              lead: pR > pD ? "rep" : null,
              note: ch === "senate" ? "Includes a 50–50 split (VP breaks ties)" : ch === "house" ? "Needs 218 seats" : "Includes a 25–25 split"});
    tile(el, {label: `${F}Expected Democratic ${UNIT[ch]}`, value: s.mean.toFixed(1),
              note: `80% range ${s.lo80}–${s.hi80}` + (c.base_dem ? ` · incl. ${c.base_dem} not up` : "")});
    const j = state.forecast.joint;
    if (j && ch !== "governor") {
      const outcomes = [["Democrats control both chambers", j.dem_both], ["Republicans control both chambers", j.rep_both],
        ["Split: Dem House, Rep Senate", j.dem_house_rep_senate], ["Split: Rep House, Dem Senate", j.dem_senate_rep_house]];
      const top = outcomes.reduce((a, b) => b[1] > a[1] ? b : a);
      tile(el, {label: `${F}Most likely Congress: ${top[0]}`, value: pct(top[1]),
                note: outcomes.filter((o) => o !== top).map((o) => `${o[0].replace(" chambers", "")} ${pct(o[1])}`).join(" · ")});
    }
  }

  /* ---------------- seats histogram ---------------- */
  function renderSeats(c) {
    const el = document.getElementById("seats-chart");
    const thr = c.threshold;
    const dist = c.dem_seats.dist.filter((d) => d.p >= 0.0005);
    document.getElementById("seats-sub").textContent =
      `Share of ${state.forecast.n_sims.toLocaleString()} simulations ending with each number of Democratic ${UNIT[state.chamber]}. Bars at or right of the line = Democratic control.`;
    const h = 260, m = {t: 24, r: 12, b: 34, l: 40};
    const svg = svgIn(el, h), w = +svg.attr("width");
    const lo = d3.min(dist, (d) => d.seats), hi = d3.max(dist, (d) => d.seats);
    const x = d3.scaleBand().domain(d3.range(Math.min(lo, thr - 1), Math.max(hi, thr) + 1)).range([m.l, w - m.r]).paddingInner(0.12);
    const pmax = d3.max(dist, (d) => d.p);
    const y = d3.scaleLinear().domain([0, pmax * 1.1]).nice().range([h - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(4).tickSize(-(w - m.l - m.r)).tickFormat(d3.format(pmax < 0.06 ? ".1%" : ".0%")))
      .call((g) => g.selectAll("text").attr("x", -6)).call((g) => g.select(".domain").remove());
    const step = Math.ceil(x.domain().length / Math.max(4, Math.floor((w - m.l) / 46)));
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`)
      .call(d3.axisBottom(x).tickValues(x.domain().filter((v) => v % step === 0)).tickSizeOuter(0));
    const bw = x.bandwidth();
    const bars = svg.append("g").selectAll("g").data(dist).join("g");
    bars.append("path").attr("class", "mark")
      .attr("d", (d) => roundedTop(x(d.seats), y(d.p), bw, y(0) - y(d.p), Math.min(4, bw / 2)))
      .attr("fill", (d) => d.seats >= thr ? css("--dem") : css("--rep"));
    bars.append("rect").attr("class", "hit").attr("x", (d) => x(d.seats) - 1).attr("width", bw + 2)
      .attr("y", m.t).attr("height", h - m.b - m.t).attr("tabindex", 0)
      .on("pointermove", (e, d) => showTip(e, [[pct(d.p), `${d.seats} Dem ${UNIT[state.chamber]}`, d.seats >= thr ? css("--dem") : css("--rep")]]))
      .on("pointerleave blur", hideTip)
      .on("focus", function (e, d) { focusTip(this, [[pct(d.p), `${d.seats} Dem ${UNIT[state.chamber]}`]]); });
    const tx = x(thr) - (x.step() * x.paddingInner()) / 2;
    svg.append("line").attr("x1", tx).attr("x2", tx).attr("y1", m.t - 8).attr("y2", h - m.b).attr("stroke", css("--ink-2"));
    svg.append("text").attr("x", tx + 4).attr("y", m.t - 10).attr("class", "label-strong").text(`Control: ${thr}`);
  }
  function roundedTop(x, y, w, h, r) {
    if (h <= 0) return "";
    r = Math.min(r, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  /* ---------------- race rows ---------------- */
  function filteredRaces(c) {
    const q = document.getElementById("race-search").value.trim().toLowerCase();
    const all = document.getElementById("show-all").checked;
    return c.races
      .filter((r) => all || (r.p_dem > 0.02 && r.p_dem < 0.98) || q)
      .filter((r) => !q || [r.state, STATE_NAMES[r.state] || "", raceLabel(r), r.dem || "", r.rep || ""].join(" ").toLowerCase().includes(q))
      .sort((a, b) => Math.abs(a.p_dem - 0.5) - Math.abs(b.p_dem - 0.5));
  }

  function renderRaces(c) {
    const el = document.getElementById("races-chart");
    document.getElementById("races-sub").textContent = "Dot = forecast margin; bar = 80% range"
      + (state.results ? "; black diamond = actual result." : ". Sorted by closeness.");
    const races = filteredRaces(c);
    if (!races.length) { message(el, "No races match. Tick “Show safe seats” to see every race."); renderRaceTable([]); return; }
    const w = width(el), narrow = w < 520;
    // On phones: state codes only on the left, and the forecast and result stacked on the right,
    // so the plot keeps most of the width.
    const rowH = 30, m = {t: 26, r: state.results && !narrow ? 130 : 64, b: 8};
    const labelW = narrow ? 64 : 210;
    const tickCount = (pw) => Math.max(2, Math.min(8, Math.floor(pw / 56)));
    const h = m.t + rowH * races.length + m.b;
    const svg = svgIn(el, h);
    const lim = Math.min(45, Math.max(15, d3.max(races, (r) => Math.max(Math.abs(r.lo80), Math.abs(r.hi80))) || 15));
    const x = d3.scaleLinear().domain([-lim, lim]).range([labelW, w - m.r]).clamp(true);
    svg.append("g").attr("class", "grid").attr("transform", `translate(0,${m.t})`)
      .call(d3.axisTop(x).ticks(tickCount(w - m.r - labelW)).tickSize(-(h - m.t - m.b)).tickFormat((v) => v === 0 ? "Even" : (v > 0 ? "D+" : "R+") + Math.abs(v)))
      .call((g) => g.select(".domain").remove());
    svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.t).attr("y2", h - m.b).attr("stroke", css("--axis"));
    const row = svg.append("g").selectAll("g").data(races).join("g").attr("class", "row")
      .attr("transform", (d, i) => `translate(0,${m.t + i * rowH})`);
    row.append("rect").attr("class", "rowbg").attr("x", 0).attr("width", w).attr("height", rowH).attr("rx", 4);
    row.append("text").attr("x", 4).attr("y", 13).attr("class", "label-strong").text((d) => raceLabel(d) + (d.uncontested ? " (uncontested)" : ""));
    row.append("text").attr("x", 4).attr("y", 25).style("font-size", "10px")
      .text((d) => w < 520 ? "" : `${d.dem || "Dem"}${d.dem_label === "I" ? " (I)" : ""} vs ${d.rep || "Rep"}`);
    row.append("rect").attr("class", "mark").attr("x", (d) => x(d.lo80)).attr("y", rowH / 2 - 3)
      .attr("width", (d) => Math.max(2, x(d.hi80) - x(d.lo80))).attr("height", 6).attr("rx", 3)
      .attr("fill", (d) => partyColor(d.median)).attr("opacity", 0.35);
    row.append("circle").attr("class", "mark").attr("cx", (d) => x(d.median)).attr("cy", rowH / 2).attr("r", 5)
      .attr("fill", (d) => partyColor(d.median)).attr("stroke", css("--surface")).attr("stroke-width", 2);
    const stacked = narrow && state.results;
    row.append("text").attr("x", w - 4).attr("y", stacked ? rowH / 2 - 1 : rowH / 2 + 4).attr("text-anchor", "end").attr("class", "label-strong")
      .text((d) => d.p_dem >= 0.5 ? `D ${pct(d.p_dem)}` : `R ${pct(1 - d.p_dem)}`);
    // Results: a diamond at the actual margin, and whether the favorite won.
    row.filter((d) => resultOf(d) && resultOf(d).margin != null).append("path")
      .attr("transform", (d) => `translate(${x(resultOf(d).margin)},${rowH / 2})`)
      .attr("d", d3.symbol(d3.symbolDiamond, 70)).attr("fill", css("--ink")).attr("stroke", css("--surface")).attr("stroke-width", 1.5);
    row.filter((d) => resultOf(d) && resultOf(d).called).append("text")
      .attr("x", stacked ? w - 4 : w - 58).attr("y", stacked ? rowH / 2 + 11 : rowH / 2 + 4).attr("text-anchor", "end")
      .style("font-size", stacked ? "10px" : null)
      .text((d) => (resultOf(d).winner === "D" ? (d.dem_label === "I" ? "I" : "D") : resultOf(d).winner) + " won");
    row.append("rect").attr("class", "hit").attr("width", w).attr("height", rowH).attr("tabindex", 0)
      .on("pointermove", (e, d) => showTip(e, raceTip(d))).on("pointerleave blur", hideTip)
      .on("focus", function (e, d) { focusTip(this, raceTip(d)); });
    renderRaceTable(races);
  }
  function raceTip(d) {
    const rows = [[`${raceLabel(d)} — ${STATE_NAMES[d.state] || d.state}`, ""],
      [pct(d.p_dem), `${d.dem || "Democrat"} wins`, css("--dem")],
      [pct(1 - d.p_dem), `${d.rep || "Republican"} wins`, css("--rep")],
      [margin(d.median), `forecast · 80%: ${margin(d.lo80)} to ${margin(d.hi80)}`]];
    if (d.poll_n) rows.push([margin(d.poll_avg), `poll avg (${d.poll_n} polls, ${Math.round(d.w_polls * 100)}% weight)`]);
    rows.push([margin(d.prior), "fundamentals prior"]);
    const ex = electitFor(d);
    if (ex) rows.push([ex.p_dem != null ? pct(ex.p_dem) : shareText(ex.dem_share), `ElectIt (Harvard), ${state.electitSet}`]);
    (d.ratings || []).forEach((r) => rows.push([r.rating, r.source]));
    const res = resultOf(d);
    if (res) rows.push([res.margin != null ? margin(res.margin) : (res.called ? `${res.winner} won` : "—"),
      res.called ? `result: ${res.winner_name || res.winner} won` : "result: not yet called"]);
    return rows;
  }
  function renderRaceTable(races) {
    const t = d3.select("#races-table"); t.selectAll("*").remove();
    const cols = [["Race", (r) => raceLabel(r)], ["Democrat", (r) => (r.dem || "—") + (r.dem_label === "I" ? " (I)" : "")],
      ["Republican", (r) => r.rep || "—"], ["Held by", (r) => r.holder], ["P(Dem)", (r) => pct(r.p_dem), 1],
      ["Forecast", (r) => margin(r.median), 1], ["80% range", (r) => `${margin(r.lo80)} to ${margin(r.hi80)}`, 1],
      ["Poll avg", (r) => r.poll_n ? `${margin(r.poll_avg)} (${r.poll_n})` : "—", 1], ["Prior", (r) => margin(r.prior), 1], ["ElectIt P(Dem)", (r) => { const e = electitFor(r); return e ? (e.p_dem != null ? pct(e.p_dem) : shareText(e.dem_share)) : "—"; }, 1],
      ["Ratings", (r) => (r.ratings || []).map((x) => `${x.source.split(" ")[0]}: ${x.rating}`).join("; ") || "—"]];
    t.append("thead").append("tr").selectAll("th").data(cols).join("th").attr("class", (c) => c[2] ? "num" : null).text((c) => c[0]);
    t.append("tbody").selectAll("tr").data(races).join("tr").selectAll("td")
      .data((r) => cols.map((c) => [c[1](r), c[2]])).join("td").attr("class", (d) => d[1] ? "num" : null).text((d) => d[0]);
  }

  /* ---------------- polls vs model ---------------- */
  function renderPolls(c) {
    const el = document.getElementById("polls-chart");
    const races = c.races.filter((r) => r.poll_n > 0).sort((a, b) => a.median - b.median);
    if (!races.length) { message(el, "No race-level polls are available for this chamber yet."); return; }
    const rowH = 24, m = {t: 26, r: 16, b: 8}, w = width(el), labelW = 64;
    const h = m.t + rowH * races.length + m.b, svg = svgIn(el, h);
    const ext = d3.max(races, (r) => Math.max(Math.abs(r.poll_avg), Math.abs(r.median))) || 10;
    const x = d3.scaleLinear().domain([-Math.min(45, ext + 3), Math.min(45, ext + 3)]).range([labelW, w - m.r]).clamp(true);
    svg.append("g").attr("class", "grid").attr("transform", `translate(0,${m.t})`)
      .call(d3.axisTop(x).ticks(Math.max(2, Math.min(8, Math.floor((w - m.r - labelW) / 56)))).tickSize(-(h - m.t - m.b)).tickFormat((v) => v === 0 ? "Even" : (v > 0 ? "D+" : "R+") + Math.abs(v)))
      .call((g) => g.select(".domain").remove());
    svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.t).attr("y2", h - m.b).attr("stroke", css("--axis"));
    const row = svg.append("g").selectAll("g").data(races).join("g").attr("class", "row")
      .attr("transform", (d, i) => `translate(0,${m.t + i * rowH})`);
    row.append("rect").attr("class", "rowbg").attr("width", w).attr("height", rowH).attr("rx", 4);
    row.append("text").attr("x", 4).attr("y", rowH / 2 + 4).attr("class", "label-strong").text(raceLabel);
    row.append("line").attr("x1", (d) => x(d.poll_avg)).attr("x2", (d) => x(d.median)).attr("y1", rowH / 2).attr("y2", rowH / 2)
      .attr("stroke", css("--muted")).attr("stroke-width", 2);
    row.append("circle").attr("cx", (d) => x(d.poll_avg)).attr("cy", rowH / 2).attr("r", 5)
      .attr("fill", css("--surface")).attr("stroke", (d) => partyColor(d.poll_avg)).attr("stroke-width", 2);
    row.append("circle").attr("cx", (d) => x(d.median)).attr("cy", rowH / 2).attr("r", 5)
      .attr("fill", (d) => partyColor(d.median)).attr("stroke", css("--surface")).attr("stroke-width", 2);
    row.append("rect").attr("class", "hit").attr("width", w).attr("height", rowH).attr("tabindex", 0)
      .on("pointermove", (e, d) => showTip(e, [[raceLabel(d), ""], [margin(d.poll_avg), `polling average (${d.poll_n} polls, last ${d.last_poll})`],
        [margin(d.median), "Electable forecast"], [Math.round(d.w_polls * 100) + "%", "weight given to polls"]]))
      .on("pointerleave blur", hideTip);
  }

  /* ---------------- tipping point ---------------- */
  function renderTipping(c) {
    const el = document.getElementById("tipping-chart");
    if (!c.tipping || !c.tipping.length) { message(el, "Tipping-point analysis is not available for this view."); return; }
    const data = c.tipping.slice(0, 12), rowH = 26, m = {t: 4, r: 52, b: 4}, w = width(el), labelW = w > 520 ? 170 : 70;
    const h = m.t + data.length * rowH + m.b, svg = svgIn(el, h);
    const x = d3.scaleLinear().domain([0, d3.max(data, (d) => d.p)]).range([labelW, w - m.r]);
    const row = svg.selectAll("g").data(data).join("g").attr("transform", (d, i) => `translate(0,${m.t + i * rowH})`);
    row.append("text").attr("x", 4).attr("y", rowH / 2 + 4).attr("class", "label-strong").text((d) => {
      const r = raceById(d.id); return r ? `${raceLabel(r)} ${STATE_NAMES[r.state] && w > 520 ? "· " + STATE_NAMES[r.state] : ""}` : d.id;
    });
    row.append("path").attr("d", (d) => roundedRight(labelW + 4, 5, x(d.p) - labelW - 4, rowH - 10, 4))
      .attr("fill", (d) => { const r = raceById(d.id); return r ? partyColor(r.median) : css("--muted"); });
    row.append("text").attr("x", (d) => x(d.p) + 6).attr("y", rowH / 2 + 4).text((d) => pct(d.p));
  }
  function roundedRight(x, y, w, h, r) {
    if (w <= 0) return ""; r = Math.min(r, w, h / 2);
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  }

  /* ---------------- history ---------------- */
  function renderHistory() {
    const el = document.getElementById("history-chart"), ch = state.chamber;
    document.querySelectorAll("#history-range button").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.days === state.historyDays));
    const all = state.history.filter((h) => h[ch] && h[ch].p_dem != null).map((h) => ({date: new Date(h.date + "T12:00:00"), ...h[ch]}));
    const end = all.length ? all[all.length - 1].date : new Date();
    const start = new Date(end.getTime() - state.historyDays * 864e5);
    const pts = all.filter((d) => d.date >= start);
    const short = all.length && all[0].date > start;
    document.getElementById("history-sub").textContent = "Chance Democrats win control, by forecast date. Earlier points re-run the model with only the polls published by that date."
      + (short ? ` History starts ${all[0].date.toLocaleDateString(undefined, {month: "short", day: "numeric"})}.` : "");
    if (pts.length < 2) { message(el, "Not enough history for this range yet. It builds up as the forecast re-runs twice a day."); return; }
    const h = 240, m = {t: 16, r: 56, b: 30, l: 40}, svg = svgIn(el, h), w = +svg.attr("width");
    const x = d3.scaleTime().domain(d3.extent(pts, (d) => d.date)).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).tickValues([0, 0.25, 0.5, 0.75, 1]).tickSize(-(w - m.l - m.r)).tickFormat(d3.format(".0%")))
      .call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(Math.min(state.historyDays <= 7 ? 7 : 6, pts.length)).tickFormat(d3.timeFormat("%b %-d")).tickSizeOuter(0));
    svg.append("path").datum(pts).attr("fill", "none").attr("stroke", css("--dem")).attr("stroke-width", 2)
      .attr("d", d3.line().x((d) => x(d.date)).y((d) => y(d.p_dem)));
    svg.append("path").datum(pts).attr("fill", "none").attr("stroke", css("--rep")).attr("stroke-width", 2)
      .attr("d", d3.line().x((d) => x(d.date)).y((d) => y(1 - d.p_dem)));
    const last = pts[pts.length - 1];
    svg.append("text").attr("x", x(last.date) + 6).attr("y", y(last.p_dem) + 4).attr("class", "label-strong").text("Dem " + pct(last.p_dem));
    svg.append("text").attr("x", x(last.date) + 6).attr("y", y(1 - last.p_dem) + 4).attr("class", "label-strong").text("Rep " + pct(1 - last.p_dem));
    const cross = svg.append("line").attr("stroke", css("--muted")).attr("y1", m.t).attr("y2", h - m.b).style("display", "none");
    svg.append("rect").attr("class", "hit").attr("x", m.l).attr("y", m.t).attr("width", w - m.l - m.r).attr("height", h - m.t - m.b)
      .on("pointermove", function (e) {
        const [mx] = d3.pointer(e, this), t = x.invert(mx);
        const d = pts[d3.leastIndex(pts, (p) => Math.abs(p.date - t))];
        cross.attr("x1", x(d.date)).attr("x2", x(d.date)).style("display", null);
        showTip(e, [[d.date.toLocaleDateString(), ""], [pct(d.p_dem), "Democratic control", css("--dem")],
          [pct(1 - d.p_dem), "Republican control", css("--rep")], [d.mean.toFixed(1), `expected Dem ${UNIT[ch]} (80%: ${d.lo80}–${d.hi80})`]]);
      })
      .on("pointerleave", () => { cross.style("display", "none"); hideTip(); });
  }

  /* ---------------- historical error ---------------- */
  function renderErrors() {
    const el = document.getElementById("errors-chart"), ch = state.chamber, cal = state.calibration;
    const tiles = document.getElementById("error-tiles"); tiles.replaceChildren();
    if (!cal || !cal.polls || !cal.polls[ch]) { message(el, "Run `python -m electable calibrate` to compute historical errors."); return; }
    const s = cal.polls[ch];
    document.getElementById("errors-sub").textContent =
      `Final-three-week polling average minus the actual result, ${s.n_races} ${CHAMBER_LABEL[ch]} races, 1998–2022. Source: ${cal.source}.`;
    tile(tiles, {label: "Typical miss (RMSE)", value: s.rmse_total.toFixed(1) + " pts"});
    tile(tiles, {label: "Mean absolute miss", value: s.mean_abs_error.toFixed(1) + " pts"});
    tile(tiles, {label: "Poll leader won", value: pct(s.called_correctly)});
    const bt = cal.backtest;
    if (bt) {
      const fav = bt.reliability.find((r) => r.bin.startsWith("(0.7"));
      tile(tiles, {label: "Backtest calibration", value: `Brier ${bt.brier.toFixed(3)}`,
        note: fav ? `Races rated 70–90% were won ${pct(fav.observed)} of the time (${fav.n} races, all types)` : `${bt.n} races`});
    }
    const data = s.hist, h = 220, m = {t: 12, r: 12, b: 34, l: 40}, svg = svgIn(el, h), w = +svg.attr("width");
    const x = d3.scaleLinear().domain([data[0].x0, data[data.length - 1].x1]).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => d.n)]).nice().range([h - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(w - m.l - m.r))).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`)
      .call(d3.axisBottom(x).ticks(8).tickFormat((v) => v === 0 ? "0" : (v > 0 ? "D+" : "R+") + Math.abs(v)).tickSizeOuter(0));
    svg.append("text").attr("x", w - m.r).attr("y", h - 2).attr("text-anchor", "end").text("polls overstated → ");
    const bars = svg.append("g").selectAll("g").data(data).join("g");
    bars.append("path").attr("d", (d) => roundedTop(x(d.x0) + 1, y(d.n), Math.max(1, x(d.x1) - x(d.x0) - 2), y(0) - y(d.n), 3))
      .attr("fill", (d) => (d.x0 + d.x1) / 2 >= 0 ? css("--dem") : css("--rep"));
    bars.append("rect").attr("class", "hit").attr("x", (d) => x(d.x0)).attr("width", (d) => x(d.x1) - x(d.x0)).attr("y", m.t).attr("height", h - m.b - m.t)
      .on("pointermove", (e, d) => showTip(e, [[`${d.n} races`, `miss of ${d.x0} to ${d.x1} pts`]])).on("pointerleave", hideTip);
    renderBias();
  }
  function renderBias() {
    const el = document.getElementById("bias-chart"), cyc = state.calibration.cycles || [];
    if (!cyc.length) { el.replaceChildren(); return; }
    const h = 200, m = {t: 12, r: 12, b: 28, l: 40}, svg = svgIn(el, h), w = +svg.attr("width");
    const x = d3.scaleBand().domain(cyc.map((c) => c.cycle)).range([m.l, w - m.r]).paddingInner(0.25);
    const ext = d3.max(cyc, (c) => Math.abs(c.bias)) + 1;
    const y = d3.scaleLinear().domain([-ext, ext]).range([h - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(w - m.l - m.r)).tickFormat((v) => v === 0 ? "0" : (v > 0 ? "D+" : "R+") + Math.abs(v))).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${y(0)})`).call(d3.axisBottom(x).tickSize(0).tickPadding(0)).call((g) => g.selectAll("text").attr("y", (d) => {
      const c = cyc.find((q) => q.cycle === d); return c.bias >= 0 ? 12 : -6; }));
    const bw = x.bandwidth();
    const g = svg.append("g").selectAll("g").data(cyc).join("g");
    g.append("path").attr("d", (c) => c.bias >= 0
        ? roundedTop(x(c.cycle), y(c.bias), bw, y(0) - y(c.bias), 3)
        : roundedBottom(x(c.cycle), y(0), bw, y(c.bias) - y(0), 3))
      .attr("fill", (c) => c.bias >= 0 ? css("--dem") : css("--rep"));
    g.append("rect").attr("class", "hit").attr("x", (c) => x(c.cycle)).attr("width", bw).attr("y", m.t).attr("height", h - m.t - m.b)
      .on("pointermove", (e, c) => showTip(e, [[margin(c.bias), `${c.cycle} average miss (${c.races} races)`]])).on("pointerleave", hideTip);
  }
  function roundedBottom(x, y, w, h, r) {
    if (h <= 0) return ""; r = Math.min(r, h);
    return `M${x},${y}H${x + w}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x + r}Q${x},${y + h} ${x},${y + h - r}Z`;
  }

  /* ---------------- sources / warnings ---------------- */
  function renderSources() {
    const ul = document.getElementById("sources"); ul.replaceChildren();
    const src = (state.forecast.sources || []).concat([
      {name: "FiveThirtyEight pollster ratings & historical polls (CC BY 4.0)", url: "https://github.com/fivethirtyeight/data/tree/master/pollster-ratings"}]
      .concat(state.results ? [{name: `2026 results: ${state.results.source}`, url: "https://en.wikipedia.org/wiki/2026_United_States_elections"}] : [])
      .concat(state.forecast.electit && state.forecast.electit.sets.length
        ? [{name: "ElectIt: King, Ebanks & Katz, Harvard University", url: "https://gking.harvard.edu/electit/"}] : []));
    src.forEach((s) => {
      const li = document.createElement("li");
      if (s.url && /^https:\/\//.test(s.url)) { const a = document.createElement("a"); a.href = s.url; a.rel = "noopener"; a.textContent = s.name; li.appendChild(a); }
      else li.textContent = s.name;
      ul.appendChild(li);
    });
    const wEl = document.getElementById("warnings"); wEl.replaceChildren();
    const ws = state.forecast.warnings || [];
    if (ws.length) {
      const d = document.createElement("details"); const s = document.createElement("summary");
      s.textContent = `Data notes (${ws.length})`; d.appendChild(s);
      const ul2 = document.createElement("ul"); ul2.className = "warn-list";
      ws.forEach((w) => { const li = document.createElement("li"); li.textContent = w; ul2.appendChild(li); });
      d.appendChild(ul2); wEl.appendChild(d);
    }
  }

  /* ---------------- Harvard ElectIt ---------------- */
  const electitFor = (r) => (r.electit && state.electitSet && r.electit[state.electitSet]) || null;
  const shareText = (s) => s == null ? "—" : `${(s * 100).toFixed(1)}% D share`;

  function renderElectit(c) {
    const el = document.getElementById("electit-chart"), tiles = document.getElementById("electit-tiles");
    const meta = state.forecast.electit || {sets: []};
    const sel = document.getElementById("electit-set");
    tiles.replaceChildren();
    d3.select("#electit-table").selectAll("*").remove();
    if (!meta.sets.length) {
      sel.parentElement.hidden = true;
      message(el, "ElectIt's forecasts haven't been loaded yet. Add their published files to data/raw/electit/ (or their links to config/harvard.json) and rebuild.");
      return;
    }
    sel.parentElement.hidden = meta.sets.length < 2;
    if (!state.electitSet || !meta.sets.includes(state.electitSet)) state.electitSet = meta.primary_set || meta.sets[0];
    if (sel.options.length !== meta.sets.length) {
      sel.replaceChildren(...meta.sets.map((s) => { const o = document.createElement("option"); o.value = s; o.textContent = s; return o; }));
    }
    sel.value = state.electitSet;
    const sum = (c.electit_summary || {})[state.electitSet];
    const pts = c.races.map((r) => ({r, e: electitFor(r)})).filter((d) => d.e && d.e.p_dem != null);
    if (!sum || !pts.length) { message(el, `ElectIt has no win probabilities for the ${CHAMBER_LABEL[state.chamber]} in this set.`); return; }
    if (sum.p_dem_control != null) tile(tiles, {label: `ElectIt: Democrats win the ${CHAMBER_LABEL[state.chamber]}`, value: pct(sum.p_dem_control),
      note: `Electable: ${pct(c.p_dem_control)}`});
    const exp = sum.dem_seats ? sum.dem_seats.mean : sum.expected_dem;
    if (exp != null) tile(tiles, {label: `ElectIt expected Dem ${UNIT[state.chamber]}`, value: exp.toFixed(1),
      note: `Electable: ${c.dem_seats.mean.toFixed(1)}`});
    tile(tiles, {label: "Races with a different favorite", value: String(sum.disagree), note: `of ${sum.races} races both models cover`});
    tile(tiles, {label: "Average gap in win probability", value: Math.round(sum.mean_abs_diff * 100) + " pts"});
    if (meta.ensemble_weight > 0) tile(tiles, {label: "Blended into Electable", value: Math.round(meta.ensemble_weight * 100) + "%", note: `weight on ElectIt "${meta.primary_set}"`});

    const size = Math.min(width(el), 560), m = {t: 12, r: 16, b: 40, l: 48};
    el.replaceChildren();
    const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${size} ${size}`).attr("width", size).attr("height", size).style("max-width", size + "px");
    const x = d3.scaleLinear().domain([0, 1]).range([m.l, size - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([size - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(size - m.l - m.r)).tickFormat(d3.format(".0%"))).call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "grid").attr("transform", `translate(0,${size - m.b})`).call(d3.axisBottom(x).ticks(4).tickSize(-(size - m.t - m.b)).tickFormat(d3.format(".0%"))).call((g) => g.select(".domain").remove());
    svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", css("--axis"));
    svg.append("text").attr("x", (m.l + size - m.r) / 2).attr("y", size - 6).attr("text-anchor", "middle").text("ElectIt: chance Democrat wins");
    svg.append("text").attr("transform", `translate(12,${(m.t + size - m.b) / 2}) rotate(-90)`).attr("text-anchor", "middle").text("Electable: chance Democrat wins");
    const g = svg.append("g").selectAll("g").data(pts).join("g");
    g.append("circle").attr("cx", (d) => x(d.e.p_dem)).attr("cy", (d) => y(d.r.p_dem)).attr("r", state.chamber === "house" ? 4 : 5)
      .attr("fill", (d) => d.r.p_dem >= 0.5 ? css("--dem") : css("--rep")).attr("stroke", css("--surface")).attr("stroke-width", 2).attr("opacity", 0.85);
    g.append("circle").attr("class", "hit").attr("cx", (d) => x(d.e.p_dem)).attr("cy", (d) => y(d.r.p_dem)).attr("r", 12).attr("tabindex", 0)
      .on("pointermove", (e, d) => showTip(e, [[raceLabel(d.r), ""], [pct(d.r.p_dem), "Electable"], [pct(d.e.p_dem), "ElectIt"]]))
      .on("pointerleave blur", hideTip);
    const top = pts.slice().sort((a, b) => Math.abs(b.r.p_dem - b.e.p_dem) - Math.abs(a.r.p_dem - a.e.p_dem)).slice(0, 15);
    const t = d3.select("#electit-table");
    const cols = [["Race", (d) => raceLabel(d.r)], ["Electable P(Dem)", (d) => pct(d.r.p_dem), 1], ["ElectIt P(Dem)", (d) => pct(d.e.p_dem), 1],
      ["Gap", (d) => Math.round((d.r.p_dem - d.e.p_dem) * 100) + " pts", 1], ["Poll avg", (d) => d.r.poll_n ? margin(d.r.poll_avg) : "no polls", 1]];
    t.append("thead").append("tr").selectAll("th").data(cols).join("th").attr("class", (c) => c[2] ? "num" : null).text((c) => c[0]);
    t.append("tbody").selectAll("tr").data(top).join("tr").selectAll("td").data((d) => cols.map((c) => [c[1](d), c[2]]))
      .join("td").attr("class", (d) => d[1] ? "num" : null).text((d) => d[0]);
  }

  /* ---------------- post-election banner ---------------- */
  function renderBanner() {
    const R = state.results, f = state.forecast, banner = document.getElementById("results-banner");
    banner.hidden = !R;
    if (!R) return;
    banner.className = "banner banner-final";
    banner.replaceChildren();
    const view = (archive) => `#${archive ? "original-" : ""}${[...state.panels].join("-")}-${state.chamber}`;
    if (ARCHIVE) {
      banner.append(`You're viewing the forecast as originally published on Election Day, ${fmtTime(f.generated_at)}, before any polls closed. Results are added for comparison. `);
      const a = el("a", null, "Back to the current forecast."); a.href = view(false); banner.appendChild(a);
    } else if (f.model_version) {
      banner.append(`The election is over. Forecasts shown are from Electable's revised model (${f.model_version}), re-run on the polls and data available on ${fmtDate(f.as_of)}, with results added as races are called. `);
      const a = el("a", null, "See the forecast as originally published on Election Day."); a.href = view(true); banner.appendChild(a);
    } else {
      banner.append(`The election is over. Forecasts shown are Electable's final forecast, published ${fmtTime(f.generated_at)}, with results added as races are called.`);
    }
  }

  /* ---------------- results dashboard ---------------- */
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const partyCls = (p) => p === "D" ? "pd" : p === "R" ? "pr" : "po";
  const ago = (iso) => {
    const m = Math.round((Date.now() - new Date(iso)) / 6e4);
    return m < 60 ? `${Math.max(m, 1)} min ago` : m < 1440 ? `${Math.round(m / 60)} hr ago` : `${Math.round(m / 1440)} d ago`;
  };
  const placeLabel = (r) => r.district != null
    ? [STATE_NAMES[r.state] || r.state, r.district === 1 && /AL$/.test(r.id) ? "At-large" : `Dist. ${r.district}`]
    : [STATE_NAMES[r.state] || r.state, CHAMBER_LABEL[state.chamber].replace(/s$/, "")];

  function renderDash(c) {
    const R = state.results, rc = R && R.chambers[state.chamber], dash = document.getElementById("results-dash");
    dash.hidden = !rc;
    if (!rc) return;
    const ch = state.chamber, name = CHAMBER_LABEL[ch];

    // 1. Seat bar
    const bar = document.getElementById("dash-bar"); bar.replaceChildren();
    const h = el("h2", null, ch === "governor" ? "Governorships" : `${name} results`); bar.appendChild(h);
    const ctrlWord = (p) => p === "D" ? "Democrats" : "Republicans";
    bar.appendChild(el("div", "projection", rc.control
      ? `${ctrlWord(rc.control)} ${rc.control === rc.control_before ? "keep" : "gain"} ${ch === "governor" ? "a majority" : "control"}`
      : `${ch === "governor" ? "Majority" : "Control"} not yet decided`));
    const total = rc.total, uncalled = Math.max(0, total - rc.dem_won - rc.rep_won - rc.other_won);
    const sb = el("div", "seatbar"); sb.setAttribute("role", "img");
    sb.setAttribute("aria-label", `Democrats ${rc.dem_won}, Republicans ${rc.rep_won}, ${uncalled} not yet called, ${rc.threshold} needed for ${ch === "governor" ? "a majority" : "control"}`);
    [["d", rc.dem_won, rc.dem_won], ["o", rc.other_won, ""], ["u", uncalled, ""], ["r", rc.rep_won, rc.rep_won]].forEach(([k, n, label]) => {
      if (!n) return; const sp = el("span", k, String(label)); sp.style.flex = `${n} 1 0`; sb.appendChild(sp);
    });
    const at = (100 * rc.threshold / total).toFixed(2);
    const tick = el("i", "ctrl"); tick.style.left = `calc(${at}% - 1px)`; sb.appendChild(tick);
    const wrap = el("div", "seatwrap");
    const cl = el("span", "ctrl-label", `${rc.threshold} for ${ch === "governor" ? "majority" : "control"}`); cl.style.left = `${at}%`;
    wrap.append(cl, sb); bar.appendChild(wrap);
    const lg = el("div", "seat-legend");
    const side = (who, p, cls) => {
      const d = el("div", cls); d.appendChild(el("b", null, who));
      d.append(`${rc.up[p]} up for election`); d.appendChild(document.createElement("br"));
      const net = rc.gained[p] - rc.gained[p === "D" ? "R" : "D"];
      d.append(net >= 0 ? `Gained: ${net}` : `Lost: ${-net}`);
      return d;
    };
    lg.append(side("Democrats", "D", ""), side("Republicans", "R", "right"));
    bar.appendChild(lg);
    if (uncalled) bar.appendChild(el("div", "race-foot", `${uncalled} not yet called (striped)` + (rc.other_won ? ` · ${rc.other_won} independent (purple)` : "")));

    // 2. Recent calls
    const calls = document.getElementById("dash-calls"); calls.replaceChildren();
    calls.appendChild(el("h2", null, "Recent calls"));
    const called = c.races.map((r) => ({r, res: resultOf(r)})).filter((x) => x.res && x.res.called && x.res.winner_name);
    const timed = called.filter((x) => x.res.called_at).sort((a, b) => b.res.called_at.localeCompare(a.res.called_at));
    const list = (timed.length ? timed : called.sort((a, b) => Math.abs(a.r.p_dem - 0.5) - Math.abs(b.r.p_dem - 0.5))).slice(0, 3);
    const ul = el("ul", "calls");
    list.forEach(({r, res}) => {
      const li = el("li"); const [place, sub] = placeLabel(r);
      const w = el("div", "where", r.state); w.appendChild(el("small", null, r.district != null ? sub : name.replace(/s$/, "")));
      const who = el("div", "who");
      const meta = el("small"); meta.append(el("span", null, "Projected winner"), el("span", null, res.called_at ? ago(res.called_at) : ""));
      const b = el("b"); b.append(`${res.winner_name} · `); b.appendChild(el("span", partyCls(res.winner), res.winner === "D" && r.dem_label === "I" ? "I" : res.winner));
      who.append(meta, b); li.append(w, who); ul.appendChild(li);
    });
    calls.appendChild(list.length ? ul : el("p", "sub", "No races called yet."));
    if (!timed.length && list.length) calls.appendChild(el("div", "race-foot", "Closest races already called when tracking began."));

    // 3. Race card with prev/next
    const card = document.getElementById("dash-race"); card.replaceChildren();
    const pool = c.races.filter((r) => !r.uncontested).map((r) => ({r, res: resultOf(r)}))
      .sort((a, b) => (a.res && a.res.called ? 1 : 0) - (b.res && b.res.called ? 1 : 0)
        || Math.abs(a.res && a.res.margin != null ? a.res.margin : a.r.median) - Math.abs(b.res && b.res.margin != null ? b.res.margin : b.r.median));
    if (!pool.length) { card.hidden = true; } else {
      card.hidden = false;
      const i = ((state.dashIdx[ch] || 0) % pool.length + pool.length) % pool.length;
      const {r, res} = pool[i];
      const nav = el("div", "race-nav");
      const prevB = el("button", null, "‹"), nextB = el("button", null, "›");
      prevB.type = nextB.type = "button"; prevB.setAttribute("aria-label", "Previous race"); nextB.setAttribute("aria-label", "Next race");
      prevB.onclick = () => { state.dashIdx[ch] = i - 1; renderDash(c); };
      nextB.onclick = () => { state.dashIdx[ch] = i + 1; renderDash(c); };
      const [place, sub] = placeLabel(r); const mid = el("div"); mid.append(el("b", null, place), el("small", null, sub));
      nav.append(prevB, mid, nextB); card.appendChild(nav);
      const t = el("table", "race-table");
      const hr = el("tr"); ["Candidate", "Party", "%", "Votes"].forEach((x, k) => { const th = el("th", k > 1 ? "num" : null, x); hr.appendChild(th); });
      t.appendChild(el("thead")).appendChild(hr);
      const tb = el("tbody");
      [[r.dem, r.dem_label || "D", res && res.dem_pct, res && res.dem_votes, "D"], [r.rep, "R", res && res.rep_pct, res && res.rep_votes, "R"]]
        .sort((a, b) => (b[2] || 0) - (a[2] || 0)).forEach(([nm, lbl, p, v, code]) => {
          const tr = el("tr"); const won = res && res.called && res.winner === code;
          tr.append(el("td", null, (nm || "—") + (won ? " ✓" : "")), el("td", partyCls(lbl === "I" ? "I" : code), lbl),
            el("td", "num", p != null ? `${p.toFixed(1)}%` : "—"), el("td", "num", v != null ? Math.round(v).toLocaleString() : "—"));
          tb.appendChild(tr);
        });
      t.appendChild(tb); card.appendChild(t);
      const fav = r.p_dem >= 0.5 ? `D ${pct(r.p_dem)}` : `R ${pct(1 - r.p_dem)}`;
      if (res && res.reporting != null) card.appendChild(el("div", "race-foot",
        `Expected vote reporting: ${Math.round(res.reporting)}%` + (res.round === "first" ? " · first-round count (ranked-choice)" : res.round === "final" ? " · final ranked-choice round" : "")));
      card.appendChild(el("div", "race-foot", (res && res.called ? `Called${res.winner_name ? ` for ${res.winner_name}` : ""}` : "Still counting")
        + ` · Electable forecast: ${fav}` + (r.district != null && !(res && res.dem_votes) ? " · vote totals not yet posted for this district" : "")));
      card.appendChild(el("div", "race-foot", `${i + 1} of ${pool.length}, closest first`));
    }

    // 4. Latest news
    const nb = document.getElementById("dash-news"); nb.replaceChildren();
    const N = state.news;
    const h2 = el("h2"); const a = el("a", null, "Latest news →"); a.href = "#news"; h2.appendChild(a); nb.appendChild(h2);
    if (N && N.items.length) {
      const ul2 = el("ul", "mini-news");
      N.items.slice(0, 3).forEach((it) => {
        const li = el("li"); li.appendChild(el("small", null, `${it.source} · ${ago(it.published)}`));
        const l = el("a", null, it.title); if (/^https?:\/\//.test(it.url)) l.href = it.url; l.target = "_blank"; l.rel = "noopener noreferrer";
        li.appendChild(l); ul2.appendChild(li);
      });
      nb.appendChild(ul2);
    } else nb.appendChild(el("p", "sub", "Headlines appear here as the news channel updates."));
  }

  /* ---------------- news ---------------- */
  function renderNews() {
    const N = state.news, list = document.getElementById("news-list"), sel = document.getElementById("news-source");
    list.replaceChildren();
    if (!N || !N.items.length) {
      document.getElementById("news-sub").textContent = "No headlines yet. The news channel refreshes twice a day.";
      return;
    }
    const outlets = [...new Set(N.items.map((i) => i.source))].sort();
    if (sel.options.length !== outlets.length + 1) {
      sel.replaceChildren(Object.assign(document.createElement("option"), {value: "", textContent: "All outlets"}),
        ...outlets.map((o) => Object.assign(document.createElement("option"), {value: o, textContent: o})));
    }
    sel.value = state.newsSource;
    document.getElementById("news-sub").textContent =
      `Election and politics headlines from ${N.sources.length} outlets across the political spectrum, updated ${fmtTime(N.generated_at)}. Each links to the original article.`;
    const ago = (iso) => {
      const m = Math.round((Date.now() - new Date(iso)) / 6e4);
      return m < 60 ? `${Math.max(m, 1)} min ago` : m < 1440 ? `${Math.round(m / 60)} hr ago` : `${Math.round(m / 1440)} d ago`;
    };
    N.items.filter((i) => !state.newsSource || i.source === state.newsSource).forEach((i) => {
      const li = document.createElement("li"); li.className = "news-item";
      const meta = document.createElement("div"); meta.className = "news-meta";
      const src = document.createElement("span"); src.className = "src"; src.textContent = i.source;
      const t = document.createElement("time"); t.dateTime = i.published; t.textContent = ago(i.published); t.title = fmtTime(i.published);
      meta.append(src, t);
      const a = document.createElement("a");
      if (/^https?:\/\//.test(i.url)) a.href = i.url;
      a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = i.title;
      li.append(meta, a);
      if (i.summary) { const p = document.createElement("p"); p.textContent = i.summary; li.appendChild(p); }
      list.appendChild(li);
    });
  }

  /* ---------------- main render ---------------- */
  function render() {
    writeHash();
    document.querySelectorAll("#chamber-tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.chamber === state.chamber));
    const isNews = state.chamber === "news";
    document.getElementById("news-view").hidden = !isNews;
    document.getElementById("panel-chips").hidden = isNews;
    document.getElementById("method").hidden = isNews;
    if (isNews) {
      ["empty", "results-banner", "results-dash"].forEach((id) => { document.getElementById(id).hidden = true; });
      document.querySelectorAll("section.panel").forEach((s) => { s.hidden = true; });
      renderNews();
      return;
    }
    document.querySelectorAll("#panel-chips button").forEach((b) => b.setAttribute("aria-pressed", state.panels.has(b.dataset.panel)));
    document.querySelectorAll("section.panel").forEach((s) => { s.hidden = !state.panels.has(s.dataset.panel); });
    const c = state.forecast.chambers[state.chamber];
    const empty = document.getElementById("empty");
    const ok = c && c.status === "ok";
    empty.hidden = ok;
    if (!ok) {
      empty.textContent = `No ${CHAMBER_LABEL[state.chamber]} forecast yet (${c ? c.status : "no data"}). See “Data notes” below for what is missing.`;
      document.querySelectorAll("section.panel").forEach((s) => { if (s.dataset.panel !== "errors") s.hidden = true; });
    } else {
      renderDash(c);
      renderBanner();
      if (state.panels.has("seats")) { renderTiles(c); renderSeats(c); }
      if (state.panels.has("races")) renderRaces(c);
      if (state.panels.has("polls")) renderPolls(c);
      if (state.panels.has("tipping")) renderTipping(c);
      if (state.panels.has("history")) renderHistory();
      if (state.panels.has("electit")) {
        if (state.chamber === "governor") { document.getElementById("panel-electit").hidden = true; }
        else renderElectit(c);
      }
    }
    if (state.panels.has("errors")) renderErrors();
  }

  async function load() {
    readHash();
    const get = (p) => fetch(p, {cache: "no-cache"}).then((r) => r.ok ? r.json() : null).catch(() => null);
    const dir = ARCHIVE ? "data/archive/" : "data/";
    const [f, h, cal, res, nws] = await Promise.all([get(dir + "forecast.json"), get(dir + "history.json"),
      get("data/calibration.json"), get("data/results.json"), get("data/news.json")]);
    if (!f) {
      const e = document.getElementById("empty"); e.hidden = false;
      e.textContent = "No forecast has been built yet. Run: python -m electable all";
      return;
    }
    Object.assign(state, {forecast: f, history: h || [], calibration: cal, results: res, news: nws});
    if (f.electit && f.electit.sets && f.electit.sets.length) state.electitSet = f.electit.primary_set || f.electit.sets[0];
    renderMeta(); renderSources(); render();
    document.querySelectorAll("#chamber-tabs button").forEach((b) => b.addEventListener("click", () => { state.chamber = b.dataset.chamber; render(); }));
    document.querySelectorAll("#panel-chips button").forEach((b) => b.addEventListener("click", () => {
      const p = b.dataset.panel; state.panels.has(p) ? state.panels.delete(p) : state.panels.add(p);
      if (!state.panels.size) state.panels.add(p); render();
    }));
    document.querySelectorAll("#history-range button").forEach((b) => b.addEventListener("click", () => {
      state.historyDays = +b.dataset.days; renderHistory();
    }));
    document.getElementById("news-source").addEventListener("change", (e) => { state.newsSource = e.target.value; renderNews(); });
    document.getElementById("electit-set").addEventListener("change", (e) => { state.electitSet = e.target.value; render(); });
    document.getElementById("race-search").addEventListener("input", () => renderRaces(state.forecast.chambers[state.chamber]));
    document.getElementById("show-all").addEventListener("change", () => renderRaces(state.forecast.chambers[state.chamber]));
    addEventListener("hashchange", () => {
      if (/(^#|[_-])original([_-]|$)/.test(location.hash) !== ARCHIVE) { location.reload(); return; }
      readHash(); render();
    });
    let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(render, 150); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  }
  load();
})();
