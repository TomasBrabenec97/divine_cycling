// Season-start snapshot every "trend" and "change" in the UI is measured against.
const SEASON_START = "2025-12-30";
const TREND_THRESHOLD_PCT = 10;
const NO_TEAM = "No trade team";
const WILDCARD_COUNT = 3;
// One row per rider-data filter in the advanced panel. `value` reads the number
// a rider is compared on; filters with `modes` can compare absolute or relative change.
const PROFILE_FILTERS = [
  { key: "winsSeason", label: "Wins", hint: "this season", value: (rider, reference) => currentSeason(reference)?.wins ?? 0 },
  { key: "winsTotal", label: "Career wins", value: (rider, reference) => reference.profile?.wins_total ?? null },
  { key: "top10sSeason", label: "Top 10 finishes", hint: "this season", value: (rider, reference) => currentSeason(reference)?.top10s ?? 0 },
  { key: "racedaysSeason", label: "Race days", hint: "this season", value: (rider, reference) => currentSeason(reference)?.racedays ?? 0 },
  { key: "age", label: "Age", value: (rider, reference) => reference.profile?.age ?? null },
  { key: "pointsChange", label: "UCI points change", hint: "since season start", modes: [{ id: "abs", label: "pts" }, { id: "pct", label: "%" }], value: (rider, reference, mode) => pointsTrend(rider, reference)?.[mode] ?? null },
  { key: "rankChange", label: "UCI rank change", hint: "since season start", modes: [{ id: "abs", label: "places" }, { id: "pct", label: "%" }], value: (rider, reference, mode) => rankTrend(rider, reference)?.[mode] ?? null },
];
const emptyProfileFilters = () => Object.fromEntries(PROFILE_FILTERS.map((filter) => [filter.key, { min: "", max: "", mode: filter.modes?.[0].id }]));
const emptyAdvancedFilters = () => ({ resultCells: [], resultMin: "", resultMax: "", profile: emptyProfileFilters() });
const emptyFilters = () => ({ search: "", countries: [], teams: [], rankMin: "", rankMax: "", pointsMin: "", pointsMax: "", ...emptyAdvancedFilters() });
const state = { event: null, player: null, reference: null, referencePromise: null, teamIcons: {}, apiStatus: "starting", apiReadyPromise: null, picks: Array(10).fill(null), savedPicks: Array(10).fill(null), wildcards: Array(WILDCARD_COUNT).fill(null), savedWildcards: Array(WILDCARD_COUNT).fill(null), undoStack: [], redoStack: [], mobilePendingRiderId: null, riderView: "country", riderSort: "alphabetical", riderSortDirection: "asc", riderSelectionFilter: "all", filters: emptyFilters(), advancedDraft: null, lists: { final: null, templates: [] }, activeList: "final", renamingList: null, favourites: new Set() };
const $ = (selector) => document.querySelector(selector);
const isMobileLayout = () => window.matchMedia("(max-width: 650px)").matches;
const countryNames = new Intl.DisplayNames(["en"], { type: "region" });
const flag = (country) => `<span class="flag"><img src="flags/${country.toLocaleLowerCase()}.png" alt="${country} flag" /></span>`;
const countryName = (country) => {
  try { return countryNames.of(country) || country; } catch (_) { return country; }
};
const teamLabel = (team) => team || NO_TEAM;
// Jerseys come from teams/index.json; a team without one gets its initials instead.
function teamIcon(team) {
  const file = team && state.teamIcons[team];
  if (file) return `<span class="team-icon"><img src="teams/${encodeURIComponent(file)}" alt="" loading="lazy" /></span>`;
  const initials = team ? team.split(/[\s|-]+/).filter((word) => /^[\p{L}\d]/u.test(word)).slice(0, 2).map((word) => word[0]).join("").toUpperCase() : "–";
  return `<span class="team-icon team-monogram" aria-hidden="true">${escapeHtml(initials)}</span>`;
}
async function loadTeamIcons() {
  try {
    const response = await fetch("teams/index.json");
    if (response.ok) state.teamIcons = await response.json();
  } catch (_) {
    state.teamIcons = {};
  }
}
const savedPicks = () => [...state.picks, ...state.wildcards].filter(Boolean);
const isPicked = (riderId) => state.picks.includes(riderId) || state.wildcards.includes(riderId);
const hasUnsavedPickChanges = () => state.picks.some((riderId, index) => riderId !== state.savedPicks[index])
  || state.wildcards.some((riderId, index) => riderId !== state.savedWildcards[index]);
// Undo history holds the Top 10 and the wildcards together: one move can touch both.
const pickSnapshot = () => ({ picks: [...state.picks], wildcards: [...state.wildcards] });
const rememberPickState = () => { state.undoStack.push(pickSnapshot()); if (state.undoStack.length > 50) state.undoStack.shift(); state.redoStack = []; };
const resetPickHistory = () => { state.undoStack = []; state.redoStack = []; };
function restorePickState(from, to, message) { const next = from.pop(); if (!next) return; to.push(pickSnapshot()); if (to.length > 50) to.shift(); state.picks = next.picks; state.wildcards = next.wildcards; state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking"); showMessage("#prediction-message", message, true); render(); }
const formatMultiplier = (value) => `×${Number(value ?? 1).toFixed(2)}`;
const HEART_PATH = "M12 20.5s-7.5-4.6-9.3-9.2C1.5 8.2 3.3 4.8 6.6 4.5c2-.2 3.7.9 5.4 3 1.7-2.1 3.4-3.2 5.4-3 3.3.3 5.1 3.7 3.9 6.8-1.8 4.6-9.3 9.2-9.3 9.2Z";
function heartButton(rider, className) {
  const on = state.favourites.has(rider.id);
  const label = `${on ? "Remove" : "Add"} ${escapeHtml(rider.name)} ${on ? "from" : "to"} your favourites`;
  return `<button type="button" class="${className} ${on ? "on" : ""}" data-rider-fav="${rider.id}" aria-pressed="${on}" aria-label="${label}" title="${on ? "Favourite: tap to remove" : "Add to favourites"}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /></svg></button>`;
}
// Favourites narrow the pool you build lists from; the heart flips at once and
// the server catches up, rolling back if it refuses.
async function toggleFavourite(riderId) {
  const on = !state.favourites.has(riderId);
  if (on) state.favourites.add(riderId); else state.favourites.delete(riderId);
  refreshFavouriteViews();
  try {
    await request(`/api/events/${state.event.id}/players/${state.player.id}/favourites/${riderId}`, { method: on ? "PUT" : "DELETE" });
  } catch (error) {
    if (on) state.favourites.delete(riderId); else state.favourites.add(riderId);
    refreshFavouriteViews();
    showMessage("#prediction-message", error.message);
  }
}
function refreshFavouriteViews() {
  render();
  const detail = $("#rider-detail-modal");
  const riderId = Number(detail.dataset.rider);
  if (detail.open && riderId) {
    const button = detail.querySelector("[data-rider-fav]");
    const rider = state.event.riders.find((item) => item.id === riderId);
    if (button && rider) button.outerHTML = heartButton(rider, "detail-fav");
  }
}
const rankingLabel = (rider) => rider.uci_rank === 999999 ? "UCI unranked" : `UCI #${rider.uci_rank}${rider.uci_points === null ? "" : ` · ${rider.uci_points.toLocaleString()} pts`}`;
let predictionMessageVersion = 0;

const API_BASE_URL = (window.DIVINE_API_BASE_URL || "").replace(/\/$/, "");
async function request(url, options = {}) {
  const response = await fetch(`${API_BASE_URL}${url}`, { headers: { "Content-Type": "application/json" }, ...options });
  // A 204 (a deleted template) has no body at all.
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    // Validation errors arrive as a list; show their messages, not "[object Object]".
    const detail = Array.isArray(body?.detail) ? body.detail.map((item) => String(item.msg).replace(/^Value error, /, "")).join(" ") : body?.detail;
    throw new Error(detail || "Something went wrong");
  }
  return body;
}

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
  if (result.position <= 10) return `<span class="result-top-ten">${result.position}</span>`;
  return `<span class="result-place">${result.position}</span>`;
}

