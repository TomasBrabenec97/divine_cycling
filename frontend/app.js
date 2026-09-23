const emptyAdvancedFilters = () => ({ raceSelections: [], yearSelections: [], resultMin: "", resultMax: "", trendMin: "", trendMax: "", ageMin: "", ageMax: "" });
const state = { event: null, player: null, reference: null, apiStatus: "starting", apiReadyPromise: null, picks: Array(10).fill(null), savedPicks: Array(10).fill(null), undoStack: [], redoStack: [], mobilePendingRiderId: null, riderView: "country", riderSort: "alphabetical", riderSortDirection: "asc", riderSelectionFilter: "all", filters: { search: "", countries: [], rankMin: "", rankMax: "", pointsMin: "", pointsMax: "", ...emptyAdvancedFilters() } };
const $ = (selector) => document.querySelector(selector);
const isMobileLayout = () => window.matchMedia("(max-width: 650px)").matches;
const countryNames = new Intl.DisplayNames(["en"], { type: "region" });
const flag = (country) => `<span class="flag"><img src="flags/${country.toLocaleLowerCase()}.png" alt="${country} flag" /></span>`;
const countryName = (country) => {
  try { return countryNames.of(country) || country; } catch (_) { return country; }
};
const savedPicks = () => state.picks.filter(Boolean);
const hasUnsavedPickChanges = () => state.picks.some((riderId, index) => riderId !== state.savedPicks[index]);
const rememberPickState = () => { state.undoStack.push([...state.picks]); if (state.undoStack.length > 50) state.undoStack.shift(); state.redoStack = []; };
const resetPickHistory = () => { state.undoStack = []; state.redoStack = []; };
function restorePickState(from, to, message) { const next = from.pop(); if (!next) return; to.push([...state.picks]); if (to.length > 50) to.shift(); state.picks = next; state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking"); showMessage("#prediction-message", message, true); render(); }
const rankingLabel = (rider) => rider.uci_rank === 999999 ? "UCI unranked" : `UCI #${rider.uci_rank}${rider.uci_points === null ? "" : ` · ${rider.uci_points.toLocaleString()} pts`}`;
let predictionMessageVersion = 0;

const API_BASE_URL = (window.DIVINE_API_BASE_URL || "").replace(/\/$/, "");
async function request(url, options = {}) { const response = await fetch(`${API_BASE_URL}${url}`, { headers: { "Content-Type": "application/json" }, ...options }); const body = await response.json(); if (!response.ok) throw new Error(body.detail || "Something went wrong"); return body; }

const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
const API_WAKE_TIMEOUT_MS = 150000;
let apiStatusTimer = 0;
function setApiStatus(status, text) {
  state.apiStatus = status;
  const banner = $("#api-status");
  banner.classList.remove("starting", "ready", "error", "hidden");
  banner.classList.add(status);
  $("#api-status-text").textContent = text;
  window.clearTimeout(apiStatusTimer);
  if (status === "ready") apiStatusTimer = window.setTimeout(() => banner.classList.add("hidden"), 2600);
}
async function wakeApi() {
  // The free Render tier sleeps after about fifteen minutes idle, so the first
  // call of a visit has to outwait a cold start instead of reporting a failure.
  setApiStatus("starting", "Application is starting…");
  const deadline = Date.now() + API_WAKE_TIMEOUT_MS;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE_URL}/api/health`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Health check returned ${response.status}`);
      setApiStatus("ready", "Application ready");
      return true;
    } catch (_) {
      if (Date.now() >= deadline) {
        setApiStatus("error", "Application unavailable. Reload the page to try again.");
        return false;
      }
      if (attempt === 1) setApiStatus("starting", "Application is starting… the free server sleeps when idle, so this takes up to a minute.");
      await delay(Math.min(1200 + attempt * 600, 4000));
    }
  }
}
const whenApiReady = () => (state.apiReadyPromise = state.apiReadyPromise || wakeApi());
function showWakeToast(text) { $("#wake-toast-text").textContent = text; $("#wake-toast").classList.remove("hidden"); }
function hideWakeToast() { $("#wake-toast").classList.add("hidden"); }
function showMessage(selector, text, ok = false, displayMs = ok ? 3000 : 8000) {
  const node = $(selector);
  node.textContent = text;
  node.classList.toggle("ok", ok);
  if (selector !== "#prediction-message") return;
  const version = ++predictionMessageVersion;
  node.classList.remove("fading");
  if (!text) return;
  window.setTimeout(() => {
    if (version === predictionMessageVersion) node.classList.add("fading");
  }, displayMs);
  window.setTimeout(() => {
    if (version === predictionMessageVersion) {
      node.textContent = "";
      node.classList.remove("fading", "ok");
    }
  }, displayMs + 600);
}
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[character]);
const displayRank = (rank) => rank ? `#${rank.toLocaleString()}` : "Unranked";
const displayPoints = (points) => points === null || points === undefined ? "—" : `${Math.round(points).toLocaleString()} pts`;

function resultCell(result) {
  if (!result) return "";
  if (result.status) return `<span class="result-status">${escapeHtml(result.status)}</span>`;
  if (result.position <= 3) return `<span class="result-medal medal-${result.position}">${result.position}</span>`;
  if (result.position <= 5) return `<span class="result-top-five">${result.position}</span>`;
  return `<span class="result-place">${result.position}</span>`;
}

