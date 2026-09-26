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
// One UCI range filter, read in points or in rank: only the unit shown applies.
const emptyFilters = (uciUnit = "points") => ({ search: "", countries: [], teams: [], uciUnit, rankMin: "", rankMax: "", pointsMin: "", pointsMax: "", ...emptyAdvancedFilters() });
const state = { event: null, player: null, reference: null, referencePromise: null, teamIcons: {}, apiStatus: "starting", apiReadyPromise: null, inviteCode: new URLSearchParams(window.location.search).get("league_code") || "", picks: Array(10).fill(null), savedPicks: Array(10).fill(null), wildcards: Array(WILDCARD_COUNT).fill(null), savedWildcards: Array(WILDCARD_COUNT).fill(null), undoStack: [], redoStack: [], mobilePendingRiderId: null, riderView: "country", riderSort: "alphabetical", riderSortDirection: "asc", riderSelectionFilter: "all", filters: emptyFilters(), advancedDraft: null, lists: { final: null, templates: [] }, activeList: "final", renamingList: null, favourites: new Set(), favouritesOnly: false };
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
// A broken heart: the same outline, split by a zigzag crack.
const CRACK_PATH = "M12.4 7.4 10.6 11l2.6 2-1.8 3.8";
const HEART_PATH = "M12 20.5s-7.5-4.6-9.3-9.2C1.5 8.2 3.3 4.8 6.6 4.5c2-.2 3.7.9 5.4 3 1.7-2.1 3.4-3.2 5.4-3 3.3.3 5.1 3.7 3.9 6.8-1.8 4.6-9.3 9.2-9.3 9.2Z";
function groupHeart(key, riders, label) {
  const on = riders.length > 0 && riders.every((rider) => state.favourites.has(rider.id));
  const action = on ? `Remove the ${riders.length} ${label} riders shown from your favourites` : `Add the ${riders.length} ${label} riders shown to your favourites`;
  return `<button type="button" class="group-fav ${on ? "on" : ""}" data-group-fav="${escapeHtml(key)}" aria-pressed="${on}" aria-label="${escapeHtml(action)}" title="${escapeHtml(action)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /></svg></button>`;
}
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
// Heart or un-heart many riders at once (a group, or everything shown) with one
// request; the server answers with the whole list, which becomes the truth.
async function updateFavourites({ add = [], remove = [] }) {
  if (!add.length && !remove.length) return;
  const previous = new Set(state.favourites);
  add.forEach((riderId) => state.favourites.add(riderId));
  remove.forEach((riderId) => state.favourites.delete(riderId));
  refreshFavouriteViews();
  try {
    const stored = await request(`/api/events/${state.event.id}/players/${state.player.id}/favourites`, { method: "PATCH", body: JSON.stringify({ add, remove }) });
    state.favourites = new Set(stored);
    refreshFavouriteViews();
  } catch (error) {
    state.favourites = previous;
    refreshFavouriteViews();
    showMessage("#prediction-message", error.message);
  }
}
function addShownToFavourites() {
  const add = visibleRiderList().filter((rider) => !state.favourites.has(rider.id)).map((rider) => rider.id);
  updateFavourites({ add });
  if (add.length) showMessage("#prediction-message", `Added ${add.length} ${add.length === 1 ? "rider" : "riders"} to your favourites.`, true);
}
// A group's heart covers the riders it shows: fill them all, or, when all are
// favourites already, empty them again.
function toggleGroupFavourites(key) {
  const riders = visibleRiderList().filter((rider) => groupKeyFor(rider) === key).map((rider) => rider.id);
  const allFavourite = riders.length > 0 && riders.every((riderId) => state.favourites.has(riderId));
  if (allFavourite) updateFavourites({ remove: riders });
  else updateFavourites({ add: riders.filter((riderId) => !state.favourites.has(riderId)) });
}