// The PCS reference set is one payload; the trend arrows want it early, the
// detail card and the advanced filters must wait for it.
function loadReference() {
  state.referencePromise = state.referencePromise || request("/api/riders/reference").then((reference) => {
    reference.currentSeason = Math.max(0, ...reference.riders.flatMap((rider) => rider.seasons.map((season) => season.season)));
    reference.byId = new Map(reference.riders.map((rider) => [rider.id, rider]));
    state.reference = reference;
    return reference;
  }).catch((error) => {
    state.referencePromise = null;
    throw error;
  });
  return state.referencePromise;
}
const riderReference = (riderId) => state.reference?.byId.get(riderId);
const currentSeason = (reference) => reference?.seasons.find((season) => season.season === state.reference?.currentSeason);
function seasonStartRanking(reference) {
  const rankings = [...(reference?.rankings || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  return rankings.find((item) => String(item.date).startsWith(SEASON_START)) || rankings[0];
}
function pointsTrend(rider, reference) {
  const start = seasonStartRanking(reference);
  if (start?.uci_points === null || start?.uci_points === undefined || rider.uci_points === null) return null;
  const abs = rider.uci_points - start.uci_points;
  return { abs, pct: start.uci_points > 0 ? (100 * abs) / start.uci_points : null, from: start.uci_points };
}
function rankTrend(rider, reference) {
  const start = seasonStartRanking(reference);
  if (!start?.uci_rank || rider.uci_rank === 999999) return null;
  const abs = start.uci_rank - rider.uci_rank;
  return { abs, pct: (100 * abs) / start.uci_rank, from: start.uci_rank };
}
function trendDirection(rider, reference) {
  const trend = pointsTrend(rider, reference);
  if (!trend) return null;
  const pct = trend.pct ?? (trend.abs > 0 ? Infinity : trend.abs < 0 ? -Infinity : 0);
  return pct >= TREND_THRESHOLD_PCT ? "up" : pct <= -TREND_THRESHOLD_PCT ? "down" : "flat";
}
const TREND_PATHS = { up: "M7 17 17 7M9 7h8v8", down: "M7 7l10 10M17 9v8H9", flat: "M5 12h14M13 7l5 5-5 5" };
function trendArrow(rider) {
  const reference = riderReference(rider.id);
  const direction = reference && trendDirection(rider, reference);
  if (!direction) return "";
  const trend = pointsTrend(rider, reference);
  const change = trend.pct === null ? `${trend.abs >= 0 ? "+" : ""}${Math.round(trend.abs).toLocaleString()} pts` : `${trend.pct >= 0 ? "+" : ""}${Math.round(trend.pct)}%`;
  const label = `${{ up: "Trending up", down: "Trending down", flat: "Holding steady" }[direction]}: UCI points ${change} since the season start`;
  return `<span class="trend-arrow trend-${direction}" title="${label}" aria-label="${label}" role="img"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${TREND_PATHS[direction]}" /></svg></span>`;
}

const activeProfileFilters = (filters) => PROFILE_FILTERS.filter((filter) => filters.profile[filter.key].min !== "" || filters.profile[filter.key].max !== "");
const hasResultFilter = (filters) => filters.resultCells.length > 0 || filters.resultMin !== "" || filters.resultMax !== "";
const countAdvancedFilters = (filters = state.filters) => (hasResultFilter(filters) ? 1 : 0) + activeProfileFilters(filters).length;
const inRange = (value, min, max) => value !== null && value !== undefined && Number.isFinite(value)
  && (min === "" || value >= Number(min)) && (max === "" || value <= Number(max));
function matchesAdvancedFilters(rider, filters = state.filters) {
  if (!countAdvancedFilters(filters)) return true;
  const reference = riderReference(rider.id);
  if (!reference) return false;
  if (hasResultFilter(filters)) {
    // With no editions picked, a place range applies to every tracked race.
    const cells = new Set(filters.resultCells);
    const qualifies = (result) => !result.status && result.position
      && (!cells.size || cells.has(`${result.race_key}:${result.year}`))
      && inRange(result.position, filters.resultMin, filters.resultMax);
    if (!reference.results.some(qualifies)) return false;
  }
  return activeProfileFilters(filters).every((filter) => {
    const { min, max, mode } = filters.profile[filter.key];
    return inRange(filter.value(rider, reference, mode), min, max);
  });
}

function updateAdvancedFilterBadge() {
  const count = countAdvancedFilters();
  const badge = $("#advanced-filter-count");
  badge.textContent = count || "";
  badge.classList.toggle("hidden", !count);
  badge.setAttribute("aria-label", count ? `${count} advanced filters applied` : "");
}

const cloneAdvancedFilters = (filters) => ({ resultCells: [...filters.resultCells], resultMin: filters.resultMin, resultMax: filters.resultMax, profile: structuredClone(filters.profile) });
function trackedRaces() {
  const calendarDate = (race) => {
    const datedEditions = race.editions.filter((edition) => edition.date);
    if (!datedEditions.length) return "99-99";
    const latestYear = Math.max(...datedEditions.map((edition) => edition.year));
    return String(datedEditions.find((edition) => edition.year === latestYear).date).slice(5);
  };
  const races = [...state.reference.races].sort((left, right) => calendarDate(left).localeCompare(calendarDate(right)) || left.label.localeCompare(right.label));
  const years = [...new Set(races.flatMap((race) => race.editions.map((edition) => edition.year)))].sort((a, b) => a - b);
  return { races, years };
}
// Editions a result filter can use: ridden ones, not the upcoming or never-held.
function selectableCells() {
  const { races } = trackedRaces();
  return races.flatMap((race) => race.editions.filter((edition) => !edition.note).map((edition) => ({ race: race.key, year: edition.year, id: `${race.key}:${edition.year}` })));
}

function renderResultMatrix() {
  const draft = state.advancedDraft;
  const { races, years } = trackedRaces();
  const selected = new Set(draft.resultCells);
  const selectable = selectableCells();
  const allOn = (cells) => cells.length > 0 && cells.every((cell) => selected.has(cell.id));
  const header = years.map((year) => {
    const cells = selectable.filter((cell) => cell.year === year);
    return `<th scope="col"><button type="button" class="matrix-axis ${allOn(cells) ? "on" : ""}" data-matrix-year="${year}" aria-pressed="${allOn(cells)}" ${cells.length ? "" : "disabled"}>${year}</button></th>`;
  }).join("");
  const rows = races.map((race) => {
    const rowCells = selectable.filter((cell) => cell.race === race.key);
    const cells = years.map((year) => {
      const edition = race.editions.find((item) => item.year === year);
      if (!edition) return `<td><span class="matrix-cell missing" title="Not held in ${year}"></span></td>`;
      if (edition.note) return `<td><span class="matrix-cell upcoming" title="${escapeHtml(race.label)} ${year}: ${escapeHtml(edition.note)}">·</span></td>`;
      const id = `${race.key}:${year}`;
      return `<td><button type="button" class="matrix-cell ${selected.has(id) ? "on" : ""}" data-matrix-cell="${id}" aria-pressed="${selected.has(id)}" aria-label="${escapeHtml(race.label)} ${year}"></button></td>`;
    }).join("");
    return `<tr><th scope="row"><button type="button" class="matrix-axis ${allOn(rowCells) ? "on" : ""}" data-matrix-race="${race.key}" aria-pressed="${allOn(rowCells)}">${escapeHtml(race.label)}</button></th>${cells}</tr>`;
  }).join("");
  const count = draft.resultCells.length;
  $("#result-matrix").innerHTML = `<table class="result-matrix"><thead><tr><th scope="col"><button type="button" class="matrix-axis ${allOn(selectable) ? "on" : ""}" data-matrix-all aria-pressed="${allOn(selectable)}">All races</button></th>${header}</tr></thead><tbody>${rows}</tbody></table>`;
  $("#result-matrix-summary").innerHTML = count
    ? `${count} ${count === 1 ? "edition" : "editions"} selected <button type="button" class="text-button" data-matrix-clear>Clear</button>`
    : "No edition selected: a place range alone applies to every tracked race.";
}

function renderAdvancedMatchCount() {
  const button = $("#apply-advanced-filters");
  if (!state.advancedDraft || !state.event) return;
  const draftFilters = { ...state.filters, ...state.advancedDraft };
  const matching = state.event.riders.filter((rider) => matchesFilters(rider, draftFilters)).length;
  button.textContent = `Show ${matching} ${matching === 1 ? "rider" : "riders"}`;
}

function renderAdvancedFilters() {
  const content = $("#advanced-filter-content");
  updateAdvancedFilterBadge();
  if (!state.reference || !state.advancedDraft) return;
  const draft = state.advancedDraft;
  const season = state.reference.currentSeason;
  const profileRows = PROFILE_FILTERS.map((filter) => {
    const values = draft.profile[filter.key];
    const modes = filter.modes ? `<div class="unit-toggle" role="group" aria-label="${filter.label} unit">${filter.modes.map((mode) => `<button type="button" data-profile-mode="${filter.key}:${mode.id}" class="${values.mode === mode.id ? "active" : ""}" aria-pressed="${values.mode === mode.id}">${mode.label}</button>`).join("")}</div>` : "";
    return `<div class="profile-filter"><div class="profile-filter-label"><span>${filter.label}</span>${filter.hint ? `<small>${filter.hint}</small>` : ""}</div>${modes}<div class="advanced-range"><input type="number" data-profile-min="${filter.key}" placeholder="From" aria-label="${filter.label} from" value="${escapeHtml(values.min)}" /><span>to</span><input type="number" data-profile-max="${filter.key}" placeholder="To" aria-label="${filter.label} to" value="${escapeHtml(values.max)}" /></div></div>`;
  }).join("");
  content.innerHTML = `<section class="advanced-filter-section"><div class="advanced-filter-section-heading"><div><h3>Past results</h3><p class="muted">Tap editions, a whole race or a whole year. A rider matches with one finish in range.</p></div><div class="advanced-range"><span class="advanced-range-label">Finished</span><input id="advanced-result-min" type="number" min="1" placeholder="1" aria-label="Best finishing place" value="${escapeHtml(draft.resultMin)}" /><span>to</span><input id="advanced-result-max" type="number" min="1" placeholder="Any" aria-label="Worst finishing place" value="${escapeHtml(draft.resultMax)}" /></div></div><div id="result-matrix" class="result-matrix-wrap"></div><p id="result-matrix-summary" class="muted matrix-summary"></p></section><section class="advanced-filter-section"><div class="advanced-filter-section-heading"><div><h3>Rider data</h3><p class="muted">Season figures are for ${season}. Changes compare today's UCI ranking with the season start (${SEASON_START}); positive means improving.</p></div></div><div class="profile-filter-grid">${profileRows}</div></section>`;
  renderResultMatrix();
  renderAdvancedMatchCount();
}

function toggleMatrixCells(cellIds) {
  const selected = new Set(state.advancedDraft.resultCells);
  const turnOn = !cellIds.every((id) => selected.has(id));
  cellIds.forEach((id) => (turnOn ? selected.add(id) : selected.delete(id)));
  state.advancedDraft.resultCells = [...selected];
  renderResultMatrix();
  renderAdvancedMatchCount();
}

function handleAdvancedFilterClick(event) {
  const target = event.target.closest("button");
  if (!target || !state.advancedDraft) return;
  const selectable = selectableCells();
  if (target.dataset.matrixCell) toggleMatrixCells([target.dataset.matrixCell]);
  else if (target.dataset.matrixRace) toggleMatrixCells(selectable.filter((cell) => cell.race === target.dataset.matrixRace).map((cell) => cell.id));
  else if (target.dataset.matrixYear) toggleMatrixCells(selectable.filter((cell) => cell.year === Number(target.dataset.matrixYear)).map((cell) => cell.id));
  else if (target.hasAttribute("data-matrix-all")) toggleMatrixCells(selectable.map((cell) => cell.id));
  else if (target.hasAttribute("data-matrix-clear")) toggleMatrixCells([...state.advancedDraft.resultCells]);
  else if (target.dataset.profileMode) {
    const [key, mode] = target.dataset.profileMode.split(":");
    state.advancedDraft.profile[key].mode = mode;
    target.parentElement.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("active", button === target);
      button.setAttribute("aria-pressed", String(button === target));
    });
    renderAdvancedMatchCount();
  }
}

