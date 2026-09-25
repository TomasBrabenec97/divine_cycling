(() => {
  const $ = selector => document.querySelector(selector);
  const formatDate = value => value ? new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(new Date(/[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`)) : "—";
  const api = async (path, options = {}) => {
    const base = (window.DIVINE_API_BASE_URL || "").replace(/\/$/, "");
    const response = await fetch(`${base}${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", "X-Admin-Key": sessionStorage.getItem("divine-admin-key") || "" },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Request failed");
    return body;
  };
  const parseFilter = source => {
    if (!source.trim()) return () => true;
    const clauses = source.trim().split(/\s+and\s+/i).map(part => {
      const match = part.match(/^"?(username|league_code|submitted_flag)"?\s*=\s*(?:"([^"]*)"|'([^']*)'|(TRUE|FALSE))$/i);
      if (!match) throw new Error('Use username, league_code, or submitted_flag with = and optional AND. Example: league_code = "prg-office" and submitted_flag = FALSE');
      const field = match[1].toLowerCase();
      const value = match[2] ?? match[3] ?? match[4];
      if (field === "submitted_flag" && !/^(TRUE|FALSE)$/i.test(value)) throw new Error("submitted_flag must be TRUE or FALSE");
      if (field !== "submitted_flag" && /^(TRUE|FALSE)$/i.test(match[4] || "")) throw new Error(`${field} needs a quoted value`);
      return { field, value: field === "submitted_flag" ? value.toUpperCase() === "TRUE" : value.toLowerCase() };
    });
    return player => clauses.every(({ field, value }) => field === "submitted_flag"
      ? player.submitted_flag === value
      : (player[field] || "").toLowerCase() === value);
  };

  let eventId;
  let data;
  let selectedPlayer;
  const render = () => {
    const rows = $("#admin-player-rows");
    rows.replaceChildren();
    let players;
    try {
      players = data.players.filter(parseFilter($("#admin-player-filter").value));
      $("#admin-filter-message").textContent = "";
    } catch (error) {
      $("#admin-filter-message").textContent = error.message;
      return;
    }
    for (const player of players) {
      const row = document.createElement("tr");
      for (const value of [player.username, player.submitted_flag ? "Yes" : "No", formatDate(player.last_edit), player.league_code || "—"]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      const action = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Delete";
      button.addEventListener("click", () => {
        selectedPlayer = player;
        $("#admin-delete-prompt").textContent = `Are you sure you want to delete player "${player.username}"? If yes, type DELETE.`;
        $("#admin-delete-confirmation").value = "";
        $("#admin-delete-message").textContent = "";
        $("#admin-delete-dialog").showModal();
        $("#admin-delete-confirmation").focus();
      });
      action.append(button);
      row.append(action);
      rows.append(row);
    }
    $("#admin-player-count").textContent = `${players.length} of ${data.players.length} players shown`;
    const leagues = $("#admin-league-rows");
    leagues.replaceChildren();
    for (const league of data.leagues) {
      const row = document.createElement("tr");
      for (const value of [league.code, league.joined_players, league.submitted_players, formatDate(league.submission_deadline)]) {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      }
      leagues.append(row);
    }
    if (!data.leagues.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No local leagues for this event.";
      row.append(cell);
      leagues.append(row);
    }
  };
  window.addEventListener("DOMContentLoaded", async () => {
    $("#admin-player-filter").addEventListener("input", () => { if (data) render(); });
    $("#admin-delete-cancel").addEventListener("click", () => $("#admin-delete-dialog").close());
    $("#admin-delete-submit").addEventListener("click", async () => {
      if (!selectedPlayer) return;
      const confirmation = $("#admin-delete-confirmation").value;
      if (confirmation !== "DELETE") {
        $("#admin-delete-message").textContent = "Type DELETE exactly to confirm.";
        return;
      }
      const button = $("#admin-delete-submit");
      button.disabled = true;
      try {
        await api(`/api/admin/players/${selectedPlayer.id}/delete`, { method: "POST", body: JSON.stringify({ confirmation }) });
        data = await api(`/api/admin/events/${eventId}/players`);
        $("#admin-delete-dialog").close();
        render();
      } catch (error) {
        $("#admin-delete-message").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
    try {
      const overview = await api("/api/admin/overview");
      const event = overview.events.find(item => item.slug === "road-worlds-2026");
      if (!event) return;
      eventId = event.id;
      data = await api(`/api/admin/events/${eventId}/players`);
      $("#admin-players").classList.remove("hidden");
      $("#admin-leagues").classList.remove("hidden");
      render();
    } catch (error) {
      $("#admin-filter-message").textContent = error.message;
      $("#admin-players").classList.remove("hidden");
    }
  });
})();
