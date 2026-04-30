// app.js – PEKA Board frontend

const REFRESH_INTERVAL = 60; // sekund
let countdown = REFRESH_INTERVAL;
let countdownTimer = null;

// ── Zegar ─────────────────────────────────────────────────────────────────────

const DAYS_PL = ["niedziela","poniedziałek","wtorek","środa","czwartek","piątek","sobota"];
const MONTHS_PL = [
  "stycznia","lutego","marca","kwietnia","maja","czerwca",
  "lipca","sierpnia","września","października","listopada","grudnia"
];

function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, "0");
  const m = String(now.getMinutes()).padStart(2, "0");
  const s = String(now.getSeconds()).padStart(2, "0");
  document.getElementById("clock-time").textContent = `${h}:${m}:${s}`;

  const day  = DAYS_PL[now.getDay()];
  const date = now.getDate();
  const mon  = MONTHS_PL[now.getMonth()];
  const year = now.getFullYear();
  document.getElementById("clock-date").textContent = `${day}, ${date} ${mon} ${year}`;
}

setInterval(updateClock, 1000);
updateClock();

// ── Odświeżanie danych ────────────────────────────────────────────────────────

async function fetchDepartures() {
  resetCountdown();
  try {
    const [depsRes, statusRes] = await Promise.all([
      fetch("/api/departures"),
      fetch("/api/status"),
    ]);
    const deps   = await depsRes.json();
    const status = await statusRes.json();
    renderDepartures(deps);
    updateStatusBar(status);
    document.getElementById("clock-status").textContent =
      `Odświeżono ${status.time}`;
  } catch (e) {
    document.getElementById("clock-status").textContent = "⚠ Błąd połączenia";
    console.error("Fetch error:", e);
  }
}

function resetCountdown() {
  countdown = REFRESH_INTERVAL;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    document.getElementById("sb-refresh").textContent =
      countdown > 0 ? `Odświeżenie za ${countdown}s` : "Odświeżanie…";
    if (countdown <= 0) {
      clearInterval(countdownTimer);
      fetchDepartures();
    }
  }, 1000);
}

// ── Renderowanie odjazdów ─────────────────────────────────────────────────────

function renderDepartures(data) {
  const container = document.getElementById("departures-container");
  container.innerHTML = "";

  if (!Array.isArray(data) || data.length === 0) {
    container.innerHTML = `<div class="dep-empty">Brak skonfigurowanych przystanków.</div>`;
    return;
  }

  for (const group of data) {
    const tile = document.createElement("div");
    tile.className = "tile-bollard";

    // Nagłówek bollardu
    tile.innerHTML = `
      <div class="bollard-header">
        <span class="bollard-label">${esc(group.bollard.label)}</span>
        <span class="bollard-symbol">${esc(group.bollard.symbol)}</span>
      </div>`;

    if (group.error) {
      tile.innerHTML += `<div class="dep-empty">⚠ ${esc(group.error)}</div>`;
    } else if (!group.departures || group.departures.length === 0) {
      tile.innerHTML += `<div class="dep-empty">Brak odjazdów w najbliższym czasie.</div>`;
    } else {
      // Nagłówek kolumn
      tile.innerHTML += `
        <div class="dep-header">
          <span>Linia</span>
          <span>Kierunek</span>
          <span>Za (min)</span>
          <span>Godz.</span>
          <span>Opóźn.</span>
          <span>Pojazd</span>
        </div>`;

      for (const dep of group.departures) {
        tile.appendChild(buildDepRow(dep));
      }
    }

    container.appendChild(tile);
  }
}

function buildDepRow(dep) {
  const row = document.createElement("div");
  row.className = "dep-row";

  // Minuty
  const min = dep.minutes;
  let minText, minClass;
  if (min < 0) {
    return row; // kurs już minął – pomiń
  } else if (dep.on_stop_point) {
    minText = ">> STOI"; minClass = "onboard";
  } else if (min === 0) {
    minText = "wjeżdża"; minClass = "arriving";
  } else {
    minText = String(min);
    minClass = dep.realtime ? "realtime" : "scheduled";
  }

  // Opóźnienie
  let delayText = "", delayClass = "nodata";
  if (dep.delay_seconds !== null && dep.delay_seconds !== undefined) {
    const dm = Math.round(dep.delay_seconds / 60);
    if (dm === 0)      { delayText = "punktualnie"; delayClass = "ontime"; }
    else if (dm > 0)   { delayText = `+${dm} min`;  delayClass = "late";  }
    else               { delayText = `${dm} min`;   delayClass = "early"; }
  }

  // Pojazd
  const vi = dep.vehicle_info || {};
  const vid = dep.vehicle_id || "";

  row.innerHTML = `
    <div class="dep-line">${esc(dep.line)}</div>
    <div class="dep-direction">${esc(dep.direction)}</div>
    <div class="dep-minutes ${minClass}">${minText}</div>
    <div class="dep-time">${esc(dep.scheduled_departure_str)}</div>
    <div class="dep-delay ${delayClass}">${delayText}</div>
    <div class="dep-vehicle">${buildVehicleBadges(vid, vi)}</div>
  `;
  return row;
}

function buildVehicleBadges(vid, vi) {
  if (!vid && Object.keys(vi).length === 0) return `<span class="veh-badge">—</span>`;

  let html = vid ? `<span class="veh-badge active" title="Numer boczny">${esc(vid)}</span>` : "";

  const badges = [
    { key: "low_floor",       icon: "🔽", label: "Niska podłoga" },
    { key: "air_conditioner", icon: "❄",  label: "Klimatyzacja"  },
    { key: "ramp",            icon: "♿",  label: "Rampa"         },
    { key: "ticket_machine",  icon: "🎟",  label: "Biletomat"    },
  ];

  for (const b of badges) {
    if (vi[b.key]) {
      html += `<span class="veh-badge active" title="${b.label}">${b.icon}</span>`;
    }
  }
  return html || `<span class="veh-badge">—</span>`;
}

// ── Status bar ────────────────────────────────────────────────────────────────

function updateStatusBar(status) {
  document.getElementById("sb-gtfs").textContent =
    `GTFS: ważny do ${status.gtfs_valid_until}`;
  document.getElementById("sb-rt").textContent =
    status.rt_error
      ? `RT: ⚠ ${status.rt_error}`
      : `RT: ${status.rt_last_update}`;
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;")
    .replace(/</g,"&lt;")
    .replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

// ── Start ─────────────────────────────────────────────────────────────────────
fetchDepartures();