function handleAdvancedFilterInput(event) {
  const input = event.target;
  if (!state.advancedDraft || !(input instanceof HTMLInputElement)) return;
  const value = input.value.trim();
  if (input.id === "advanced-result-min") state.advancedDraft.resultMin = value;
  else if (input.id === "advanced-result-max") state.advancedDraft.resultMax = value;
  else if (input.dataset.profileMin) state.advancedDraft.profile[input.dataset.profileMin].min = value;
  else if (input.dataset.profileMax) state.advancedDraft.profile[input.dataset.profileMax].max = value;
  else return;
  renderAdvancedMatchCount();
}

async function openAdvancedFilters() {
  const dialog = $("#advanced-filter-modal");
  const content = $("#advanced-filter-content");
  const pageScrollY = window.scrollY;
  const positionModalAndPage = () => {
    dialog.scrollTop = 0;
    window.scrollTo(0, pageScrollY);
  };
  state.advancedDraft = cloneAdvancedFilters(state.filters);
  $("#apply-advanced-filters").textContent = "Apply filters";
  if (!state.reference) content.innerHTML = "<p class=\"muted\">Loading race history…</p>";
  if (!dialog.open) {
    dialog.showModal();
    window.requestAnimationFrame(positionModalAndPage);
  }
  try {
    await loadReference();
    renderAdvancedFilters();
  } catch (error) {
    content.innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
  }
  window.requestAnimationFrame(positionModalAndPage);
}

