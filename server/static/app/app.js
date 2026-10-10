// Touchline web app: Studio mode (broadcast production) and Fan mode (personalized second screen).
// Preact + htm, no build step. Talks to the server over REST and one WebSocket per match.
import { h, render } from "preact";
import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
const api = (path, opts) => fetch(path, opts).then(r => (r.ok ? r.json() : r.json().then(e => Promise.reject(e))));
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const LANGS = { en: "English", es: "Español", fr: "Français", pt: "Português" };
const ICON = { goal: "⚽", card: "🟥", big_chance_missed: "😱", great_save: "🧤", momentum_swing: "📈", counter_attack: "⚡",
  key_pass: "🎯", pressing_spell: "🔥", chaos_spike: "🌪️", sustained_possession: "🔄", thunderbolt: "💥", milestone: "🏅",
  substitution: "🔁", half_time: "⏸", full_time: "🏁" };
const AGENT = { analyst: ["🧠", "Analyst"], commentator: ["🎙️", "Commentator"], fact_checker: ["✅", "Fact-checker"],
  producer: ["🎛️", "Producer"], recap_writer: ["📰", "Recap writer"], recap_fact_checker: ["✅", "Fact-checker"] };
const STUDIO_PROFILE = { persona: "analyst", language: "en", favorite_team: null, favorite_player: null };
const profileKey = p => `${p.persona}|${p.language}|${p.favorite_team || "-"}|${p.favorite_player || "-"}`;
const icon = m => (m.type === "card" && m.importance < 0.5 ? "🟨" : ICON[m.type] || "•");
const minuteOf = clock => parseInt(clock || "0", 10);
// Mirror of broadcast/relevance.py: which moments the crew covers for this viewer.
const TACTICAL = new Set(["pressing_spell", "chaos_spike", "sustained_possession", "key_pass"]);
const NEVER = new Set(["substitution", "half_time", "full_time"]);
function crewCovers(p, m) {
  if (NEVER.has(m.type)) return false;
  const mine = (p.favorite_team && m.team_id === p.favorite_team) || (p.favorite_player && (m.player_ids || []).includes(p.favorite_player));
  if (mine) return m.importance >= 0.3;
  if (p.persona === "analyst") return m.importance >= 0.4;
  return m.importance >= 0.5 && !TACTICAL.has(m.type);
}

// ------------------------------------------------------------------ match connection

const initial = () => ({ meta: null, durationMin: 95, status: { playing: false, speed: 10, clock: "00:00" }, clock: "00:00", period: 1,
  trail: [], snapshot: null, moments: [], cues: [], badges: [], banner: null, lower: null, holder: null, recap: null, lastAI: null });

function reduce(s, msg, key) {
  const now = performance.now();
  switch (msg.type) {
    case "hello":
      return { ...initial(), meta: msg.match, durationMin: msg.duration_minutes, status: s.status };
    case "status":
      return { ...s, status: msg };
    case "event": {
      const ev = msg.event;
      const trail = ev.display ? [...s.trail.slice(-9), { ...ev.display, team: ev.team_id }] : s.trail;
      return { ...s, trail, clock: ev.clock, period: ev.period };
    }
    case "cue": {
      const c = msg.cue, cues = [c, ...s.cues].slice(0, 150);
      if (c.type === "player_tag") return { ...s, cues, holder: c.payload };
      if (c.type === "stat_badge") return { ...s, cues, badges: [...s.badges.filter(b => b.until > now).slice(-2), { ...c.payload, id: c.cue_id, until: now + 3800 }] };
      if (c.type === "moment_banner") return { ...s, cues, banner: { ...c.payload, until: now + 3200 } };
      if (c.type === "lower_third" && c.payload.profile_key && c.payload.profile_key !== key) return s;
      return { ...s, cues };
    }
    case "moment": {
      const m = msg.moment;
      const moments = [m, ...s.moments];
      const lower = m.importance >= 0.45 ? { momentId: m.id, caption: m.title, line: null, pending: true, clock: m.clock } : s.lower;
      return { ...s, moments, lower };
    }
    case "commentary": {
      const c = msg.coverage;
      if (c.profile_key !== key) return s;
      const moments = s.moments.map(m => (m.id === msg.moment.id ? { ...m, ai: c } : m));
      return { ...s, moments, lower: { momentId: msg.moment.id, caption: c.caption, line: c.line, source: c.source, clock: msg.moment.clock },
        lastAI: { ...c, moment: msg.moment } };
    }
    case "state":
      return { ...s, snapshot: msg.state };
    case "reset":
      return { ...s, trail: [], badges: [], banner: null, lower: null, holder: null, cues: [], recap: null,
        snapshot: msg.state, moments: [...msg.state.moments].reverse().map(m => ({ ...m, past: true })), clock: msg.event.clock };
    case "recap":
      return msg.profile_key === key && msg.recap ? { ...s, recap: msg } : s;
    default:
      return s;
  }
}

