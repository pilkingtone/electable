/* Electable front end. Reads data/forecast.json, data/history.json, data/calibration.json.
 * URL hash = panel tokens + chamber, e.g. #seats_races_history_senate (like electit's scheme). */
(function () {
  "use strict";

  const CHAMBERS = ["senate", "house", "governor"];
  const PANELS = ["seats", "races", "polls", "tipping", "history", "errors"];
  const DEFAULT_PANELS = ["seats", "races", "polls", "tipping", "history", "errors"];
  const STATE_NAMES = {AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",CT:"Connecticut",DE:"Delaware",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming"};
  const CHAMBER_LABEL = {senate: "Senate", house: "House", governor: "Governors"};
  const UNIT = {senate: "seats", house: "seats", governor: "governorships"};

  const state = {chamber: "senate", panels: new Set(DEFAULT_PANELS), forecast: null, history: [], calibration: null};
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const pct = (p) => p == null ? "—" : p > 0.99 ? ">99%" : p < 0.01 ? "<1%" : Math.round(p * 100) + "%";
  const margin = (m) => m == null ? "—" : (m >= 0 ? "D+" : "R+") + Math.abs(m).toFixed(1);
  const fmtDate = (s) => new Date(s + "T12:00:00").toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});
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
  function readHash() {
    const toks = location.hash.replace(/^#/, "").split("_").filter(Boolean);
    const ch = toks.find((t) => CHAMBERS.includes(t));
    const ps = toks.filter((t) => PANELS.includes(t));
    if (ch) state.chamber = ch;
    if (ps.length) state.panels = new Set(ps);
  }
  function writeHash() {
    const toks = PANELS.filter((p) => state.panels.has(p)).concat(state.chamber);
    history.replaceState(null, "", "#" + toks.join("_"));
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
    a.append("Updated ");
    const s = document.createElement("strong"); s.textContent = fmtDate(f.as_of); a.appendChild(s);
    const b = document.createElement("div");
    b.textContent = days > 0 ? `${days} day${days === 1 ? "" : "s"} until Nov 3, 2026` : "Election day: Nov 3, 2026";
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
    tile(el, {label: `Democrats win ${what}`, value: pct(pD), key: css("--dem"), lead: pD >= pR ? "dem" : null,
              note: `Needs ${c.threshold} ${UNIT[ch]}`});
    tile(el, {label: `Republicans ${ch === "governor" ? "keep a majority" : "control"}`, value: pct(pR), key: css("--rep"),
              lead: pR > pD ? "rep" : null,
              note: ch === "senate" ? "Includes a 50–50 split (VP breaks ties)" : ch === "house" ? "Needs 218 seats" : "Includes a 25–25 split"});
    tile(el, {label: `Expected Democratic ${UNIT[ch]}`, value: s.mean.toFixed(1),
              note: `80% range ${s.lo80}–${s.hi80}` + (c.base_dem ? ` · incl. ${c.base_dem} not up` : "")});
    const j = state.forecast.joint;
    if (j && ch !== "governor") {
      tile(el, {label: "Congress outcome", value: pct(Math.max(j.rep_both, j.dem_both, j.dem_house_rep_senate, j.dem_senate_rep_house)),
                note: `R both ${pct(j.rep_both)} · D both ${pct(j.dem_both)} · split ${pct(j.dem_house_rep_senate + j.dem_senate_rep_house)}`});
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
    const races = filteredRaces(c);
    if (!races.length) { message(el, "No races match. Tick “Show safe seats” to see every race."); renderRaceTable([]); return; }
    const rowH = 30, m = {t: 26, r: 64, b: 8};
    const w = width(el), labelW = w < 520 ? 110 : 210;
    const h = m.t + rowH * races.length + m.b;
    const svg = svgIn(el, h);
    const lim = Math.min(45, Math.max(15, d3.max(races, (r) => Math.max(Math.abs(r.lo80), Math.abs(r.hi80))) || 15));
    const x = d3.scaleLinear().domain([-lim, lim]).range([labelW, w - m.r]).clamp(true);
    svg.append("g").attr("class", "grid").attr("transform", `translate(0,${m.t})`)
      .call(d3.axisTop(x).ticks(w < 520 ? 4 : 8).tickSize(-(h - m.t - m.b)).tickFormat((v) => v === 0 ? "Even" : (v > 0 ? "D+" : "R+") + Math.abs(v)))
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
    row.append("text").attr("x", w - 4).attr("y", rowH / 2 + 4).attr("text-anchor", "end").attr("class", "label-strong")
      .text((d) => d.p_dem >= 0.5 ? `D ${pct(d.p_dem)}` : `R ${pct(1 - d.p_dem)}`);
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
    (d.ratings || []).forEach((r) => rows.push([r.rating, r.source]));
    return rows;
  }
  function renderRaceTable(races) {
    const t = d3.select("#races-table"); t.selectAll("*").remove();
    const cols = [["Race", (r) => raceLabel(r)], ["Democrat", (r) => (r.dem || "—") + (r.dem_label === "I" ? " (I)" : "")],
      ["Republican", (r) => r.rep || "—"], ["Held by", (r) => r.holder], ["P(Dem)", (r) => pct(r.p_dem), 1],
      ["Forecast", (r) => margin(r.median), 1], ["80% range", (r) => `${margin(r.lo80)} to ${margin(r.hi80)}`, 1],
      ["Poll avg", (r) => r.poll_n ? `${margin(r.poll_avg)} (${r.poll_n})` : "—", 1], ["Prior", (r) => margin(r.prior), 1],
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
      .call(d3.axisTop(x).ticks(w < 520 ? 4 : 8).tickSize(-(h - m.t - m.b)).tickFormat((v) => v === 0 ? "Even" : (v > 0 ? "D+" : "R+") + Math.abs(v)))
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
    const pts = state.history.filter((h) => h[ch] && h[ch].p_dem != null).map((h) => ({date: new Date(h.date + "T12:00:00"), ...h[ch]}));
    document.getElementById("history-sub").textContent = "Chance Democrats win control, by forecast date. Earlier points use only the polls published by that date.";
    if (pts.length < 2) { message(el, "History builds up as the forecast is re-run each day (or backfill with --backfill-days)."); return; }
    const h = 240, m = {t: 16, r: 56, b: 30, l: 40}, svg = svgIn(el, h), w = +svg.attr("width");
    const x = d3.scaleTime().domain(d3.extent(pts, (d) => d.date)).range([m.l, w - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).tickValues([0, 0.25, 0.5, 0.75, 1]).tickSize(-(w - m.l - m.r)).tickFormat(d3.format(".0%")))
      .call((g) => g.select(".domain").remove());
    svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(Math.min(6, pts.length)).tickSizeOuter(0));
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
      {name: "FiveThirtyEight pollster ratings & historical polls (CC BY 4.0)", url: "https://github.com/fivethirtyeight/data/tree/master/pollster-ratings"}]);
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

  /* ---------------- main render ---------------- */
  function render() {
    writeHash();
    document.querySelectorAll("#chamber-tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.chamber === state.chamber));
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
      if (state.panels.has("seats")) { renderTiles(c); renderSeats(c); }
      if (state.panels.has("races")) renderRaces(c);
      if (state.panels.has("polls")) renderPolls(c);
      if (state.panels.has("tipping")) renderTipping(c);
      if (state.panels.has("history")) renderHistory();
    }
    if (state.panels.has("errors")) renderErrors();
  }

  async function load() {
    readHash();
    const get = (p) => fetch(p, {cache: "no-cache"}).then((r) => r.ok ? r.json() : null).catch(() => null);
    const [f, h, cal] = await Promise.all([get("data/forecast.json"), get("data/history.json"), get("data/calibration.json")]);
    if (!f) {
      const e = document.getElementById("empty"); e.hidden = false;
      e.textContent = "No forecast has been built yet. Run: python -m electable all";
      return;
    }
    Object.assign(state, {forecast: f, history: h || [], calibration: cal});
    renderMeta(); renderSources(); render();
    document.querySelectorAll("#chamber-tabs button").forEach((b) => b.addEventListener("click", () => { state.chamber = b.dataset.chamber; render(); }));
    document.querySelectorAll("#panel-chips button").forEach((b) => b.addEventListener("click", () => {
      const p = b.dataset.panel; state.panels.has(p) ? state.panels.delete(p) : state.panels.add(p);
      if (!state.panels.size) state.panels.add(p); render();
    }));
    document.getElementById("race-search").addEventListener("input", () => renderRaces(state.forecast.chambers[state.chamber]));
    document.getElementById("show-all").addEventListener("change", () => renderRaces(state.forecast.chambers[state.chamber]));
    addEventListener("hashchange", () => { readHash(); render(); });
    let t; addEventListener("resize", () => { clearTimeout(t); t = setTimeout(render, 150); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  }
  load();
})();