const hasAdvancedFilters = () => {
  const { raceSelections, yearSelections, resultMin, resultMax, trendMin, trendMax, ageMin, ageMax } = state.filters;
  return raceSelections.length + yearSelections.length + [resultMin, resultMax, trendMin, trendMax, ageMin, ageMax].filter(Boolean).length;
};
const riderReference = (riderId) => state.reference?.riders.find((item) => item.id === riderId);
const rankingTrend = (rider, reference) => {
  const rankings = [...(reference?.rankings || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const seasonStart = rankings.find((item) => String(item.date).startsWith("2025-12-30")) || rankings[0];
  return seasonStart?.uci_rank && rider.uci_rank !== 999999 ? seasonStart.uci_rank - rider.uci_rank : null;
};
function matchesAdvancedFilters(rider) {
  if (!hasAdvancedFilters()) return true;
  const reference = riderReference(rider.id);
  if (!reference) return false;
  const { raceSelections, yearSelections, resultMin, resultMax, trendMin, trendMax, ageMin, ageMax } = state.filters;
  const resultInRange = (result) => !result.status && result.position
    && (!resultMin || result.position >= Number(resultMin))
    && (!resultMax || result.position <= Number(resultMax));
  const matchesRace = (result) => resultInRange(result) && (yearSelections.includes(String(result.year)) || raceSelections.some((selection) => {
    const [raceKey, year] = selection.split(":");
    return result.race_key === raceKey && (year === "all" || Number(year) === result.year);
  }));
  const trend = rankingTrend(rider, reference);
  const age = reference.profile?.age;
  return (!(raceSelections.length || yearSelections.length) || reference.results.some(matchesRace))
    && (!trendMin || (trend !== null && trend >= Number(trendMin)))
    && (!trendMax || (trend !== null && trend <= Number(trendMax)))
    && (!ageMin || (age !== null && age !== undefined && age >= Number(ageMin)))
    && (!ageMax || (age !== null && age !== undefined && age <= Number(ageMax)));
}

function renderAdvancedFilters() {
  const content = $("#advanced-filter-content");
  const { raceSelections, yearSelections, resultMin, resultMax, trendMin, trendMax, ageMin, ageMax } = state.filters;
  const selected = new Set(raceSelections);
  $("#advanced-filter-count").textContent = hasAdvancedFilters() ? hasAdvancedFilters() : "";
  $("#advanced-filter-count").classList.toggle("hidden", !hasAdvancedFilters());
  if (!state.reference) return;
  const races = state.reference.races;
  const years = [...new Set(races.flatMap((race) => race.editions.map((edition) => edition.year)))].sort((a, b) => b - a);
  const selectedYears = new Set(yearSelections);
  content.innerHTML = `<section class="advanced-filter-section"><div class="advanced-filter-section-heading"><div><h3>Race results</h3><p class="muted">Match a finish in any selected race, edition, or whole year.</p></div><div class="advanced-race-actions"><button id="expand-race-filters" type="button">Expand all</button><button id="collapse-race-filters" type="button">Collapse all</button><div class="advanced-range"><span class="advanced-range-label">Place</span><input id="advanced-result-min" type="number" min="1" placeholder="From" aria-label="Minimum finishing place" value="${escapeHtml(resultMin)}" /><span>to</span><input id="advanced-result-max" type="number" min="1" placeholder="To" aria-label="Maximum finishing place" value="${escapeHtml(resultMax)}" /></div></div></div><div class="year-filter-row"><span>All races in</span>${years.map((year) => `<label><input type="checkbox" data-year-selection="${year}" ${selectedYears.has(String(year)) ? "checked" : ""} /> ${year}</label>`).join("")}</div><div class="race-filter-list">${races.map((race) => { const aggregate = `${race.key}:all`; return `<details class="race-filter"><summary><label><input type="checkbox" data-race-selection="${aggregate}" ${selected.has(aggregate) ? "checked" : ""} /> ${escapeHtml(race.label)} <span>all years</span></label></summary><div class="race-editions">${race.editions.map((edition) => `<label><input type="checkbox" data-race-selection="${race.key}:${edition.year}" ${selected.has(`${race.key}:${edition.year}`) ? "checked" : ""} /> ${edition.year}${edition.note ? ` <span>${escapeHtml(edition.note)}</span>` : ""}</label>`).join("")}</div></details>`; }).join("")}</div></section><section class="advanced-filter-section advanced-profile-filters"><div><h3>Rider profile</h3><p class="muted">Ranking trend is places gained since the season-start UCI ranking.</p></div><div class="advanced-profile-grid"><label>Ranking trend <div class="advanced-range"><input id="advanced-trend-min" type="number" placeholder="From" value="${escapeHtml(trendMin)}" /><span>to</span><input id="advanced-trend-max" type="number" placeholder="To" value="${escapeHtml(trendMax)}" /></div><small>Positive means moving up.</small></label><label>Age <div class="advanced-range"><input id="advanced-age-min" type="number" min="16" max="60" placeholder="From" value="${escapeHtml(ageMin)}" /><span>to</span><input id="advanced-age-max" type="number" min="16" max="60" placeholder="To" value="${escapeHtml(ageMax)}" /></div></label></div></section>`;
  $("#expand-race-filters").addEventListener("click", () => document.querySelectorAll(".race-filter").forEach((race) => { race.open = true; }));
  $("#collapse-race-filters").addEventListener("click", () => document.querySelectorAll(".race-filter").forEach((race) => { race.open = false; }));
}

function readAdvancedFilterValues() {
  state.filters.raceSelections = [...document.querySelectorAll("[data-race-selection]:checked")].map((input) => input.dataset.raceSelection);
  state.filters.yearSelections = [...document.querySelectorAll("[data-year-selection]:checked")].map((input) => input.dataset.yearSelection);
  ["resultMin", "resultMax", "trendMin", "trendMax", "ageMin", "ageMax"].forEach((key) => {
    const input = $(`#advanced-${key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`);
    state.filters[key] = input?.value.trim() || "";
  });
}

async function openAdvancedFilters() {
  const dialog = $("#advanced-filter-modal");
  if (!dialog.open) dialog.showModal();
  $("#advanced-filter-content").innerHTML = "<p class=\"muted\">Loading race history…</p>";
  try {
    if (!state.reference) state.reference = await request("/api/riders/reference");
    renderAdvancedFilters();
  } catch (error) {
    $("#advanced-filter-content").innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
  }
}

function renderRiderDetail(riderId) {
  const rider = state.event.riders.find((item) => item.id === riderId);
  const reference = state.reference.riders.find((item) => item.id === riderId);
  const content = $("#rider-detail-content");
  if (!rider || !reference) {
    content.innerHTML = "<p class=\"muted\">No reference data is available for this rider.</p>";
    return;
  }
  const rankings = [...reference.rankings].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const seasonStart = rankings.find((item) => String(item.date).startsWith("2025-12-30")) || rankings[0];
  const profile = reference.profile || {};
  const rankChange = seasonStart?.uci_rank && rider.uci_rank !== 999999 ? seasonStart.uci_rank - rider.uci_rank : null;
  const pointChange = seasonStart?.uci_points !== null && seasonStart?.uci_points !== undefined && rider.uci_points !== null
    ? rider.uci_points - seasonStart.uci_points : null;
  const trendClass = rankChange === null ? "neutral" : rankChange > 0 ? "better" : rankChange < 0 ? "worse" : "neutral";
  const trendText = rankChange === null
    ? "No comparable ranking"
    : `${rankChange > 0 ? "↑" : rankChange < 0 ? "↓" : "→"} ${Math.abs(rankChange)} places · ${pointChange >= 0 ? "+" : ""}${Math.round(pointChange).toLocaleString()} pts`;
  const years = [...new Set(state.reference.races.flatMap((race) => race.editions.map((edition) => edition.year)))].sort((a, b) => a - b);
  const resultMap = new Map(reference.results.map((result) => [`${result.race_key}-${result.year}`, result]));
  const calendarDate = (race) => {
    const datedEditions = race.editions.filter((edition) => edition.date);
    if (!datedEditions.length) return "99-99";
    const latestYear = Math.max(...datedEditions.map((edition) => edition.year));
    return String(datedEditions.find((edition) => edition.year === latestYear).date).slice(5);
  };
  const races = state.reference.races
    .filter((race) => race.editions.some((edition) => years.includes(edition.year)))
    .sort((left, right) => calendarDate(left).localeCompare(calendarDate(right)) || left.label.localeCompare(right.label));
  const currentSeason = [...reference.seasons].sort((a, b) => b.season - a.season)[0];
  content.innerHTML = `<header class="rider-detail-header"><div><p class="eyebrow">RIDER PROFILE</p><h2>${flag(rider.nation)}${escapeHtml(rider.name)}</h2><p class="muted">${escapeHtml(profile.team || countryName(rider.nation))}${profile.wins_total === null || profile.wins_total === undefined ? "" : ` · ${profile.wins_total} career wins`}</p></div></header><section class="rider-stat-grid"><div><span>Age</span><strong>${profile.age ?? "—"}</strong></div><div><span>UCI now</span><strong>${displayPoints(rider.uci_points)}</strong><small>${displayRank(rider.uci_rank === 999999 ? null : rider.uci_rank)}</small></div><div><span>Season start</span><strong>${displayPoints(seasonStart?.uci_points)}</strong><small>${displayRank(seasonStart?.uci_rank)}</small></div><div class="ranking-trend ${trendClass}"><span>Ranking trend</span><strong>${trendText}</strong><small>${currentSeason ? `${currentSeason.season}: ${currentSeason.wins} wins · ${currentSeason.top10s} top 10s` : "Season totals unavailable"}</small></div></section><section class="rider-history"><div class="rider-history-heading"><div><p class="eyebrow">PAST RESULTS</p><h3>Tracked one-day races</h3></div></div><div class="rider-history-table-wrap"><table class="rider-history-table"><thead><tr><th scope="col">Race</th>${years.map((year) => `<th scope="col">${year}${state.reference.races.some((race) => race.editions.some((edition) => edition.year === year && edition.note)) ? "<small>upcoming</small>" : ""}</th>`).join("")}</tr></thead><tbody>${races.map((race) => `<tr><th scope="row">${escapeHtml(race.label)}</th>${years.map((year) => `<td>${resultCell(resultMap.get(`${race.key}-${year}`))}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>`;
}

async function openRiderDetail(riderId) {
  const dialog = $("#rider-detail-modal");
  const content = $("#rider-detail-content");
  const pageScrollY = window.scrollY;
  const positionModalAndPage = () => {
    dialog.scrollTop = 0;
    window.scrollTo(0, pageScrollY);
  };
  content.innerHTML = "<p class=\"muted\">Loading rider history…</p>";
  if (!dialog.open) {
    dialog.showModal();
    window.requestAnimationFrame(positionModalAndPage);
  }
  try {
    if (!state.reference) state.reference = await request("/api/riders/reference");
    renderRiderDetail(riderId);
    window.requestAnimationFrame(positionModalAndPage);
  } catch (error) {
    content.innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
    window.requestAnimationFrame(positionModalAndPage);
  }
}
function renderSessionControls() { const signedIn = Boolean(state.player); $("#session-controls").classList.toggle("hidden", !signedIn); $("#event-context").classList.toggle("hidden", !signedIn); $("#session-username").textContent = signedIn ? state.player.username : ""; }
function insertRider(riderId, position) {
  const previous = state.picks.indexOf(riderId);
  if (previous >= 0) {
    if (previous === position) return;
    rememberPickState();
    if (state.picks[position] === null) {
      state.picks[previous] = null;
      state.picks[position] = riderId;
    } else if (position > previous) {
      for (let index = previous; index < position; index += 1) state.picks[index] = state.picks[index + 1];
      state.picks[position] = riderId;
    } else {
      for (let index = previous; index > position; index -= 1) state.picks[index] = state.picks[index - 1];
      state.picks[position] = riderId;
    }
    render();
    return;
  }
  rememberPickState();
  const nextEmpty = state.picks.findIndex((picked, index) => index >= position && picked === null);
  const end = nextEmpty >= 0 ? nextEmpty : 9;
  for (let index = end; index > position; index -= 1) state.picks[index] = state.picks[index - 1];
  state.picks[position] = riderId;
  render();
}

const riderName = (riderId) => state.event.riders.find((rider) => rider.id === riderId)?.name || "this rider";
function addRiderToPicks(riderId) {
  if (isMobileLayout()) {
    const isCancelling = state.mobilePendingRiderId === riderId;
    state.mobilePendingRiderId = isCancelling ? null : riderId;
    document.body.classList.toggle("mobile-picking", !isCancelling);
    showMessage("#prediction-message", isCancelling ? "Pick cancelled." : `Tap a Top 10 position for ${riderName(riderId)}.`, !isCancelling);
    render();
    return;
  }
  if (state.picks.includes(riderId)) return showMessage("#prediction-message", `${riderName(riderId)} is already in your Top 10.`);
  const firstEmpty = state.picks.indexOf(null);
  if (firstEmpty < 0) return showMessage("#prediction-message", "Your top 10 is full. Drag a rider onto a position to insert them.");
  insertRider(riderId, firstEmpty);
}

function rankFill(rider) {
  if (rider.uci_rank === 999999) return 0;
  return Math.max(8, Math.round(100 * (1 - Math.min(rider.uci_rank - 1, 499) / 499)));
}

function matchesFilters(rider) {
  const { search, countries, rankMin, rankMax, pointsMin, pointsMax } = state.filters;
  const haystack = `${rider.name} ${countryName(rider.nation)} ${rider.nation}`.toLocaleLowerCase();
  const hasRank = rider.uci_rank !== 999999;
  const hasPoints = rider.uci_points !== null;
  return (!search || haystack.includes(search.toLocaleLowerCase()))
    && (!countries.length || countries.includes(rider.nation))
    && (!rankMin || (hasRank && rider.uci_rank >= Number(rankMin)))
    && (!rankMax || (hasRank && rider.uci_rank <= Number(rankMax)))
    && (!pointsMin || (hasPoints && rider.uci_points >= Number(pointsMin)))
    && (!pointsMax || (hasPoints && rider.uci_points <= Number(pointsMax)))
    && matchesAdvancedFilters(rider);
}

function ensurePickActions() {
  let clear = $("#clear-picks");
  if (!clear) {
    clear = document.createElement("button");
    clear.id = "clear-picks";
    clear.className = "clear-picks";
    clear.type = "button";
    clear.textContent = "Clear Top 10";
    clear.addEventListener("click", () => {
      if (!savedPicks().length) return;
      rememberPickState();
      state.picks = Array(10).fill(null);
      state.mobilePendingRiderId = null;
      document.body.classList.remove("mobile-picking");
      showMessage("#prediction-message", "Top 10 cleared.", true);
      render();
    });
    $("#picks").insertAdjacentElement("afterend", clear);
  }
  let actions = $("#revert-actions");
  if (!actions) {
    actions = document.createElement("div");
    actions.id = "revert-actions";
    actions.className = "revert-actions";
    const undo = document.createElement("button");
    undo.id = "undo-picks";
    undo.className = "history-button";
    undo.type = "button";
    undo.textContent = "↶";
    undo.setAttribute("aria-label", "Undo last Top 10 change");
    undo.addEventListener("click", () => restorePickState(state.undoStack, state.redoStack, "Undid last change."));
    actions.append(undo);
    const revert = document.createElement("button");
    revert.id = "revert-picks";
    revert.className = "revert-picks";
    revert.type = "button";
    revert.textContent = "Revert to last saved";
    revert.addEventListener("click", () => {
      if (!hasUnsavedPickChanges()) return;
      rememberPickState();
      state.picks = [...state.savedPicks];
      state.mobilePendingRiderId = null;
      document.body.classList.remove("mobile-picking");
      showMessage("#prediction-message", "Reverted to your last saved prediction.", true);
      render();
    });
    actions.append(revert);
    const redo = document.createElement("button");
    redo.id = "redo-picks";
    redo.className = "history-button";
    redo.type = "button";
    redo.textContent = "↷";
    redo.setAttribute("aria-label", "Redo last Top 10 change");
    redo.addEventListener("click", () => restorePickState(state.redoStack, state.undoStack, "Redid last change."));
    actions.append(redo);
    clear.insertAdjacentElement("afterend", actions);
  }
  const revert = $("#revert-picks");
  const save = $("#save");
  if (save && save.previousElementSibling !== actions) actions.insertAdjacentElement("afterend", save);
  const message = $("#prediction-message");
  if (message && message.previousElementSibling !== save) {
    message.classList.add("top10-message");
    save.insertAdjacentElement("afterend", message);
  }
  return { clear, revert, undo: $("#undo-picks"), redo: $("#redo-picks") };
}

function ensureRiderViewControls() {
  let controls = $("#rider-view-controls");
  if (!controls) {
    controls = document.createElement("div");
    controls.id = "rider-view-controls";
    controls.className = "rider-view-controls";
    $("#filter-summary").insertAdjacentElement("afterend", controls);
    controls.addEventListener("click", (event) => {
      const button = event.target.closest("[data-rider-view]");
      if (!button) return;
      state.riderView = button.dataset.riderView;
      if (state.riderView === "plain" && state.riderSort === "rider-count") state.riderSort = "alphabetical";
      render();
    });
    controls.addEventListener("click", (event) => {
      const sort = event.target.closest("[data-rider-sort]");
      if (!sort) return;
      const nextSort = sort.dataset.riderSort;
      state.riderSortDirection = state.riderSort === nextSort
        ? (state.riderSortDirection === "asc" ? "desc" : "asc")
        : (nextSort === "rider-count" ? "desc" : "asc");
      state.riderSort = nextSort;
      render();
    });
    controls.addEventListener("click", (event) => {
      const selection = event.target.closest("[data-rider-selection]");
      if (!selection) return;
      state.riderSelectionFilter = selection.dataset.riderSelection;
      render();
    });
  }
  const arrow = state.riderSortDirection === "asc" ? "↑" : "↓";
  const countryCountSort = state.riderView === "country" ? `<span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rider-count" class="${state.riderSort === "rider-count" ? "active" : ""}">No. riders ${state.riderSort === "rider-count" ? `<i>${arrow}</i>` : ""}</span>` : "";
  controls.innerHTML = `<div class="view-toggle" role="group" aria-label="Rider list view"><span role="button" tabindex="0" data-rider-view="country" class="${state.riderView === "country" ? "active" : ""}">Group by Country</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-view="plain" class="${state.riderView === "plain" ? "active" : ""}">Riders list</span></div><div class="rider-sort-label"><span>Sort:</span><span role="button" tabindex="0" data-rider-sort="alphabetical" class="${state.riderSort === "alphabetical" ? "active" : ""}">Alphabetical ${state.riderSort === "alphabetical" ? `<i>${arrow}</i>` : ""}</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rank" class="${state.riderSort === "rank" ? `active` : ""}">UCI Rank ${state.riderSort === "rank" ? `<i>${arrow}</i>` : ""}</span>${countryCountSort}</div><div class="rider-selection-label" role="group" aria-label="Top 10 selection filter"><span>Riders:</span><span role="button" tabindex="0" data-rider-selection="all" class="${state.riderSelectionFilter === "all" ? "active" : ""}">All</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="selected" class="${state.riderSelectionFilter === "selected" ? "active" : ""}">Selected</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="unselected" class="${state.riderSelectionFilter === "unselected" ? "active" : ""}">Not selected</span></div>`;
}

function sortRiders(riders) {
  return [...riders].sort((a, b) => {
    const direction = state.riderSortDirection === "asc" ? 1 : -1;
    if (state.riderSort === "rank") return direction * (a.uci_rank - b.uci_rank || a.name.localeCompare(b.name));
    return direction * a.name.localeCompare(b.name);
  });
}

function render() {
  const pickActions = ensurePickActions();
  pickActions.clear.disabled = savedPicks().length === 0;
  pickActions.revert.disabled = !hasUnsavedPickChanges();
  pickActions.undo.disabled = state.undoStack.length === 0;
  pickActions.redo.disabled = state.redoStack.length === 0;
  const riderById = new Map(state.event.riders.map((rider) => [rider.id, rider]));
  $("#picks").innerHTML = state.picks.map((riderId, index) => { const rider = riderById.get(riderId); return `<li data-position="${index}" class="${rider ? "pick-filled" : "pick-empty"}" ${rider ? `draggable="true" data-picked-rider="${rider.id}" title="Open rider details"` : ""}><span class="position">${index + 1}.</span>${rider ? `${flag(rider.nation)}${rider.name}<button class="pick-move" type="button" data-move="${index}" aria-label="Move ${escapeHtml(rider.name)} to another position">⇅</button><button class="remove" data-remove="${index}" aria-label="Remove ${rider.name}">×</button><span class="rank-scale pick-rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span>` : "Drop rider here"}</li>`; }).join("");
  const filteredRiders = state.event.riders.filter(matchesFilters);
  const visibleRiders = filteredRiders.filter((rider) => state.riderSelectionFilter === "all"
    || (state.riderSelectionFilter === "selected" && state.picks.includes(rider.id))
    || (state.riderSelectionFilter === "unselected" && !state.picks.includes(rider.id)));
  const countryStats = new Map();
  state.event.riders.forEach((rider) => {
    const current = countryStats.get(rider.nation) || { count: 0, points: 0 };
    countryStats.set(rider.nation, { count: current.count + 1, points: current.points + (rider.uci_points ?? 0) });
  });
  const ridersByCountry = new Map(); visibleRiders.forEach((rider) => ridersByCountry.set(rider.nation, [...(ridersByCountry.get(rider.nation) || []), rider]));
  $("#filter-summary").textContent = `${visibleRiders.length} of ${filteredRiders.length} filtered riders shown (${state.event.riders.length} total). The bar shows UCI rank strength (red = stronger).`;
  ensureRiderViewControls();
  const riderCard = (rider) => { const topTenPosition = state.picks.indexOf(rider.id); const riderFlag = state.riderView === "plain" ? flag(rider.nation) : ""; return `<article draggable="true" class="rider ${topTenPosition >= 0 ? "selected" : ""}" data-rider="${rider.id}" role="button" tabindex="0" title="${topTenPosition >= 0 ? `Top 10 position ${topTenPosition + 1}. Open rider details, or drag to move it.` : "Open rider details"}">${riderFlag}${rider.name}<button type="button" class="rider-add" data-rider-add="${rider.id}" aria-label="Add ${escapeHtml(rider.name)} to your Top 10">+</button>${topTenPosition >= 0 ? `<span class="pick-position"><strong>#${topTenPosition + 1}</strong><small>Top 10</small></span>` : ""}<br><span class="rank">${rankingLabel(rider)}</span><span class="rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span></article>`; };
  if (state.riderView === "plain") {
    $("#riders").innerHTML = `<div class="plain-riders">${sortRiders(visibleRiders).map(riderCard).join("")}</div>`;
  } else {
    const sortedCountries = [...ridersByCountry.entries()].sort(([a], [b]) => {
      const statsA = countryStats.get(a); const statsB = countryStats.get(b);
      const direction = state.riderSortDirection === "asc" ? 1 : -1;
      if (state.riderSort === "rider-count") return direction * (statsA.count - statsB.count || countryName(a).localeCompare(countryName(b)));
      if (state.riderSort === "rank") {
        const bestRankA = Math.min(...state.event.riders.filter((rider) => rider.nation === a).map((rider) => rider.uci_rank));
        const bestRankB = Math.min(...state.event.riders.filter((rider) => rider.nation === b).map((rider) => rider.uci_rank));
        return direction * (bestRankA - bestRankB || countryName(a).localeCompare(countryName(b)));
      }
      return direction * countryName(a).localeCompare(countryName(b));
    });
    $("#riders").innerHTML = sortedCountries.map(([country, riders]) => { const stats = countryStats.get(country); return `<section class="country-group"><h3>${flag(country)}${countryName(country)} <span class="country-meta">${stats.count} riders · ${Math.round(stats.points).toLocaleString()} pts</span></h3><div class="country-riders">${sortRiders(riders).map(riderCard).join("")}</div></section>`; }).join("");
  }
  document.querySelectorAll(".rider").forEach((node) => { node.addEventListener("click", (event) => { if (event.target.closest("[data-rider-add]")) return; openRiderDetail(Number(node.dataset.rider)); }); node.addEventListener("keydown", (event) => { if (event.target.closest("[data-rider-add]")) return; if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openRiderDetail(Number(node.dataset.rider)); } }); node.addEventListener("dragstart", (event) => { event.dataTransfer.setData("text/plain", node.dataset.rider); document.body.classList.add("mobile-dragging"); }); node.addEventListener("dragend", () => document.body.classList.remove("mobile-dragging")); });
  document.querySelectorAll("[data-rider-add]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); addRiderToPicks(Number(button.dataset.riderAdd)); }));
  document.querySelectorAll("[data-picked-rider]").forEach((node) => node.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", node.dataset.pickedRider)));
  document.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); rememberPickState(); state.picks[Number(button.dataset.remove)] = null; render(); }));
  document.querySelectorAll("[data-position]").forEach((slot) => { slot.addEventListener("click", (event) => { if (event.target.closest("[data-remove], [data-move]")) return; if (state.mobilePendingRiderId) { const riderId = state.mobilePendingRiderId; state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking"); insertRider(riderId, Number(slot.dataset.position)); return; } const riderId = Number(slot.dataset.pickedRider); if (riderId) openRiderDetail(riderId); }); slot.addEventListener("dragover", (event) => { event.preventDefault(); slot.classList.add("drag-over"); }); slot.addEventListener("dragleave", () => slot.classList.remove("drag-over")); slot.addEventListener("drop", (event) => { event.preventDefault(); slot.classList.remove("drag-over"); document.body.classList.remove("mobile-dragging"); insertRider(Number(event.dataTransfer.getData("text/plain")), Number(slot.dataset.position)); }); });
  document.querySelectorAll("[data-move]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); const riderId = state.picks[Number(button.dataset.move)]; const isCancelling = state.mobilePendingRiderId === riderId; state.mobilePendingRiderId = isCancelling ? null : riderId; document.body.classList.toggle("mobile-picking", !isCancelling); showMessage("#prediction-message", isCancelling ? "Move cancelled." : `Tap a new Top 10 position for ${riderName(riderId)}.`, !isCancelling); render(); }));
}

function configureFilters() {
  const countries = [...new Set(state.event.riders.map((rider) => rider.nation))]
    .sort((a, b) => countryName(a).localeCompare(countryName(b)));
  const countryPicker = $("#country-picker");
  const countryQuery = $("#country-query");
  const countryOptions = $("#country-options");
  const selectedCountries = $("#selected-countries");
  const updateCountryLabel = () => {
    const selected = state.filters.countries;
    $("#country-filter-label").textContent = selected.length === 0 ? "All countries" : selected.length === 1 ? countryName(selected[0]) : `${selected.length} countries`;
    selectedCountries.classList.toggle("hidden", selected.length === 0);
    selectedCountries.innerHTML = selected.map((country) => `<button type="button" class="country-chip" data-remove-country="${country}">${flag(country)}${countryName(country)} <span aria-hidden="true">×</span></button>`).join("");
  };
  const renderCountryOptions = () => {
    const query = countryQuery.value.trim().toLocaleLowerCase();
    const matches = (country) => `${countryName(country)} ${country}`.toLocaleLowerCase().includes(query);
    const exactCode = (country) => country.toLocaleLowerCase() === query;
    const startsWith = (country) => countryName(country).toLocaleLowerCase().startsWith(query) || country.toLocaleLowerCase().startsWith(query);
    const matchingCountries = [
      ...countries.filter(exactCode),
      ...countries.filter((country) => !exactCode(country) && startsWith(country)),
      ...countries.filter((country) => !startsWith(country) && matches(country)),
    ];
    countryOptions.innerHTML = matchingCountries.length ? matchingCountries.map((country) => `<button type="button" class="country-option ${state.filters.countries.includes(country) ? "selected" : ""}" data-country="${country}" aria-pressed="${state.filters.countries.includes(country)}"><span class="country-check" aria-hidden="true">${state.filters.countries.includes(country) ? "✓" : ""}</span>${flag(country)}<span>${countryName(country)} <span class="muted">${country}</span></span></button>`).join("") : `<p class="muted country-option">No matching countries</p>`;
  };
  countryQuery.addEventListener("input", () => { renderCountryOptions(); countryOptions.classList.remove("hidden"); });
  countryQuery.addEventListener("focus", () => { renderCountryOptions(); countryOptions.classList.remove("hidden"); });
  countryPicker.addEventListener("focusout", (event) => { if (!countryPicker.contains(event.relatedTarget)) countryOptions.classList.add("hidden"); });
  countryOptions.addEventListener("click", (event) => {
    const option = event.target.closest("button[data-country]");
    if (!option) return;
    const country = option.dataset.country;
    state.filters.countries = state.filters.countries.includes(country)
      ? state.filters.countries.filter((selected) => selected !== country)
      : [...state.filters.countries, country];
    updateCountryLabel();
    renderCountryOptions();
    render();
  });
  selectedCountries.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-remove-country]");
    if (!chip) return;
    state.filters.countries = state.filters.countries.filter((country) => country !== chip.dataset.removeCountry);
    updateCountryLabel();
    renderCountryOptions();
    render();
  });

  $("#rider-search").addEventListener("input", (event) => { state.filters.search = event.target.value.trim(); render(); });
  const ranks = state.event.riders.filter((rider) => rider.uci_rank !== 999999).map((rider) => rider.uci_rank);
  const points = state.event.riders.filter((rider) => rider.uci_points !== null).map((rider) => rider.uci_points);
  const maxRank = Math.max(1, ...ranks);
  const maxPoints = Math.max(1, Math.ceil(Math.max(0, ...points)));
  const pointsScaleKnee = 100;
  const rankControl = configureRangeFilter({
    prefix: "rank", min: 1, max: maxRank, format: (value) => `#${value}`,
    toScale: (value) => maxRank === 1 ? 0 : 1000 * Math.log(value) / Math.log(maxRank),
    fromScale: (value) => maxRank === 1 ? 1 : Math.exp((value / 1000) * Math.log(maxRank)),
    roundMin: Math.round, roundMax: Math.round,
  });
  const pointsControl = configureRangeFilter({
    prefix: "points", min: 0, max: maxPoints, format: (value) => `${Number(value).toLocaleString()} pts`,
    toScale: (value) => 1000 * Math.asinh(value / pointsScaleKnee) / Math.asinh(maxPoints / pointsScaleKnee),
    fromScale: (value) => pointsScaleKnee * Math.sinh((value / 1000) * Math.asinh(maxPoints / pointsScaleKnee)),
    roundMin: Math.floor, roundMax: Math.ceil,
  });
  const rankedRiders = state.event.riders
    .filter((rider) => rider.uci_rank !== 999999 && rider.uci_points !== null)
    .sort((a, b) => a.uci_rank - b.uci_rank);
  const rankForPoints = (pointsValue) => {
    const eligible = rankedRiders.filter((rider) => rider.uci_points >= pointsValue);
    return eligible.length ? Math.max(...eligible.map((rider) => rider.uci_rank)) : 1;
  };
  const pointsForRank = (rankValue) => {
    const eligible = rankedRiders.filter((rider) => rider.uci_rank <= rankValue);
    return eligible.length ? Math.min(...eligible.map((rider) => rider.uci_points)) : maxPoints;
  };
  const syncPair = (sourceSelectors, getSourceValue, targetControl, setTargetValue) => sourceSelectors.forEach((selector) => {
    $(selector).addEventListener("input", () => {
      const value = getSourceValue();
      if (value === "") return;
      setTargetValue(targetControl, value);
      render();
    });
  });
  syncPair(["#points-min", "#points-min-scale"], () => state.filters.pointsMin, rankControl, (control, value) => control.setMax(rankForPoints(Number(value))));
  syncPair(["#rank-max", "#rank-max-scale"], () => state.filters.rankMax, pointsControl, (control, value) => control.setMin(pointsForRank(Number(value))));
  syncPair(["#points-max", "#points-max-scale"], () => state.filters.pointsMax, rankControl, (control, value) => control.setMin(Math.min(...rankedRiders.filter((rider) => rider.uci_points <= Number(value)).map((rider) => rider.uci_rank))));
  syncPair(["#rank-min", "#rank-min-scale"], () => state.filters.rankMin, pointsControl, (control, value) => control.setMax(Math.max(...rankedRiders.filter((rider) => rider.uci_rank >= Number(value)).map((rider) => rider.uci_points))));

  $("#clear-filters").addEventListener("click", () => {
    state.filters = { search: "", countries: [], rankMin: "", rankMax: "", pointsMin: "", pointsMax: "", ...emptyAdvancedFilters() };
    $("#rider-search").value = "";
    countryQuery.value = "";
    updateCountryLabel();
    renderCountryOptions();
    ["rank", "points"].forEach((prefix) => {
      $(`#${prefix}-min`).value = "";
      $(`#${prefix}-max`).value = "";
      $(`#${prefix}-min-scale`).value = $(`#${prefix}-min-scale`).min;
      $(`#${prefix}-max-scale`).value = $(`#${prefix}-max-scale`).max;
      $(`#${prefix}-min-scale`).parentElement.style.setProperty("--range-start", "0%");
      $(`#${prefix}-min-scale`).parentElement.style.setProperty("--range-end", "100%");
      $(`#${prefix}-range-value`).textContent = "Any";
    });
    renderAdvancedFilters();
    render();
  });
}

function configureRangeFilter({ prefix, min, max, format, toScale, fromScale, roundMin, roundMax }) {
  const minInput = $(`#${prefix}-min`);
  const maxInput = $(`#${prefix}-max`);
  const minScale = $(`#${prefix}-min-scale`);
  const maxScale = $(`#${prefix}-max-scale`);
  const valueLabel = $(`#${prefix}-range-value`);
  const filterMinKey = `${prefix}Min`;
  const filterMaxKey = `${prefix}Max`;
  const rawValue = (scale) => (scale === minScale ? roundMin : roundMax)(fromScale(Number(scale.value)));
  const updateTrack = () => {
    const track = minScale.parentElement;
    track.style.setProperty("--range-start", `${Number(minScale.value) / 10}%`);
    track.style.setProperty("--range-end", `${Number(maxScale.value) / 10}%`);
  };
  [minScale, maxScale].forEach((scale) => { scale.min = 0; scale.max = 1000; scale.step = 1; });
  minScale.value = 0;
  maxScale.value = 1000;
  updateTrack();

  const updateLabel = () => {
    const low = state.filters[filterMinKey];
    const high = state.filters[filterMaxKey];
    valueLabel.textContent = low || high ? `${low ? format(low) : "Any"} – ${high ? format(high) : "Any"}` : "Any";
  };
  const setFromText = (input, scale, key) => {
    const value = input.value.trim();
    state.filters[key] = value;
    if (value && Number(value) >= min && Number(value) <= max) scale.value = Math.round(toScale(Number(value)));
    updateTrack();
    updateLabel();
    render();
  };
  minInput.addEventListener("input", () => setFromText(minInput, minScale, filterMinKey));
  maxInput.addEventListener("input", () => setFromText(maxInput, maxScale, filterMaxKey));
  minScale.addEventListener("input", () => {
    if (Number(minScale.value) > Number(maxScale.value)) maxScale.value = minScale.value;
    updateTrack();
    minInput.value = rawValue(minScale);
    maxInput.value = rawValue(maxScale);
    state.filters[filterMinKey] = minInput.value;
    state.filters[filterMaxKey] = maxInput.value;
    updateLabel();
    render();
  });
  maxScale.addEventListener("input", () => {
    if (Number(maxScale.value) < Number(minScale.value)) minScale.value = maxScale.value;
    updateTrack();
    minInput.value = rawValue(minScale);
    maxInput.value = rawValue(maxScale);
    state.filters[filterMinKey] = minInput.value;
    state.filters[filterMaxKey] = maxInput.value;
    updateLabel();
    render();
  });
  const setBoundary = (input, scale, key, value, round) => {
    const numeric = Math.max(min, Math.min(max, round(value)));
    input.value = numeric;
    scale.value = Math.round(toScale(numeric));
    state.filters[key] = String(numeric);
    updateTrack();
    updateLabel();
  };
  return { setMin: (value) => setBoundary(minInput, minScale, filterMinKey, value, roundMin), setMax: (value) => setBoundary(maxInput, maxScale, filterMaxKey, value, roundMax) };
}

async function loadPrediction() { try { const prediction = await request(`/api/events/${state.event.id}/predictions/${state.player.id}`); state.picks = Array(10).fill(null); prediction.selections.forEach((item) => state.picks[item.position - 1] = item.rider_id); } catch (_) { state.picks = Array(10).fill(null); } state.savedPicks = [...state.picks]; resetPickHistory(); render(); }
let filtersConfigured = false;
async function loadEvent() {
  state.event = await request("/api/events/active");
  if (!filtersConfigured) { configureFilters(); filtersConfigured = true; }
  const deadline = new Intl.DateTimeFormat(undefined, { weekday:"long", month:"long", day:"numeric", hour:"numeric", minute:"2-digit" }).format(new Date(state.event.prediction_deadline));
  $("#event-title").textContent = state.event.name;
  $("#event-meta").textContent = `Submit your prediction by ${deadline}.`;
}
function restoreSession() {
  const saved = localStorage.getItem("ten-up-player");
  if (saved) {
    try { state.player = JSON.parse(saved); } catch (_) { localStorage.removeItem("ten-up-player"); state.player = null; }
  }
  // The sign-in card starts hidden so a returning player never sees it flash
  // during the seconds the API spends waking up.
  if (!state.player) $("#identity").classList.remove("hidden");
  renderSessionControls();
}
async function boot() {
  restoreSession();
  const awake = await whenApiReady();
  if (!awake) return;
  try {
    await loadEvent();
    if (state.player) {
      $("#identity").classList.add("hidden");
      $("#prediction").classList.remove("hidden");
      renderSessionControls();
      await loadPrediction();
    } else {
      $("#identity").classList.remove("hidden");
    }
  } catch (error) {
    $("#event-title").textContent = "Event unavailable";
    $("#event-meta").textContent = error.message;
    setApiStatus("error", error.message);
    if (!state.player) $("#identity").classList.remove("hidden");
  }
}
$("#join").addEventListener("click", async () => {
  const username = $("#username").value.trim();
  if (!username) return showMessage("#identity-message", "Enter a username first.");
  const waking = "Waking the server… you will go straight to your Top 10 as soon as it answers.";
  const slowRequest = window.setTimeout(() => showWakeToast(waking), 2500);
  $("#join").disabled = true;
  try {
    if (state.apiStatus !== "ready") {
      showWakeToast(waking);
      if (!await whenApiReady()) return showMessage("#identity-message", "The server is still not answering. Give it a minute and try again.");
    }
    if (!state.event) await loadEvent();
    try {
      state.player = await request(`/api/players/by-username/${encodeURIComponent(username)}`);
    } catch (error) {
      if (!error.message.includes("Username not found")) throw error;
      state.player = await request("/api/players", { method:"POST", body: JSON.stringify({username}) });
    }
    localStorage.setItem("ten-up-player", JSON.stringify(state.player));
    $("#identity").classList.add("hidden");
    $("#prediction").classList.remove("hidden");
    renderSessionControls();
    await loadPrediction();
  } catch (error) {
    showMessage("#identity-message", error.message);
  } finally {
    window.clearTimeout(slowRequest);
    hideWakeToast();
    $("#join").disabled = false;
  }
});
$("#username").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); $("#join").click(); } });
$("#close-rider-detail").addEventListener("click", () => $("#rider-detail-modal").close());
$("#rider-detail-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#advanced-filter-button").addEventListener("click", openAdvancedFilters);
$("#close-advanced-filters").addEventListener("click", () => $("#advanced-filter-modal").close());
$("#advanced-filter-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#apply-advanced-filters").addEventListener("click", () => {
  readAdvancedFilterValues();
  $("#advanced-filter-modal").close();
  renderAdvancedFilters();
  render();
});
$("#reset-advanced-filters").addEventListener("click", () => {
  Object.assign(state.filters, emptyAdvancedFilters());
  renderAdvancedFilters();
  render();
});
$("#logout").addEventListener("click", () => { if (hasUnsavedPickChanges() && !window.confirm("Did you forget to save your prediction?")) return; localStorage.removeItem("ten-up-player"); state.player = null; state.picks = Array(10).fill(null); state.savedPicks = Array(10).fill(null); resetPickHistory(); state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking", "mobile-dragging"); $("#prediction").classList.add("hidden"); $("#identity").classList.remove("hidden"); $("#username").value = ""; renderSessionControls(); showMessage("#identity-message", "You have logged out on this device.", true); $("#username").focus(); });
$("#save").addEventListener("click", async () => { if (!savedPicks().length) return showMessage("#prediction-message", "Pick at least one rider first."); try { await request(`/api/events/${state.event.id}/predictions`, { method:"PUT", body: JSON.stringify({ player_id: state.player.id, selections: state.picks.flatMap((rider_id, index) => rider_id ? [{position:index+1, rider_id}] : []) }) }); state.savedPicks = [...state.picks]; showMessage("#prediction-message", "Prediction saved. You can edit it until the deadline.", true); render(); } catch (error) { showMessage("#prediction-message", error.message); } });
window.addEventListener("beforeunload", (event) => { if (!state.player || !hasUnsavedPickChanges()) return; event.preventDefault(); event.returnValue = ""; });
window.addEventListener("keydown", (event) => { if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target instanceof HTMLElement && event.target.matches("input, textarea, select")) return; if (event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) restorePickState(state.redoStack, state.undoStack, "Redid last change."); else restorePickState(state.undoStack, state.redoStack, "Undid last change."); } else if (event.key.toLowerCase() === "y") { event.preventDefault(); restorePickState(state.redoStack, state.undoStack, "Redid last change."); } });
boot();