async function clearFavourites() {
  if (!state.favourites.size) return;
  const previous = new Set(state.favourites);
  state.favourites = new Set();
  // An empty favourites-only view would look like a broken filter.
  state.favouritesOnly = false;
  refreshFavouriteViews();
  try {
    await request(`/api/events/${state.event.id}/players/${state.player.id}/favourites`, { method: "DELETE" });
    showMessage("#prediction-message", "Favourites cleared.", true);
  } catch (error) {
    state.favourites = previous;
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
  const seasonLine = season ? `<span class="rider-detail-season">${season.season}: ${season.wins} wins · ${season.top10s} top 10s</span>` : "";
  const team = `<span class="rider-detail-team-name">${rider.team ? `${teamIcon(rider.team)}${escapeHtml(rider.team)}` : escapeHtml(countryName(rider.nation))}</span>`;
  const pcsLink = profile.profile_url ? `<a class="pcs-link" href="${escapeHtml(profile.profile_url)}" target="_blank" rel="noopener noreferrer">ProCyclingStats profile <span aria-hidden="true">↗</span></a>` : "";
  const favourite = state.player ? heartButton(rider, "detail-fav") : "";
  const addButton = state.player ? riderDetailAddButton(rider) : "";
  content.innerHTML = `<header class="rider-detail-header"><div><p class="eyebrow">RIDER PROFILE</p><h2>${flag(rider.nation)}${escapeHtml(rider.name)}</h2><p class="rider-detail-team">${team}${careerWins}${seasonLine}</p><div class="rider-detail-links">${favourite}${pcsLink}</div></div>${addButton}</header><section class="rider-stat-grid"><div><span>Age</span><strong>${profile.age ?? "—"}</strong></div><div><span>UCI now</span><strong>${displayRank(rider.uci_rank === 999999 ? null : rider.uci_rank)}</strong><small>${displayPoints(rider.uci_points)}</small></div><div><span>Season start</span><strong>${displayRank(seasonStart?.uci_rank)}</strong><small>${displayPoints(seasonStart?.uci_points)}</small></div><div class="ranking-trend ${trendClass}"><span>Ranking trend</span><strong>${trendText}</strong></div></section><section class="rider-history"><div class="rider-history-heading"><div><p class="eyebrow">PAST RESULTS</p><h3>Tracked one-day races</h3></div></div><div class="rider-history-table-wrap"><table class="rider-history-table"><thead><tr><th scope="col">Race</th>${years.map((year) => `<th scope="col">${year}${races.some((race) => race.editions.some((edition) => edition.year === year && edition.note)) ? "<small>upcoming</small>" : ""}</th>`).join("")}</tr></thead><tbody>${races.map((race) => `<tr><th scope="row">${escapeHtml(race.label)}</th>${years.map((year) => `<td>${resultCell(resultMap.get(`${race.key}-${year}`))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="rider-history-legend"><span class="result-medal medal-1">1</span> podium <span class="result-top-ten">7</span> top 10 <span class="result-status">DNF</span> did not finish · blank: not on the startlist</p></section>`;
}
// The modal's own add button mirrors the startlist card's `+`/badge, so a
// rider already in the Top 10 or wildcards shows its slot instead of `+`.
function riderDetailAddButton(rider) {
  const topTenPosition = state.picks.indexOf(rider.id);
  const isWildcard = state.wildcards.includes(rider.id);
  if (topTenPosition >= 0) return `<button type="button" class="rider-add rider-detail-add on" data-rider-add="${rider.id}" aria-label="${escapeHtml(rider.name)} is Top 10 pick #${topTenPosition + 1}" title="Top 10 position ${topTenPosition + 1}">#${topTenPosition + 1}</button>`;
  if (isWildcard) return `<button type="button" class="rider-add rider-detail-add on" data-rider-add="${rider.id}" aria-label="${escapeHtml(rider.name)} is a wildcard" title="Wildcard">★</button>`;
  return `<button type="button" class="rider-add rider-detail-add" data-rider-add="${rider.id}" aria-label="Add ${escapeHtml(rider.name)} to your Top 10 or wildcards" title="Add to Top 10 or wildcards">+</button>`;
}

async function openRiderDetail(riderId) {
  const dialog = $("#rider-detail-modal");
  dialog.dataset.rider = String(riderId);
  const content = $("#rider-detail-content");
  const pageScrollY = window.scrollY;
  const positionModalAndPage = () => {
    dialog.querySelector(".rider-detail-scroll").scrollTop = 0;
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
function renderSessionControls() {
  const signedIn = Boolean(state.player);
  $("#session-controls").classList.toggle("hidden", !signedIn);
  $("#event-context").classList.toggle("hidden", !signedIn);
  $("#session-username").textContent = signedIn ? state.player.username : "";
  $("#league-summary").textContent = state.league ? `Local League · ${state.league.code}` : "Global leaderboard";
  $("#league-counts").textContent = state.league ? `${state.league.joined_players} joined · ${state.league.submitted_players} submitted` : "";
  $("#join-league-button").classList.toggle("hidden", Boolean(state.league));
  $("#copy-league-link").classList.toggle("hidden", !state.league);
  $("#leave-league-button").classList.toggle("hidden", !state.league);
}
function renderDeadline() {
  if (!state.event) return;
  const deadline = state.league?.submission_deadline || state.event.prediction_deadline;
  const utcDeadline = /[zZ]|[+-]\d{2}:\d{2}$/.test(deadline) ? deadline : `${deadline}Z`;
  const formatted = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(utcDeadline));
  $("#event-meta").textContent = `Submit your ${state.league ? `${state.league.code} league` : "global"} prediction by ${formatted}.`;
}
async function loadLeagueInfo() {
  if (!state.player || !state.event) return;
  const status = await request(`/api/events/${state.event.id}/players/${state.player.id}/league`);
  state.league = status.league;
  renderSessionControls();
  renderDeadline();
}
async function joinLeagueCode(code) {
  const status = await request(`/api/events/${state.event.id}/players/${state.player.id}/league`, { method: "PUT", body: JSON.stringify({ code }) });
  state.league = status.league;
  renderSessionControls();
  renderDeadline();
}
async function applyInvite() {
  if (!state.inviteCode) return;
  await joinLeagueCode(state.inviteCode);
  state.inviteCode = "";
  $("#league-invite").classList.add("hidden");
  const url = new URL(window.location.href);
  url.searchParams.delete("league_code");
  window.history.replaceState({}, "", url);
}
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
  $("#mobile-picks-handle").setAttribute("aria-expanded", "false");
  showMessage("#prediction-message", "Pick cancelled.", true);
  render();
}
function closeMobilePicks() {
  document.body.classList.remove("mobile-picks-open");
  if (state.mobilePendingRiderId) return cancelPendingPick();
  $("#mobile-picks-handle").setAttribute("aria-expanded", "false");
}
function toggleMobilePicks() {
  if (state.mobilePendingRiderId) return closeMobilePicks();
  const open = document.body.classList.toggle("mobile-picks-open");
  $("#mobile-picks-handle").setAttribute("aria-expanded", String(open));
}
function addRiderToPicks(riderId) {
  if (isMobileLayout()) {
    if (state.mobilePendingRiderId === riderId) return cancelPendingPick();
    state.mobilePendingRiderId = riderId;
    document.body.classList.remove("mobile-picks-open");
    document.body.classList.add("mobile-picking");
    $("#mobile-picks-handle").setAttribute("aria-expanded", "true");
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
  const { search, countries, teams, uciUnit, rankMin, rankMax, pointsMin, pointsMax } = filters;
  const haystack = `${rider.name} ${countryName(rider.nation)} ${rider.nation} ${rider.team || ""}`.toLocaleLowerCase();
  const hasRank = rider.uci_rank !== 999999;
  const hasPoints = rider.uci_points !== null;
  const byRank = uciUnit === "rank";
  return (!search || haystack.includes(search.toLocaleLowerCase()))
    && (!countries.length || countries.includes(rider.nation))
    && (!teams.length || teams.includes(teamLabel(rider.team)))
    && (!byRank || !rankMin || (hasRank && rider.uci_rank >= Number(rankMin)))
    && (!byRank || !rankMax || (hasRank && rider.uci_rank <= Number(rankMax)))
    && (byRank || !pointsMin || (hasPoints && rider.uci_points >= Number(pointsMin)))
    && (byRank || !pointsMax || (hasPoints && rider.uci_points <= Number(pointsMax)))
    && matchesAdvancedFilters(rider, filters);
}

function ensurePickActions() {
  let autosave = $("#final-autosave-control");
  if (!autosave) {
    autosave = document.createElement("div");
    autosave.id = "final-autosave-control";
    autosave.className = "final-autosave-control";
    autosave.innerHTML = '<label for="final-autosave"><input id="final-autosave" type="checkbox" /> Auto-save</label><span id="final-save-status" role="status" aria-live="polite"></span><button id="retry-final-save" type="button" class="hidden">Retry</button>';
    autosave.querySelector("input").addEventListener("change", (event) => {
      state.finalAutosave = event.target.checked;
      localStorage.setItem(finalAutosavePreferenceKey(), String(state.finalAutosave));
      failedFinalAutosave = "";
      if (!state.finalAutosave) window.clearTimeout(finalAutosaveTimer);
      render();
    });
    autosave.querySelector("#retry-final-save").addEventListener("click", () => {
      failedFinalAutosave = "";
      saveFinal({ automatic: true });
    });
    $("#wildcards-block").insertAdjacentElement("afterend", autosave);
  }
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
    autosave.insertAdjacentElement("afterend", clear);
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
      if (state.activeList !== "final") return showMessage("#prediction-message", "Templates save themselves, so there is nothing to revert. Use ↶ to undo a change.", true);
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
  const message = $("#prediction-message");
  if (message && message.previousElementSibling !== save) {
    message.classList.add("top10-message");
    save.insertAdjacentElement("afterend", message);
  }
  return { autosave, clear, revert, save, undo: $("#undo-picks"), redo: $("#redo-picks") };
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
const listName = (id) => (id === "final" ? "My Picks" : `“${templateById(id)?.name || "this list"}”`);
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
const finalAutosavePreferenceKey = () => `divine-final-autosave:${state.player.id}`;
function loadFinalAutosavePreference() {
  state.finalAutosave = localStorage.getItem(finalAutosavePreferenceKey()) !== "false";
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
  state.finalDraft = null;
  state.lists.templates = templates.map((template) => ({ id: template.id, name: template.name, ...listFromPicks(template.selections, template.wildcards) }));
  loadFinalAutosavePreference();
  openList("final", { force: true });
}

function openList(listId, { force = false } = {}) {
  if (!force && listId === state.activeList) return;
  // A template saves itself on the way out; only the final can hold unsaved picks.
  flushTemplateAutosave();
  const autosavingFinal = state.activeList === "final" && state.finalAutosave && state.picks.some(Boolean);
  if (!force && hasUnsavedPickChanges() && !autosavingFinal && !window.confirm(`You have unsaved changes in ${listName(state.activeList)}. Switch lists and discard them?`)) return;
  if (!force && autosavingFinal && hasUnsavedPickChanges()) {
    state.finalDraft = workingList();
    flushFinalAutosave();
  }
  const source = (listId === "final" ? state.finalDraft || state.lists.final : templateById(listId)) || emptyList();
  state.activeList = listId;
  state.picks = [...source.picks];
  state.wildcards = [...source.wildcards];
  markSaved(listId === "final" ? state.lists.final || emptyList() : source);
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
// Ids are taken when a write is asked for: the player may log out before it runs.
const writeIds = () => ({ event: state.event.id, player: state.player.id });

// Final writes are serialized, so a slow earlier request cannot overwrite the
// most recent picks. A failed autosave waits for a new edit or an explicit retry.
const FINAL_AUTOSAVE_MS = 600;
let finalAutosaveTimer = 0;
let finalWrites = Promise.resolve();
let pendingFinalSaves = new Set();
let failedFinalAutosave = "";
const finalSaveKey = (ids, list) => `${ids.event}|${ids.player}|${listKey("final", list)}`;
function scheduleFinalAutosave() {
  window.clearTimeout(finalAutosaveTimer);
  if (!state.player || state.activeList !== "final" || !state.finalAutosave || !hasUnsavedPickChanges() || !state.picks.some(Boolean)) return;
  const key = finalSaveKey(writeIds(), workingList());
  if (key !== failedFinalAutosave) failedFinalAutosave = "";
  if (key === failedFinalAutosave || pendingFinalSaves.has(key)) return;
  finalAutosaveTimer = window.setTimeout(() => saveFinal({ automatic: true }), FINAL_AUTOSAVE_MS);
}
function flushFinalAutosave() {
  window.clearTimeout(finalAutosaveTimer);
  if (state.activeList === "final" && state.finalAutosave && hasUnsavedPickChanges() && state.picks.some(Boolean)) return saveFinal({ automatic: true });
  return finalWrites;
}
// Each save records the list it sent, not whatever is on screen when the answer
// arrives: the player may have switched lists or kept editing meanwhile.
function saveFinal({ automatic = false } = {}) {
  if (!state.picks.some(Boolean)) return showMessage("#prediction-message", "Pick at least one Top 10 rider first.");
  const list = workingList();
  const listId = state.activeList;
  const ids = writeIds();
  const key = finalSaveKey(ids, list);
  if (automatic && (pendingFinalSaves.has(key) || failedFinalAutosave === key)) return finalWrites;
  pendingFinalSaves.add(key);
  if (state.player?.id === ids.player) renderFinalSaveStatus();
  finalWrites = finalWrites.catch(() => {}).then(async () => {
    try {
      await request(`/api/events/${ids.event}/predictions`, { method: "PUT", body: JSON.stringify({ player_id: ids.player, ...picksPayload(list) }) });
      if (state.player?.id !== ids.player) return true;
      state.lists.final = list;
      if (!automatic || sameList(state.finalDraft, list)) state.finalDraft = null;
      if (listId === "final") {
        if (state.activeList === "final") markSaved(list);
        if (!automatic) showMessage("#prediction-message", "My Picks saved. You can edit them until the deadline.", true);
      } else {
        showMessage("#prediction-message", `Saved ${listName(listId)} as My Picks. These picks will count toward your score.`, true);
      }
      if (failedFinalAutosave === key) failedFinalAutosave = "";
      return true;
    } catch (error) {
      if (automatic) failedFinalAutosave = key;
      if (state.player?.id === ids.player) showMessage("#prediction-message", `${automatic ? "Auto-save failed: " : ""}${error.message}`);
      return false;
    } finally {
      pendingFinalSaves.delete(key);
      if (state.player?.id === ids.player) render();
    }
  });
  return finalWrites;
}
function renderFinalSaveStatus() {
  const control = $("#final-autosave-control");
  if (!control || !state.player) return;
  const editingFinal = state.activeList === "final";
  control.classList.toggle("hidden", !editingFinal);
  control.querySelector("input").checked = state.finalAutosave;
  if (!editingFinal) return;
  const dirty = hasUnsavedPickChanges();
  const key = finalSaveKey(writeIds(), workingList());
  const failed = state.finalAutosave && dirty && failedFinalAutosave === key;
  const saving = [...pendingFinalSaves].some((pending) => pending.startsWith(`${state.event.id}|${state.player.id}|`));
  const status = !state.finalAutosave ? (dirty ? "Auto-save off · Unsaved changes" : "Auto-save off")
    : failed ? "Auto-save failed"
    : dirty && !state.picks.some(Boolean) ? "Add a Top 10 rider to save"
    : saving ? "Saving…"
    : dirty ? "Saving shortly…" : state.lists.final ? "All changes saved" : "On · Add a rider to start";
  control.querySelector("#final-save-status").textContent = status;
  control.querySelector("#retry-final-save").classList.toggle("hidden", !failed);
}

async function putTemplate(ids, template, name, list, options = {}) {
  return request(`/api/events/${ids.event}/templates/${template.id}`, { method: "PUT", body: JSON.stringify({ player_id: ids.player, name, ...picksPayload(list) }), ...options });
}

// Templates save themselves: a change goes out once the picks have been still
// for a moment, and at once when you leave the template or the page.
const TEMPLATE_AUTOSAVE_MS = 600;
let autosaveTimer = 0;
let failedAutosave = "";
const listKey = (id, list) => `${id}|${list.picks.join(",")}|${list.wildcards.join(",")}`;
function scheduleTemplateAutosave() {
  window.clearTimeout(autosaveTimer);
  if (state.activeList === "final" || !hasUnsavedPickChanges()) return;
  // After a failed save, wait for the next change rather than retry in a loop.
  if (listKey(state.activeList, workingList()) === failedAutosave) return;
  autosaveTimer = window.setTimeout(flushTemplateAutosave, TEMPLATE_AUTOSAVE_MS);
}
function flushTemplateAutosave(options) {
  window.clearTimeout(autosaveTimer);
  const template = templateById(state.activeList);
  if (template && hasUnsavedPickChanges()) saveTemplate(template, workingList(), options);
}
// The picks count as saved the moment they are sent, so leaving the template
// and coming back shows them at once; a failed write puts the stored ones back.
function saveTemplate(template, list, options = {}) {
  const ids = writeIds();
  const previous = { picks: [...template.picks], wildcards: [...template.wildcards] };
  Object.assign(template, list);
  if (state.activeList === template.id) markSaved(list);
  return queueTemplateWrite(async () => {
    try {
      // The name is read when the write runs, after any rename queued before it.
      await putTemplate(ids, template, template.name, list, options);
      failedAutosave = "";
    } catch (error) {
      failedAutosave = listKey(template.id, list);
      if (sameList(template, list)) Object.assign(template, previous);
      if (state.activeList === template.id) markSaved(template);
      showMessage("#prediction-message", `${listName(template.id)} was not saved: ${error.message}`);
      if (state.player) render();
    }
  });
}

function copyName(name) {
  const taken = new Set(state.lists.templates.map((template) => template.name.toLocaleLowerCase()));
  for (let number = 1; ; number += 1) {
    const suffix = number === 1 ? " copy" : ` copy ${number}`;
    const candidate = `${name.slice(0, 40 - suffix.length).trim()}${suffix}`;
    if (!taken.has(candidate.toLocaleLowerCase())) return candidate;
  }
}

// A new template opens straight away with its name ready to edit. The name is
// picked when the write runs, so two quick adds never ask for the same one.
function addTemplate(nameFor, list, { carryFrom = null } = {}) {
  const ids = writeIds();
  return queueTemplateWrite(async () => {
    try {
      const created = await request(`/api/events/${ids.event}/templates`, { method: "POST", body: JSON.stringify({ player_id: ids.player, name: nameFor(), ...picksPayload(list) }) });
      const template = { id: created.id, name: created.name, ...list };
      state.lists.templates.push(template);
      if (carryFrom !== null && state.activeList === carryFrom) {
        // Keep editing the same picks, now as the copy.
        state.activeList = template.id;
        markSaved(list);
      } else {
        openList(template.id);
      }
      if (state.activeList === template.id) state.renamingList = template.id;
      render();
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
  });
}
const createTemplate = () => addTemplate(uniqueTemplateName, emptyList());
// Copying the list on screen takes what you see; the original keeps what it had saved.
function duplicateList(listId) {
  const onScreen = listId === state.activeList;
  flushTemplateAutosave();
  const source = (onScreen ? workingList() : listId === "final" ? state.lists.final : templateById(listId)) || emptyList();
  const list = { picks: [...source.picks], wildcards: [...source.wildcards] };
  const nameFor = listId === "final" ? uniqueTemplateName : () => copyName(templateById(listId)?.name || "Template");
  return addTemplate(nameFor, list, { carryFrom: onScreen ? listId : null });
}

function renameTemplate(id, rawName) {
  const template = templateById(id);
  const name = rawName.replace(/\s+/g, " ").trim();
  state.renamingList = null;
  if (!template || !name || name === template.name) return render();
  const ids = writeIds();
  return queueTemplateWrite(async () => {
    try {
      // A rename sends the template's own picks, which autosave keeps current.
      await putTemplate(ids, template, name, template);
      template.name = name;
      showMessage("#prediction-message", `Renamed to “${name}”.`, true);
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
    render();
  });
}

// Deleting is immediate, with no question asked; if the server refuses, the
// tab comes back where it was.
function deleteTemplate(id) {
  const template = templateById(id);
  if (!template) return;
  const index = state.lists.templates.indexOf(template);
  const ids = writeIds();
  state.lists.templates.splice(index, 1);
  if (state.activeList === id) openList("final", { force: true });
  else render();
  showMessage("#prediction-message", `Deleted “${template.name}”.`, true);
  return queueTemplateWrite(async () => {
    try {
      await request(`/api/events/${ids.event}/templates/${id}?player_id=${ids.player}`, { method: "DELETE" });
    } catch (error) {
      state.lists.templates.splice(Math.min(index, state.lists.templates.length), 0, template);
      showMessage("#prediction-message", error.message);
      render();
    }
  });
}

function saveTemplateOrder() {
  const ids = writeIds();
  return queueTemplateWrite(async () => {
    // The order is read when the write runs, so a copy made just before is in it.
    if (!state.player || state.player.id !== ids.player) return;
    try {
      await request(`/api/events/${ids.event}/players/${ids.player}/templates/order`, { method: "PUT", body: JSON.stringify({ template_ids: state.lists.templates.map((template) => template.id) }) });
    } catch (error) {
      showMessage("#prediction-message", error.message);
    }
  });
}

// -- the tab strip: Final first, then the templates in the player's order -----
const parseListId = (value) => (value === "final" ? "final" : Number(value));
let shownList = null;
let lastPointerType = "mouse";
let lastTabTap = { id: null, time: 0 };

function ensureListSwitcher() {
  let switcher = $("#list-switcher");
  if (switcher) return switcher;
  switcher = document.createElement("div");
  switcher.id = "list-switcher";
  switcher.className = "list-switcher";
  switcher.innerHTML = `<div class="list-tabs" role="tablist" aria-label="Your lists"></div><div class="list-menu hidden" role="menu" aria-label="List actions"></div>`;
  $("#picks").insertAdjacentElement("beforebegin", switcher);
  const strip = switcher.querySelector(".list-tabs");
  const menu = switcher.querySelector(".list-menu");
  strip.addEventListener("click", (event) => {
    if (event.target.closest("[data-list-add]")) return createTemplate();
    const close = event.target.closest("[data-list-delete]");
    if (close) return deleteTemplate(Number(close.dataset.listDelete));
    const label = event.target.closest(".list-tab-label");
    if (!label || tabDropped) return;
    const listId = parseListId(label.closest("[data-list]").dataset.list);
    const now = Date.now();
    const double = lastTabTap.id === listId && now - lastTabTap.time < 400;
    lastTabTap = double ? { id: null, time: 0 } : { id: listId, time: now };
    if (!double) return openList(listId);
    // A double-click renames, as tabs do; on a touch screen a double tap opens the menu.
    if (lastPointerType === "touch") openListMenu(listId);
    else if (listId !== "final") { state.renamingList = listId; render(); }
  });
  strip.addEventListener("contextmenu", (event) => {
    const tab = event.target.closest("[data-list]");
    if (!tab) return;
    event.preventDefault();
    // On a touch screen a long press starts a drag instead.
    if (lastPointerType !== "touch") openListMenu(parseListId(tab.dataset.list));
  });
  strip.addEventListener("pointerdown", (event) => {
    lastPointerType = event.pointerType;
    const tab = event.target.closest(".list-tab-label")?.closest(".list-tab:not(.final)");
    if (tab && event.button === 0 && state.lists.templates.length > 1) startTabDrag(event, tab);
  });
  // Once a hold has picked a tab up, the finger drags it instead of scrolling.
  strip.addEventListener("touchmove", (event) => { if (tabDrag?.active) event.preventDefault(); }, { passive: false });
  strip.addEventListener("scroll", () => markHiddenTabs(strip), { passive: true });
  strip.addEventListener("keydown", (event) => {
    const input = event.target.closest("[data-rename]");
    if (input) {
      if (event.key === "Enter") { event.preventDefault(); input.blur(); }
      if (event.key === "Escape") { input.dataset.cancelled = "true"; state.renamingList = null; render(); }
      return;
    }
    const tab = event.target.closest(".list-tab:not(.final)[data-list]");
    if (tab && event.key === "F2") { event.preventDefault(); state.renamingList = Number(tab.dataset.list); render(); }
  });
  strip.addEventListener("focusout", (event) => {
    const input = event.target.closest("[data-rename]");
    if (input && !input.dataset.cancelled) renameTemplate(Number(input.dataset.rename), input.value);
  });
  menu.addEventListener("click", (event) => {
    const action = event.target.closest("[data-menu-action]")?.dataset.menuAction;
    if (!action) return;
    const listId = parseListId(menu.dataset.list);
    closeListMenu();
    if (action === "rename") { state.renamingList = listId; render(); }
    if (action === "duplicate") duplicateList(listId);
  });
  document.addEventListener("pointerdown", (event) => { if (!menu.contains(event.target)) closeListMenu(); });
  window.addEventListener("pointermove", moveTabDrag);
  window.addEventListener("pointerup", endTabDrag);
  window.addEventListener("pointercancel", cancelTabDrag);
  return switcher;
}

function openListMenu(listId) {
  const switcher = $("#list-switcher");
  const menu = switcher.querySelector(".list-menu");
  const tab = switcher.querySelector(`.list-tab[data-list="${listId}"]`);
  if (!tab) return;
  menu.dataset.list = String(listId);
  menu.innerHTML = `${listId === "final" ? "" : `<button type="button" role="menuitem" data-menu-action="rename">Rename</button>`}<button type="button" role="menuitem" data-menu-action="duplicate">Duplicate</button>`;
  menu.classList.remove("hidden");
  const box = switcher.getBoundingClientRect();
  const anchor = tab.getBoundingClientRect();
  menu.style.top = `${anchor.bottom - box.top + 4}px`;
  menu.style.left = `${Math.max(0, Math.min(anchor.left - box.left, switcher.clientWidth - menu.offsetWidth))}px`;
  menu.querySelector("button").focus();
}
function closeListMenu() { $("#list-switcher .list-menu")?.classList.add("hidden"); }

// Template tabs reorder by dragging: straight away with a mouse, after a short
// hold on a touch screen so that a swipe still scrolls the strip.
const TAB_HOLD_MS = 350;
let tabDrag = null;
let tabDropped = false;
const templateOrder = (strip) => [...strip.querySelectorAll(".list-tab:not(.final)[data-list]")].map((node) => Number(node.dataset.list));
function startTabDrag(event, tab) {
  const strip = tab.parentElement;
  tabDrag = { tab, strip, pointerId: event.pointerId, touch: event.pointerType !== "mouse", startX: event.clientX, startY: event.clientY, active: false, grab: 0, holdTimer: 0, order: templateOrder(strip) };
  if (tabDrag.touch) tabDrag.holdTimer = window.setTimeout(() => activateTabDrag(event.clientX), TAB_HOLD_MS);
}
function activateTabDrag(clientX) {
  const drag = tabDrag;
  if (!drag) return;
  drag.active = true;
  drag.grab = clientX - drag.tab.getBoundingClientRect().left;
  drag.tab.classList.add("dragging");
  drag.strip.classList.add("reordering");
  try { drag.tab.setPointerCapture(drag.pointerId); } catch (_) { /* the pointer is already gone */ }
  closeListMenu();
}
function moveTabDrag(event) {
  const drag = tabDrag;
  if (!drag || event.pointerId !== drag.pointerId) return;
  if (!drag.active) {
    const moved = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (drag.touch) { if (moved > 8) cancelTabDrag(); return; }
    if (moved < 5) return;
    activateTabDrag(drag.startX);
  }
  const { tab, strip } = drag;
  const box = strip.getBoundingClientRect();
  if (event.clientX < box.left + 24) strip.scrollLeft -= 8;
  else if (event.clientX > box.right - 24) strip.scrollLeft += 8;
  // Final stays first: a template can go no further left than just after it.
  const final = strip.querySelector(".list-tab.final");
  const add = strip.querySelector(".list-tab-add");
  const pointer = event.clientX - box.left + strip.scrollLeft;
  const left = Math.max(final.offsetLeft + final.offsetWidth, Math.min(strip.scrollWidth - add.offsetWidth - tab.offsetWidth, pointer - drag.grab));
  // The tab goes where the pointer is, whatever the widths of the tabs around
  // it; the + button always stays last.
  const next = [...strip.querySelectorAll(".list-tab:not(.final)")].find((node) => node !== tab && node.offsetLeft + node.offsetWidth / 2 > pointer) || add;
  if (next !== tab.nextElementSibling) strip.insertBefore(tab, next);
  tab.style.transform = `translateX(${left - tab.offsetLeft}px)`;
}
function stopTabDrag() {
  const drag = tabDrag;
  tabDrag = null;
  if (!drag) return null;
  window.clearTimeout(drag.holdTimer);
  drag.tab.classList.remove("dragging");
  drag.tab.style.transform = "";
  drag.strip.classList.remove("reordering");
  return drag.active ? drag : null;
}
function endTabDrag(event) {
  if (!tabDrag || event.pointerId !== tabDrag.pointerId) return;
  const drag = stopTabDrag();
  if (!drag) return;
  // The click that ends a drag must not also open the tab.
  tabDropped = true;
  window.setTimeout(() => { tabDropped = false; }, 0);
  const order = templateOrder(drag.strip);
  if (order.join() === drag.order.join()) return render();
  const byId = new Map(state.lists.templates.map((template) => [template.id, template]));
  state.lists.templates = order.map((id) => byId.get(id)).filter(Boolean);
  render();
  saveTemplateOrder();
}
function cancelTabDrag(event) {
  if (!tabDrag || (event && event.pointerId !== tabDrag.pointerId)) return;
  // Back to the order the tabs had before.
  if (stopTabDrag()) render();
}

// Final stays pinned at the left edge and + at the right, so a template counts
// as shown only once it is clear of both.
function revealTab(strip, node) {
  if (!node) return;
  const final = strip.querySelector(".list-tab.final");
  const coveredLeft = node === final ? 0 : final.offsetWidth;
  const coveredRight = strip.querySelector(".list-tab-add").offsetWidth;
  const left = node.offsetLeft;
  const right = left + node.offsetWidth;
  if (left < strip.scrollLeft + coveredLeft) strip.scrollLeft = left - coveredLeft;
  else if (right > strip.scrollLeft + strip.clientWidth - coveredRight) strip.scrollLeft = right - strip.clientWidth + coveredRight;
}
// A fade at the right edge says more tabs wait there; a touch screen shows no scrollbar.
const markHiddenTabs = (strip) => strip.classList.toggle("more-right", strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 1);
// Tabs shrink to fit, cutting long names; once a name is cut, the strip uses
// smaller type so more of each still shows. Only then does it scroll.
function fitListTabs(strip) {
  strip.classList.remove("crowded");
  const cut = [...strip.querySelectorAll(".list-tab-label")].some((label) => label.scrollWidth > label.clientWidth + 1);
  strip.classList.toggle("crowded", cut || strip.scrollWidth > strip.clientWidth + 1);
}

function renderListSwitcher() {
  const strip = ensureListSwitcher().querySelector(".list-tabs");
  if (tabDrag?.active) return;
  // Leave a name the player is still typing alone when something else re-renders.
  const editing = strip.querySelector("[data-rename]");
  if (editing && editing === document.activeElement && Number(editing.dataset.rename) === state.renamingList) return;
  const tab = (id, name, title) => {
    const active = state.activeList === id;
    const close = id === "final" ? "" : `<button type="button" class="list-tab-close" data-list-delete="${id}" aria-label="Delete ${escapeHtml(name)}" title="Delete">×</button>`;
    return `<div class="list-tab ${id === "final" ? "final" : ""} ${active ? "active" : ""}" data-list="${id}"><button type="button" role="tab" class="list-tab-label" aria-selected="${active}" title="${escapeHtml(title)}">${escapeHtml(name)}</button>${close}</div>`;
  };
  const scrollLeft = strip.scrollLeft;
  strip.innerHTML = [
    tab("final", "My Picks", "These picks count toward your score and can be changed until the deadline"),
    ...state.lists.templates.map((template) => state.renamingList === template.id
      ? `<div class="list-tab renaming"><input class="list-name-input" data-rename="${template.id}" value="${escapeHtml(template.name)}" maxlength="40" aria-label="Template name" /></div>`
      : tab(template.id, template.name, template.name)),
    `<button type="button" class="list-tab-add" data-list-add aria-label="New template" title="New template">+</button>`,
  ].join("");
  fitListTabs(strip);
  strip.scrollLeft = scrollLeft;
  if (state.activeList !== shownList) {
    shownList = state.activeList;
    revealTab(strip, strip.querySelector(".list-tab.active"));
  }
  const input = strip.querySelector("[data-rename]");
  if (input) {
    revealTab(strip, input.parentElement);
    input.focus();
    input.select();
  }
  markHiddenTabs(strip);
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
    controls.addEventListener("click", (event) => {
      if (event.target.closest("[data-favourites-clear]")) return clearFavourites();
      if (event.target.closest("[data-favourites-add-shown]")) return addShownToFavourites();
      if (!event.target.closest("[data-favourites-only]")) return;
      state.favouritesOnly = !state.favouritesOnly;
      render();
    });
  }
  const arrow = state.riderSortDirection === "asc" ? "↑" : "↓";
  const addableShown = visibleRiderList().filter((rider) => !state.favourites.has(rider.id)).length;
  const countryCountSort = state.riderView !== "plain" ? `<span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rider-count" class="${state.riderSort === "rider-count" ? "active" : ""}">No. riders ${state.riderSort === "rider-count" ? `<i>${arrow}</i>` : ""}</span>` : "";
  const viewOption = (view, label) => `<span role="button" tabindex="0" data-rider-view="${view}" class="${state.riderView === view ? "active" : ""}">${label}</span>`;
  controls.innerHTML = `<div class="view-toggle" role="group" aria-label="Rider list view">${viewOption("country", "Group by Country")}<span class="view-divider">|</span>${viewOption("team", "Group by Team")}<span class="view-divider">|</span>${viewOption("plain", "Riders list")}</div><div class="rider-sort-label"><span>Sort:</span><span role="button" tabindex="0" data-rider-sort="alphabetical" class="${state.riderSort === "alphabetical" ? "active" : ""}">Alphabetical ${state.riderSort === "alphabetical" ? `<i>${arrow}</i>` : ""}</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-sort="rank" class="${state.riderSort === "rank" ? `active` : ""}" title="${state.riderView === "plain" ? "Riders by UCI rank" : "Groups by their riders' combined UCI points; riders inside a group are always in UCI rank order"}">UCI Rank ${state.riderSort === "rank" ? `<i>${arrow}</i>` : ""}</span>${countryCountSort}</div><div class="rider-selection-label" role="group" aria-label="Top 10 selection filter"><span>Riders:</span><span role="button" tabindex="0" data-rider-selection="all" class="${state.riderSelectionFilter === "all" ? "active" : ""}">All</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="selected" class="${state.riderSelectionFilter === "selected" ? "active" : ""}">Selected</span><span class="view-divider">|</span><span role="button" tabindex="0" data-rider-selection="unselected" class="${state.riderSelectionFilter === "unselected" ? "active" : ""}">Not selected</span></div><div class="favourites-controls" role="group" aria-label="Favourites"><button type="button" class="favourites-toggle ${state.favouritesOnly ? "on" : ""}" data-favourites-only aria-pressed="${state.favouritesOnly}" title="Show only your favourites; combines with All, Selected and Not selected"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /></svg>Favourites <i>${state.favourites.size}</i></button>${addableShown ? `<button type="button" class="favourites-add" data-favourites-add-shown aria-label="Add the ${addableShown} riders shown to your favourites" title="Add the ${addableShown} riders shown to your favourites"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /></svg></button>` : ""}${state.favourites.size ? `<button type="button" class="favourites-clear" data-favourites-clear aria-label="Clear all favourites" title="Clear all favourites"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${HEART_PATH}" /><path d="${CRACK_PATH}" /></svg></button>` : ""}</div>`;
}

// Inside a country or team group riders always read in UCI rank order; the
// sort controls order the groups.
const byUciRank = (riders) => [...riders].sort((a, b) => a.uci_rank - b.uci_rank || a.name.localeCompare(b.name));
function sortRiders(riders) {
  return [...riders].sort((a, b) => {
    const direction = state.riderSortDirection === "asc" ? 1 : -1;
    if (state.riderSort === "rank") return direction * (a.uci_rank - b.uci_rank || a.name.localeCompare(b.name));
    return direction * a.name.localeCompare(b.name);
  });
}

function visibleRiderList() {
  return state.event.riders.filter((rider) => matchesFilters(rider)).filter((rider) => (state.riderSelectionFilter === "all"
    || (state.riderSelectionFilter === "selected" && isPicked(rider.id))
    || (state.riderSelectionFilter === "unselected" && !isPicked(rider.id)))
    && (!state.favouritesOnly || state.favourites.has(rider.id)));
}
const groupKeyFor = (rider) => (state.riderView === "team" ? teamLabel(rider.team) : rider.nation);

function render() {
  const pickActions = ensurePickActions();
  const editingTemplate = state.activeList !== "final";
  document.body.classList.toggle("mobile-manual-final", !editingTemplate && state.finalAutosave === false);
  pickActions.save.textContent = editingTemplate ? "Use as My Picks" : "Save My Picks";
  pickActions.save.classList.toggle("hidden", !editingTemplate && state.finalAutosave);
  renderListSwitcher();
  scheduleTemplateAutosave();
  scheduleFinalAutosave();
  renderFinalSaveStatus();
  pickActions.clear.disabled = savedPicks().length === 0;
  // On a template, Revert stays clickable to explain why there is nothing to revert.
  pickActions.revert.disabled = !editingTemplate && (state.finalAutosave || !hasUnsavedPickChanges());
  pickActions.undo.disabled = state.undoStack.length === 0;
  pickActions.redo.disabled = state.redoStack.length === 0;
  const riderById = new Map(state.event.riders.map((rider) => [rider.id, rider]));
  $("#picks").innerHTML = state.picks.map((riderId, index) => { const rider = riderById.get(riderId); return `<li data-position="${index}" class="${rider ? "pick-filled" : "pick-empty"}" ${rider ? `draggable="true" data-picked-rider="${rider.id}" title="Open rider details"` : ""}><span class="position">${index + 1}.</span>${rider ? `${flag(rider.nation)}${escapeHtml(rider.name)}<span class="pick-multiplier" title="Placement points ${formatMultiplier(rider.position_multiplier)} for this rider's UCI rank">${formatMultiplier(rider.position_multiplier)}</span><button class="remove" data-remove="${index}" aria-label="Remove ${escapeHtml(rider.name)}">×</button><span class="rank-scale pick-rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span>` : "Drop rider here"}</li>`; }).join("");
  $("#wildcards").innerHTML = state.wildcards.map((riderId, slot) => { const rider = riderById.get(riderId); return `<li data-wildcard-slot="${slot}" class="wildcard-slot ${rider ? "pick-filled" : "pick-empty"}" ${rider ? `draggable="true" data-picked-rider="${rider.id}" title="Open rider details"` : ""}><span class="position wildcard-mark" aria-label="Wildcard ${slot + 1}">★</span>${rider ? `${flag(rider.nation)}${escapeHtml(rider.name)}<span class="pick-multiplier" title="Wildcard bonus ${formatMultiplier(rider.wildcard_multiplier)} for this rider's UCI rank">${formatMultiplier(rider.wildcard_multiplier)}</span><button class="remove" data-remove-wildcard="${slot}" aria-label="Remove ${escapeHtml(rider.name)}">×</button><span class="rank-scale pick-rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span>` : "Drop a wildcard here"}</li>`; }).join("");
  const filteredRiders = state.event.riders.filter((rider) => matchesFilters(rider));
  const visibleRiders = visibleRiderList();
  // Country and team views share one grouping path; only the key and header differ.
  const byTeam = state.riderView === "team";
  const groupKey = groupKeyFor;
  const groupLabel = (key) => (byTeam ? key : countryName(key));
  const groupIcon = (key) => (byTeam ? teamIcon(key === NO_TEAM ? "" : key) : flag(key));
  const groupStats = new Map();
  state.event.riders.forEach((rider) => {
    const key = groupKey(rider);
    const current = groupStats.get(key) || { count: 0, points: 0 };
    groupStats.set(key, { count: current.count + 1, points: current.points + (rider.uci_points ?? 0) });
  });
  const ridersByGroup = new Map(); visibleRiders.forEach((rider) => ridersByGroup.set(groupKey(rider), [...(ridersByGroup.get(groupKey(rider)) || []), rider]));
  $("#filter-summary").textContent = `${visibleRiders.length} of ${filteredRiders.length} filtered riders shown (${state.event.riders.length} total). The bar shows UCI rank strength (red = stronger); the arrow shows the UCI points trend since the season start.`;
  ensureRiderViewControls();
  const riderCard = (rider) => { const topTenPosition = state.picks.indexOf(rider.id); const isWildcard = state.wildcards.includes(rider.id); const selected = topTenPosition >= 0 || isWildcard; const riderFlag = state.riderView === "country" ? "" : flag(rider.nation); const badge = topTenPosition >= 0 ? `<span class="pick-position"><strong>#${topTenPosition + 1}</strong><small>Top 10</small></span>` : isWildcard ? `<span class="pick-position"><strong>★</strong><small>Wildcard</small></span>` : ""; return `<article draggable="true" class="rider ${selected ? "selected" : ""} ${isWildcard ? "wildcard" : ""} ${state.favourites.has(rider.id) ? "favourite" : ""}" data-rider="${rider.id}" role="button" tabindex="0" title="${topTenPosition >= 0 ? `Top 10 position ${topTenPosition + 1}. Open rider details, or drag to move it.` : isWildcard ? "Wildcard. Open rider details, or drag to move it." : "Open rider details"}">${riderFlag}${escapeHtml(rider.name)}${heartButton(rider, "rider-fav")}<button type="button" class="rider-add" data-rider-add="${rider.id}" aria-label="Add ${escapeHtml(rider.name)} to your picks">+</button>${badge}<br><span class="rank">${rankingLabel(rider)}</span>${trendArrow(rider)}<span class="rank-scale" style="--rank-fill:${rankFill(rider)}%" aria-hidden="true"></span></article>`; };
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
      // UCI Rank ranks the groups themselves, by their riders' combined UCI points.
      if (state.riderSort === "rank") return direction * (statsB.points - statsA.points || byLabel);
      return direction * byLabel;
    });
    $("#riders").innerHTML = sortedGroups.map(([key, riders]) => { const stats = groupStats.get(key); return `<section class="country-group ${byTeam ? "team-group" : ""}"><h3>${groupIcon(key)}${escapeHtml(groupLabel(key))} <span class="country-meta">${stats.count} ${stats.count === 1 ? "rider" : "riders"} · ${Math.round(stats.points).toLocaleString()} pts</span>${groupHeart(key, riders, groupLabel(key))}</h3><div class="country-riders">${byUciRank(riders).map(riderCard).join("")}</div></section>`; }).join("");
  }
  document.querySelectorAll(".rider").forEach((node) => { node.addEventListener("click", (event) => { if (event.target.closest("[data-rider-add], [data-rider-fav]")) return; openRiderDetail(Number(node.dataset.rider)); }); node.addEventListener("keydown", (event) => { if (event.target.closest("[data-rider-add], [data-rider-fav]")) return; if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openRiderDetail(Number(node.dataset.rider)); } }); node.addEventListener("dragstart", (event) => { event.dataTransfer.setData("text/plain", node.dataset.rider); document.body.classList.add("mobile-dragging"); }); node.addEventListener("dragend", () => document.body.classList.remove("mobile-dragging")); });
  document.querySelectorAll("[data-rider-add]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); addRiderToPicks(Number(button.dataset.riderAdd)); }));
  document.querySelectorAll("#riders [data-rider-fav]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); toggleFavourite(Number(button.dataset.riderFav)); }));
  document.querySelectorAll("#riders [data-group-fav]").forEach((button) => button.addEventListener("click", () => toggleGroupFavourites(button.dataset.groupFav)));
  document.querySelectorAll("[data-picked-rider]").forEach((node) => node.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", node.dataset.pickedRider)));
  document.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); rememberPickState(); state.picks[Number(button.dataset.remove)] = null; render(); }));
  document.querySelectorAll("[data-remove-wildcard]").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); rememberPickState(); state.wildcards[Number(button.dataset.removeWildcard)] = null; render(); }));
  document.querySelectorAll("[data-wildcard-slot]").forEach((slot) => {
    const slotIndex = Number(slot.dataset.wildcardSlot);
    slot.addEventListener("click", (event) => {
      if (suppressMobilePickClick) return;
      if (event.target.closest("[data-remove-wildcard]")) return;
      if (state.mobilePendingRiderId) {
        const riderId = state.mobilePendingRiderId;
        state.mobilePendingRiderId = null;
        document.body.classList.remove("mobile-picking");
        $("#mobile-picks-handle").setAttribute("aria-expanded", "false");
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
  document.querySelectorAll("[data-position]").forEach((slot) => { slot.addEventListener("click", (event) => { if (suppressMobilePickClick || event.target.closest("[data-remove]")) return; if (state.mobilePendingRiderId) { const riderId = state.mobilePendingRiderId; state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking"); $("#mobile-picks-handle").setAttribute("aria-expanded", "false"); insertRider(riderId, Number(slot.dataset.position)); return; } const riderId = Number(slot.dataset.pickedRider); if (riderId) openRiderDetail(riderId); }); slot.addEventListener("dragover", (event) => { event.preventDefault(); slot.classList.add("drag-over"); }); slot.addEventListener("dragleave", () => slot.classList.remove("drag-over")); slot.addEventListener("drop", (event) => { event.preventDefault(); slot.classList.remove("drag-over"); document.body.classList.remove("mobile-dragging"); insertRider(Number(event.dataTransfer.getData("text/plain")), Number(slot.dataset.position)); }); });
}

// A searchable multi-select: the country and team filters are two instances.
function configureMultiPicker({ prefix, chipsId, filterKey, values, label, icon, code = () => "", emptyLabel }) {
  const picker = $(`#${prefix}-picker`);
  const query = $(`#${prefix}-query`);
  const options = $(`#${prefix}-options`);
  const chips = $(`#${chipsId}`);
  const clear = $(`#${prefix}-clear`);
  const selected = () => state.filters[filterKey];
  // A chosen value shows as its flag or jersey alone; its name is the tooltip.
  const updateLabel = () => {
    const chosen = selected();
    $(`#${prefix}-filter-label`).classList.toggle("hidden", chosen.length > 0);
    clear.classList.toggle("hidden", chosen.length === 0);
    chips.classList.toggle("hidden", chosen.length === 0);
    chips.innerHTML = chosen.map((value) => `<button type="button" class="country-chip icon-chip" data-remove-value="${escapeHtml(value)}" title="${escapeHtml(label(value))}: tap to remove" aria-label="Remove ${escapeHtml(label(value))}">${icon(value)}</button>`).join("");
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
  picker.addEventListener("click", (event) => { if (!event.target.closest(`#${prefix}-options, #${prefix}-clear`)) open(); });
  clear.addEventListener("click", () => {
    state.filters[filterKey] = [];
    close();
    updateLabel();
    render();
  });
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
    label: countryName, icon: flag, code: (country) => country, emptyLabel: "No matching countries",
  });
  const teamPicker = configureMultiPicker({
    prefix: "team", chipsId: "selected-teams", filterKey: "teams", values: teams,
    label: (team) => team, icon: (team) => teamIcon(team === NO_TEAM ? "" : team), emptyLabel: "No matching teams",
  });

  $("#rider-search").addEventListener("input", (event) => { state.filters.search = event.target.value.trim(); render(); });
  const ranks = state.event.riders.filter((rider) => rider.uci_rank !== 999999).map((rider) => rider.uci_rank);
  const points = state.event.riders.filter((rider) => rider.uci_points !== null).map((rider) => rider.uci_points);
  const maxRank = Math.max(1, ...ranks);
  const maxPoints = Math.max(1, Math.ceil(Math.max(0, ...points)));
  const pointsScaleKnee = 100;
  const rankControl = configureRangeFilter({
    prefix: "rank", min: 1, max: maxRank,
    toScale: (value) => maxRank === 1 ? 0 : 1000 * Math.log(value) / Math.log(maxRank),
    fromScale: (value) => maxRank === 1 ? 1 : Math.exp((value / 1000) * Math.log(maxRank)),
    roundMin: Math.round, roundMax: Math.round,
  });
  const pointsControl = configureRangeFilter({
    prefix: "points", min: 0, max: maxPoints,
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
  const rankForMaxPoints = (pointsValue) => {
    const eligible = rankedRiders.filter((rider) => rider.uci_points <= pointsValue);
    return eligible.length ? Math.min(...eligible.map((rider) => rider.uci_rank)) : maxRank;
  };
  const pointsForMinRank = (rankValue) => {
    const eligible = rankedRiders.filter((rider) => rider.uci_rank >= rankValue);
    return eligible.length ? Math.max(...eligible.map((rider) => rider.uci_points)) : 0;
  };
  // Switching the unit carries the range over, so the same riders stay shown:
  // a points floor becomes a rank ceiling and the other way round.
  const setUciUnit = (unit) => {
    if (unit === state.filters.uciUnit) return;
    const { pointsMin, pointsMax, rankMin, rankMax } = state.filters;
    if (unit === "rank") {
      rankControl.clear();
      if (pointsMin !== "") rankControl.setMax(rankForPoints(Number(pointsMin)));
      if (pointsMax !== "") rankControl.setMin(rankForMaxPoints(Number(pointsMax)));
      pointsControl.clear();
    } else {
      pointsControl.clear();
      if (rankMax !== "") pointsControl.setMin(pointsForRank(Number(rankMax)));
      if (rankMin !== "") pointsControl.setMax(pointsForMinRank(Number(rankMin)));
      rankControl.clear();
    }
    state.filters.uciUnit = unit;
    showUciUnit();
    render();
  };
  document.querySelectorAll("[data-uci-unit]").forEach((button) => button.addEventListener("click", () => setUciUnit(button.dataset.uciUnit)));

  $("#clear-filters").addEventListener("click", () => {
    // The unit is how you read the filter, not a filter: it stays as chosen.
    state.filters = emptyFilters(state.filters.uciUnit);
    $("#rider-search").value = "";
    countryPicker.reset();
    teamPicker.reset();
    rankControl.clear();
    pointsControl.clear();
    state.advancedDraft = null;
    updateAdvancedFilterBadge();
    render();
  });
}

function showUciUnit() {
  const unit = state.filters.uciUnit;
  document.querySelectorAll("[data-uci-panel]").forEach((node) => node.classList.toggle("hidden", node.dataset.uciPanel !== unit));
  document.querySelectorAll("[data-uci-unit]").forEach((button) => {
    button.classList.toggle("active", button.dataset.uciUnit === unit);
    button.setAttribute("aria-pressed", String(button.dataset.uciUnit === unit));
  });
}

// The bounds are edited in place, in the line that reads them out; when empty,
// they show the full range in placeholder grey.
function configureRangeFilter({ prefix, min, max, toScale, fromScale, roundMin, roundMax }) {
  const minInput = $(`#${prefix}-min`);
  const maxInput = $(`#${prefix}-max`);
  const minScale = $(`#${prefix}-min-scale`);
  const maxScale = $(`#${prefix}-max-scale`);
  minInput.placeholder = String(min);
  maxInput.placeholder = String(max);
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

  const updateLabel = () => [minInput, maxInput].forEach((input) => {
    input.style.width = `${Math.max(1, (input.value || input.placeholder).length) + 0.4}ch`;
  });
  updateLabel();
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
  // Dragging a thumb sets only its own side; the other side moves only when the
  // thumbs are pushed together. A thumb at its end of the track means no limit.
  const readScale = (scale, input, key) => {
    const atEnd = scale === minScale ? Number(scale.value) <= 0 : Number(scale.value) >= 1000;
    input.value = atEnd ? "" : rawValue(scale);
    state.filters[key] = String(input.value);
  };
  const onScale = (moved, other, pushed) => {
    if (pushed) other.value = moved.value;
    [[minScale, minInput, filterMinKey], [maxScale, maxInput, filterMaxKey]]
      .filter(([scale]) => scale === moved || pushed)
      .forEach(([scale, input, key]) => readScale(scale, input, key));
    updateTrack();
    updateLabel();
    render();
  };
  minScale.addEventListener("input", () => onScale(minScale, maxScale, Number(minScale.value) > Number(maxScale.value)));
  maxScale.addEventListener("input", () => onScale(maxScale, minScale, Number(maxScale.value) < Number(minScale.value)));
  const setBoundary = (input, scale, key, value, round) => {
    const numeric = Math.max(min, Math.min(max, round(value)));
    input.value = numeric;
    scale.value = Math.round(toScale(numeric));
    state.filters[key] = String(numeric);
    updateTrack();
    updateLabel();
  };
  const clear = () => {
    minInput.value = "";
    maxInput.value = "";
    minScale.value = 0;
    maxScale.value = 1000;
    state.filters[filterMinKey] = "";
    state.filters[filterMaxKey] = "";
    updateTrack();
    updateLabel();
  };
  return { clear, setMin: (value) => setBoundary(minInput, minScale, filterMinKey, value, roundMin), setMax: (value) => setBoundary(maxInput, maxScale, filterMaxKey, value, roundMax) };
}

const loadPrediction = loadLists;
let filtersConfigured = false;
async function loadEvent() {
  state.event = await request("/api/events/active");
  if (!filtersConfigured) { configureFilters(); filtersConfigured = true; }
  $("#event-title").textContent = state.event.name;
  renderDeadline();
}
function restoreSession() {
  const saved = localStorage.getItem("ten-up-player");
  if (saved) {
    try { state.player = JSON.parse(saved); } catch (_) { localStorage.removeItem("ten-up-player"); state.player = null; }
  }
  // The sign-in card starts hidden so a returning player never sees it flash
  // during the seconds the API spends waking up.
  if (!state.player) $("#identity").classList.remove("hidden");
  if (state.inviteCode) {
    $("#league-invite").textContent = `League invite: ${state.inviteCode}. Continue with your race name to join.`;
    $("#league-invite").classList.remove("hidden");
  }
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
      try { await applyInvite(); } catch (error) { showMessage("#prediction-message", error.message); }
      await loadLeagueInfo();
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
    if (state.inviteCode) await request(`/api/events/${state.event.id}/leagues/${encodeURIComponent(state.inviteCode)}`);
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
    try { await applyInvite(); } catch (error) { showMessage("#prediction-message", error.message); }
    await loadLeagueInfo();
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
$("#join-league-button").addEventListener("click", () => {
  $("#league-code").value = "";
  $("#league-message").textContent = "";
  $("#join-league-dialog").showModal();
  $("#league-code").focus();
});
$("#cancel-join-league").addEventListener("click", () => $("#join-league-dialog").close());
$("#league-code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); $("#confirm-join-league").click(); }
});
$("#confirm-join-league").addEventListener("click", async () => {
  const code = $("#league-code").value.trim().toLowerCase();
  if (!code) return showMessage("#league-message", "Enter a league code first.");
  try {
    await joinLeagueCode(code);
    $("#join-league-dialog").close();
    showMessage("#prediction-message", `Joined ${state.league.code}. Your deadline is shown above.`, true);
  } catch (error) { showMessage("#league-message", error.message); }
});
$("#leave-league-button").addEventListener("click", async () => {
  try {
    await request(`/api/events/${state.event.id}/players/${state.player.id}/league`, { method: "DELETE" });
    state.league = null;
    renderSessionControls();
    renderDeadline();
    showMessage("#prediction-message", "You left the local league. The global deadline now applies.", true);
  } catch (error) { showMessage("#prediction-message", error.message); }
});
$("#copy-league-link").addEventListener("click", async () => {
  if (!state.league) return;
  const invite = new URL("join_league/", new URL(".", window.location.href));
  invite.searchParams.set("league_code", state.league.code);
  try {
    await navigator.clipboard.writeText(invite.href);
    showMessage("#prediction-message", "League invite link copied.", true);
  } catch (_) { window.prompt("Copy your league invite link", invite.href); }
});
$("#close-rider-detail").addEventListener("click", () => $("#rider-detail-modal").close());
$("#rider-detail-modal").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) return event.currentTarget.close();
  const heart = event.target.closest("[data-rider-fav]");
  if (heart) toggleFavourite(Number(heart.dataset.riderFav));
  const add = event.target.closest("[data-rider-add]");
  if (add) {
    const riderId = Number(add.dataset.riderAdd);
    // On mobile, adding hands off to the tap-a-slot flow, which needs the
    // picks panel visible, so the modal closes first.
    if (isMobileLayout()) event.currentTarget.close();
    addRiderToPicks(riderId);
    if (event.currentTarget.open) renderRiderDetail(riderId);
  }
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
$("#logout").addEventListener("click", () => { flushTemplateAutosave(); if (hasUnsavedPickChanges() && !window.confirm("Did you forget to save your prediction?")) return; window.clearTimeout(autosaveTimer); closeListMenu(); localStorage.removeItem("ten-up-player"); state.player = null; state.picks = Array(10).fill(null); state.savedPicks = Array(10).fill(null); state.wildcards = Array(WILDCARD_COUNT).fill(null); state.savedWildcards = Array(WILDCARD_COUNT).fill(null); state.lists = { final: null, templates: [] }; state.activeList = "final"; state.renamingList = null; state.favourites = new Set(); state.favouritesOnly = false; resetPickHistory(); state.mobilePendingRiderId = null; document.body.classList.remove("mobile-picking", "mobile-dragging"); $("#prediction").classList.add("hidden"); $("#identity").classList.remove("hidden"); $("#username").value = ""; renderSessionControls(); showMessage("#identity-message", "You have logged out on this device.", true); $("#username").focus(); });
$("#save").addEventListener("click", saveFinal);
window.addEventListener("beforeunload", (event) => {
  if (!state.player) return;
  flushTemplateAutosave({ keepalive: true });
  if (!hasUnsavedPickChanges()) return;
  const canAutosave = state.activeList === "final" && state.finalAutosave && state.picks.some(Boolean);
  if (canAutosave) {
    const prefix = `${state.event.id}|${state.player.id}|`;
    const saving = [...pendingFinalSaves].some((key) => key.startsWith(prefix));
    const failed = failedFinalAutosave === finalSaveKey(writeIds(), workingList());
    if (!saving && !failed) {
      window.clearTimeout(finalAutosaveTimer);
      request(`/api/events/${state.event.id}/predictions`, { method: "PUT", keepalive: true, body: JSON.stringify({ player_id: state.player.id, ...picksPayload() }) }).catch(() => {});
      return;
    }
  }
  event.preventDefault();
  event.returnValue = "";
});
window.addEventListener("keydown", (event) => { if (!(event.ctrlKey || event.metaKey) || event.altKey || event.target instanceof HTMLElement && event.target.matches("input, textarea, select")) return; if (event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) restorePickState(state.redoStack, state.undoStack, "Redid last change."); else restorePickState(state.undoStack, state.redoStack, "Undid last change."); } else if (event.key.toLowerCase() === "y") { event.preventDefault(); restorePickState(state.redoStack, state.undoStack, "Redid last change."); } });
$("#cancel-pick").addEventListener("click", cancelPendingPick);
$("#mobile-picks-backdrop").addEventListener("click", closeMobilePicks);
const mobilePicksHandle = $("#mobile-picks-handle");
let handleStartX = null;
let handledHandleSwipe = false;
mobilePicksHandle.addEventListener("pointerdown", (event) => {
  handleStartX = event.clientX;
  mobilePicksHandle.setPointerCapture(event.pointerId);
});
mobilePicksHandle.addEventListener("pointerup", (event) => {
  if (handleStartX === null || Math.abs(event.clientX - handleStartX) < 35) return;
  handledHandleSwipe = true;
  if (event.clientX > handleStartX) document.body.classList.add("mobile-picks-open");
  else closeMobilePicks();
  mobilePicksHandle.setAttribute("aria-expanded", String(document.body.classList.contains("mobile-picks-open")));
  handleStartX = null;
  window.setTimeout(() => { handledHandleSwipe = false; }, 500);
});
mobilePicksHandle.addEventListener("pointercancel", () => { handleStartX = null; });
mobilePicksHandle.addEventListener("click", () => {
  if (handledHandleSwipe) { handledHandleSwipe = false; return; }
  toggleMobilePicks();
});
$("#mobile-save").addEventListener("click", () => $("#save").click());

// A held pick can be moved by touch between Top 10 positions and wildcards.
// An ordinary swipe still scrolls the drawer; the drag starts after the hold.
let mobilePickTouch = null;
let suppressMobilePickClick = false;
const clearMobilePickTouch = () => {
  if (!mobilePickTouch) return;
  window.clearTimeout(mobilePickTouch.timer);
  mobilePickTouch.source.classList.remove("mobile-pick-source");
  mobilePickTouch.target?.classList.remove("mobile-pick-target");
  mobilePickTouch = null;
};
const mobilePickSlotAt = (x, y) => document.elementFromPoint(x, y)?.closest("#picks li[data-position], #wildcards li[data-wildcard-slot]");
$("#prediction .picks-column").addEventListener("touchstart", (event) => {
  if (!isMobileLayout() || event.touches.length !== 1 || !document.body.matches(".mobile-picks-open, .mobile-picking")) return;
  if (event.target.closest("[data-remove], [data-remove-wildcard]")) return;
  const source = event.target.closest("li[data-picked-rider]");
  if (!source) return;
  const touch = event.touches[0];
  clearMobilePickTouch();
  mobilePickTouch = { source, riderId: Number(source.dataset.pickedRider), x: touch.clientX, y: touch.clientY, active: false, target: null, timer: 0 };
  mobilePickTouch.timer = window.setTimeout(() => {
    if (!mobilePickTouch) return;
    mobilePickTouch.active = true;
    mobilePickTouch.source.classList.add("mobile-pick-source");
  }, 350);
}, { passive: true });
document.addEventListener("touchmove", (event) => {
  if (!mobilePickTouch) return;
  const touch = event.touches[0];
  if (!touch) return;
  if (!mobilePickTouch.active) {
    if (Math.hypot(touch.clientX - mobilePickTouch.x, touch.clientY - mobilePickTouch.y) > 8) clearMobilePickTouch();
    return;
  }
  event.preventDefault();
  const target = mobilePickSlotAt(touch.clientX, touch.clientY);
  mobilePickTouch.target?.classList.remove("mobile-pick-target");
  mobilePickTouch.target = target;
  target?.classList.add("mobile-pick-target");
}, { passive: false });
document.addEventListener("touchend", (event) => {
  if (!mobilePickTouch) return;
  const drag = mobilePickTouch;
  if (drag.active) {
    const touch = event.changedTouches[0];
    const target = touch && mobilePickSlotAt(touch.clientX, touch.clientY);
    suppressMobilePickClick = true;
    window.setTimeout(() => { suppressMobilePickClick = false; }, 400);
    clearMobilePickTouch();
    if (target?.dataset.position !== undefined) insertRider(drag.riderId, Number(target.dataset.position));
    else if (target?.dataset.wildcardSlot !== undefined) insertWildcard(drag.riderId, Number(target.dataset.wildcardSlot));
  } else clearMobilePickTouch();
});
document.addEventListener("touchcancel", clearMobilePickTouch);
// The help popover is a <details>; close it on a tap anywhere else, as a phone user expects.
document.addEventListener("pointerdown", (event) => { const help = $(".event-help[open]"); if (help && !help.contains(event.target)) help.open = false; });
const backToTop = $("#back-to-top");
backToTop.addEventListener("click", () => window.scrollTo({ top: Math.max(0, $("#prediction").getBoundingClientRect().top + window.scrollY - 12), behavior: "smooth" }));
window.addEventListener("scroll", () => backToTop.classList.toggle("hidden", window.scrollY < 400), { passive: true });
window.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("dialog[open]")) { closeListMenu(); cancelPendingPick(); } });
boot();
