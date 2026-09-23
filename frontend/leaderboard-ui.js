(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (tag, attributes = {}) => {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
    return node;
  };

  const riderName = (riderById, id) => riderById.get(id)?.name || "Unknown rider";
  const number = value => Number(value).toFixed(2).replace(/\.00$/, "");
  const points = value => `${number(value)} pts`;
  const factor = value => `×${Number(value).toFixed(2)}`;
  const ordinal = value => {
    const tens = value % 100;
    const suffix = tens >= 11 && tens <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[value % 10] || "th";
    return `${value}${suffix}`;
  };
  const finishLabel = position => (position ? ordinal(position) : "no top finish");
  const SCOPE_LABELS = { top3: "Top 3", top5: "Top 5", top10: "Top 10" };
  const movementClass = line => {
    if (!line.actual_position || line.actual_position === line.predicted_position) return "exact";
    return line.actual_position < line.predicted_position ? "better" : "worse";
  };

  // -- what earned the points ---------------------------------------------------
  function contributions(entry, riderById) {
    return [
      ...entry.placements.map(line => ({
        kind: "placement",
        label: `#${line.predicted_position} ${riderName(riderById, line.rider_id)}`,
        detail: `guessed ${ordinal(line.predicted_position)}, finished ${finishLabel(line.actual_position)}`,
        formula: line.points > 0 ? `${line.base_points} × ${factor(line.distance_factor)} × ${factor(line.multiplier)}` : "",
        points: line.points,
      })),
      ...entry.permutations.map(line => ({
        kind: "permutation",
        label: `${SCOPE_LABELS[line.scope]} set`,
        detail: `${line.matched} of ${line.size} riders named`,
        formula: "",
        points: line.points,
      })),
      ...entry.wildcards.map(line => ({
        kind: "wildcard",
        label: `★ ${riderName(riderById, line.rider_id)}`,
        detail: `wildcard, finished ${finishLabel(line.actual_position)}`,
        formula: line.points > 0 ? `${line.base_points} × ${factor(line.multiplier)}` : "",
        points: line.points,
      })),
    ];
  }
  const bestGuesses = (entry, riderById, count = 3) => contributions(entry, riderById)
    .filter(item => item.points > 0)
    .sort((a, b) => b.points - a.points)
    .slice(0, count);

  // The score "as the result comes in": placement points arrive with the actual
  // finishing place that earned them, then the group bonuses, then wildcards.
  function progression(entry, depth) {
    const steps = [];
    for (let place = 1; place <= depth; place += 1) {
      const line = entry.placements.find(item => item.actual_position === place);
      steps.push({ label: `${ordinal(place)} place`, short: String(place), points: line ? line.points : 0 });
    }
    entry.permutations.forEach(line => steps.push({ label: `${SCOPE_LABELS[line.scope]} set`, short: SCOPE_LABELS[line.scope].replace("Top ", "T"), points: line.points }));
    steps.push({ label: "Wildcards", short: "WC", points: entry.wildcards.reduce((sum, line) => sum + line.points, 0) });
    let running = 0;
    steps.forEach(step => { running += step.points; step.cumulative = running; });
    // Every part is non-negative, so the line never falls; pin its end to the
    // rounded total so the chart finishes exactly on the player's score.
    steps[steps.length - 1].cumulative = entry.total_points;
    return steps;
  }

  // -- chart plumbing -------------------------------------------------------------
  let tooltip;
  function showTooltip(event, rows) {
    if (!tooltip) {
      tooltip = element("div", "viz-tooltip");
      tooltip.setAttribute("role", "status");
      document.body.append(tooltip);
    }
    tooltip.replaceChildren(...rows.map(([value, label, key]) => {
      const row = element("div", "viz-tooltip-row");
      if (key) {
        const swatch = element("span", "viz-key");
        swatch.style.background = key;
        row.append(swatch);
      }
      if (value) row.append(element("strong", "", value));
      if (label) row.append(element("span", "", label));
      return row;
    }));
    tooltip.classList.add("visible");
    const box = event.target.getBoundingClientRect();
    const x = event.clientX ?? box.left + box.width / 2;
    const y = event.clientY ?? box.top;
    tooltip.style.left = `${Math.max(8, Math.min(window.innerWidth - tooltip.offsetWidth - 8, x + 14))}px`;
    tooltip.style.top = `${Math.max(8, y - tooltip.offsetHeight - 12) + window.scrollY}px`;
  }
  const hideTooltip = () => tooltip?.classList.remove("visible");

  function niceMax(value) {
    if (value <= 0) return 10;
    const magnitude = 10 ** Math.floor(Math.log10(value / 4));
    const step = [1, 2, 2.5, 5, 10].map(candidate => candidate * magnitude).find(candidate => candidate * 4 >= value);
    return step * 4;
  }
  const ticksFor = max => [0, 1, 2, 3, 4].map(index => (index * max) / 4);

  function frame(width, height, margin, yMax, label) {
    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "viz-svg", role: "img", "aria-label": label });
    const plotBottom = height - margin.bottom;
    const plotHeight = plotBottom - margin.top;
    const y = value => plotBottom - (value / yMax) * plotHeight;
    ticksFor(yMax).forEach(tick => {
      root.append(svg("line", { x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick), class: "viz-grid" }));
      const text = svg("text", { x: margin.left - 8, y: y(tick) + 4, class: "viz-axis", "text-anchor": "end" });
      text.textContent = number(tick);
      root.append(text);
    });
    return { root, y };
  }

  // A column with a 4px rounded data end and a square foot.
  function roundedBar(x, top, bottom, width, className) {
    const radius = Math.min(4, Math.max(0, (bottom - top) / 2));
    const left = x - width / 2;
    const right = x + width / 2;
    return svg("path", {
      class: className,
      d: `M${left},${bottom} L${left},${top + radius} Q${left},${top} ${left + radius},${top} L${right - radius},${top} Q${right},${top} ${right},${top + radius} L${right},${bottom} Z`,
    });
  }

  function hitTarget(root, x, width, top, bottom, rows, label) {
    const target = svg("rect", { x: x - width / 2, y: top, width, height: Math.max(0, bottom - top), class: "viz-hit", tabindex: "0", "aria-label": label });
    target.addEventListener("pointermove", event => showTooltip(event, rows()));
    target.addEventListener("pointerleave", hideTooltip);
    target.addEventListener("focus", event => showTooltip(event, rows()));
    target.addEventListener("blur", hideTooltip);
    root.append(target);
  }

  // -- analyzer: waterfall of one player's points ------------------------------------
  function waterfall(entry, riderById) {
    const steps = [];
    for (let pick = 1; pick <= 10; pick += 1) {
      const line = entry.placements.find(item => item.predicted_position === pick);
      steps.push({
        short: String(pick),
        points: line ? line.points : 0,
        rows: line
          ? [[points(line.points), `Pick #${pick}: ${riderName(riderById, line.rider_id)}`], ["", `guessed ${ordinal(pick)}, finished ${finishLabel(line.actual_position)}`], ["", line.points > 0 ? `${line.base_points} base × ${Number(line.distance_factor).toFixed(2)} distance × ${Number(line.multiplier).toFixed(2)} rank` : "outside the scored places: no placement points"]]
          : [["0 pts", `Pick #${pick}: empty`]],
      });
    }
    ["top3", "top5", "top10"].forEach(scope => {
      const line = entry.permutations.find(item => item.scope === scope);
      steps.push({
        short: SCOPE_LABELS[scope].replace("Top ", "T"),
        points: line.points,
        rows: [[points(line.points), `${SCOPE_LABELS[scope]} set`], ["", `${line.matched} of ${line.size} riders named`]],
      });
    });
    for (let slot = 1; slot <= 3; slot += 1) {
      const line = entry.wildcards.find(item => item.slot === slot);
      steps.push({
        short: "★",
        points: line ? line.points : 0,
        rows: line
          ? [[points(line.points), `Wildcard: ${riderName(riderById, line.rider_id)}`], ["", `finished ${finishLabel(line.actual_position)}`], ["", line.points > 0 ? `${line.base_points} bonus × ${Number(line.multiplier).toFixed(2)} rank` : "no top 10 finish: no bonus"]]
          : [["0 pts", `Wildcard ${slot}: empty`]],
      });
    }
    const width = 760;
    const height = 290;
    const margin = { top: 26, right: 16, bottom: 62, left: 44 };
    const yMax = niceMax(entry.total_points);
    const { root, y } = frame(width, height, margin, yMax, `Where ${entry.username}'s points came from`);
    const band = (width - margin.left - margin.right) / (steps.length + 1);
    const barWidth = Math.min(24, band * 0.6);
    const xAt = index => margin.left + band * (index + 0.5);
    const axisY = height - margin.bottom + 16;
    let running = 0;
    steps.forEach((step, index) => {
      const x = xAt(index);
      const before = running;
      running += step.points;
      if (step.points > 0.004) root.append(roundedBar(x, y(running), y(before), barWidth, "viz-bar"));
      else root.append(svg("line", { x1: x - barWidth / 2, x2: x + barWidth / 2, y1: y(running), y2: y(running), class: "viz-zero" }));
      root.append(svg("line", { x1: x + barWidth / 2, x2: xAt(index + 1) - barWidth / 2, y1: y(running), y2: y(running), class: "viz-connector" }));
      const label = svg("text", { x, y: axisY, class: "viz-axis", "text-anchor": "middle" });
      label.textContent = step.short;
      root.append(label);
      const total = running;
      hitTarget(root, x, band, margin.top, height - margin.bottom, () => [...step.rows, [points(total), "running total"]], step.rows[0][1]);
    });
    const totalX = xAt(steps.length);
    root.append(roundedBar(totalX, y(entry.total_points), y(0), barWidth, "viz-bar viz-bar-total"));
    const totalLabel = svg("text", { x: totalX, y: y(entry.total_points) - 7, class: "viz-value", "text-anchor": "middle" });
    totalLabel.textContent = number(entry.total_points);
    root.append(totalLabel);
    const totalAxis = svg("text", { x: totalX, y: axisY, class: "viz-axis viz-axis-strong", "text-anchor": "middle" });
    totalAxis.textContent = "Total";
    root.append(totalAxis);
    hitTarget(root, totalX, band, margin.top, height - margin.bottom, () => [[points(entry.total_points), "Total"], ["", `placement ${points(entry.placement_points)} · permutations ${points(entry.permutation_points)} · wildcards ${points(entry.wildcard_points)}`]], "Total");
    [["Your picks, #1 to #10", 0, 9], ["Group bonuses", 10, 12], ["Wildcards", 13, 15]].forEach(([text, first, last]) => {
      const left = xAt(first) - barWidth / 2;
      const right = xAt(last) + barWidth / 2;
      root.append(svg("line", { x1: left, x2: right, y1: axisY + 10, y2: axisY + 10, class: "viz-bracket" }));
      const caption = svg("text", { x: (left + right) / 2, y: axisY + 26, class: "viz-axis", "text-anchor": "middle" });
      caption.textContent = text;
      root.append(caption);
    });
    return root;
  }

  // -- compare: two players' scores as the result comes in -----------------------
  const SERIES = ["var(--viz-1)", "var(--viz-2)"];
  function rampColor(t) {
    // One hue graded light to dark (OKLCH; validated as an ordinal ramp on the surface).
    const L = 0.74 - 0.40 * t;
    const C = 0.08 + 0.04 * t;
    const hue = (245 * Math.PI) / 180;
    const a = C * Math.cos(hue);
    const b = C * Math.sin(hue);
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
    const linear = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
    const channel = value => Math.round(255 * Math.min(1, Math.max(0, value <= 0.0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - 0.055)));
    return `rgb(${linear.map(channel).join(",")})`;
  }

  function lineChart(players, depth) {
    const series = players.map(entry => ({ entry, steps: progression(entry, depth) }));
    const count = series[0].steps.length;
    const width = 760;
    const height = 300;
    const margin = { top: 22, right: 130, bottom: 44, left: 44 };
    const yMax = niceMax(Math.max(...players.map(entry => entry.total_points)));
    const { root, y } = frame(width, height, margin, yMax, `Points as the result comes in: ${players.map(entry => entry.username).join(" and ")}`);
    const xAt = index => margin.left + (index / count) * (width - margin.left - margin.right);
    // x = 0 is "before the result"; step i lands at x = i + 1.
    series[0].steps.forEach((step, index) => {
      const label = svg("text", { x: xAt(index + 1), y: height - margin.bottom + 18, class: `viz-axis ${index >= depth ? "viz-axis-strong" : ""}`, "text-anchor": "middle" });
      label.textContent = step.short;
      root.append(label);
    });
    const caption = svg("text", { x: margin.left, y: height - 6, class: "viz-axis" });
    caption.textContent = `after finishing places 1–${depth}, then the group bonuses and wildcards`;
    root.append(caption);
    root.append(svg("line", { x1: xAt(depth + 0.5), x2: xAt(depth + 0.5), y1: margin.top, y2: height - margin.bottom, class: "viz-divider" }));
    const crosshair = svg("line", { y1: margin.top, y2: height - margin.bottom, class: "viz-crosshair" });
    root.append(crosshair);
    series.forEach(({ steps }, index) => {
      const path = [`M${xAt(0)},${y(0)}`, ...steps.map((step, stepIndex) => `L${xAt(stepIndex + 1)},${y(step.cumulative)}`)].join(" ");
      root.append(svg("path", { d: path, class: "viz-line", stroke: SERIES[index] }));
    });
    series.forEach(({ steps }, index) => root.append(svg("circle", { cx: xAt(count), cy: y(steps[count - 1].cumulative), r: 4.5, class: "viz-dot", fill: SERIES[index] })));
    // Direct end labels only when they separate; otherwise the legend carries identity.
    const ends = series.map(({ entry, steps }) => ({ entry, y: y(steps[count - 1].cumulative) }));
    if (ends.length < 2 || Math.abs(ends[0].y - ends[1].y) >= 16) {
      ends.forEach(({ entry, y: endY }) => {
        const label = svg("text", { x: xAt(count) + 10, y: endY + 4, class: "viz-end-label" });
        label.textContent = `${entry.username} ${number(entry.total_points)}`;
        root.append(label);
      });
    }
    const overlay = svg("rect", { x: margin.left, y: margin.top, width: width - margin.left - margin.right, height: height - margin.top - margin.bottom, class: "viz-overlay", tabindex: "0", "aria-label": "Scores step by step; use the arrow keys" });
    let focusIndex = count - 1;
    const readout = (event, index) => {
      crosshair.setAttribute("x1", xAt(index + 1));
      crosshair.setAttribute("x2", xAt(index + 1));
      crosshair.classList.add("visible");
      const step = series[0].steps[index];
      showTooltip(event, [["", `After ${step.label}`], ...series.map(({ entry, steps }, seriesIndex) => [number(steps[index].cumulative), `${entry.username} (+${number(steps[index].points)})`, SERIES[seriesIndex]])]);
    };
    overlay.addEventListener("pointermove", event => {
      const box = root.getBoundingClientRect();
      const px = ((event.clientX - box.left) / box.width) * width;
      focusIndex = Math.max(0, Math.min(count - 1, Math.round(((px - margin.left) / (width - margin.left - margin.right)) * count) - 1));
      readout(event, focusIndex);
    });
    overlay.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      focusIndex = Math.max(0, Math.min(count - 1, focusIndex + (event.key === "ArrowRight" ? 1 : -1)));
      readout(event, focusIndex);
    });
    overlay.addEventListener("focus", event => readout(event, focusIndex));
    const leave = () => { crosshair.classList.remove("visible"); hideTooltip(); };
    overlay.addEventListener("pointerleave", leave);
    overlay.addEventListener("blur", leave);
    root.append(overlay);
    return root;
  }

  function stackedChart(players, depth) {
    const width = 760;
    const height = 300;
    const margin = { top: 22, right: 16, bottom: 44, left: 44 };
    const yMax = niceMax(Math.max(...players.map(entry => entry.total_points)));
    const { root, y } = frame(width, height, margin, yMax, `Stacked points: ${players.map(entry => entry.username).join(" and ")}`);
    const barWidth = 24;
    players.forEach((entry, index) => {
      const x = margin.left + ((width - margin.left - margin.right) * (index + 1)) / (players.length + 1);
      const steps = progression(entry, depth);
      const visible = steps.filter(step => step.points > 0.004);
      let running = 0;
      visible.forEach((step, visibleIndex) => {
        const last = visibleIndex === visible.length - 1;
        const bottom = y(running) - (visibleIndex > 0 ? 1 : 0);
        running = last ? entry.total_points : running + step.points;
        // A 2px surface gap separates touching segments; the bar's height is the total.
        const top = y(running) + (last ? 0 : 1);
        const color = rampColor(steps.indexOf(step) / (steps.length - 1));
        const segment = last
          ? roundedBar(x, top, bottom, barWidth, "viz-segment")
          : svg("rect", { x: x - barWidth / 2, y: top, width: barWidth, height: Math.max(0.5, bottom - top), class: "viz-segment" });
        segment.setAttribute("fill", color);
        root.append(segment);
        const total = running;
        hitTarget(root, x, 72, top - 1, bottom + 1, () => [[`+${number(step.points)}`, `${entry.username}: ${step.label}`, color], [number(total), "running total"]], `${entry.username} ${step.label}`);
      });
      const value = svg("text", { x, y: y(entry.total_points) - 8, class: "viz-value", "text-anchor": "middle" });
      value.textContent = number(entry.total_points);
      root.append(value);
      const name = svg("text", { x, y: height - margin.bottom + 18, class: "viz-axis viz-axis-strong", "text-anchor": "middle" });
      name.textContent = entry.username;
      root.append(name);
    });
    return root;
  }

  function rampLegend(depth) {
    const legend = element("div", "viz-legend viz-ramp-legend");
    const strip = element("span", "viz-ramp");
    strip.style.background = `linear-gradient(90deg, ${[0, 0.25, 0.5, 0.75, 1].map(rampColor).join(", ")})`;
    legend.append(element("span", "", "1st place"), strip, element("span", "", `${ordinal(depth)} place, then bonuses and wildcards`));
    return legend;
  }

  function seriesLegend(players) {
    const legend = element("div", "viz-legend");
    players.forEach((entry, index) => {
      const item = element("span", "viz-legend-item");
      const key = element("span", "viz-line-key");
      key.style.background = SERIES[index];
      item.append(key, element("span", "", `${entry.username} · ${points(entry.total_points)}`));
      legend.append(item);
    });
    return legend;
  }

  // -- tables ----------------------------------------------------------------------
  function scoreTable(className, labels, rows) {
    const table = element("div", `score-breakdown ${className}`);
    const header = element("div", "score-row score-header");
    labels.forEach(label => header.append(element("span", "", label)));
    table.append(header);
    rows.forEach(cells => {
      const row = element("div", "score-row");
      cells.forEach(([tag, cls, text]) => row.append(element(tag, cls, text)));
      table.append(row);
    });
    return table;
  }

  function scoreDetails(entry, riderById, rules) {
    const details = element("details", "score-details");
    details.open = true;
    details.append(element("summary", "", "Score details"));
    const depth = rules?.placement_depth ?? 10;
    const placements = [...entry.placements].sort((a, b) => a.predicted_position - b.predicted_position);
    details.append(element("h4", "score-section-title", "Placement"));
    details.append(scoreTable("placement-table", ["Pick", "Rider", "Finish", "Base", "Distance", "UCI rank", "Earned"], placements.map(line => [
      ["span", "", `#${line.predicted_position}`],
      ["span", "score-rider", riderName(riderById, line.rider_id)],
      ["span", "", line.actual_position ? `#${line.actual_position}` : "Out"],
      ["span", "", String(line.base_points)],
      ["span", "", line.actual_position && line.actual_position <= depth ? factor(line.distance_factor) : "—"],
      ["span", "", factor(line.multiplier)],
      ["strong", "", points(line.points)],
    ])));
    details.append(element("h4", "score-section-title", "Bonuses"));
    details.append(scoreTable("bonus-table", ["Bonus", "Detail", "", "Earned"], [
      ...entry.permutations.map(line => [
        ["span", "", SCOPE_LABELS[line.scope]],
        ["span", "score-rider", `${line.matched} of ${line.size} riders named`],
        ["span", "", ""],
        ["strong", "", points(line.points)],
      ]),
      ...entry.wildcards.map(line => [
        ["span", "", "★ Wildcard"],
        ["span", "score-rider", riderName(riderById, line.rider_id)],
        ["span", "", `${line.actual_position ? `#${line.actual_position}` : "Out"} · ${line.base_points} ${factor(line.multiplier)}`],
        ["strong", "", points(line.points)],
      ]),
    ]));
    return details;
  }

  function drawLineageConnectors(stage, predictedRows, actualRows, links) {
    const layer = svg("svg", { class: "lineage-connectors" });
    stage.append(layer);
    const draw = () => {
      const stageBox = stage.getBoundingClientRect();
      layer.setAttribute("viewBox", `0 0 ${stage.scrollWidth} ${stage.scrollHeight}`);
      layer.style.width = `${stage.scrollWidth}px`;
      layer.style.height = `${stage.scrollHeight}px`;
      layer.replaceChildren();
      links.forEach(({ from, to, className }) => {
        const start = predictedRows.get(from)?.getBoundingClientRect();
        const end = actualRows.get(to)?.getBoundingClientRect();
        if (!start || !end) return;
        const fromX = start.right - stageBox.left + stage.scrollLeft;
        const fromY = start.top + start.height / 2 - stageBox.top;
        const toX = end.left - stageBox.left + stage.scrollLeft;
        const toY = end.top + end.height / 2 - stageBox.top;
        const bend = Math.max(28, (toX - fromX) * 0.38);
        layer.append(svg("path", { d: `M ${fromX} ${fromY} C ${fromX + bend} ${fromY}, ${toX - bend} ${toY}, ${toX} ${toY}`, class: `lineage-connector ${className}` }));
        [[fromX, fromY], [toX, toY]].forEach(([cx, cy]) => layer.append(svg("circle", { cx, cy, r: 3, class: `lineage-connector-dot ${className}` })));
      });
    };
    requestAnimationFrame(draw);
    if (window.ResizeObserver) new ResizeObserver(draw).observe(stage);
  }

  function lineage(entry, riderById, resultByPosition) {
    const section = element("section", "lineage");
    section.append(element("h3", "", "Prediction lineage"));
    section.append(element("p", "muted", "Each line follows one of your riders from the position you gave them to where they actually finished. Wildcards (★) join in grey; the rule under 10th marks the last scoring place."));
    const lastPosition = Math.max(10, ...resultByPosition.keys());
    const stage = element("div", "lineage-stage");
    const grid = element("div", "lineage-grid");
    const predictedRows = new Map();
    const actualRows = new Map();
    const predicted = element("div", "lineage-column");
    predicted.append(element("h4", "", "Your prediction"));
    const links = [];
    [...entry.placements].sort((a, b) => a.predicted_position - b.predicted_position).forEach(line => {
      const row = element("div", "lineage-row");
      row.append(element("span", "lineage-position", `#${line.predicted_position}`));
      row.append(element("span", line.actual_position ? `lineage-link ${movementClass(line)}` : "lineage-link out", line.actual_position ? `→ #${line.actual_position}` : "→ out"));
      row.append(element("span", "lineage-rider", riderName(riderById, line.rider_id)));
      predicted.append(row);
      const key = `p${line.predicted_position}`;
      predictedRows.set(key, row);
      if (line.actual_position) links.push({ from: key, to: line.actual_position, className: movementClass(line) });
    });
    entry.wildcards.forEach(line => {
      const row = element("div", "lineage-row lineage-wildcard");
      row.append(element("span", "lineage-position", "★"));
      row.append(element("span", line.actual_position ? "lineage-link exact" : "lineage-link out", line.actual_position ? `→ #${line.actual_position}` : "→ out"));
      row.append(element("span", "lineage-rider", riderName(riderById, line.rider_id)));
      predicted.append(row);
      const key = `w${line.slot}`;
      predictedRows.set(key, row);
      if (line.actual_position) links.push({ from: key, to: line.actual_position, className: "wildcard" });
    });
    const actual = element("div", "lineage-column");
    actual.append(element("h4", "", `Actual result, top ${lastPosition}`));
    for (let position = 1; position <= lastPosition; position += 1) {
      const riderId = resultByPosition.get(position);
      const row = element("div", `lineage-row ${position === 10 ? "lineage-cut" : ""}`);
      row.append(element("span", "lineage-position", `#${position}`));
      row.append(element("span", "lineage-rider", riderId ? riderName(riderById, riderId) : "Not entered"));
      actual.append(row);
      actualRows.set(position, row);
    }
    grid.append(predicted, actual);
    stage.append(grid);
    section.append(stage);
    drawLineageConnectors(stage, predictedRows, actualRows, links);
    return section;
  }

  function statTiles(entry, place, field) {
    const tiles = element("div", "viz-stats");
    [
      ["Total", number(entry.total_points), `${ordinal(place)} of ${field}`],
      ["Placement", number(entry.placement_points), "your ten riders"],
      ["Permutations", number(entry.permutation_points), "top 3, 5 and 10 sets"],
      ["Wildcards", number(entry.wildcard_points), "three extra riders"],
    ].forEach(([label, value, note]) => {
      const tile = element("div", "viz-stat");
      tile.append(element("span", "viz-stat-label", label), element("strong", "viz-stat-value", value), element("small", "", note));
      tiles.append(tile);
    });
    return tiles;
  }

  function playerSelect(entries, selected, onChange, label) {
    const wrapper = element("label", "viz-select");
    wrapper.append(element("span", "", label));
    const select = element("select");
    entries.forEach((entry, index) => {
      const option = element("option", "", `${index + 1}. ${entry.username}`);
      option.value = String(index);
      option.selected = index === selected;
      select.append(option);
    });
    select.addEventListener("change", () => onChange(Number(select.value)));
    wrapper.append(select);
    return wrapper;
  }

  function compareTable(players, riderById) {
    const table = element("div", "compare-table");
    const header = element("div", "compare-row compare-header");
    header.append(element("span", "", ""));
    players.forEach(entry => header.append(element("span", "compare-player", entry.username), element("span", "compare-points", "pts")));
    table.append(header);
    const addRow = (label, cells, className = "") => {
      const row = element("div", `compare-row ${className}`);
      row.append(element("span", "compare-label", label));
      const best = Math.max(...cells.map(cell => cell.points));
      const tied = cells.every(cell => cell.points === best);
      cells.forEach(cell => {
        const rider = element("span", "compare-rider");
        rider.append(element("span", "", cell.text));
        if (cell.note) rider.append(element("small", "", cell.note));
        row.append(rider, element("strong", `compare-points ${cell.points > 0 && cell.points === best && !tied ? "leading" : ""}`, number(cell.points)));
      });
      table.append(row);
    };
    const pickCell = line => (line ? { text: riderName(riderById, line.rider_id), note: `finished ${finishLabel(line.actual_position)}`, points: line.points } : { text: "—", note: "", points: 0 });
    for (let pick = 1; pick <= 10; pick += 1) {
      addRow(`#${pick}`, players.map(entry => pickCell(entry.placements.find(item => item.predicted_position === pick))));
    }
    for (let slot = 1; slot <= 3; slot += 1) {
      addRow("★", players.map(entry => pickCell(entry.wildcards.find(item => item.slot === slot))), slot === 1 ? "compare-section" : "");
    }
    ["top3", "top5", "top10"].forEach((scope, index) => addRow(SCOPE_LABELS[scope], players.map(entry => {
      const line = entry.permutations.find(item => item.scope === scope);
      return { text: `${line.matched} of ${line.size} named`, note: "", points: line.points };
    }), index === 0 ? "compare-section" : ""));
    addRow("Total", players.map(entry => ({ text: "", note: "", points: entry.total_points })), "compare-total");
    return table;
  }

  // -- the page ---------------------------------------------------------------------
  window.renderLeaderboard = (container, event, leaderboard) => {
    container.replaceChildren();
    hideTooltip();
    const entries = leaderboard.entries;
    if (!entries.length) {
      container.textContent = "No submitted predictions yet.";
      return;
    }
    const riderById = new Map(event.riders.map(rider => [rider.id, rider]));
    const resultByPosition = new Map(leaderboard.results.map(result => [result.position, result.rider_id]));
    const depth = leaderboard.rules?.placement_depth ?? 10;
    const view = { tab: "analyzer", analyzed: 0, compare: [0, Math.min(1, entries.length - 1)], chart: "line" };

    const board = element("section", "lb");
    const table = element("table", "lb-table");
    const head = element("thead");
    const headRow = element("tr");
    ["#", "Player", "Points", "Behind", "Best guesses"].forEach((label, index) => {
      const cell = element("th", index === 2 || index === 3 ? "numeric" : "", label);
      cell.scope = "col";
      headRow.append(cell);
    });
    head.append(headRow);
    const body = element("tbody");
    const leader = entries[0].total_points;
    const panel = element("section", "viz-panel");
    entries.forEach((entry, index) => {
      const row = element("tr", "lb-row");
      row.tabIndex = 0;
      row.dataset.index = String(index);
      row.append(element("td", "lb-place", String(index + 1)));
      row.append(element("td", "lb-player", entry.username));
      row.append(element("td", "numeric lb-points", number(entry.total_points)));
      row.append(element("td", "numeric lb-behind", index === 0 ? "—" : `−${number(leader - entry.total_points)}`));
      const best = element("td", "lb-best");
      const guesses = bestGuesses(entry, riderById);
      if (!guesses.length) best.append(element("span", "muted", "nothing scored"));
      guesses.forEach(item => {
        const chip = element("span", `lb-chip lb-chip-${item.kind}`);
        chip.title = `${item.detail}${item.formula ? ` · ${item.formula}` : ""}`;
        chip.append(element("span", "", item.label), element("strong", "", number(item.points)));
        best.append(chip);
      });
      row.append(best);
      const open = () => {
        view.tab = "analyzer";
        view.analyzed = index;
        draw();
        tabs.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", keyEvent => { if (keyEvent.key === "Enter") open(); });
      body.append(row);
    });
    table.append(head, body);
    const tableWrap = element("div", "lb-table-wrap");
    tableWrap.append(table);
    board.append(tableWrap, element("p", "muted lb-hint", "Select a player to analyse their score, or compare two players below."));

    const tabs = element("div", "view-toggle viz-tabs");
    tabs.setAttribute("role", "tablist");
    const tabButton = (id, label) => {
      const button = element("span", "", label);
      button.setAttribute("role", "tab");
      button.tabIndex = 0;
      button.dataset.tab = id;
      const choose = () => { view.tab = id; draw(); };
      button.addEventListener("click", choose);
      button.addEventListener("keydown", keyEvent => { if (keyEvent.key === "Enter" || keyEvent.key === " ") { keyEvent.preventDefault(); choose(); } });
      return button;
    };
    tabs.append(tabButton("analyzer", "Analyzer"), element("span", "view-divider", "|"), tabButton("compare", "Compare two players"));

    function drawAnalyzer() {
      const entry = entries[view.analyzed];
      const header = element("div", "viz-header");
      header.append(playerSelect(entries, view.analyzed, index => { view.analyzed = index; draw(); }, "Player"));
      panel.append(header, statTiles(entry, view.analyzed + 1, entries.length));
      const figure = element("figure", "viz-figure");
      figure.append(element("figcaption", "", "Where the points came from"));
      figure.append(element("p", "muted viz-note", "Each bar adds what one pick, group bonus or wildcard earned; a flat tick earned nothing. Hover or focus a bar for the working."));
      const scroll = element("div", "viz-scroll");
      scroll.append(waterfall(entry, riderById));
      figure.append(scroll);
      panel.append(figure, scoreDetails(entry, riderById, leaderboard.rules), lineage(entry, riderById, resultByPosition));
    }

    function drawCompare() {
      if (entries.length < 2) {
        panel.append(element("p", "muted", "Comparing needs at least two players."));
        return;
      }
      const players = view.compare.map(index => entries[index]);
      const header = element("div", "viz-header");
      header.append(
        playerSelect(entries, view.compare[0], index => { view.compare[0] = index; draw(); }, "Player"),
        playerSelect(entries, view.compare[1], index => { view.compare[1] = index; draw(); }, "against"),
      );
      panel.append(header);
      const figure = element("figure", "viz-figure");
      const top = element("div", "viz-figure-top");
      top.append(element("figcaption", "", "Points as the result comes in"));
      const toggle = element("div", "view-toggle viz-chart-toggle");
      toggle.setAttribute("role", "group");
      toggle.setAttribute("aria-label", "Chart type");
      [["line", "Line"], ["stacked", "Stacked bars"]].forEach(([id, label], index) => {
        if (index) toggle.append(element("span", "view-divider", "|"));
        const option = element("span", view.chart === id ? "active" : "", label);
        option.setAttribute("role", "button");
        option.tabIndex = 0;
        const choose = () => { view.chart = id; draw(); };
        option.addEventListener("click", choose);
        option.addEventListener("keydown", keyEvent => { if (keyEvent.key === "Enter" || keyEvent.key === " ") { keyEvent.preventDefault(); choose(); } });
        toggle.append(option);
      });
      top.append(toggle);
      figure.append(top);
      figure.append(element("p", "muted viz-note", view.chart === "line"
        ? `Placement points arrive with the finishing place that earned them (1st to ${ordinal(depth)}), then the group bonuses and wildcards. Hover, or focus and use the arrow keys, for both scores at each step.`
        : "The same steps stacked: each bar is a player's total, built from first place (lightest) up to wildcards (darkest). Hover a segment for its points."));
      figure.append(view.chart === "line" ? seriesLegend(players) : rampLegend(depth));
      const scroll = element("div", "viz-scroll");
      scroll.append(view.chart === "line" ? lineChart(players, depth) : stackedChart(players, depth));
      figure.append(scroll);
      panel.append(compareTable(players, riderById), figure);
    }

    function draw() {
      hideTooltip();
      tabs.querySelectorAll("[data-tab]").forEach(button => {
        button.classList.toggle("active", button.dataset.tab === view.tab);
        button.setAttribute("aria-selected", String(button.dataset.tab === view.tab));
      });
      body.querySelectorAll("tr").forEach(row => row.classList.toggle("selected", view.tab === "analyzer" && Number(row.dataset.index) === view.analyzed));
      panel.replaceChildren();
      if (view.tab === "analyzer") drawAnalyzer();
      else drawCompare();
    }

    container.append(board, tabs, panel);
    draw();
  };
})();
