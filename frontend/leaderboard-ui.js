(() => {
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const riderName = (riderById, id) => riderById.get(id)?.name || "Unknown rider";
  const points = value => `${Number(value).toFixed(2).replace(/\.00$/, "")} pts`;
  const factor = value => `×${Number(value).toFixed(2)}`;
  const SCOPE_LABELS = { top3: "Top 3", top5: "Top 5", top10: "Top 10" };
  const movementClass = line => {
    if (!line.actual_position || line.actual_position === line.predicted_position) return "exact";
    return line.actual_position < line.predicted_position ? "better" : "worse";
  };

  function drawLineageConnectors(stage, predictedRows, actualRows, lines) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("lineage-connectors");
    stage.append(svg);
    const draw = () => {
      const stageBox = stage.getBoundingClientRect();
      const width = stage.scrollWidth;
      const height = stage.scrollHeight;
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.style.width = `${width}px`;
      svg.style.height = `${height}px`;
      svg.replaceChildren();
      lines.filter(line => line.actual_position).forEach(line => {
        const start = predictedRows.get(line.predicted_position)?.getBoundingClientRect();
        const end = actualRows.get(line.actual_position)?.getBoundingClientRect();
        if (!start || !end) return;
        const fromX = start.right - stageBox.left + stage.scrollLeft;
        const fromY = start.top + start.height / 2 - stageBox.top;
        const toX = end.left - stageBox.left + stage.scrollLeft;
        const toY = end.top + end.height / 2 - stageBox.top;
        const bend = Math.max(28, (toX - fromX) * 0.38);
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M ${fromX} ${fromY} C ${fromX + bend} ${fromY}, ${toX - bend} ${toY}, ${toX} ${toY}`);
        path.setAttribute("class", `lineage-connector ${movementClass(line)}`);
        svg.append(path);
        [
          [fromX, fromY],
          [toX, toY],
        ].forEach(([x, y]) => {
          const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("r", "3");
          dot.setAttribute("class", `lineage-connector-dot ${movementClass(line)}`);
          svg.append(dot);
        });
      });
    };
    requestAnimationFrame(draw);
    if (window.ResizeObserver) new ResizeObserver(draw).observe(stage);
  }

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

  function renderDetail(container, entry, riderById, resultByPosition, rules) {
    container.replaceChildren();
    const heading = element("div", "detail-heading");
    heading.append(element("h3", "", `${entry.username}'s prediction`));
    heading.append(element("strong", "detail-total", points(entry.total_points)));
    container.append(heading);
    const summary = element("p", "score-summary muted");
    summary.textContent = `Placement ${points(entry.placement_points)} · permutations ${points(entry.permutation_points)} · wildcards ${points(entry.wildcard_points)}`;
    container.append(summary);

    const depth = rules?.placement_depth ?? 10;
    const placements = [...entry.placements].sort((a, b) => a.predicted_position - b.predicted_position);
    container.append(element("h4", "score-section-title", "Placement"));
    container.append(scoreTable("placement-table", ["Pick", "Rider", "Finish", "Base", "Distance", "UCI rank", "Earned"], placements.map(line => [
      ["span", "", `#${line.predicted_position}`],
      ["span", "score-rider", riderName(riderById, line.rider_id)],
      ["span", "", line.actual_position ? `#${line.actual_position}` : "Out"],
      ["span", "", String(line.base_points)],
      ["span", "", line.actual_position && line.actual_position <= depth ? factor(line.distance_factor) : "—"],
      ["span", "", `${factor(line.multiplier)}`],
      ["strong", "", points(line.points)],
    ])));

    container.append(element("h4", "score-section-title", "Bonuses"));
    const bonusRows = [
      ...entry.permutations.map(line => [
        ["span", "", SCOPE_LABELS[line.scope] || line.scope],
        ["span", "score-rider", `${line.matched} of ${line.size} riders named`],
        ["span", "", ""],
        ["strong", "", points(line.points)],
      ]),
      ...entry.wildcards.map(line => [
        ["span", "", `★ Wildcard`],
        ["span", "score-rider", riderName(riderById, line.rider_id)],
        ["span", "", `${line.actual_position ? `#${line.actual_position}` : "Out"} · ${line.base_points} ${factor(line.multiplier)}`],
        ["strong", "", points(line.points)],
      ]),
    ];
    container.append(scoreTable("bonus-table", ["Bonus", "Detail", "", "Earned"], bonusRows));

    const lineage = element("section", "lineage");
    lineage.append(element("h3", "", "Prediction lineage"));
    lineage.append(element("p", "muted", "Each line follows one predicted rider from the chosen position to the actual result."));
    const lastPosition = Math.max(10, ...resultByPosition.keys());
    const stage = element("div", "lineage-stage");
    const grid = element("div", "lineage-grid");
    const predictedRows = new Map();
    const actualRows = new Map();
    const predicted = element("div", "lineage-column");
    predicted.append(element("h4", "", "Prediction"));
    const predictionLines = placements;
    predictionLines.forEach(line => {
      const row = element("div", "lineage-row");
      row.append(element("span", "lineage-position", `#${line.predicted_position}`));
      row.append(element("span", line.actual_position ? `lineage-link ${movementClass(line)}` : "lineage-link out", line.actual_position ? `→ #${line.actual_position}` : "→ out"));
      row.append(element("span", "lineage-rider", riderName(riderById, line.rider_id)));
      predicted.append(row);
      predictedRows.set(line.predicted_position, row);
    });
    const actual = element("div", "lineage-column");
    actual.append(element("h4", "", "Actual result"));
    for (let position = 1; position <= lastPosition; position += 1) {
      const riderId = resultByPosition.get(position);
      const row = element("div", "lineage-row");
      row.append(element("span", "lineage-position", `#${position}`));
      row.append(element("span", "lineage-rider", riderId ? riderName(riderById, riderId) : "Not entered"));
      actual.append(row);
      actualRows.set(position, row);
    }
    grid.append(predicted, actual);
    stage.append(grid);
    lineage.append(stage);
    container.append(lineage);
    drawLineageConnectors(stage, predictedRows, actualRows, predictionLines);
  }

  window.renderLeaderboard = (container, event, leaderboard) => {
    container.replaceChildren();
    if (!leaderboard.entries.length) {
      container.textContent = "No submitted predictions yet.";
      return;
    }
    const riderById = new Map(event.riders.map(rider => [rider.id, rider]));
    const resultByPosition = new Map(leaderboard.results.map(result => [result.position, result.rider_id]));
    const ranking = element("div", "leaderboard-ranking");
    const detail = element("div", "leaderboard-detail");
    leaderboard.entries.forEach((entry, index) => {
      const button = element("button", "leaderboard-entry", "");
      button.type = "button";
      button.append(element("span", "leaderboard-place", `#${index + 1}`));
      button.append(element("strong", "", entry.username));
      button.append(element("span", "", points(entry.total_points)));
      button.addEventListener("click", () => {
        ranking.querySelectorAll("button").forEach(node => node.classList.toggle("selected", node === button));
        renderDetail(detail, entry, riderById, resultByPosition, leaderboard.rules);
      });
      ranking.append(button);
      if (index === 0) {
        button.classList.add("selected");
        renderDetail(detail, entry, riderById, resultByPosition, leaderboard.rules);
      }
    });
    container.append(ranking, detail);
  };
})();
