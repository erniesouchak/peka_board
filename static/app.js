// app.js – PEKA Board frontend

// Auto-reload raz po starcie żeby CSS cursor:none zadziałał przez Wayland
if (!sessionStorage.getItem('reloaded')) {
  sessionStorage.setItem('reloaded', '1');
  setTimeout(() => location.reload(), 10000);
}

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

  // Proporcjonalny grid — każdy bollard zajmuje tyle miejsca ile ma wierszy
  const gridRows = data.map(g => `${g.rows_per_bollard || 2}fr`).join(" ");
  container.style.display = "grid";
  container.style.gridTemplateRows = gridRows;
  container.style.gridTemplateColumns = "1fr";
  container.style.gap = "6px";
  container.style.height = "100%";

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
          <span>Godz.rozk.</span>
          <span style="text-align:left;padding-left:8px">Kierunek</span>
          <span style="text-align:left;padding-left:6px">Gdzie jest</span>
          <span>Za</span>
          <span>Godz.rzecz.</span>
          <span>Opóźn.</span>
          <span>Pojazd</span>
        </div>`;
      tile.appendChild(hdr);

      // Kontener wierszy — rozciąga się do wypełnienia kafelka
      const body = document.createElement("div");
      body.className = "dep-body";

      const rows = group.rows_per_bollard || 3;
      const deps = group.departures.slice(0, rows);
      for (const dep of deps) {
        const row = buildDepRow(dep);
        if (row) body.appendChild(row);
      }
      tile.appendChild(body);
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
    minText = "<1 min"; minClass = "arriving";
  } else {
    minText = `${min} min`;
    minClass = dep.realtime ? "realtime" : "scheduled";
  }

  // Godzina rozkładowa — obsługa >24h (nocne)
  const schedRaw = dep.scheduled_departure_str || "—";
  let schedStr = schedRaw;
  if (schedRaw !== "—") {
    const h = parseInt(schedRaw.split(":")[0], 10);
    if (h >= 24) {
      const hFixed = String(h - 24).padStart(2, "0");
      const m = schedRaw.split(":")[1];
      schedStr = `${hFixed}:${m}`;
    }
  }

  // Godzina rzeczywista = teraz + minuty — tylko gdy mamy dane RT
  let realStr = "—";
  let realClass = "";
  if (dep.realtime && min >= 0) {
    const realTime = new Date(Date.now() + min * 60000);
    realStr = String(realTime.getHours()).padStart(2,"0") + ":" +
              String(realTime.getMinutes()).padStart(2,"0");
    realClass = "realtime";
  }

  // Opóźnienie
  let delayText = "", delayClass = "nodata";
  if (dep.delay_seconds !== null && dep.delay_seconds !== undefined) {
    const dm = Math.round(dep.delay_seconds / 60);
    if (dm === 0)    { delayText = "na czas";  delayClass = "ontime"; }
    else if (dm > 0) { delayText = `+${dm}'`;  delayClass = "late";   }
    else             { delayText = `${dm}'`;   delayClass = "early";  }
  }

  // Pojazd — numer boczny + brygada + ikony FA
  const vid    = dep.vehicle_id    || "";
  const vlabel = dep.vehicle_label || "";
  const isLive = dep.realtime && vid;
  const vehNumClass = isLive ? "live" : "nodata";
  const vehDisplay  = vid || "—";

  const vi = dep.vehicle_info || {};

  // Niska podłoga: 0=brak, 1=pełna, 2=częściowa
  const lf = vi.low_floor_level !== undefined ? vi.low_floor_level
           : (vi.low_floor ? 1 : 0);
  const lfClass = lf === 1 ? "full" : lf === 2 ? "partial" : "none";
  const lfTitle = lf === 1 ? "Niska podłoga" : lf === 2 ? "Niska podłoga (częściowa)" : "Brak niskiej podłogi";

  // Klimatyzacja
  const acClass = vi.air_conditioner ? "active" : "none";
  const acTitle = vi.air_conditioner ? "Klimatyzacja" : "Brak klimatyzacji";

  const vehCell = `
    <div class="dep-veh-cell">
      <span class="dep-veh-num ${vehNumClass}">${esc(vehDisplay)}</span>
      <span class="dep-veh-label">${esc(vlabel)}</span>
      <i class="fa-solid fa-wheelchair icon-wheelchair ${lfClass}" title="${lfTitle}"></i>
      <i class="fa-solid fa-snowflake icon-ac ${acClass}" title="${acTitle}"></i>
    </div>`;

  const row = document.createElement("div");
  row.className = "dep-row";

  // Gdzie jest pojazd
  const currentStop  = dep.current_stop || "";
  const currentClass = dep.realtime && currentStop ? "live" : "";

  row.innerHTML = `
    <div class="dep-grid">
      <div class="dep-line">${esc(dep.line)}</div>
      <div class="dep-sched">${esc(schedStr)}</div>
      <div class="dep-direction">${esc(dep.direction)}</div>
      <div class="dep-current ${currentClass}">${esc(currentStop) || "—"}</div>
      <div class="dep-minutes ${minClass}">${minText}</div>
      <div class="dep-real ${realClass}">${realStr}</div>
      <div class="dep-delay ${delayClass}">${delayText}</div>
      ${vehCell}
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

// ── Harmonogram wywozów ───────────────────────────────────────────────────────

async function fetchWaste() {
  try {
    const r = await fetch("/api/waste");
    const data = await r.json();
    renderWaste(data);
  } catch(e) {
    document.getElementById("waste-body").innerHTML =
      "<div class='cal-placeholder'>Błąd ładowania</div>";
  }
}

function renderWaste(data) {
  const el = document.getElementById("waste-body");
  if (!data || !data.length) {
    el.innerHTML = "<div class='waste-ok'>✓ Brak wywozu w ciągu 3 dni</div>";
    return;
  }

  el.innerHTML = data.map(day => `
    <div class="waste-day">
      <span class="waste-day-label ${day.days_until === 0 ? 'today' : day.days_until === 1 ? 'tomorrow' : ''}">
        ${day.date_label}
      </span>
      <span class="waste-icons">
        ${day.items.map(item =>
          `<span class="waste-item" style="color:${item.color}" title="${item.label}">
            <i class="fa-solid ${item.icon}"></i>
            <span class="waste-item-label">${item.label}</span>
          </span>`
        ).join("")}
      </span>
    </div>
  `).join("");
}

// Uruchom przy starcie i odświeżaj co godzinę
fetchWaste();
setInterval(fetchWaste, 3600000);

// ── Zdjęcia Synology ─────────────────────────────────────────────────────────

async function fetchPhoto() {
  try {
    const r = await fetch("/api/photo/random");
    if (!r.ok) return;
    const data = await r.json();
    const el = document.getElementById("photo-body");
    if (el && data.proxy_url) {
      el.innerHTML = `<img src="${data.proxy_url}" alt="Zdjęcie"
        style="width:100%;height:100%;object-fit:cover;border-radius:0 0 10px 10px">`;
    }
  } catch(e) {
    // Cisza jeśli Synology niedostępny
  }
}

// Uruchom przy starcie i co 15 minut
fetchPhoto();
setInterval(fetchPhoto, 15 * 60 * 1000);

// ── Pogoda ────────────────────────────────────────────────────────────────────

async function fetchWeather() {
  try {
    const r = await fetch("/api/weather");
    if (!r.ok) return;
    const data = await r.json();
    renderWeather(data);
  } catch(e) {
    // Cisza gdy brak połączenia
  }
}

function renderWeather(data) {
  const c = data.current;
  if (!c) return;

  // Ikona SVG i temperatura
  document.getElementById("w-icon").outerHTML =
    `<img id="w-icon" src="/static/weather-icons/${c.icon}.svg" class="weather-icon-svg" alt="${c.description}">`;
  document.getElementById("w-temp").textContent = `${c.temp}°C`;

  // Szczegóły
  document.getElementById("w-details").innerHTML = `
    ${c.description}<br>
    Odczuwalna ${c.feels_like}°C &nbsp;·&nbsp; Wilgotność ${c.humidity}%<br>
    Wiatr ${c.wind_speed} m/s ${c.wind_dir}
    ${c.precip > 0 ? `&nbsp;·&nbsp; Opady ${c.precip} mm` : ""}
  `;

  // Prognoza 3-dniowa
  const fc = data.forecast || [];
  document.getElementById("weather-forecast").innerHTML = fc.map(d => `
    <div class="forecast-day">
      <span class="forecast-day-name">${d.day}</span>
      <span class="forecast-desc">${d.description}</span>
      <img src="/static/weather-icons/${d.icon}.svg" class="forecast-icon-svg" alt="${d.description}">
      <span class="forecast-temps">
        <span class="t-max">${d.t_max}°</span>
        <span class="t-min"> / ${d.t_min}°</span>
      </span>
    </div>
  `).join("");
}

// Uruchom przy starcie i odświeżaj co 30 minut
fetchWeather();
setInterval(fetchWeather, 30 * 60 * 1000);

// ── Kalendarz ─────────────────────────────────────────────────────────────────

async function fetchCalendar() {
  try {
    const r = await fetch("/api/calendar");
    if (!r.ok) return;
    const data = await r.json();
    renderCalendar(data);
  } catch(e) {
    document.getElementById("calendar-body").innerHTML =
      "<div class='cal-placeholder'>Błąd ładowania</div>";
  }
}

function renderCalendar(events) {
  const el = document.getElementById("calendar-body");
  if (!events || !events.length) {
    el.innerHTML = "<div class='cal-placeholder'>Brak nadchodzących wydarzeń</div>";
    return;
  }

  el.innerHTML = events.map(ev => {
    const isToday    = ev.days_until === 0;
    const isTomorrow = ev.days_until === 1;
    const isSoon     = ev.days_until >= 2 && ev.days_until <= 3;
    const labelClass = isToday ? "today" : isTomorrow ? "tomorrow" : isSoon ? "soon" : "";
    const time = ev.all_day ? "" : `<span class="cal-time">${ev.start_time}</span>`;

    return `
      <div class="cal-event ${isToday ? 'cal-today' : ''}">
        <div class="cal-date-label ${labelClass}">${ev.date_label}</div>
        <div class="cal-event-main">
          ${time}
          <span class="cal-summary">${esc(ev.summary)}</span>
        </div>
      </div>`;
  }).join("");
}

// Uruchom przy starcie i odświeżaj co godzinę
fetchCalendar();
setInterval(fetchCalendar, 3600000);

// ── Sport ─────────────────────────────────────────────────────────────────────

async function fetchSports() {
  try {
    const [r1, r2] = await Promise.all([
      fetch("/api/sports"),
      fetch("/api/sports/scores"),
    ]);
    if (!r1.ok) return;
    const data   = await r1.json();
    const scores = r2.ok ? await r2.json() : {};
    renderSports(data, scores);
  } catch(e) {}
}

function renderSports(data, scores) {
  const el = document.getElementById("sports-body");
  if (!el) return;
  let html = "";

  // Piłka nożna — najpierw
  if (data.soccer && data.soccer.length > 0) {
    for (const s of data.soccer) {
      html += `<div class="sport-league">${esc(s.league)}</div>`;
      html += `
        <div class="sport-row-simple">
          <span class="sport-team">${esc(s.team)}</span>
          <span class="sport-record">${s.rank}. · ${s.wins}-${s.draws}-${s.losses} · ${s.points} pkt</span>
        </div>`;
      const leagueKey = s.league_key;
      if (leagueKey && scores[leagueKey]) html += renderGameRow(scores[leagueKey]);
    }
  }

  // NFL
  if (data.nfl && data.nfl.length > 0) {
    html += `<div class="sport-league">NFL – NFC West</div>`;
    for (const t of data.nfl) {
      html += `
        <div class="sport-row-simple">
          <span class="sport-team sport-highlight">${esc(t.team)}</span>
          <span class="sport-record">${t.wins}-${t.losses}</span>
        </div>`;
    }
    if (scores.nfl) html += renderGameRow(scores.nfl);
  }

  // MLB
  if (data.mlb && data.mlb.length > 0) {
    html += `<div class="sport-league">MLB – AL West</div>`;
    for (const t of data.mlb) {
      html += `
        <div class="sport-row-simple">
          <span class="sport-team sport-highlight">${esc(t.team)}</span>
          <span class="sport-record">${t.wins}-${t.losses}</span>
        </div>`;
    }
    if (scores.mlb) html += renderGameRow(scores.mlb);
  }

  el.innerHTML = html || "<div class='cal-placeholder'>Brak danych</div>";
}

function renderGameRow(sc) {
  let html = "";
  const fmt = (g) => {
    if (!g) return "";
    const d  = new Date(g.date);
    const vs = g.home ? `vs ${g.opp}` : `@ ${g.opp}`;
    if (g.status === "post") {
      const icon = g.won === true ? "✓" : g.won === false ? "✗" : "=";
      const cls  = g.won === true ? "sport-win" : g.won === false ? "sport-loss" : "sport-draw";
      return `<div class="sport-game-row ${cls}">${icon} ${vs} <span class="sport-score-val">${g.our_score}–${g.opp_score}</span></div>`;
    } else if (g.status === "in") {
      return `<div class="sport-game-row sport-live">● LIVE ${vs} <span class="sport-score-val">${g.our_score}–${g.opp_score}</span></div>`;
    } else {
      const time = d.toLocaleString("pl-PL", {weekday:"short", day:"numeric", month:"numeric", hour:"2-digit", minute:"2-digit"});
      return `<div class="sport-game-row sport-next">→ ${vs} ${time}</div>`;
    }
  };
  if (sc.last)  html += fmt(sc.last);
  if (sc.next)  html += fmt(sc.next);
  return html;
}

// Uruchom przy starcie i odświeżaj co godzinę (standings) / co minutę (scores)
fetchSports();
setInterval(async () => {
  try {
    const r = await fetch("/api/sports/scores");
    if (!r.ok) return;
    const scores = await r.json();
    // Pobierz też standings ze cache
    const r2 = await fetch("/api/sports");
    if (!r2.ok) return;
    const data = await r2.json();
    renderSports(data, scores);
  } catch(e) {}
}, 60000); // co minutę
setInterval(fetchSports, 3600000); // standings co godzinę
