// app.js – PEKA Board frontend

const REFRESH_INTERVAL = 60;
let countdown = REFRESH_INTERVAL;
let countdownTimer = null;

// ── Zegar ─────────────────────────────────────────────────────────────────────
const DAYS_PL   = ["niedziela","poniedziałek","wtorek","środa","czwartek","piątek","sobota"];
const MONTHS_PL = ["stycznia","lutego","marca","kwietnia","maja","czerwca",
                   "lipca","sierpnia","września","października","listopada","grudnia"];

function updateClock() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2,"0");
  const m = String(now.getMinutes()).padStart(2,"0");
  const s = String(now.getSeconds()).padStart(2,"0");
  document.getElementById("clock-time").textContent = `${h}:${m}:${s}`;
  const day  = DAYS_PL[now.getDay()];
  const date = now.getDate();
  const mon  = MONTHS_PL[now.getMonth()];
  const year = now.getFullYear();
  document.getElementById("clock-date").textContent = `${day}, ${date} ${mon} ${year}`;
}

setInterval(updateClock, 1000);
updateClock();

// ── Odświeżanie ───────────────────────────────────────────────────────────────
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
    document.getElementById("clock-status").textContent = `Odświeżono ${status.time}`;
  } catch(e) {
    document.getElementById("clock-status").textContent = "⚠ Błąd połączenia";
  }
}

function resetCountdown() {
  countdown = REFRESH_INTERVAL;
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown--;
    document.getElementById("sb-refresh").textContent =
      countdown > 0 ? `Odświeżenie za ${countdown}s` : "Odświeżanie…";
    if (countdown <= 0) { clearInterval(countdownTimer); fetchDepartures(); }
  }, 1000);
}

// ── Renderowanie tablicy ──────────────────────────────────────────────────────
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
      const hdr = document.createElement("div");
      hdr.className = "dep-header";
      hdr.innerHTML = `
        <div class="dep-grid">
          <span>Linia</span>
          <span>Kierunek</span>
          <span>Godz.rozk.</span>
          <span>Za(min)</span>
          <span>Godz.rzecz.</span>
          <span>Opóźn.</span>
          <span>Pojazd</span>
          <span>Info</span>
        </div>`;
      tile.appendChild(hdr);

      // Wiersze — max 3
      const deps = group.departures.slice(0, 3);
      for (const dep of deps) {
        const row = buildDepRow(dep);
        if (row) tile.appendChild(row);
      }
    }

    container.appendChild(tile);
  }
}

function buildDepRow(dep) {
  const min = dep.minutes;
  if (min < 0) return null;

  // Minuty
  let minText, minClass;
  if (dep.on_stop_point) {
    minText = "STOI"; minClass = "onboard";
  } else if (min === 0) {
    minText = "<<"; minClass = "arriving";
  } else {
    minText = String(min);
    minClass = dep.realtime ? "realtime" : "scheduled";
  }

  // Godzina rozkładowa
  const schedStr = dep.scheduled_departure_str || "—";

  // Godzina rzeczywista = teraz + minuty
  let realStr = "—";
  if (min >= 0) {
    const realTime = new Date(Date.now() + min * 60000);
    realStr = String(realTime.getHours()).padStart(2,"0") + ":" +
              String(realTime.getMinutes()).padStart(2,"0");
  }
  const realClass = dep.realtime ? "realtime" : "";

  // Opóźnienie
  let delayText = "", delayClass = "nodata";
  if (dep.delay_seconds !== null && dep.delay_seconds !== undefined) {
    const dm = Math.round(dep.delay_seconds / 60);
    if (dm === 0)    { delayText = "punk.";    delayClass = "ontime"; }
    else if (dm > 0) { delayText = `+${dm}'`;  delayClass = "late";   }
    else             { delayText = `${dm}'`;   delayClass = "early";  }
  }

  // Pojazd
  const vid = dep.vehicle_id || "";
  const isLive = dep.realtime && vid;
  const vehicleClass = isLive ? "live" : "";

  // Ikony
  const vi = dep.vehicle_info || {};
  const icons = [
    { key: "low_floor",       icon: "🔽", title: "Niska podłoga" },
    { key: "air_conditioner", icon: "❄",  title: "Klimatyzacja"  },
    { key: "ticket_machine",  icon: "🎟",  title: "Biletomat"    },
  ];

  const iconsHtml = icons.map(b =>
    `<span class="icon-badge ${vi[b.key] ? 'active' : ''}" title="${b.title}">${b.icon}</span>`
  ).join("");

  const row = document.createElement("div");
  row.className = "dep-row";
  row.innerHTML = `
    <div class="dep-grid">
      <div class="dep-line">${esc(dep.line)}</div>
      <div class="dep-direction">${esc(dep.direction)}</div>
      <div class="dep-sched">${esc(schedStr)}</div>
      <div class="dep-minutes ${minClass}">${minText}</div>
      <div class="dep-real ${realClass}">${realStr}</div>
      <div class="dep-delay ${delayClass}">${delayText}</div>
      <div class="dep-vehicle ${vehicleClass}">${esc(vid) || "—"}</div>
      <div class="dep-icons">${iconsHtml}</div>
    </div>`;
  return row;
}

// ── Status bar ────────────────────────────────────────────────────────────────
function updateStatusBar(status) {
  document.getElementById("sb-gtfs").textContent =
    `GTFS: ${status.gtfs_valid_from || "—"} – ${status.gtfs_valid_until}`;
  document.getElementById("sb-rt").textContent =
    status.rt_error ? `RT: ⚠ ${status.rt_error}` : `RT: ${status.rt_last_update}`;
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

fetchDepartures();