function renderRiderDetail(riderId) {
  const rider = state.event.riders.find((item) => item.id === riderId);
  const reference = riderReference(riderId);
  const content = $("#rider-detail-content");
  if (!rider || !reference) {
    content.innerHTML = "<p class=\"muted\">No reference data is available for this rider.</p>";
    return;
  }
  const seasonStart = seasonStartRanking(reference);
  const profile = reference.profile || {};
  const rankChange = rankTrend(rider, reference)?.abs ?? null;
  const pointChange = pointsTrend(rider, reference)?.abs ?? null;
  const trendClass = rankChange === null ? "neutral" : rankChange > 0 ? "better" : rankChange < 0 ? "worse" : "neutral";
  const trendText = rankChange === null
    ? "No comparable ranking"
    : `${rankChange > 0 ? "↑" : rankChange < 0 ? "↓" : "→"} ${Math.abs(rankChange)} places${pointChange === null ? "" : ` · ${pointChange >= 0 ? "+" : ""}${Math.round(pointChange).toLocaleString()} pts`}`;
  const { races, years } = trackedRaces();
  const resultMap = new Map(reference.results.map((result) => [`${result.race_key}-${result.year}`, result]));
  const season = currentSeason(reference);
  const careerWins = profile.wins_total === null || profile.wins_total === undefined ? "" : `<span class="rider-detail-wins">${profile.wins_total} career wins</span>`;
  const team = `<span class="rider-detail-team-name">${rider.team ? `${teamIcon(rider.team)}${escapeHtml(rider.team)}` : escapeHtml(countryName(rider.nation))}</span>`;
  const pcsLink = profile.profile_url ? `<a class="pcs-link" href="${escapeHtml(profile.profile_url)}" target="_blank" rel="noopener noreferrer">ProCyclingStats profile <span aria-hidden="true">↗</span></a>` : "";
  const favourite = state.player ? heartButton(rider, "detail-fav") : "";
  content.innerHTML = `<header class="rider-detail-header"><div><p class="eyebrow">RIDER PROFILE</p><h2>${flag(rider.nation)}${escapeHtml(rider.name)}</h2><p class="rider-detail-team">${team}${careerWins}</p><div class="rider-detail-links">${favourite}${pcsLink}</div></div></header><section class="rider-stat-grid"><div><span>Age</span><strong>${profile.age ?? "—"}</strong></div><div><span>UCI now</span><strong>${displayPoints(rider.uci_points)}</strong><small>${displayRank(rider.uci_rank === 999999 ? null : rider.uci_rank)}</small></div><div><span>Season start</span><strong>${displayPoints(seasonStart?.uci_points)}</strong><small>${displayRank(seasonStart?.uci_rank)}</small></div><div class="ranking-trend ${trendClass}"><span>Ranking trend</span><strong>${trendText}</strong><small>${season ? `${season.season}: ${season.wins} wins · ${season.top10s} top 10s` : "Season totals unavailable"}</small></div></section><section class="rider-history"><div class="rider-history-heading"><div><p class="eyebrow">PAST RESULTS</p><h3>Tracked one-day races</h3></div></div><div class="rider-history-table-wrap"><table class="rider-history-table"><thead><tr><th scope="col">Race</th>${years.map((year) => `<th scope="col">${year}${races.some((race) => race.editions.some((edition) => edition.year === year && edition.note)) ? "<small>upcoming</small>" : ""}</th>`).join("")}</tr></thead><tbody>${races.map((race) => `<tr><th scope="row">${escapeHtml(race.label)}</th>${years.map((year) => `<td>${resultCell(resultMap.get(`${race.key}-${year}`))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="rider-history-legend"><span class="result-medal medal-1">1</span> podium <span class="result-top-ten">7</span> top 10 <span class="result-status">DNF</span> did not finish · blank: not on the startlist</p></section>`;
}

async function openRiderDetail(riderId) {
  const dialog = $("#rider-detail-modal");
  dialog.dataset.rider = String(riderId);
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
    await loadReference();
    renderRiderDetail(riderId);
    window.requestAnimationFrame(positionModalAndPage);
  } catch (error) {
    content.innerHTML = `<p class="message">${escapeHtml(error.message)}</p>`;
    window.requestAnimationFrame(positionModalAndPage);
  }
}
function renderSessionControls() { const signedIn = Boolean(state.player); $("#session-controls").classList.toggle("hidden", !signedIn); $("#event-context").classList.toggle("hidden", !signedIn); $("#session-username").textContent = signedIn ? state.player.username : ""; }
function insertRider(riderId, position) {
  const wildcardSlot = state.wildcards.indexOf(riderId);
  if (wildcardSlot < 0 && state.picks.indexOf(riderId) === position) return;
  rememberPickState();
  // Promoting a wildcard into the Top 10 frees its slot.
  if (wildcardSlot >= 0) state.wildcards[wildcardSlot] = null;
  placeInTopTen(riderId, position);
  render();
}
function placeInTopTen(riderId, position) {
  const previous = state.picks.indexOf(riderId);
  if (previous >= 0) {
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
    return;
  }
  const nextEmpty = state.picks.findIndex((picked, index) => index >= position && picked === null);
  const end = nextEmpty >= 0 ? nextEmpty : 9;
  for (let index = end; index > position; index -= 1) state.picks[index] = state.picks[index - 1];
  state.picks[position] = riderId;
}
// Moving a rider onto a wildcard slot swaps them with whoever is there: a
// wildcard trades slots, a Top 10 pick trades places with the displaced
// wildcard, and a rider from the list simply replaces it.
function insertWildcard(riderId, slot) {
  const previousSlot = state.wildcards.indexOf(riderId);
  if (previousSlot === slot) return;
  rememberPickState();
  const displaced = state.wildcards[slot];
  const topTenPosition = state.picks.indexOf(riderId);
  if (previousSlot >= 0) state.wildcards[previousSlot] = displaced;
  else if (topTenPosition >= 0) state.picks[topTenPosition] = displaced;
  state.wildcards[slot] = riderId;
  render();
}

const riderName = (riderId) => state.event.riders.find((rider) => rider.id === riderId)?.name || "this rider";
function cancelPendingPick() {
  if (!state.mobilePendingRiderId) return;
  state.mobilePendingRiderId = null;
  document.body.classList.remove("mobile-picking");
  showMessage("#prediction-message", "Pick cancelled.", true);
  render();
}
function addRiderToPicks(riderId) {
  if (isMobileLayout()) {
    if (state.mobilePendingRiderId === riderId) return cancelPendingPick();
    state.mobilePendingRiderId = riderId;
    document.body.classList.add("mobile-picking");
    showMessage("#prediction-message", `Tap a Top 10 position or a wildcard slot for ${riderName(riderId)}.`, true);
    render();
    return;
  }
  if (state.picks.includes(riderId)) return showMessage("#prediction-message", `${riderName(riderId)} is already in your Top 10.`);
  if (state.wildcards.includes(riderId)) return showMessage("#prediction-message", `${riderName(riderId)} is already one of your wildcards.`);
  // + fills the Top 10 first, then the wildcards.
  const firstEmpty = state.picks.indexOf(null);
  if (firstEmpty >= 0) return insertRider(riderId, firstEmpty);
  const firstWildcard = state.wildcards.indexOf(null);
  if (firstWildcard >= 0) return insertWildcard(riderId, firstWildcard);
  showMessage("#prediction-message", "Your Top 10 and wildcards are full. Drag a rider onto a position or a wildcard slot to swap them in.");
}

function rankFill(rider) {
  if (rider.uci_rank === 999999) return 0;
  return Math.max(8, Math.round(100 * (1 - Math.min(rider.uci_rank - 1, 499) / 499)));
}

function matchesFilters(rider, filters = state.filters) {
  const { search, countries, teams, rankMin, rankMax, pointsMin, pointsMax } = filters;
  const haystack = `${rider.name} ${countryName(rider.nation)} ${rider.nation} ${rider.team || ""}`.toLocaleLowerCase();
  const hasRank = rider.uci_rank !== 999999;
  const hasPoints = rider.uci_points !== null;
  return (!search || haystack.includes(search.toLocaleLowerCase()))
    && (!countries.length || countries.includes(rider.nation))
    && (!teams.length || teams.includes(teamLabel(rider.team)))
    && (!rankMin || (hasRank && rider.uci_rank >= Number(rankMin)))
    && (!rankMax || (hasRank && rider.uci_rank <= Number(rankMax)))
    && (!pointsMin || (hasPoints && rider.uci_points >= Number(pointsMin)))
    && (!pointsMax || (hasPoints && rider.uci_points <= Number(pointsMax)))
    && matchesAdvancedFilters(rider, filters);
}

function ensurePickActions() {
  let clear = $("#clear-picks");
  if (!clear) {
    clear = document.createElement("button");
    clear.id = "clear-picks";
    clear.className = "clear-picks";
    clear.type = "button";
    clear.textContent = "Clear all picks";
    clear.addEventListener("click", () => {
      if (!savedPicks().length) return;
      rememberPickState();
      state.picks = Array(10).fill(null);
      state.wildcards = Array(WILDCARD_COUNT).fill(null);
      state.mobilePendingRiderId = null;
      document.body.classList.remove("mobile-picking");
      showMessage("#prediction-message", "Top 10 and wildcards cleared.", true);
      render();
    });
    $("#wildcards-block").insertAdjacentElement("afterend", clear);
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
      state.wildcards = [...state.savedWildcards];
      state.mobilePendingRiderId = null;
      document.body.classList.remove("mobile-picking");
      showMessage("#prediction-message", `Reverted to the last saved version of ${listName(state.activeList)}.`, true);
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
  let templateActions = $("#template-actions");
  if (!templateActions) {
    templateActions = document.createElement("div");
    templateActions.id = "template-actions";
    templateActions.className = "template-actions";
    templateActions.innerHTML = `<button id="save-template" type="button">Save template</button><button id="save-new-template" type="button">Save as new template</button>`;
    save.insertAdjacentElement("afterend", templateActions);
    $("#save-template").addEventListener("click", saveActiveTemplate);
    $("#save-new-template").addEventListener("click", saveAsNewTemplate);
  }
  const message = $("#prediction-message");
  if (message && message.previousElementSibling !== templateActions) {
    message.classList.add("top10-message");
    templateActions.insertAdjacentElement("afterend", message);
  }
  return { clear, revert, save, undo: $("#undo-picks"), redo: $("#redo-picks") };
}

// -- lists: the final prediction plus named templates --------------------------
const emptyList = () => ({ picks: Array(10).fill(null), wildcards: Array(WILDCARD_COUNT).fill(null) });
// Missing fields read as empty, so a response from an API one deploy behind
// (no wildcards yet) still loads.
function listFromPicks(selections = [], wildcards = []) {
  const list = emptyList();
  selections.forEach((item) => { list.picks[item.position - 1] = item.rider_id; });
  wildcards.slice(0, WILDCARD_COUNT).forEach((riderId, slot) => { list.wildcards[slot] = riderId; });
  return list;
}
const workingList = () => ({ picks: [...state.picks], wildcards: [...state.wildcards] });
const picksPayload = (list = workingList()) => ({ selections: list.picks.flatMap((rider_id, index) => rider_id ? [{ position: index + 1, rider_id }] : []), wildcards: list.wildcards.filter(Boolean) });
const templateById = (id) => state.lists.templates.find((template) => template.id === id);
const listName = (id) => (id === "final" ? "your final prediction" : `“${templateById(id)?.name || "this list"}”`);
const wildcardKey = (list) => list.wildcards.filter(Boolean).sort((a, b) => a - b).join(",");
const sameList = (a, b) => Boolean(a && b) && a.picks.every((riderId, index) => riderId === b.picks[index]) && wildcardKey(a) === wildcardKey(b);
function uniqueTemplateName() {
  const taken = new Set(state.lists.templates.map((template) => template.name.toLocaleLowerCase()));
  for (let number = 1; ; number += 1) if (!taken.has(`template ${number}`)) return `Template ${number}`;
}
function markSaved(list) {
  state.savedPicks = [...list.picks];
  state.savedWildcards = [...list.wildcards];
}

async function loadLists() {
  const base = `/api/events/${state.event.id}`;
  const [prediction, templates, favourites] = await Promise.all([
    request(`${base}/predictions/${state.player.id}`).catch(() => null),
    request(`${base}/players/${state.player.id}/templates`).catch(() => []),
    request(`${base}/players/${state.player.id}/favourites`).catch(() => []),
  ]);
  state.favourites = new Set(favourites);
  state.lists.final = prediction ? listFromPicks(prediction.selections, prediction.wildcards) : null;
  state.lists.templates = templates.map((template) => ({ id: template.id, name: template.name, ...listFromPicks(template.selections, template.wildcards) }));
  openList("final", { force: true });
}

function openList(listId, { force = false } = {}) {
  if (!force && listId === state.activeList) return;
  if (!force && hasUnsavedPickChanges() && !window.confirm(`You have unsaved changes in ${listName(state.activeList)}. Switch lists and discard them?`)) return;
  const source = (listId === "final" ? state.lists.final : templateById(listId)) || emptyList();
  state.activeList = listId;
  state.picks = [...source.picks];
  state.wildcards = [...source.wildcards];
  markSaved(source);
  state.renamingList = null;
  state.mobilePendingRiderId = null;
  document.body.classList.remove("mobile-picking");
  resetPickHistory();
  render();
}

// Template writes run one at a time, in the order they were asked for, so a
// rename and a save of the same template can never overtake each other.
let templateWrites = Promise.resolve();
const queueTemplateWrite = (task) => (templateWrites = templateWrites.catch(() => {}).then(task));

// Each save records the list it sent, not whatever is on screen when the answer
// arrives: the player may have switched lists or kept editing meanwhile.
async function saveFinal() {
  if (!state.picks.some(Boolean)) return showMessage("#prediction-message", "Pick at least one Top 10 rider first.");
  const list = workingList();
  const listId = state.activeList;
  try {
    await request(`/api/events/${state.event.id}/predictions`, { method: "PUT", body: JSON.stringify({ player_id: state.player.id, ...picksPayload(list) }) });
    state.lists.final = list;
    if (listId === "final") {
      if (state.activeList === "final") markSaved(list);
      showMessage("#prediction-message", "Final prediction saved. You can edit it until the deadline.", true);
    } else {
      showMessage("#prediction-message", `Saved ${listName(listId)} as your final prediction. It is the one that will be scored.`, true);
    }
    render();
  } catch (error) {
    showMessage("#prediction-message", error.message);
  }
}

async function putTemplate(template, name, list) {
  return request(`/api/events/${state.event.id}/templates/${template.id}`, { method: "PUT", body: JSON.stringify({ player_id: state.player.id, name, ...picksPayload(list) }) });
}

function saveActiveTemplate() {
  const template = templateById(state.activeList);
  if (!template) return;
  const list = workingList();
  return queueTemplateWrite(async () => {
    try {
      // The name is read when the write runs, after any rename queued before it.
      await putTemplate(template, template.name, list);
      Object.assign(template, list);
      if (state.activeList === template.id) markSaved(list);
      showMessage("#prediction-message", `Saved ${listName(template.id)}.`, true);
      render();
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
  });
}

async function saveAsNewTemplate() {
  const list = workingList();
  const listId = state.activeList;
  try {
    const created = await request(`/api/events/${state.event.id}/templates`, { method: "POST", body: JSON.stringify({ player_id: state.player.id, name: uniqueTemplateName(), ...picksPayload(list) }) });
    const template = { id: created.id, name: created.name, ...list };
    state.lists.templates.push(template);
    if (state.activeList === listId) {
      // Keep editing the same picks, now as the new template; its name opens for editing.
      state.activeList = template.id;
      markSaved(list);
      state.renamingList = template.id;
      showMessage("#prediction-message", `Saved as “${template.name}”. Type a name for it, or keep this one.`, true);
    } else {
      showMessage("#prediction-message", `Saved as “${template.name}”.`, true);
    }
    render();
  } catch (error) {
    showMessage("#prediction-message", error.message);
  }
}

function renameTemplate(id, rawName) {
  const template = templateById(id);
  const name = rawName.replace(/\s+/g, " ").trim();
  state.renamingList = null;
  if (!template || !name || name === template.name) return render();
  return queueTemplateWrite(async () => {
    try {
      // A rename keeps the template's saved picks; unsaved edits stay unsaved.
      await putTemplate(template, name, template);
      template.name = name;
      showMessage("#prediction-message", `Renamed to “${name}”.`, true);
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
    render();
  });
}

async function deleteTemplate(id) {
  const template = templateById(id);
  if (!template || !window.confirm(`Delete the template “${template.name}”? This cannot be undone.`)) return;
  return queueTemplateWrite(async () => {
    try {
      await request(`/api/events/${state.event.id}/templates/${id}?player_id=${state.player.id}`, { method: "DELETE" });
      state.lists.templates = state.lists.templates.filter((item) => item.id !== id);
      showMessage("#prediction-message", `Deleted “${template.name}”.`, true);
      if (state.activeList === id) openList("final", { force: true });
      else render();
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
  });
}

function renderListSwitcher() {
  let switcher = $("#list-switcher");
  if (!switcher) {
    switcher = document.createElement("div");
    switcher.id = "list-switcher";
    switcher.className = "list-switcher";
    $("#picks").insertAdjacentElement("beforebegin", switcher);
    switcher.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-list]");
      if (tab) return openList(tab.dataset.list === "final" ? "final" : Number(tab.dataset.list));
      const action = event.target.closest("[data-list-action]")?.dataset.listAction;
      if (action === "rename") { state.renamingList = state.activeList; render(); }
      if (action === "delete") deleteTemplate(state.activeList);
    });
    switcher.addEventListener("keydown", (event) => {
      const input = event.target.closest("[data-rename]");
      if (!input) return;
      if (event.key === "Enter") { event.preventDefault(); input.blur(); }
      if (event.key === "Escape") { input.dataset.cancelled = "true"; state.renamingList = null; render(); }
    });
    switcher.addEventListener("focusout", (event) => {
      const input = event.target.closest("[data-rename]");
      if (input && !input.dataset.cancelled) renameTemplate(Number(input.dataset.rename), input.value);
    });
  }
  // Leave a name the player is still typing alone when something else re-renders.
  const editing = switcher.querySelector("[data-rename]");
  if (editing && editing === document.activeElement && Number(editing.dataset.rename) === state.renamingList) return;
  const dirty = hasUnsavedPickChanges();
  const final = state.lists.final;
  const tab = (id, label, extra = "") => {
    const active = state.activeList === id;
    const unsaved = active && dirty ? `<span class="list-unsaved" title="Unsaved changes" aria-label="unsaved changes">•</span>` : "";
    return `<button type="button" role="tab" class="list-tab ${id === "final" ? "final" : ""} ${active ? "active" : ""}" data-list="${id}" aria-selected="${active}">${label}${extra}${unsaved}</button>`;
  };
  const tabs = [
    tab("final", "Final", `<span class="list-tab-status">${final ? "scored" : "not saved"}</span>`),
    ...state.lists.templates.map((template) => state.renamingList === template.id
      ? `<input class="list-name-input" data-rename="${template.id}" value="${escapeHtml(template.name)}" maxlength="40" aria-label="Template name" />`
      : tab(template.id, escapeHtml(template.name), sameList(template, final) ? `<span class="list-same" title="Same picks as your final prediction" aria-label="same as final"></span>` : "")),
  ].join("");
  const caption = state.activeList === "final"
    ? `<strong>Final prediction</strong> — the one that is scored.${final ? "" : " Not saved yet."}`
    : `<strong>Template</strong> — a private draft, never scored. <button type="button" class="text-button" data-list-action="rename">Rename</button><button type="button" class="text-button" data-list-action="delete">Delete</button>`;
  switcher.innerHTML = `<div class="list-tabs" role="tablist" aria-label="Your lists">${tabs}</div><p class="list-caption">${caption}</p>`;
  const input = switcher.querySelector("[data-rename]");
  if (input) { input.focus(); input.select(); }
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
  const countryCountSort = state.riderView !== "plain" ? `<span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rider-count" class="${state.riderSort === "rider-count" ? "active" : ""}">No. riders ${state.riderSort === "rider-count" ? `<i>${arrow}</i>` : ""}</span>` : "";
  const viewOption = (view, label) => `<span role="button" tabindex="0" data-rider-view="${view}" class="${state.riderView === view ? "active" : ""}">${label}</span>`;
  controls.innerHTML = `<div class="view-toggle" role="group" aria-label="Rider list view">${viewOption("country", "Group by Country")}<span class="view-divider">|</span>${viewOption("team", "Group by Team")}<span class="view-divider">|</span>${viewOption("plain", "Riders list")}</div><div class="rider-sort-label"><span>Sort:</span><span role="button" tabindex="0" data-rider-sort="alphabetical" class="${state.riderSort === "alphabetical" ? "active" : ""}">Alphabetical ${state.riderSort === "alphabetical" ? `<i>${arrow}</i>` : ""}</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rank" class="${state.riderSort === "rank" ? `active` : ""}">UCI Rank ${state.riderSort === "rank" ? `<i>${arrow}</i>` : ""}</span>${countryCountSort}</div><div class="rider-selection-label" role="group" aria-label="Top 10 selection filter"><span>Riders:</span><span role="button" tabindex="0" data-rider-selection="all" class="${state.riderSelectionFilter === "all" ? "active" : ""}">All</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="selected" class="${state.riderSelectionFilter === "selected" ? "active" : ""}">Selected</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="unselected" class="${state.riderSelectionFilter === "unselected" ? "active" : ""}">Not selected</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="favourites" class="favourites-filter ${state.riderSelectionFilter === "favourites" ? "active" : ""}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /></svg>Favourites <i>${state.favourites.size}</i></span></div>`;
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
  const editingTemplate = state.activeList !== "final";
  pickActions.save.textContent = editingTemplate ? "Save as final prediction" : "Save final prediction";
  $("#save-template").classList.toggle("hidden", !editingTemplate);
  $("#template-actions").classList.toggle("single", !editingTemplate);
  renderListSwitcher();
  pickActions.clear.disabled = savedPicks().length === 0;
  pickActions.revert.disabled = !hasUnsavedPickChanges();
  pickActions.undo.disabled = state.undoStack.length === 0;
  pickActions.redo.disabled = state.redoStack.length === 0;
  const riderById = new Map(state.event.riders.map((rider) => [rider.id, rider]));
  $("#picks").innerHTML = state.picks.map((riderId, index) => { const rider = riderById.get(riderId); return `<li data-position="${index}" class="${rider ? "pick-filled" : "pick-empty"}" ${rider ? `draggable="true" data-picked-rider="${rider.id}" title="Open rider details"` : ""}><span class="position">${index + 1}.</span>${rider ? `${flag(rider.nation)}${escapeHtml(rider.name)}<span class="pick-multiplier" title="Placement points ${formatMultiplier(rider.position_multiplier)} for this rider's UCI rank">${formatMultiplier(rider.position_multiplier)}</span><button class="remove" data-remove="${index}" aria-label="Remove ${escapeHtml(rider.name)}">×</button><span class="rank-scale pick-rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span>` : "Drop rider here"}</li>`; }).join("");
  $("#wildcards").innerHTML = state.wildcards.map((riderId, slot) => { const rider = riderById.get(riderId); return `<li data-wildcard-slot="${slot}" class="wildcard-slot ${rider ? "pick-filled" : "pick-empty"}" ${rider ? `draggable="true" data-picked-rider="${rider.id}" title="Open rider details"` : ""}><span class="position wildcard-mark" aria-label="Wildcard ${slot + 1}">★</span>${rider ? `${flag(rider.nation)}${escapeHtml(rider.name)}<span class="pick-multiplier" title="Wildcard bonus ${formatMultiplier(rider.wildcard_multiplier)} for this rider's UCI rank">${formatMultiplier(rider.wildcard_multiplier)}</span><button class="remove" data-remove-wildcard="${slot}" aria-label="Remove ${escapeHtml(rider.name)}">×</button><span class="rank-scale pick-rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span>` : "Drop a wildcard here"}</li>`; }).join("");
  const filteredRiders = state.event.riders.filter((rider) => matchesFilters(rider));
  const visibleRiders = filteredRiders.filter((rider) => state.riderSelectionFilter === "all"
    || (state.riderSelectionFilter === "selected" && isPicked(rider.id))
    || (state.riderSelectionFilter === "unselected" && !isPicked(rider.id))
    || (state.riderSelectionFilter === "favourites" && state.favourites.has(rider.id)));
  // Country and team views share one grouping path; only the key and header differ.
  const byTeam = state.riderView === "team";
  const groupKey = (rider) => (byTeam ? teamLabel(rider.team) : rider.nation);
  const groupLabel = (key) => (byTeam ? key : countryName(key));
  const groupIcon = (key) => (byTeam ? teamIcon(key === NO_TEAM ? "" : key) : flag(key));
  const groupStats = new Map();
  state.event.riders.forEach((rider) => {
    const key = groupKey(rider);
    const current = groupStats.get(key) || { count: 0, points: 0, bestRank: Infinity };
    groupStats.set(key, { count: current.count + 1, points: current.points + (rider.uci_points ?? 0), bestRank: Math.min(current.bestRank, rider.uci_rank) });
  });
  const ridersByGroup = new Map(); visibleRiders.forEach((rider) => ridersByGroup.set(groupKey(rider), [...(ridersByGroup.get(groupKey(rider)) || []), rider]));
  $("#filter-summary").textContent = `${visibleRiders.length} of ${filteredRiders.length} filtered riders shown (${state.event.riders.length} total). The bar shows UCI rank strength (red = stronger); the arrow shows the UCI points trend since the season start.`;
  ensureRiderViewControls();
  const riderCard = (rider) => { const topTenPosition = state.picks.indexOf(rider.id); const isWildcard = state.wildcards.includes(rider.id); const selected = topTenPosition >= 0 || isWildcard; const riderFlag = state.riderView === "country" ? "" : flag(rider.nation); const badge = topTenPosition >= 0 ? `<span class="pick-position"><strong>#${topTenPosition + 1}</strong><small>Top 10</small></span>` : isWildcard ? `<span class="pick-position"><strong>★</strong><small>Wildcard</small></span>` : ""; return `<article draggable="true" class="rider ${selected ? "selected" : ""} ${isWildcard ? "wildcard" : ""}" data-rider="${rider.id}" role="button" tabindex="0" title="${topTenPosition >= 0 ? `Top 10 position ${topTenPosition + 1}. Open rider details, or drag to move it.` : isWildcard ? "Wildcard. Open rider details, or drag to move it." : "Open rider details"}">${riderFlag}${escapeHtml(rider.name)}${heartButton(rider, "rider-fav")}<button type="button" class="rider-add" data-rider-add="${rider.id}" aria-label="Add ${escapeHtml(rider.name)} to your picks">+</button>${badge}<br><span class="rank">${rankingLabel(rider)}</span>${trendArrow(rider)}<span class="rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span></article>`; };
  if (state.riderView === "plain") {
    $("#riders").innerHTML = `<div class="plain-riders">${sortRiders(visibleRiders).map(riderCard).join("")}</div>`;
  } else {
    const sortedGroups = [...ridersByGroup.entries()].sort(([a], [b]) => {
      // Riders without a trade team always close the team list.
      if (byTeam && (a === NO_TEAM || b === NO_TEAM)) return (a === NO_TEAM) - (b === NO_TEAM);
      const statsA = groupStats.get(a); const statsB = groupStats.get(b);
      const direction = state.riderSortDirection === "asc" ? 1 : -1;
      const byLabel = groupLabel(a).localeCompare(groupLabel(b));
      if (state.riderSort === "rider-count") return direction * (statsA.count - statsB.count || byLabel);
      if (state.riderSort === "rank") return direction * (statsA.bestRank - statsB.bestRank || byLabel);
      return direction * byLabel;
    });
    $("#riders").innerHTML = sortedGroups.map(([key, riders]) => { const stats = groupStats.get(key); return `<section class="country-group ${byTeam ? "team-group" : ""}"><h3>${groupIcon(key)}${escapeHtml(groupLabel(key))} <span class="country-meta">${stats.count} ${stats.count === 1 ? "rider" : "riders"} · ${Math.round(stats.points).toLocaleString()} pts</span></h3><div class="country-riders">${sortRiders(riders).map(riderCard).join("")}</div></section>`; }).join("");
  }
  document.querySelectorAll(".rider").forEach((node) => { node.addEventListener("click", (event) => { if (event.target.closest("[data-rider-add], [data-rider-fav]")) return; openRiderDetail(Number(node.dataset.rider)); }); node.addEventListener("keydown", (event) => { if (event.target.closest("[data-rider-add], [data-rider-fav]")) return; if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openRiderDetail(Number(node.dataset.rider)); } }); node.addEventListener("dragstart", (event) => { event.dataTransfer.setData("text/plain", node.dataset.rider); document.body.classList.add("mobile-dragging"); }); node.addEventListener("dragend", () => document.body.classList.remove("mobile-dragging")); });
  document.querySelectorAll("[data-rider-add]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); addRiderToPicks(Number(button.dataset.riderAdd)); }));
  document.querySelectorAll("#riders [data-rider-fav]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); toggleFavourite(Number(button.dataset.riderFav)); }));
  document.querySelectorAll("[data-picked-rider]").forEach((node) => node.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", node.dataset.pickedRider)));
  document.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); rememberPickState(); state.picks[Number(button.dataset.remove)] = null; render(); }));
  document.querySelectorAll("[data-remove-wildcard]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); rememberPickState(); state.wildcards[Number(button.dataset.removeWildcard)] = null; render(); }));
  document.querySelectorAll("[data-wildcard-slot]").forEach((slot) => {
    const slotIndex = Number(slot.dataset.wildcardSlot);
    slot.addEventListener("click", (event) => {
      if (event.target.closest("[data-remove-wildcard]")) return;
      if (state.mobilePendingRiderId) {
        const riderId = state.mobilePendingRiderId;
        state.mobilePendingRiderId = null;
        document.body.classList.remove("mobile-picking");
        insertWildcard(riderId, slotIndex);
        return;
      }
      const riderId = Number(slot.dataset.pickedRider);
      if (riderId) openRiderDetail(riderId);
    });
    slot.addEventListener("dragover", (event) => { event.preventDefault(); slot.classList.add("drag-over"); });
    slot.addEventListener("dragleave", () => slot.classList.remove("drag-over"));
    slot.addEventListener("drop", (event) => { event.preventDefault(); slot.classList.remove("drag-over"); document.body.classList.remove("mobile-dragging"); insertWildcard(Number(event.dataTransfer.getData("text/plain")), slotIndex); });
  });
  document.querySelectorAll("[data-position]").forEach((slot) => { slot.addEventListener("click", (event) => { if (event.target.closest("[data-remove]")) return; if (state.mobilePendingRiderId) { const riderId = state.mobilePendingRiderId; state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking"); insertRider(riderId, Number(slot.dataset.position)); return; } const riderId = Number(slot.dataset.pickedRider); if (riderId) openRiderDetail(riderId); }); slot.addEventListener("dragover", (event) => { event.preventDefault(); slot.classList.add("drag-over"); }); slot.addEventListener("dragleave", () => slot.classList.remove("drag-over")); slot.addEventListener("drop", (event) => { event.preventDefault(); slot.classList.remove("drag-over"); document.body.classList.remove("mobile-dragging"); insertRider(Number(event.dataTransfer.getData("text/plain")), Number(slot.dataset.position)); }); });
}

// A searchable multi-select: the country and team filters are two instances.
function configureMultiPicker({ prefix, chipsId, filterKey, values, label, icon, code = () => "", allLabel, pluralLabel, emptyLabel }) {
  const picker = $(`#${prefix}-picker`);
  const query = $(`#${prefix}-query`);
  const options = $(`#${prefix}-options`);
  const chips = $(`#${chipsId}`);
  const selected = () => state.filters[filterKey];
  const updateLabel = () => {
    const chosen = selected();
    $(`#${prefix}-filter-label`).textContent = chosen.length === 0 ? allLabel : chosen.length === 1 ? label(chosen[0]) : `${chosen.length} ${pluralLabel}`;
    chips.classList.toggle("hidden", chosen.length === 0);
    chips.innerHTML = chosen.map((value) => `<button type="button" class="country-chip" data-remove-value="${escapeHtml(value)}">${icon(value)}${escapeHtml(label(value))} <span aria-hidden="true">×</span></button>`).join("");
  };
  const renderOptions = () => {
    const text = query.value.trim().toLocaleLowerCase();
    const matches = (value) => `${label(value)} ${code(value)}`.toLocaleLowerCase().includes(text);
    const exactCode = (value) => code(value) !== "" && code(value).toLocaleLowerCase() === text;
    const startsWith = (value) => label(value).toLocaleLowerCase().startsWith(text) || (code(value) !== "" && code(value).toLocaleLowerCase().startsWith(text));
    const matching = [
      ...values.filter(exactCode),
      ...values.filter((value) => !exactCode(value) && startsWith(value)),
      ...values.filter((value) => !startsWith(value) && matches(value)),
    ];
    options.innerHTML = matching.length ? matching.map((value) => { const on = selected().includes(value); return `<button type="button" class="country-option ${on ? "selected" : ""}" data-value="${escapeHtml(value)}" aria-pressed="${on}"><span class="country-check" aria-hidden="true">${on ? "✓" : ""}</span>${icon(value)}<span>${escapeHtml(label(value))}${code(value) ? ` <span class="muted">${escapeHtml(code(value))}</span>` : ""}</span></button>`; }).join("") : `<p class="muted country-option">${emptyLabel}</p>`;
  };
  const open = () => { renderOptions(); options.classList.remove("hidden"); };
  const close = () => options.classList.add("hidden");
  const toggle = (value) => {
    state.filters[filterKey] = selected().includes(value) ? selected().filter((item) => item !== value) : [...selected(), value];
    updateLabel();
    renderOptions();
    render();
  };
  query.addEventListener("input", open);
  query.addEventListener("focus", open);
  picker.addEventListener("click", (event) => { if (!event.target.closest(`#${prefix}-options`)) open(); });
  // Picking a value re-renders the list, which used to drop the focused option
  // and close it through focusout; keeping focus on the search box instead lets
  // you pick several in a row. A touch device never focuses the option at all,
  // so relying on focus to close the list lost the tap outright.
  options.addEventListener("mousedown", (event) => event.preventDefault());
  document.addEventListener("pointerdown", (event) => { if (!picker.contains(event.target)) close(); });
  query.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  options.addEventListener("click", (event) => {
    const option = event.target.closest("button[data-value]");
    if (option) toggle(option.dataset.value);
  });
  chips.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-remove-value]");
    if (chip) toggle(chip.dataset.removeValue);
  });
  return { reset: () => { query.value = ""; updateLabel(); renderOptions(); } };
}

function configureFilters() {
  const countries = [...new Set(state.event.riders.map((rider) => rider.nation))]
    .sort((a, b) => countryName(a).localeCompare(countryName(b)));
  const teams = [...new Set(state.event.riders.map((rider) => teamLabel(rider.team)))]
    .sort((a, b) => (a === NO_TEAM) - (b === NO_TEAM) || a.localeCompare(b));
  const countryPicker = configureMultiPicker({
    prefix: "country", chipsId: "selected-countries", filterKey: "countries", values: countries,
    label: countryName, icon: flag, code: (country) => country,
    allLabel: "All countries", pluralLabel: "countries", emptyLabel: "No matching countries",
  });
  const teamPicker = configureMultiPicker({
    prefix: "team", chipsId: "selected-teams", filterKey: "teams", values: teams,
    label: (team) => team, icon: (team) => teamIcon(team === NO_TEAM ? "" : team),
    allLabel: "All teams", pluralLabel: "teams", emptyLabel: "No matching teams",
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
    state.filters = emptyFilters();
    $("#rider-search").value = "";
    countryPicker.reset();
    teamPicker.reset();
    ["rank", "points"].forEach((prefix) => {
      $(`#${prefix}-min`).value = "";
      $(`#${prefix}-max`).value = "";
      $(`#${prefix}-min-scale`).value = $(`#${prefix}-min-scale`).min;
      $(`#${prefix}-max-scale`).value = $(`#${prefix}-max-scale`).max;
      $(`#${prefix}-min-scale`).parentElement.style.setProperty("--range-start", "0%");
      $(`#${prefix}-min-scale`).parentElement.style.setProperty("--range-end", "100%");
      $(`#${prefix}-range-value`).textContent = "Any";
    });
    state.advancedDraft = null;
    updateAdvancedFilterBadge();
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

const loadPrediction = loadLists;
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
// Trend arrows need the PCS reference set; fetch it behind the first render.
const loadReferenceInBackground = () => loadReference().then(() => { if (state.player && state.event) render(); }).catch(() => {});
async function boot() {
  restoreSession();
  const teamIconsReady = loadTeamIcons();
  const awake = await whenApiReady();
  if (!awake) return;
  try {
    await Promise.all([loadEvent(), teamIconsReady]);
    if (state.player) {
      $("#identity").classList.add("hidden");
      $("#prediction").classList.remove("hidden");
      renderSessionControls();
      await loadPrediction();
      loadReferenceInBackground();
    } else {
      $("#identity").classList.remove("hidden");
    }
  } catch (error) {
    $("#event-title").textContent = "Event unavailable";
    $("#event-meta").textContent = error.message;
    setApiStatus("error", error.message);
    if (!state.player) $("#identity").classList.remove("hidden");
    showFinishedRace();
  }
}
// Once the result is published there is no open event: point to the leaderboard.
async function showFinishedRace() {
  try {
    const finished = await request("/api/events/latest-finished");
    $("#event-context").classList.remove("hidden");
    $("#event-context").classList.add("race-finished");
    $("#event-title").textContent = finished.name;
    $("#event-meta").innerHTML = 'The race is over. <a href="leaderboard.html">See the leaderboard and your score</a>.';
    setApiStatus("ready", "Results are in");
  } catch (_) {
    // No finished race either; the error above stands.
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
    loadReferenceInBackground();
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
$("#rider-detail-modal").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) return event.currentTarget.close();
  const heart = event.target.closest("[data-rider-fav]");
  if (heart) toggleFavourite(Number(heart.dataset.riderFav));
});
$("#advanced-filter-button").addEventListener("click", openAdvancedFilters);
$("#close-advanced-filters").addEventListener("click", () => $("#advanced-filter-modal").close());
$("#advanced-filter-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#advanced-filter-content").addEventListener("click", handleAdvancedFilterClick);
$("#advanced-filter-content").addEventListener("input", handleAdvancedFilterInput);
$("#apply-advanced-filters").addEventListener("click", () => {
  if (state.advancedDraft) Object.assign(state.filters, cloneAdvancedFilters(state.advancedDraft));
  $("#advanced-filter-modal").close();
  updateAdvancedFilterBadge();
  render();
});
$("#reset-advanced-filters").addEventListener("click", () => {
  Object.assign(state.filters, emptyAdvancedFilters());
  state.advancedDraft = cloneAdvancedFilters(state.filters);
  renderAdvancedFilters();
  render();
});
$("#logout").addEventListener("click", () => { if (hasUnsavedPickChanges() && !window.confirm("Did you forget to save your prediction?")) return; localStorage.removeItem("ten-up-player"); state.player = null; state.picks = Array(10).fill(null); state.savedPicks = Array(10).fill(null); state.wildcards = Array(WILDCARD_COUNT).fill(null); state.savedWildcards = Array(WILDCARD_COUNT).fill(null); state.lists = { final: null, templates: [] }; state.activeList = "final"; state.renamingList = null; state.favourites = new Set(); if (state.riderSelectionFilter === "favourites") state.riderSelectionFilter = "all"; resetPickHistory(); state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking", "mobile-dragging"); $("#prediction").classList.add("hidden"); $("#identity").classList.remove("hidden"); $("#username").value = ""; renderSessionControls(); showMessage("#identity-message", "You have logged out on this device.", true); $("#username").focus(); });
$("#save").addEventListener("click", saveFinal);
window.addEventListener("beforeunload", (event) => { if (!state.player || !hasUnsavedPickChanges()) return; event.preventDefault(); event.returnValue = ""; });
window.addEventListener("keydown", (event) => { if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target instanceof HTMLElement && event.target.matches("input, textarea, select")) return; if (event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) restorePickState(state.redoStack, state.undoStack, "Redid last change."); else restorePickState(state.undoStack, state.redoStack, "Undid last change."); } else if (event.key.toLowerCase() === "y") { event.preventDefault(); restorePickState(state.redoStack, state.undoStack, "Redid last change."); } });
$("#cancel-pick").addEventListener("click", cancelPendingPick);
// The help popover is a <details>; close it on a tap anywhere else, as a phone user expects.
document.addEventListener("pointerdown", (event) => { const help = $(".event-help[open]"); if (help && !help.contains(event.target)) help.open = false; });
const backToTop = $("#back-to-top");
backToTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
window.addEventListener("scroll", () => backToTop.classList.toggle("hidden", window.scrollY < 400), { passive: true });
window.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("dialog[open]")) cancelPendingPick(); });
boot();