function useMatch(matchId, profile) {
  const [s, setS] = useState(initial);
  const ws = useRef(null), keyRef = useRef(profileKey(profile));
  keyRef.current = profileKey(profile);
  const send = useCallback(o => ws.current && ws.current.readyState === 1 && ws.current.send(JSON.stringify(o)), []);
  useEffect(() => {
    if (!matchId) return;
    setS(initial());
    const q = new URLSearchParams({ speed: 10, persona: profile.persona, language: profile.language });
    if (profile.favorite_team) q.set("team", profile.favorite_team);
    if (profile.favorite_player) q.set("player", profile.favorite_player);
    const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/matches/${matchId}?${q}`);
    ws.current = sock;
    sock.onmessage = e => { const msg = JSON.parse(e.data); setS(prev => reduce(prev, msg, keyRef.current)); };
    return () => sock.close();
  }, [matchId]);
  // Profile changes are sent live, so the match keeps playing from where it is.
  useEffect(() => { send({ cmd: "profile", ...profile }); }, [keyRef.current]);
  return [s, send];
}

function useTick(ms) {
  const [, set] = useState(0);
  useEffect(() => { const t = setInterval(() => set(x => x + 1), ms); return () => clearInterval(t); }, [ms]);
  return performance.now();
}

// ------------------------------------------------------------------ small pieces

function Crest({ club, size = 28 }) {
  if (!club) return null;
  const a = club.colors[0];
  const lum = parseInt(a.slice(1, 3), 16) * 0.299 + parseInt(a.slice(3, 5), 16) * 0.587 + parseInt(a.slice(5, 7), 16) * 0.114;
  const ink = lum > 150 ? "#0b1220" : "#ffffff";
  return html`<svg width=${size} height=${size * 1.15} viewBox="0 0 40 46" aria-label=${club.name}>
    <path d="M20 2 L37 8 V22 C37 33 29 41 20 44 C11 41 3 33 3 22 V8 Z" fill=${a} stroke="rgba(255,255,255,.35)" stroke-width="1.5"/>
    <path d="M20 2 L37 8 V22 C37 33 29 41 20 44 Z" fill="#000" opacity=".14"/>
    <text x="20" y="28" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-weight="800" font-size="13" fill=${ink}>${club.short}</text>
  </svg>`;
}

const teamOf = (meta, id) => (meta ? [meta.home, meta.away].find(t => t.id === id) : null);
const playerName = (meta, id) => {
  if (!meta || !id) return null;
  for (const t of [meta.home, meta.away]) for (const p of [...t.starters, ...t.bench]) if (p.id === id) return p.name;
  return null;
};

function SourceChip({ source }) {
  return source === "ai" ? html`<span class="chip ai">AI · fact-checked</span>`
    : source === "fallback" ? html`<span class="chip fb">Safe fallback</span>` : null;
}

// ------------------------------------------------------------------ broadcast frame

function drawPitch(ctx, w, hgt, trail, colors) {
  const k = w / 105;
  for (let i = 0; i < 12; i++) { ctx.fillStyle = i % 2 ? "#0f4a2b" : "#0d4427"; ctx.fillRect((i * w) / 12, 0, w / 12 + 1, hgt); }
  const g = ctx.createRadialGradient(w / 2, hgt / 2, hgt * 0.2, w / 2, hgt / 2, w * 0.75);
  g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, "rgba(0,0,0,.45)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, hgt);
  ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = Math.max(1.5, k * 0.18);
  const m = k * 1.2;
  ctx.strokeRect(m, m, w - 2 * m, hgt - 2 * m);
  ctx.beginPath(); ctx.moveTo(w / 2, m); ctx.lineTo(w / 2, hgt - m); ctx.stroke();
  ctx.beginPath(); ctx.arc(w / 2, hgt / 2, 9.15 * k, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(w / 2, hgt / 2, k * 0.4, 0, Math.PI * 2); ctx.fillStyle = "rgba(255,255,255,.7)"; ctx.fill();
  for (const left of [true, false]) {
    const x0 = left ? m : w - m;
    const dir = left ? 1 : -1;
    ctx.strokeRect(Math.min(x0, x0 + dir * 16.5 * k), hgt / 2 - 20.16 * k, 16.5 * k, 40.32 * k);
    ctx.strokeRect(Math.min(x0, x0 + dir * 5.5 * k), hgt / 2 - 9.16 * k, 5.5 * k, 18.32 * k);
    ctx.beginPath(); ctx.arc(x0 + dir * 11 * k, hgt / 2, k * 0.35, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(x0 + dir * 11 * k, hgt / 2, 9.15 * k, left ? -0.93 : Math.PI - 0.93, left ? 0.93 : Math.PI + 0.93); ctx.stroke();
  }
  trail.forEach((t, i) => {
    if (!t.end) return;
    const a = (i + 1) / trail.length;
    ctx.strokeStyle = colors[t.team] || "#fff"; ctx.globalAlpha = 0.25 + a * 0.7; ctx.lineWidth = Math.max(2, k * 0.35);
    ctx.beginPath(); ctx.moveTo(t.start[0] * k, t.start[1] * k); ctx.lineTo(t.end[0] * k, t.end[1] * k); ctx.stroke();
  });
  ctx.globalAlpha = 1;
  const last = trail[trail.length - 1];
  if (last) {
    const p = last.end || last.start;
    ctx.fillStyle = colors[last.team] || "#fff";
    ctx.beginPath(); ctx.arc(last.start[0] * k, last.start[1] * k, k * 0.9, 0, Math.PI * 2); ctx.fill();
    ctx.shadowColor = "rgba(0,0,0,.6)"; ctx.shadowBlur = 8;
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(p[0] * k, p[1] * k, k * 0.75, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
  }
}

function Broadcast({ s, compact = false }) {
  const canvas = useRef(null), box = useRef(null);
  const now = useTick(400);
  const meta = s.meta;
  const colors = useMemo(() => (meta ? { [meta.home.id]: meta.home.colors[0], [meta.away.id]: meta.away.colors[0] } : {}), [meta]);
  useEffect(() => {
    const c = canvas.current, b = box.current;
    if (!c || !b) return;
    const dpr = window.devicePixelRatio || 1, w = b.clientWidth, hgt = b.clientHeight;
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(hgt * dpr); }
    const ctx = c.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawPitch(ctx, w, hgt, s.trail, colors);
  });
  const score = s.snapshot ? s.snapshot.match.score : {};
  const last = s.trail[s.trail.length - 1];
  const holderPos = last ? (last.end || last.start) : null;
  const badges = s.badges.filter(b => b.until > now);
  const banner = s.banner && s.banner.until > now ? s.banner : null;
  return html`<div class=${"frame" + (compact ? " compact" : "")} ref=${box}>
    <canvas ref=${canvas}></canvas>
    ${meta && html`<div class="scorebug">
      <div class="team"><i style=${{ background: meta.home.colors[0] }}></i>${meta.home.short}</div>
      <div class="score">${score[meta.home.id] ?? 0}–${score[meta.away.id] ?? 0}</div>
      <div class="team away">${meta.away.short}<i style=${{ background: meta.away.colors[0] }}></i></div>
      <div class="clock">${s.clock}<small>${s.period === 1 ? "1H" : "2H"}</small></div>
    </div>`}
    ${s.holder && holderPos && html`<div class="tag" style=${{ left: `${(holderPos[0] / 105) * 100}%`, top: `${(holderPos[1] / 68) * 100}%` }}>
      <b style=${{ background: s.holder.color }}>${s.holder.number}</b>${s.holder.name}</div>`}
    <div class="badges">${badges.map(b => html`<div class="badge" key=${b.id}>
      <div class="v">${b.value}<small>${b.unit}</small></div>
      <div class="l"><b>${b.label}</b>${b.player ? b.player.name : ""}</div></div>`)}</div>
    ${banner && html`<div class="banner" style=${{ "--c": banner.color }}><div>${banner.label}<small>${banner.title}</small></div></div>`}
    ${s.lower && html`<div class="lower" key=${s.lower.momentId + (s.lower.pending ? "p" : "")}>
      <div class=${"cap" + (s.lower.pending ? " pending" : "")}>${s.lower.caption}</div>
      <div class="line"><p>${s.lower.line || html`<span class="muted">The broadcast crew is on it…</span>`}</p>
        <${SourceChip} source=${s.lower.source} /></div></div>`}
  </div>`;
}

// ------------------------------------------------------------------ transport

function Transport({ s, send }) {
  const ref = useRef(null);
  const max = s.durationMin || 95;
  const pos = Math.min(minuteOf(s.clock), max);
  const marks = s.moments.filter(m => ["goal", "card", "half_time"].includes(m.type) && (m.type !== "card" || m.importance >= 0.5));
  const seek = e => {
    const r = ref.current.getBoundingClientRect();
    send({ cmd: "seek", minute: Math.round(((e.clientX - r.left) / r.width) * max) });
  };
  return html`<div class="transport">
    <button class="play" onClick=${() => send({ cmd: s.status.playing ? "pause" : "play" })} aria-label="play/pause">${s.status.playing ? "❚❚" : "▶"}</button>
    <div class="speeds">${[1, 10, 30, 60, 120].map(x => html`<button class=${s.status.speed === x ? "on" : ""} onClick=${() => send({ cmd: "speed", value: x })}>${x}×</button>`)}</div>
    <div class="scrub" ref=${ref} onClick=${seek} title="Click to jump to a minute">
      <div class="track"></div><div class="fill" style=${{ width: `${(pos / max) * 100}%` }}></div>
      <div class="ht" style=${{ left: `${(45 / max) * 100}%` }}></div>
      ${marks.map(m => html`<span class=${"mk " + m.type} style=${{ left: `${(minuteOf(m.clock) / max) * 100}%` }}>${icon(m)}</span>`)}
      <div class="head" style=${{ left: `${(pos / max) * 100}%` }}></div>
      ${[0, 15, 30, 45, 60, 75, 90].map(x => html`<span class="lbl" style=${{ left: `${(x / max) * 100}%` }}>${x}'</span>`)}
    </div>
    <button class="btn sm ghost" onClick=${() => send({ cmd: "restart" })}>↺ Restart</button>
  </div>`;
}

// ------------------------------------------------------------------ analyst desk

function StatBars({ snap, keys }) {
  if (!snap) return html`<div class="empty">Waiting for kick-off…</div>`;
  const [h, a] = [snap.match.home.id, snap.match.away.id];
  return keys.map(([k, label]) => {
    const hv = snap.teams[h][k], av = snap.teams[a][k];
    const tot = (+hv || 0) + (+av || 0) || 1;
    return html`<div class="stat"><span class="lbl">${label}</span><span class="n">${hv ?? "–"}</span>
      <div class="bar"><i style=${{ width: `${((+hv || 0) / tot) * 100}%` }}></i></div><span class="n r">${av ?? "–"}</span></div>`;
  });
}

function Momentum({ snap, meta, max }) {
  const tl = snap ? snap.momentum.timeline : [];
  const W = 340, H = 90, mid = H / 2;
  const x = i => (i / Math.max(max, 1)) * W, y = v => mid - v * (mid - 6);
  const pts = tl.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = tl.length ? `M0,${mid} L${pts.split(" ").join(" L")} L${x(tl.length - 1)},${mid} Z` : "";
  return html`<svg viewBox="0 0 ${W} ${H}" width="100%" height=${H} preserveAspectRatio="none" role="img" aria-label="momentum">
    <defs><clipPath id="top"><rect x="0" y="0" width=${W} height=${mid} /></clipPath><clipPath id="bot"><rect x="0" y=${mid} width=${W} height=${mid} /></clipPath></defs>
    <line x1="0" x2=${W} y1=${mid} y2=${mid} stroke="#283654" />
    <line x1=${x(45)} x2=${x(45)} y1="0" y2=${H} stroke="#1d2842" stroke-dasharray="3 3" />
    ${area && html`<path d=${area} fill=${meta ? meta.home.colors[0] : "#5BB8F0"} opacity=".55" clip-path="url(#top)" />
    <path d=${area} fill=${meta ? meta.away.colors[0] : "#C8102E"} opacity=".55" clip-path="url(#bot)" />`}
  </svg>
  <div class="legend">${meta && html`<span><i style=${{ background: meta.home.colors[0] }}></i>${meta.home.name} on top</span>
    <span><i style=${{ background: meta.away.colors[0] }}></i>${meta.away.name} on top</span>`}</div>`;
}

function XgRace({ snap, meta, max }) {
  if (!snap || !meta) return null;
  const tl = snap.xg_timeline, W = 340, H = 100;
  const top = Math.max(1, ...tl.map(p => Math.max(p[meta.home.id], p[meta.away.id]))) * 1.1;
  const line = id => tl.map((p, i) => `${((i / Math.max(max, 1)) * W).toFixed(1)},${(H - (p[id] / top) * (H - 8)).toFixed(1)}`).join(" ");
  const goals = snap.moments.filter(m => m.type === "goal");
  return html`<svg viewBox="0 0 ${W} ${H}" width="100%" height=${H} preserveAspectRatio="none" role="img" aria-label="xG race">
    <line x1="0" x2=${W} y1=${H - 0.5} y2=${H - 0.5} stroke="#283654" />
    <polyline points=${line(meta.home.id)} fill="none" stroke=${meta.home.colors[0]} stroke-width="2.5" vector-effect="non-scaling-stroke" />
    <polyline points=${line(meta.away.id)} fill="none" stroke=${meta.away.colors[0]} stroke-width="2.5" vector-effect="non-scaling-stroke" />
    ${goals.map(g => html`<text x=${(minuteOf(g.clock) / max) * W} y="12" font-size="11" text-anchor="middle">⚽</text>`)}
  </svg>
  <div class="legend"><span><i style=${{ background: meta.home.colors[0] }}></i>${meta.home.short} ${snap.teams[meta.home.id].xg} xG</span>
    <span><i style=${{ background: meta.away.colors[0] }}></i>${meta.away.short} ${snap.teams[meta.away.id].xg} xG</span></div>`;
}

function Chaos({ snap }) {
  const v = snap ? snap.control_chaos.value : 0, label = snap ? snap.control_chaos.label : "–";
  const ang = Math.PI * (1 - v / 100), cx = 60, cy = 56, r = 46;
  const color = v < 35 ? "#5BB8F0" : v < 60 ? "#ffcf5c" : "#ff5470";
  return html`<div class="gauge">
    <svg width="120" height="66" viewBox="0 0 120 66" aria-label="control vs chaos">
      <path d="M14 56 A46 46 0 0 1 106 56" fill="none" stroke="#1d2842" stroke-width="10" stroke-linecap="round" />
      <path d=${`M14 56 A46 46 0 0 1 ${cx + r * Math.cos(ang)} ${cy - r * Math.sin(ang)}`} fill="none" stroke=${color} stroke-width="10" stroke-linecap="round" />
    </svg>
    <div><div class="big" style=${{ color }}>${v}</div><div class="lab">${label}</div>
      <div class="muted" style="font-size:12px">0 calm build-up · 100 end-to-end chaos</div></div></div>`;
}

function Moments({ s, send, onCompare, limit = 40 }) {
  const list = s.moments.filter(m => m.importance >= 0.3).slice(0, limit);
  if (!list.length) return html`<div class="empty">Key moments appear here as the stats engine detects them.</div>`;
  return html`<div class="moments">${list.map(m => html`<button class="mo ${m.importance >= 0.6 ? "big" : ""}" key=${m.id}
      onClick=${() => send({ cmd: "seek", minute: Math.max(0, minuteOf(m.clock) - 1) })} title="Replay from here">
      <span class="ic">${icon(m)}</span>
      <span><div class="t">${m.clock} · ${m.type.replace(/_/g, " ")}</div><div class="title">${m.title}</div>
        ${m.ai && html`<div class="ai">🎙️ ${m.ai.line}</div>`}
        ${onCompare && m.ai && html`<div style="margin-top:6px"><span class="btn sm" onClick=${e => { e.stopPropagation(); onCompare(m); }}>Compare viewers ↔</span></div>`}</span>
    </button>`)}</div>`;
}

function CueLog({ s, matchId }) {
  return html`<div class="card"><h3>Graphics out <span class="right"><a href=${`/api/matches/${matchId}/cues`} target="_blank">all cues as JSON ↗</a></span></h3>
    <div class="muted" style="font-size:12px;margin:-4px 0 10px">Timed overlay cues for a broadcast graphics renderer. This screen draws the same feed.</div>
    <div class="cues">${s.cues.filter(c => c.type !== "player_tag").slice(0, 40).map(c => html`<div class="cue" key=${c.cue_id}>
      <span class="muted">${c.clock}</span><span class=${"k " + c.type}>${c.type}</span>
      <span class="p">${cueText(c)}</span></div>`)}
      ${!s.cues.length && html`<div class="empty">Cues stream here during play.</div>`}</div></div>`;
}
const cueText = c => {
  const p = c.payload;
  if (c.type === "stat_badge") return `${p.label} ${p.value}${p.unit}${p.player ? " · " + p.player.name : ""}`;
  if (c.type === "moment_banner") return `${p.label} · ${p.title}`;
  if (c.type === "lower_third") return `"${p.caption}" (${p.source}, ${p.language})`;
  if (c.type === "score_bug") return `${p.home.short} ${p.home.goals}–${p.away.goals} ${p.away.short}`;
  return JSON.stringify(p);
};

function Trace({ ai }) {
  if (!ai) return html`<div class="empty">When the crew covers a moment, each agent's work shows up here: who ran, how long it took, and what the fact-checker said.</div>`;
  const total = ai.trace.reduce((a, s) => a + s.ms, 0);
  return html`<div>
    <div class="muted" style="font-size:12px;margin-bottom:8px">${ai.moment.clock} · ${ai.moment.title} · ${(total / 1000).toFixed(1)}s total</div>
    ${ai.insight && ai.insight.why_it_matters && html`<p class="insight">“${ai.insight.why_it_matters}”</p>`}
    <div class="trace">${ai.trace.map(st => {
      const [ic, name] = AGENT[st.agent] || ["•", st.agent];
      const bad = /^(rejected|failed|gave up)/.test(st.note), ok = st.note === "passed";
      return html`<div class=${"step" + (bad ? " rej" : ok ? " ok" : "")}><span>${ic}</span><b>${name}</b>
        <span class="ms">${st.ms ? (st.ms / 1000).toFixed(1) + "s" : ""}</span>${st.note && html`<span class="note">${st.note}</span>`}</div>`;
    })}</div></div>`;
}

const DESK_STATS = [["possession_pct", "Possession %"], ["shots", "Shots"], ["on_target", "On target"], ["xg", "xG"],
  ["box_entries", "Box entries"], ["field_tilt_pct", "Field tilt %"], ["ppda", "PPDA · lower = more pressing"]];

function Studio({ s, send, matchId, onCompare }) {
  const why = s.lastAI && s.lastAI.insight && s.lastAI.insight.why_it_matters;
  return html`<main class="studio">
    <div class="col">
      <${Broadcast} s=${s} />
      <${Transport} s=${s} send=${send} />
      <div class="row2">
        <${CueLog} s=${s} matchId=${matchId} />
        <div class="card"><h3>Behind the scenes <span class="right">Microsoft Agent Framework · Foundry</span></h3><${Trace} ai=${s.lastAI} /></div>
      </div>
    </div>
    <aside class="col">
      <div class="card"><h3>🧠 Why it matters</h3>${why ? html`<p class="why">${why}</p>` : html`<p class="why empty">The Analyst agent explains each key moment here.</p>`}</div>
      <div class="card"><h3>Match stats</h3><${StatBars} snap=${s.snapshot} keys=${DESK_STATS} /></div>
      <div class="card"><h3>Momentum <span class="right">threat, last ~10 min</span></h3><${Momentum} snap=${s.snapshot} meta=${s.meta} max=${s.durationMin} /></div>
      <div class="card"><h3>xG race</h3><${XgRace} snap=${s.snapshot} meta=${s.meta} max=${s.durationMin} /></div>
      <div class="card"><h3>Control vs chaos <span class="right">last 5 min</span></h3><${Chaos} snap=${s.snapshot} /></div>
      <div class="card"><h3>Key moments <span class="right">click to replay</span></h3><${Moments} s=${s} send=${send} onCompare=${onCompare} /></div>
    </aside>
  </main>`;
}

// ------------------------------------------------------------------ fan mode

function FanSetup({ meta, profile, setProfile, onDone }) {
  if (!meta) return html`<div class="setup"><div class="empty">Loading match…</div></div>`;
  const team = teamOf(meta, profile.favorite_team);
  return html`<div class="setup">
    <h1>Watch it your way.</h1>
    <p class="lead">${meta.home.name} v ${meta.away.name}. Touchline's AI crew tailors every key moment to you, and every number is fact-checked.</p>
    <div class="q">Your club</div>
    <div class="choices">
      ${[meta.home, meta.away].map(t => html`<button class=${"choice" + (profile.favorite_team === t.id ? " on" : "")}
        onClick=${() => setProfile({ ...profile, favorite_team: t.id, favorite_player: null })}><${Crest} club=${t} size=${26} />${t.name}</button>`)}
      <button class=${"choice" + (!profile.favorite_team ? " on" : "")} onClick=${() => setProfile({ ...profile, favorite_team: null, favorite_player: null })}>Neutral</button>
    </div>
    ${team && html`<div class="q">Your player <span class="muted" style="text-transform:none;letter-spacing:0">(optional)</span></div>
      <select class="select" value=${profile.favorite_player || ""} onChange=${e => setProfile({ ...profile, favorite_player: e.target.value || null })}>
        <option value="">Nobody in particular</option>
        ${[...team.starters, ...team.bench].map(p => html`<option value=${p.id}>${p.number} · ${p.name}${p.position ? " · " + p.position : ""}</option>`)}
      </select>`}
    <div class="q">How do you like your football?</div>
    <div class="choices">
      <button class=${"choice" + (profile.persona === "casual" ? " on" : "")} onClick=${() => setProfile({ ...profile, persona: "casual" })}>
        <span>Just the story<small>Plain words, drama and people</small></span></button>
      <button class=${"choice" + (profile.persona === "analyst" ? " on" : "")} onClick=${() => setProfile({ ...profile, persona: "analyst" })}>
        <span>Give me the tactics<small>xG, pressing, patterns</small></span></button>
    </div>
    <div class="q">Language</div>
    <div class="choices">${Object.entries(LANGS).map(([k, v]) => html`<button class=${"choice" + (profile.language === k ? " on" : "")} onClick=${() => setProfile({ ...profile, language: k })}>${v}</button>`)}</div>
    <div style="margin-top:30px"><button class="btn primary" style="padding:12px 26px;font-size:16px" onClick=${onDone}>Kick off ▶</button></div>
  </div>`;
}

function FanCard({ m, onCompare, yourTeam, send }) {
  const [open, setOpen] = useState(false);
  const ai = m.ai;
  const side = yourTeam && m.team_id === yourTeam;
  return html`<div class=${"fc" + (m.importance >= 0.6 ? " big" : "")}>
    <div class="h"><span>${icon(m)}</span><span>${m.clock}</span>${side && html`<span class="chip ai">your team</span>`}<span class="spacer"></span>${ai && html`<${SourceChip} source=${ai.source} />`}</div>
    <div class="cap">${ai ? ai.caption : m.title}</div>
    <p>${ai ? ai.line : m.past
      ? html`<span class="muted">You skipped past this one. </span><a href="#" onClick=${e => { e.preventDefault(); send({ cmd: "seek", minute: Math.max(0, minuteOf(m.clock) - 1) }); }}>Replay it ↺</a>`
      : html`<span class="muted">The crew is writing this for you…</span>`}</p>
    ${ai && html`<div class="acts">
      ${ai.insight && ai.insight.why_it_matters && html`<button class="btn sm" onClick=${() => setOpen(!open)}>${open ? "Hide" : "Why?"}</button>`}
      <button class="btn sm" onClick=${() => onCompare(m)}>See it through other fans' eyes ↔</button></div>
      ${open && html`<div class="whytext">🧠 ${ai.insight.why_it_matters}</div>`}`}
  </div>`;
}

function RecapCard({ recap }) {
  const r = recap.recap;
  return html`<div class="fc recap"><div class="h"><span>📰</span><span>${recap.when === "full-time" ? "Full-time" : "Half-time"} recap</span>
    <span class="spacer"></span><span class="chip ai">AI · fact-checked</span></div>
    <h2>${r.headline}</h2>${r.summary.map(p => html`<p>${p}</p>`)}
    <div class="potm">⭐ <span><b>${r.player_of_the_match}</b><br/><span class="muted">${r.player_reason}</span></span></div></div>`;
}

function YourPlayer({ s, id }) {
  const p = s.snapshot && s.snapshot.top_players.find(x => x.id === id);
  const name = playerName(s.meta, id);
  return html`<div class="card"><h3>⭐ Your player <span class="right">${name}</span></h3>
    ${p ? html`<div class="muted" style="font-size:13px">${[p.goals && `${p.goals} goals`, p.assists && `${p.assists} assists`, `${p.passes} passes (${p.pass_accuracy_pct}%)`,
      p.key_passes && `${p.key_passes} key passes`, p.xg && `${p.xg} xG`].filter(Boolean).join(" · ")}</div>`
      : html`<div class="muted" style="font-size:13px">Moments involving ${name} are always covered for you.</div>`}</div>`;
}

function Fan({ s, send, profile, onEdit, onCompare }) {
  const meta = s.meta, team = teamOf(meta, profile.favorite_team);
  const feed = s.moments.filter(m => m.ai || crewCovers(profile, m)).slice(0, 30);
  return html`<main class="fan">
    <div class="col">
      <${Broadcast} s=${s} compact=${true} />
      <${Transport} s=${s} send=${send} />
      <div class="card"><div class="me">${team ? html`<${Crest} club=${team} size=${24} />` : "👀"}
        <span><b>${team ? team.name + " fan" : "Neutral"}</b> · ${profile.persona === "analyst" ? "tactics" : "the story"} · ${LANGS[profile.language]}${profile.favorite_player ? " · ⭐ " + playerName(meta, profile.favorite_player) : ""}</span>
        <span class="spacer"></span><button class="btn sm" onClick=${onEdit}>Change</button></div></div>
      ${profile.favorite_player && html`<${YourPlayer} s=${s} id=${profile.favorite_player} />`}
      <div class="card"><h3>Match stats</h3><${StatBars} snap=${s.snapshot} keys=${DESK_STATS.slice(0, 4)} /></div>
    </div>
    <div class="col"><div class="feed">
      ${s.recap && html`<${RecapCard} recap=${s.recap} />`}
      ${feed.map(m => html`<${FanCard} key=${m.id} m=${m} onCompare=${onCompare} yourTeam=${profile.favorite_team} send=${send} />`)}
      ${!feed.length && html`<div class="card empty">Your personalized commentary will appear here as the match unfolds.</div>`}
    </div></div>
  </main>`;
}

// ------------------------------------------------------------------ modals

function CompareModal({ matchId, moment, meta, onClose }) {
  const [res, setRes] = useState(null), [err, setErr] = useState(null), t0 = useRef(performance.now()), [ms, setMs] = useState(0);
  useEffect(() => {
    post(`/api/matches/${matchId}/compare`, { moment_id: moment.id })
      .then(r => { setRes(r); setMs(performance.now() - t0.current); })
      .catch(e => setErr(e.detail || "Something went wrong"));
  }, [moment.id]);
  const who = p => {
    const t = teamOf(meta, p.favorite_team);
    return html`${t ? html`<${Crest} club=${t} size=${20} />` : "👀"}<span><b>${t ? t.name + " fan" : "Neutral"}</b> · ${p.persona === "analyst" ? "Analyst" : "Casual"} · ${LANGS[p.language]}</span>`;
  };
  return html`<div class="modal-bg" onClick=${onClose}><div class="modal" onClick=${e => e.stopPropagation()}>
    <button class="btn sm x" onClick=${onClose}>Close ✕</button>
    <h2>One moment. Every fan.</h2>
    <div class="muted">${icon(moment)} ${moment.clock} · ${moment.title}${res ? ` · tailored for ${res.profiles.length} viewers in ${(ms / 1000).toFixed(1)}s (Producer agents run in parallel)` : ""}</div>
    ${err && html`<div class="empty">${err}</div>`}
    <div class="cmp">${(res ? res.profiles : [0, 1, 2]).map((p, i) => html`<div class="col1">
      ${res ? html`<div class="who">${who(p)}</div><div class="cap">${res.coverage[i].caption}</div><div>${res.coverage[i].line}</div>
        <div style="margin-top:10px"><${SourceChip} source=${res.coverage[i].source} /></div>`
        : html`<div class="skeleton" style="width:50%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div>`}
    </div>`)}</div>
  </div></div>`;
}

function GenerateModal({ clubs, scenarios, onCreated, onClose }) {
  const [home, setHome] = useState(clubs[0] ? clubs[0].id : "kestrel"), [away, setAway] = useState(clubs[1] ? clubs[1].id : "marlowe");
  const [scenario, setScenario] = useState("standard"), [busy, setBusy] = useState(false), [err, setErr] = useState(null);
  const go = () => {
    setBusy(true);
    post("/api/matches", { home, away, scenario }).then(r => onCreated(r.match_id)).catch(e => { setErr(e.detail || "Failed"); setBusy(false); });
  };
  return html`<div class="modal-bg" onClick=${onClose}><div class="modal" style="max-width:820px" onClick=${e => e.stopPropagation()}>
    <button class="btn sm x" onClick=${onClose}>Close ✕</button>
    <h2>Generate a synthetic match</h2>
    <div class="muted">The simulator plays a full 90 minutes in under a second: realistic events calibrated against Premier League averages, with fictional clubs.</div>
    <div class="form">
      <label>Home<select class="select" value=${home} onChange=${e => setHome(e.target.value)}>${clubs.map(c => html`<option value=${c.id}>${c.name} · ${c.formation}</option>`)}</select></label>
      <label>Away<select class="select" value=${away} onChange=${e => setAway(e.target.value)}>${clubs.map(c => html`<option value=${c.id}>${c.name} · ${c.formation}</option>`)}</select></label>
    </div>
    <div class="scen">${scenarios.map(sc => html`<button class=${scenario === sc.id ? "on" : ""} onClick=${() => setScenario(sc.id)}>
      <b>${sc.id.replace(/_/g, " ")}</b><small>${sc.description}</small></button>`)}</div>
    ${err && html`<div class="empty" style="color:var(--danger)">${err}</div>`}
    <div style="margin-top:18px"><button class="btn primary" disabled=${busy || home === away} onClick=${go}>${busy ? "Simulating…" : home === away ? "Pick two different clubs" : "Simulate & watch ▶"}</button></div>
  </div></div>`;
}

// ------------------------------------------------------------------ app

function App() {
  const [mode, setMode] = useState(() => new URLSearchParams(location.search).get("mode") || "studio");
  const [matches, setMatches] = useState([]), [matchId, setMatchId] = useState(null);
  const [clubs, setClubs] = useState([]), [scenarios, setScenarios] = useState([]), [ai, setAi] = useState(null);
  const [fanProfile, setFanProfile] = useState({ persona: "casual", language: "en", favorite_team: null, favorite_player: null });
  const [fanReady, setFanReady] = useState(false), [compare, setCompare] = useState(null), [gen, setGen] = useState(false);
  const profile = mode === "fan" && fanReady ? fanProfile : STUDIO_PROFILE;
  const [s, send] = useMatch(matchId, profile);

  const loadMatches = sel => api("/api/matches").then(list => { setMatches(list); setMatchId(sel || (list[0] && list[0].match_id)); });
  useEffect(() => {
    loadMatches();
    api("/api/clubs").then(setClubs); api("/api/scenarios").then(setScenarios); api("/api/health").then(hh => setAi(hh.ai));
  }, []);
  useEffect(() => { // pause while the fan is choosing, so they don't miss anything
    if (mode === "fan" && !fanReady) send({ cmd: "pause" });
  }, [mode, fanReady, s.meta]);
  useEffect(() => { setFanReady(false); setFanProfile(p => ({ ...p, favorite_team: null, favorite_player: null })); }, [matchId]);

  return html`<div>
    <header class="top">
      <div class="brand"><b>TOUCH<i>LINE</i></b><span>Match intelligence</span></div>
      <div class="seg"><button class=${mode === "studio" ? "on" : ""} onClick=${() => setMode("studio")}>Studio</button>
        <button class=${mode === "fan" ? "on" : ""} onClick=${() => setMode("fan")}>Fan</button></div>
      <select class="select" value=${matchId || ""} onChange=${e => setMatchId(e.target.value)}>
        ${matches.map(m => html`<option value=${m.match_id}>${m.home} v ${m.away} · ${m.scenario.replace(/_/g, " ")}</option>`)}</select>
      <button class="btn" onClick=${() => setGen(true)}>＋ Generate match</button>
      <span class="spacer"></span>
      <span class=${"pill" + (ai ? " live" : "")}><span class="dot"></span>${ai == null ? "…" : ai ? "AI crew live · Microsoft Foundry" : "AI off"}</span>
    </header>
    ${mode === "studio" && html`<${Studio} s=${s} send=${send} matchId=${matchId} onCompare=${ai ? setCompare : null} />`}
    ${mode === "fan" && !fanReady && html`<${FanSetup} meta=${s.meta} profile=${fanProfile} setProfile=${setFanProfile}
      onDone=${() => { setFanReady(true); setTimeout(() => send({ cmd: "play" }), 150); }} />`}
    ${mode === "fan" && fanReady && html`<${Fan} s=${s} send=${send} profile=${fanProfile} onEdit=${() => setFanReady(false)} onCompare=${ai ? setCompare : () => {}} />`}
    ${compare && html`<${CompareModal} matchId=${matchId} moment=${compare} meta=${s.meta} onClose=${() => setCompare(null)} />`}
    ${gen && html`<${GenerateModal} clubs=${clubs} scenarios=${scenarios} onClose=${() => setGen(false)}
      onCreated=${id => { setGen(false); loadMatches(id); }} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("root"));
