// app.js – PEKA Board frontend (dashboard a'la HA: tablice + moduły)

// Auto-reload raz po starcie żeby CSS cursor:none zadziałał przez Wayland
if (!sessionStorage.getItem('reloaded')) {
  sessionStorage.setItem('reloaded', '1');
  setTimeout(() => location.reload(), 10000);
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function addHeader(tile, icon, title) {
  const h = document.createElement("div");
  h.className = "tile-header";
  h.innerHTML = `<i class="fa-solid ${icon}"></i> ${esc(title)}`;
  tile.appendChild(h);
}

function addBody(tile, cls) {
  const b = document.createElement("div");
  b.className = "tile-content" + (cls ? " " + cls : "");
  tile.appendChild(b);
  return b;
}

// ═══════════════════════════════════════════════════════════════════════════════
// MODUŁY — każdy renderuje w przekazanym kontenerze (kafelku), zwraca {timers:[]}
// ═══════════════════════════════════════════════════════════════════════════════

// ── Zegar ──────────────────────────────────────────────────────────────────────
const DAYS_PL   = ["niedziela","poniedziałek","wtorek","środa","czwartek","piątek","sobota"];
const MONTHS_PL = ["stycznia","lutego","marca","kwietnia","maja","czerwca",
                   "lipca","sierpnia","września","października","listopada","grudnia"];

function tickClock(el) {
  const now = new Date();
  const h = String(now.getHours()).padStart(2,"0");
  const m = String(now.getMinutes()).padStart(2,"0");
  const s = String(now.getSeconds()).padStart(2,"0");
  const t = el.querySelector(".clock-time");
  const d = el.querySelector(".clock-date");
  if (t) t.textContent = `${h}:${m}:${s}`;
  if (d) d.textContent = `${DAYS_PL[now.getDay()]}, ${now.getDate()} ${MONTHS_PL[now.getMonth()]} ${now.getFullYear()}`;
}

// ── Pogoda ─────────────────────────────────────────────────────────────────────
async function fetchWeather(el) {
  try {
    const r = await fetch("/api/weather");
    if (!r.ok) return;
    renderWeather(el, await r.json());
  } catch(e) {}
}

function renderWeather(el, data) {
  const c = data.current;
  if (!c) return;
  const icon = el.querySelector(".w-icon");
  if (icon) { icon.src = `/static/weather-icons/${c.icon}.svg`; icon.alt = c.description; }
  const temp = el.querySelector(".w-temp");
  if (temp) temp.textContent = `${c.temp}°C`;

  const det = el.querySelector(".w-details");
  if (det) det.innerHTML = `
    ${c.description}<br>
    Odczuwalna ${c.feels_like}°C &nbsp;·&nbsp; Wilgotność ${c.humidity}%<br>
    Wiatr ${c.wind_speed} m/s ${c.wind_dir}
    ${c.precip > 0 ? `&nbsp;·&nbsp; Opady ${c.precip} mm` : ""}`;

  const fcEl = el.querySelector(".weather-forecast");
  if (fcEl) fcEl.innerHTML = (data.forecast || []).map(d => `
    <div class="forecast-day">
      <span class="forecast-day-name">${d.day}</span>
      <span class="forecast-desc">${d.description}</span>
      <img src="/static/weather-icons/${d.icon}.svg" class="forecast-icon-svg" alt="${d.description}">
      <span class="forecast-temps"><span class="t-max">${d.t_max}°</span><span class="t-min"> / ${d.t_min}°</span></span>
    </div>`).join("");

  const hrEl = el.querySelector(".weather-hourly");
  if (hrEl) hrEl.innerHTML = (data.hourly || []).map(h => `
    <div class="forecast-day">
      <span class="forecast-day-name hourly-label">${h.label}</span>
      <span class="forecast-desc">${h.description}</span>
      <img src="/static/weather-icons/${h.icon}.svg" class="forecast-icon-svg" alt="${h.description}">
      <span class="forecast-temps"><span class="t-max">${h.temp}°</span></span>
    </div>`).join("");
}

// ── Kalendarz ──────────────────────────────────────────────────────────────────
async function fetchCalendar(el) {
  try {
    const r = await fetch("/api/calendar");
    if (!r.ok) return;
    renderCalendar(el, await r.json());
  } catch(e) {
    el.innerHTML = "<div class='cal-placeholder'>Błąd ładowania</div>";
  }
}

function renderCalendar(el, events) {
  if (!events || !events.length) {
    el.innerHTML = "<div class='cal-placeholder'>Brak nadchodzących wydarzeń</div>";
    return;
  }
  el.innerHTML = events.map(ev => {
    const isToday    = ev.days_until === 0;
    const isTomorrow = ev.days_until === 1;
    const isSoon     = ev.days_until >= 2 && ev.days_until <= 3;
    const labelClass = ev.is_holiday ? "holiday"
                     : isToday    ? "today"
                     : isTomorrow ? "tomorrow"
                     : isSoon     ? "soon" : "";
    const time = ev.all_day ? "" : `<span class="cal-time">${ev.start_time}</span>`;
    return `
      <div class="cal-event ${isToday ? 'cal-today' : ''}">
        <div class="cal-date-label ${labelClass}">${ev.date_label}</div>
        <div class="cal-event-main">${time}<span class="cal-summary">${esc(ev.summary)}</span></div>
      </div>`;
  }).join("");
}

// ── Wywóz odpadów ──────────────────────────────────────────────────────────────
async function fetchWaste(el) {
  try {
    const r = await fetch("/api/waste");
    renderWaste(el, await r.json());
  } catch(e) {
    el.innerHTML = "<div class='cal-placeholder'>Błąd ładowania</div>";
  }
}

function renderWaste(el, data) {
  if (!data || !data.length) {
    el.innerHTML = "<div class='waste-ok'>✓ Brak wywozu w ciągu 3 dni</div>";
    return;
  }
  el.innerHTML = data.map(day => `
    <div class="waste-day">
      <span class="waste-day-label ${day.days_until === 0 ? 'today' : day.days_until === 1 ? 'tomorrow' : ''}">${day.date_label}</span>
      <span class="waste-icons">
        ${day.items.map(item =>
          `<span class="waste-item" style="color:${item.color}" title="${item.label}">
            <i class="fa-solid ${item.icon}"></i><span class="waste-item-label">${item.label}</span>
          </span>`).join("")}
      </span>
    </div>`).join("");
}

// ── Zdjęcia ────────────────────────────────────────────────────────────────────
async function fetchPhoto(el) {
  try {
    const r = await fetch("/api/photo/random");
    if (!r.ok) return;
    const data = await r.json();
    if (data.proxy_url) {
      el.innerHTML = `<img src="${data.proxy_url}" alt="Zdjęcie" class="photo-img">`;
    }
  } catch(e) {}
}

// ── Sport ──────────────────────────────────────────────────────────────────────
async function fetchSports(el) {
  try {
    const [r1, r2] = await Promise.all([fetch("/api/sports"), fetch("/api/sports/scores")]);
    if (!r1.ok) return;
    renderSports(el, await r1.json(), r2.ok ? await r2.json() : {});
  } catch(e) {}
}

function renderSports(el, data, scores) {
  if (data.disabled) {
    el.innerHTML = "<div class='cal-placeholder'>Widget sportu wyłączony w ustawieniach</div>";
    return;
  }
  let html = "";
  if (data.soccer && data.soccer.length > 0) {
    for (const s of data.soccer) {
      html += `<div class="sport-league">${esc(s.league)}</div>`;
      html += `<div class="sport-row-simple">
          <span class="sport-team sport-highlight">${esc(s.team)}</span>
          <span class="sport-record">${s.rank}. · ${s.wins}-${s.draws}-${s.losses} · ${s.points} pkt</span>
        </div>`;
      if (s.league_key && scores[s.league_key]) html += renderGameRow(scores[s.league_key]);
    }
  }
  if (data.nfl && data.nfl.length > 0) {
    html += `<div class="sport-league">NFL${data.nfl_division ? " – " + esc(data.nfl_division) : ""}</div>`;
    for (const t of data.nfl) {
      html += `<div class="sport-row-simple"><span class="sport-team sport-highlight">${esc(t.team)}</span><span class="sport-record">${t.wins}-${t.losses}</span></div>`;
    }
    if (scores.nfl) html += renderGameRow(scores.nfl);
  }
  if (data.mlb && data.mlb.length > 0) {
    html += `<div class="sport-league">MLB${data.mlb_division ? " – " + esc(data.mlb_division) : ""}</div>`;
    for (const t of data.mlb) {
      html += `<div class="sport-row-simple"><span class="sport-team sport-highlight">${esc(t.team)}</span><span class="sport-record">${t.wins}-${t.losses}</span></div>`;
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
    let scorersInline = "";
    if (g.scorers && g.scorers.length > 0) {
      scorersInline = `<span class="sport-scorers-inline">` +
        g.scorers.map(s => {
          const og = s.own_goal ? " (og)" : "";
          const cls = s.is_ours ? "sport-scorer-ours" : "sport-scorer-opp";
          return `<span class="${cls}">${esc(s.name)}${og} ${s.minute}'</span>`;
        }).join(" · ") + `</span>`;
    }
    if (g.status === "post") {
      const icon = g.won === true ? "✓" : g.won === false ? "✗" : "=";
      const cls  = g.won === true ? "sport-win" : g.won === false ? "sport-loss" : "sport-draw";
      return `<div class="sport-game-row ${cls}">${icon} ${vs} <span class="sport-score-val">${g.our_score}–${g.opp_score}</span>${scorersInline}</div>`;
    } else if (g.status === "in") {
      const clock = g.game_clock ? ` <span class="sport-live-clock">${esc(g.game_clock)}</span>` : "";
      return `<div class="sport-game-row sport-live"><span class="sport-live-dot">●</span> LIVE${clock} ${vs} <span class="sport-score-val">${g.our_score}–${g.opp_score}</span>${scorersInline}</div>`;
    } else {
      const time = d.toLocaleString("pl-PL", {weekday:"short", day:"numeric", month:"numeric", hour:"2-digit", minute:"2-digit"});
      return `<div class="sport-game-row sport-next">→ ${vs} ${time}</div>`;
    }
  };
  if (sc.last)  html += fmt(sc.last);
  if (sc.next)  html += fmt(sc.next);
  return html;
}

// ── Rozkład jazdy — jeden przystanek = jedna encja ──────────────────────────────
async function fetchStop(body, head, cfg) {
  try {
    const rows = Math.max(1, Math.min(5, cfg.rows || 3));
    const r = await fetch(`/api/departures/${encodeURIComponent(cfg.symbol)}?rows=${rows}`);
    if (!r.ok) { body.innerHTML = `<div class="dep-empty">⚠ Błąd ładowania</div>`; return; }
    renderStop(body, head, await r.json());
  } catch(e) {
    body.innerHTML = `<div class="dep-empty">⚠ Błąd połączenia</div>`;
  }
}

function renderStop(body, head, group) {
  const deps = group.departures || [];
  const hasAnyRt = deps.some(d => d.realtime);
  const badge = head.querySelector(".bollard-rt-badge");
  if (deps.length > 0 && !hasAnyRt) {
    if (!badge) {
      const b = document.createElement("span");
      b.className = "bollard-rt-badge";
      b.textContent = "brak RT";
      head.appendChild(b);
    }
  } else if (badge) {
    badge.remove();
  }

  body.innerHTML = "";
  if (group.error) {
    body.innerHTML = `<div class="dep-empty">⚠ ${esc(group.error)}</div>`;
    return;
  }
  if (!deps.length) {
    body.innerHTML = `<div class="dep-empty">Brak odjazdów w najbliższym czasie.</div>`;
    return;
  }

  const hdr = document.createElement("div");
  hdr.className = "dep-header";
  hdr.innerHTML = `
    <div class="dep-grid">
      <span>Linia</span>
      <span>Rozk.</span>
      <span style="text-align:left;padding-left:8px">Kierunek</span>
      <span style="text-align:left;padding-left:6px">Gdzie jest</span>
      <span>Za</span>
      <span>Rzecz.</span>
      <span>Opóźn.</span>
      <span>Pojazd</span>
    </div>`;
  body.appendChild(hdr);

  const rowsWrap = document.createElement("div");
  rowsWrap.className = "dep-body";
  for (const dep of deps) {
    const row = buildDepRow(dep);
    if (row) rowsWrap.appendChild(row);
  }
  body.appendChild(rowsWrap);
}

function buildDepRow(dep) {
  const min = dep.minutes;
  if (min < 0) return null;

  let minText, minClass;
  if (dep.on_stop_point) { minText = "STOI"; minClass = "onboard"; }
  else if (min === 0)    { minText = "<1 min"; minClass = "arriving"; }
  else { minText = `${min} min`; minClass = dep.realtime ? "realtime" : "scheduled"; }

  const schedRaw = dep.scheduled_departure_str || "—";
  let schedStr = schedRaw;
  if (schedRaw !== "—") {
    const h = parseInt(schedRaw.split(":")[0], 10);
    if (h >= 24) {
      const hFixed = String(h - 24).padStart(2, "0");
      schedStr = `${hFixed}:${schedRaw.split(":")[1]}`;
    }
  }

  let realStr = "—", realClass = "";
  if (dep.realtime && min >= 0) {
    const realTime = new Date(Date.now() + min * 60000);
    realStr = String(realTime.getHours()).padStart(2,"0") + ":" +
              String(realTime.getMinutes()).padStart(2,"0");
    realClass = "realtime";
  }

  let delayText = "", delayClass = "nodata";
  if (dep.delay_seconds !== null && dep.delay_seconds !== undefined) {
    const dm = Math.round(dep.delay_seconds / 60);
    if (dm === 0)    { delayText = "na czas"; delayClass = "ontime"; }
    else if (dm > 0) { delayText = `+${dm}'`; delayClass = "late"; }
    else             { delayText = `${dm}'`;  delayClass = "early"; }
  }

  const vid    = dep.vehicle_id    || "";
  const vlabel = dep.vehicle_label || "";
  const isLive = dep.realtime && vid;
  const vehNumClass = isLive ? "live" : "nodata";
  const vehDisplay  = vid || "—";

  const vi = dep.vehicle_info || {};
  const lf = vi.low_floor_level !== undefined ? vi.low_floor_level : (vi.low_floor ? 1 : 0);
  const lfClass = lf === 1 ? "full" : lf === 2 ? "partial" : "none";
  const lfTitle = lf === 1 ? "Niska podłoga" : lf === 2 ? "Niska podłoga (częściowa)" : "Brak niskiej podłogi";
  const acClass = vi.air_conditioner ? "active" : "none";
  const acTitle = vi.air_conditioner ? "Klimatyzacja" : "Brak klimatyzacji";

  const vehCell = `
    <div class="dep-veh-cell">
      <span class="dep-veh-num ${vehNumClass}">${esc(vehDisplay)}</span>
      <span class="dep-veh-label">${esc(vlabel)}</span>
      <i class="fa-solid fa-wheelchair icon-wheelchair ${lfClass}" title="${lfTitle}"></i>
      <i class="fa-solid fa-snowflake icon-ac ${acClass}" title="${acTitle}"></i>
    </div>`;

  const currentStop  = dep.current_stop || "";
  const currentClass = dep.realtime && currentStop ? "live" : "";

  const row = document.createElement("div");
  row.className = "dep-row";
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

// ═══════════════════════════════════════════════════════════════════════════════
// REJESTR MODUŁÓW
// ═══════════════════════════════════════════════════════════════════════════════

let _photoIntervalMin = 5;

const MODULES = {
  clock: {
    mount(tile, cfg) {
      const body = addBody(tile, "clock-body");
      body.innerHTML = `
        <div class="clock-time">--:--:--</div>
        <div class="clock-date">-----------</div>`;
      tickClock(body);
      return { timers: [setInterval(() => tickClock(body), 1000)] };
    }
  },
  weather: {
    mount(tile, cfg) {
      addHeader(tile, "fa-cloud-sun", "Pogoda");
      const body = addBody(tile, "weather-tile-body");
      body.innerHTML = `
        <div class="weather-split">
          <div class="weather-left">
            <div class="weather-main">
              <img class="w-icon weather-icon-svg" src="/static/weather-icons/day.svg" alt="Pogoda">
              <span class="w-temp weather-temp">--°</span>
            </div>
            <div class="w-details weather-details"></div>
          </div>
          <div class="weather-right">
            <div class="weather-hourly"></div>
            <div class="weather-hourly-sep"></div>
            <div class="weather-forecast"></div>
          </div>
        </div>`;
      fetchWeather(body);
      return { timers: [setInterval(() => fetchWeather(body), 30 * 60 * 1000)] };
    }
  },
  calendar: {
    mount(tile, cfg) {
      addHeader(tile, "fa-calendar-days", "Kalendarz");
      const body = addBody(tile, "calendar-body");
      body.innerHTML = `<div class="cal-placeholder">Ładowanie…</div>`;
      fetchCalendar(body);
      return { timers: [setInterval(() => fetchCalendar(body), 3600000)] };
    }
  },
  waste: {
    mount(tile, cfg) {
      addHeader(tile, "fa-trash", "Wywóz odpadów");
      const body = addBody(tile, "waste-body");
      body.innerHTML = `<div class="cal-placeholder">Ładowanie…</div>`;
      fetchWaste(body);
      return { timers: [setInterval(() => fetchWaste(body), 3600000)] };
    }
  },
  photos: {
    mount(tile, cfg) {
      const body = addBody(tile, "photo-body");
      body.innerHTML = `<div class="cal-placeholder"><i class="fa-solid fa-image" style="font-size:2rem;opacity:0.3"></i></div>`;
      fetchPhoto(body);
      const mins = Math.max(2, Math.min(60, _photoIntervalMin || 5));
      return { timers: [setInterval(() => fetchPhoto(body), mins * 60 * 1000)] };
    }
  },
  sports: {
    mount(tile, cfg) {
      addHeader(tile, "fa-trophy", "Sport");
      const body = addBody(tile, "sports-body");
      body.innerHTML = `<div class="cal-placeholder">Ładowanie…</div>`;
      fetchSports(body);
      return { timers: [setInterval(() => fetchSports(body), 60000)] };
    }
  },
  transit: {
    mount(tile, cfg) {
      tile.classList.add("tile-transit");
      const head = document.createElement("div");
      head.className = "bollard-header";
      head.innerHTML = `<span class="bollard-label">${esc(cfg.label || "")}</span>
                        <span class="bollard-symbol">${esc(cfg.symbol || "")}</span>`;
      tile.appendChild(head);
      const body = addBody(tile, "dep-wrap");
      if (!cfg.symbol) {
        body.innerHTML = `<div class="dep-empty">Brak wybranego słupka.</div>`;
        return { timers: [] };
      }
      const load = () => fetchStop(body, head, cfg);
      load();
      return { timers: [setInterval(load, 60000)] };
    }
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// RENDERER TABLICY + PRZEŁĄCZANIE
// ═══════════════════════════════════════════════════════════════════════════════

let boardsData   = null;
let activeIndex  = 0;
let activeMounts = [];
let rendered     = false;

function teardown() {
  activeMounts.forEach(m => (m.timers || []).forEach(clearInterval));
  activeMounts = [];
}

async function loadBoards() {
  try {
    const r = await fetch("/api/boards");
    boardsData = await r.json();
  } catch(e) {
    boardsData = { active: 0, row_height: 90, boards: [] };
  }
}

function renderActiveBoard() {
  teardown();
  const root = document.getElementById("board-root");
  if (!root) return;
  root.innerHTML = "";

  const boards = (boardsData && boardsData.boards) || [];
  if (!boards.length) {
    root.classList.add("board-empty-mode");
    root.innerHTML = `<div class="board-empty">Brak skonfigurowanych tablic. Wejdź w <a href="/config-page">Konfigurację</a>.</div>`;
    updateBoardIndicator();
    rendered = true;
    return;
  }
  root.classList.remove("board-empty-mode");
  if (activeIndex < 0 || activeIndex >= boards.length) activeIndex = 0;

  const board = boards[activeIndex];
  root.style.setProperty("--row-h", ((boardsData.row_height || 90)) + "px");

  for (const w of (board.widgets || [])) {
    const mod = MODULES[w.type];
    if (!mod) continue;
    const tile = document.createElement("div");
    tile.className = "tile tile-" + w.type;
    tile.style.setProperty("--x", (w.x || 0) + 1);
    tile.style.setProperty("--y", (w.y || 0) + 1);
    tile.style.setProperty("--w", w.w || 3);
    tile.style.setProperty("--h", w.h || 3);
    root.appendChild(tile);
    try {
      activeMounts.push(mod.mount(tile, w.config || {}) || { timers: [] });
    } catch(e) {
      console.error("Błąd montowania modułu", w.type, e);
    }
  }
  updateBoardIndicator();
  rendered = true;
}

function updateBoardIndicator() {
  const el = document.getElementById("sb-board");
  if (!el) return;
  const boards = (boardsData && boardsData.boards) || [];
  if (!boards.length) { el.textContent = ""; return; }
  const name = boards[activeIndex] ? (boards[activeIndex].name || `Tablica ${activeIndex + 1}`) : "";
  el.textContent = `Tablica ${activeIndex + 1}/${boards.length} · ${name}`;
}

async function switchBoard(payload) {
  // Serwer rozgłosi zmianę przez SSE → wszystkie kioski (i ten) się odświeżą.
  try {
    await fetch("/api/active-board", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch(e) {}
}

// Keypad USB (widziany jako klawiatura) — 6 klawiszy A–F + pokrętło wysyłające klawisze:
//   A/B/C   → bezpośredni wybór Tablicy 1/2/3 (D/E/F wolne na przyszłość)
//   pokrętło: obrót w lewo = serie "1" → poprzednia; w prawo = serie "3" → następna
//             klik = "2" → powrót do Tablicy 1
//   zapas:   strzałki / PageUp-Down = poprzednia/następna
document.addEventListener("keydown", (e) => {
  if (e.repeat) return;  // pomiń auto-powtarzanie przy przytrzymaniu klawisza
  if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  const k = (e.key || "").toLowerCase();
  if      (k === "a") switchBoard({ index: 0 });
  else if (k === "b") switchBoard({ index: 1 });
  else if (k === "c") switchBoard({ index: 2 });
  else if (k === "1" || k === "arrowleft"  || k === "pageup")   switchBoard({ delta: -1 });
  else if (k === "3" || k === "arrowright" || k === "pagedown") switchBoard({ delta: 1 });
  else if (k === "2") switchBoard({ index: 0 });
});

// SSE — zmiany aktywnej tablicy oraz zapisy układu z edytora
function initBoardStream() {
  const es = new EventSource("/api/board-stream");
  es.onmessage = async (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch(_) { return; }
    if (ev.type === "reload") {
      await loadBoards();
      activeIndex = boardsData.active || 0;
      renderActiveBoard();
    } else if (ev.type === "active") {
      if (!boardsData) await loadBoards();
      if (rendered && ev.active === activeIndex) return;  // bez zbędnego przerysowania
      activeIndex = ev.active || 0;
      renderActiveBoard();
    }
  };
}

// ── Pasek statusu (globalny, niezależny od widgetów transit) ────────────────────
async function fetchStatus() {
  try {
    const r = await fetch("/api/status");
    if (!r.ok) return;
    const status = await r.json();
    const g = document.getElementById("sb-gtfs");
    const rt = document.getElementById("sb-rt");
    if (g)  g.textContent  = `GTFS: ${status.gtfs_valid_from || "—"} – ${status.gtfs_valid_until}`;
    if (rt) rt.textContent = status.rt_error ? `RT: ⚠ ${status.rt_error}` : `RT: ${status.rt_last_update}`;
  } catch(e) {}
}

// ── Start ───────────────────────────────────────────────────────────────────────
(async function init() {
  try {
    const cfg = await (await fetch("/api/board-config")).json();
    _photoIntervalMin = cfg.photo_interval_min || 5;
  } catch(e) {}
  await loadBoards();
  activeIndex = boardsData.active || 0;
  renderActiveBoard();
  initBoardStream();
  fetchStatus();
  setInterval(fetchStatus, 60000);
})();
