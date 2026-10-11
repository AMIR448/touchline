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
const AGENT = { analyst: ["🧠", "Analyst", "Explains why the moment matters, from the numbers"],
  commentator: ["🎙️", "Commentator", "Writes the on-screen caption and the line"],
  fact_checker: ["✅", "Fact-checker", "Code, not AI: every number and name must be in the facts"],
  producer: ["🎛️", "Producer", "Tailors the line to this viewer"],
  recap_writer: ["📰", "Recap writer", "Tells the story of the match"],
  recap_fact_checker: ["✅", "Fact-checker", "Checks the recap"] };
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

// ------------------------------------------------------------------ colours

const lum = hex => parseInt(hex.slice(1, 3), 16) * 0.299 + parseInt(hex.slice(3, 5), 16) * 0.587 + parseInt(hex.slice(5, 7), 16) * 0.114;
const inkOn = hex => (lum(hex) > 150 ? "#0b1220" : "#ffffff");
const dist = (a, b) => Math.hypot(...[1, 3, 5].map(i => parseInt(a.slice(i, i + 2), 16) - parseInt(b.slice(i, i + 2), 16)));

// One readable colour per team: the kit colour, unless it vanishes on the dark UI or clashes with the opponent.
function teamColors(meta) {
  if (!meta) return {};
  const pick = c => (lum(c.colors[0]) < 60 ? c.colors[1] : c.colors[0]);
  const home = pick(meta.home);
  let away = pick(meta.away);
  if (dist(home, away) < 110) away = meta.away.colors.find(c => c !== away && dist(home, c) >= 110) || "#f2f2f2";
  return { [meta.home.id]: home, [meta.away.id]: away };
}

// ------------------------------------------------------------------ match connection

const startLineups = meta => (meta ? { [meta.home.id]: meta.home.starters.map(p => p.id), [meta.away.id]: meta.away.starters.map(p => p.id) } : {});
const initial = () => ({ meta: null, durationMin: 95, status: { playing: false, speed: 10, clock: "00:00" }, clock: "00:00", period: 1,
  trail: [], last: null, lineups: {}, snapshot: null, moments: [], cues: [], badges: [], banner: null, lower: null, recap: null, lastAI: null });

function applyLineup(lineups, ev) {
  const team = lineups[ev.team_id];
  if (!team) return lineups;
  if (ev.type === "substitution") {
    const d = ev.details || {};
    return { ...lineups, [ev.team_id]: team.map(p => (p === d.player_off_id ? d.player_on_id : p)) };
  }
  if (ev.type === "card" && ["red", "second_yellow"].includes((ev.details || {}).card))
    return { ...lineups, [ev.team_id]: team.map(p => (p === ev.player_id ? null : p)) };
  return lineups;
}

function reduce(s, msg, key) {
  const now = performance.now();
  switch (msg.type) {
    case "hello":
      return { ...initial(), meta: msg.match, durationMin: msg.duration_minutes, status: s.status, lineups: startLineups(msg.match) };
    case "status":
      return { ...s, status: msg };
    case "event": {
      const ev = msg.event;
      const lineups = applyLineup(s.lineups, ev);
      if (!ev.display) return { ...s, lineups, clock: ev.clock, period: ev.period };
      const step = { ...ev.display, team: ev.team_id, player: ev.player_id, type: ev.type };
      return { ...s, lineups, trail: [...s.trail.slice(-5), step], last: step, clock: ev.clock, period: ev.period };
    }
    case "cue": {
      const c = msg.cue, cues = [c, ...s.cues].slice(0, 150);
      if (c.type === "stat_badge") return { ...s, cues, badges: [...s.badges.filter(b => b.until > now).slice(-1), { ...c.payload, id: c.cue_id, until: now + 3800 }] };
      if (c.type === "moment_banner") return { ...s, cues, banner: { ...c.payload, until: now + 3200 } };
      if (c.type === "lower_third" && c.payload.profile_key && c.payload.profile_key !== key) return s;
      return { ...s, cues };
    }
    case "moment": {
      const m = msg.moment;
      const lower = m.importance >= 0.45 ? { moment: m, caption: m.title, line: null, pending: true, until: now + 12000 } : s.lower;
      return { ...s, moments: [m, ...s.moments], lower };
    }
    case "commentary": {
      const c = msg.coverage;
      if (c.profile_key !== key) return s;
      const moments = s.moments.map(m => (m.id === msg.moment.id ? { ...m, ai: c } : m));
      const m = moments.find(x => x.id === msg.moment.id) || msg.moment;
      return { ...s, moments, lower: { moment: m, caption: c.caption, line: c.line, ai: c, until: now + 10000 }, lastAI: { ...c, moment: msg.moment } };
    }
    case "state":
      return { ...s, snapshot: msg.state };
    case "reset": {
      const d = msg.event.display;
      const last = d ? { ...d, team: msg.event.team_id, player: msg.event.player_id, type: msg.event.type } : null;
      return { ...s, trail: last ? [last] : [], last, lineups: msg.lineups || s.lineups, badges: [], banner: null, lower: null, cues: [],
        recap: null, snapshot: msg.state, moments: [...msg.state.moments].reverse().map(m => ({ ...m, past: true })), clock: msg.event.clock };
    }
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
  return html`<svg width=${size} height=${size * 1.15} viewBox="0 0 40 46" aria-label=${club.name}>
    <path d="M20 2 L37 8 V22 C37 33 29 41 20 44 C11 41 3 33 3 22 V8 Z" fill=${a} stroke="rgba(255,255,255,.35)" stroke-width="1.5"/>
    <path d="M20 2 L37 8 V22 C37 33 29 41 20 44 Z" fill="#000" opacity=".14"/>
    <text x="20" y="28" text-anchor="middle" font-family="Barlow Condensed, sans-serif" font-weight="800" font-size="13" fill=${inkOn(a)}>${club.short}</text>
  </svg>`;
}

const teamOf = (meta, id) => (meta ? [meta.home, meta.away].find(t => t.id === id) : null);
const playerName = (meta, id) => {
  if (!meta || !id) return null;
  for (const t of [meta.home, meta.away]) for (const p of [...t.starters, ...t.bench]) if (p.id === id) return p.name;
  return null;
};
const playerById = meta => {
  const out = {};
  if (meta) for (const t of [meta.home, meta.away]) for (const p of [...t.starters, ...t.bench]) out[p.id] = { ...p, team: t.id };
  return out;
};

// The provenance chip: fans see a small badge; one click shows how that exact line was made.
function ProofChip({ ai, onProof }) {
  if (!ai) return null;
  const ok = ai.source === "ai";
  return html`<button class=${"chip " + (ok ? "ai" : "fb")} onClick=${e => { e.stopPropagation(); onProof && onProof(ai); }}
    title="How was this made?">${ok ? "✓ Fact-checked" : "Safe fallback"}${onProof ? " ⓘ" : ""}</button>`;
}

// ------------------------------------------------------------------ pitch: 22 players that hold their shape

const PL = 105, PW = 68;
const clampX = x => Math.min(Math.max(x, 1.5), PL - 1.5), clampY = y => Math.min(Math.max(y, 1.5), PW - 1.5);

// Same idea as the simulator's MatchEngine.positions(): every player keeps their formation slot,
// and the whole block slides with the ball. Returned in the shared display frame (home attacks right).
function shapeTargets(meta, lineups, last) {
  const out = {};
  if (!meta || !meta.home.slots) return out;
  const ball = last ? (last.end || last.start) : [PL / 2, PW / 2];
  const poss = last ? last.team : meta.home.id;
  for (const t of [meta.home, meta.away]) {
    const home = t.id === meta.home.id;
    const toOwn = ([x, y]) => (home ? [x, y] : [PL - x, PW - y]);
    const toDisp = ([x, y]) => (home ? [x, y] : [PL - x, PW - y]);
    const [bx, by] = toOwn(ball);
    const ids = lineups[t.id] || [];
    t.slots.forEach(([, role, x0, y0], i) => {
      const id = ids[i];
      if (!id) return;
      let p;
      if (role === "GK") p = [x0, y0 + (by - PW / 2) * 0.12];
      else {
        const shift = (bx - PL / 2) * 0.45 - (t.id !== poss ? 6 : 0);
        p = [Math.min(x0 + shift, 88), y0 + (by - PW / 2) * 0.25];
      }
      out[id] = { pos: toDisp([clampX(p[0]), clampY(p[1])]), team: t.id, gk: role === "GK" };
    });
  }
  if (!last) return out;
  // The player on the ball stands where the action started; the nearest team-mate to where it ended
  // receives it; the nearest opponent closes them down.
  if (out[last.player]) out[last.player].pos = [...last.start];
  const near = (team, pt, skip) => Object.entries(out).filter(([id, o]) => o.team === team && !o.gk && id !== skip)
    .sort((a, b) => Math.hypot(a[1].pos[0] - pt[0], a[1].pos[1] - pt[1]) - Math.hypot(b[1].pos[0] - pt[0], b[1].pos[1] - pt[1]))[0];
  if (last.end) {
    const r = near(poss, last.end, last.player);
    if (r) r[1].pos = [last.end[0], last.end[1]];
  }
  const opp = poss === meta.home.id ? meta.away.id : meta.home.id;
  const d = near(opp, ball);
  if (d) {
    const dir = opp === meta.home.id ? -1 : 1; // defenders sit goal-side
    d[1].pos = [clampX(ball[0] + dir * 2.2), clampY(ball[1] + 1.2)];
  }
  return out;
}

function drawLines(ctx, w, hgt) {
  const k = w / PL;
  for (let i = 0; i < 12; i++) { ctx.fillStyle = i % 2 ? "#0f4a2b" : "#0d4427"; ctx.fillRect((i * w) / 12, 0, w / 12 + 1, hgt); }
  const g = ctx.createRadialGradient(w / 2, hgt / 2, hgt * 0.2, w / 2, hgt / 2, w * 0.75);
  g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, "rgba(0,0,0,.45)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, hgt);
  ctx.strokeStyle = "rgba(255,255,255,.5)"; ctx.fillStyle = "rgba(255,255,255,.6)"; ctx.lineWidth = Math.max(1.2, k * 0.15);
  const m = k * 1.2;
  ctx.strokeRect(m, m, w - 2 * m, hgt - 2 * m);
  ctx.beginPath(); ctx.moveTo(w / 2, m); ctx.lineTo(w / 2, hgt - m); ctx.stroke();
  ctx.beginPath(); ctx.arc(w / 2, hgt / 2, 9.15 * k, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(w / 2, hgt / 2, k * 0.4, 0, Math.PI * 2); ctx.fill();
  for (const left of [true, false]) {
    const x0 = left ? m : w - m, dir = left ? 1 : -1;
    ctx.strokeRect(Math.min(x0, x0 + dir * 16.5 * k), hgt / 2 - 20.16 * k, 16.5 * k, 40.32 * k);
    ctx.strokeRect(Math.min(x0, x0 + dir * 5.5 * k), hgt / 2 - 9.16 * k, 5.5 * k, 18.32 * k);
    ctx.beginPath(); ctx.arc(x0 + dir * 11 * k, hgt / 2, k * 0.35, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(x0 + dir * 11 * k, hgt / 2, 9.15 * k, left ? -0.93 : Math.PI - 0.93, left ? 0.93 : Math.PI + 0.93); ctx.stroke();
  }
}

function Pitch({ s, colors }) {
  const canvas = useRef(null), live = useRef({}), anim = useRef({ players: {}, ball: null, t: 0 }), bg = useRef(null);
  const people = useMemo(() => playerById(s.meta), [s.meta]);
  const targets = useMemo(() => shapeTargets(s.meta, s.lineups, s.last), [s.meta, s.lineups, s.last]);
  live.current = { s, colors, people, targets };

  useEffect(() => {
    let raf, prev = performance.now();
    const frame = now => {
      raf = requestAnimationFrame(frame);
      const c = canvas.current;
      if (!c) return;
      const dt = Math.min((now - prev) / 1000, 0.1); prev = now;
      const { s, colors, people, targets } = live.current;
      const dpr = window.devicePixelRatio || 1, w = c.clientWidth, hgt = c.clientHeight;
      if (!w || !hgt) return;
      if (c.width !== Math.round(w * dpr) || c.height !== Math.round(hgt * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(hgt * dpr); bg.current = null; }
      if (!bg.current) { // pitch markings are drawn once per size
        const o = document.createElement("canvas"); o.width = c.width; o.height = c.height;
        const octx = o.getContext("2d"); octx.setTransform(dpr, 0, 0, dpr, 0, 0); drawLines(octx, w, hgt); bg.current = o;
      }
      const ctx = c.getContext("2d");
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(bg.current, 0, 0); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const k = w / PL, A = anim.current;
      A.t += dt;
      // ease every player toward their target; new players appear in place
      const ease = 1 - Math.exp(-dt * 3.2);
      for (const id of Object.keys(A.players)) if (!targets[id]) delete A.players[id];
      for (const [id, tg] of Object.entries(targets)) {
        const p = A.players[id];
        if (!p) A.players[id] = { x: tg.pos[0], y: tg.pos[1] };
        else { p.x += (tg.pos[0] - p.x) * ease; p.y += (tg.pos[1] - p.y) * ease; }
      }
      const last = s.last, ballT = last ? (last.end || last.start) : [PL / 2, PW / 2];
      if (!A.ball) A.ball = { x: ballT[0], y: ballT[1] };
      const be = 1 - Math.exp(-dt * 7);
      A.ball.x += (ballT[0] - A.ball.x) * be; A.ball.y += (ballT[1] - A.ball.y) * be;

      // recent ball path, faint
      s.trail.forEach((t, i) => {
        if (!t.end) return;
        ctx.strokeStyle = colors[t.team] || "#fff"; ctx.globalAlpha = 0.08 + (0.32 * (i + 1)) / s.trail.length;
        ctx.lineWidth = Math.max(1.5, k * 0.22); ctx.setLineDash(t.type === "carry" || t.type === "dribble" ? [k * 0.8, k * 0.6] : []);
        ctx.beginPath(); ctx.moveTo(t.start[0] * k, t.start[1] * k); ctx.lineTo(t.end[0] * k, t.end[1] * k); ctx.stroke();
      });
      ctx.setLineDash([]); ctx.globalAlpha = 1;

      // players
      const r = Math.max(5, k * 1.25);
      ctx.font = `800 ${Math.round(r * 1.05)}px "Barlow Condensed", sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const [id, p] of Object.entries(A.players)) {
        const tg = targets[id], who = people[id];
        if (!tg || !who) continue;
        const i = who.number || 0;
        const x = (p.x + Math.sin(A.t * 0.9 + i) * 0.35) * k, y = (p.y + Math.cos(A.t * 0.7 + i * 1.7) * 0.35) * k;
        const col = tg.gk ? "#e8e8e8" : colors[tg.team] || "#fff";
        const onBall = last && last.player === id;
        ctx.shadowColor = "rgba(0,0,0,.5)"; ctx.shadowBlur = 6;
        ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
        ctx.shadowBlur = 0;
        ctx.lineWidth = onBall ? 2.5 : 1.2; ctx.strokeStyle = onBall ? "#ffffff" : "rgba(0,0,0,.35)"; ctx.stroke();
        if (tg.gk) { ctx.lineWidth = 2; ctx.strokeStyle = colors[tg.team]; ctx.beginPath(); ctx.arc(x, y, r - 1.5, 0, Math.PI * 2); ctx.stroke(); }
        ctx.fillStyle = inkOn(col); ctx.fillText(String(who.number), x, y + 0.5);
      }
      // ball
      const bx = A.ball.x * k, by = A.ball.y * k;
      ctx.shadowColor = "rgba(0,0,0,.7)"; ctx.shadowBlur = 8;
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(bx, by, Math.max(3, k * 0.55), 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      // name tag for the player on the ball
      const holder = last && people[last.player] && A.players[last.player];
      if (holder) {
        const name = people[last.player].name.split(" ").slice(-1)[0];
        const hx = holder.x * k, hy = holder.y * k - r - 12;
        ctx.font = `600 ${Math.max(11, Math.round(k * 1.1))}px Inter, sans-serif`;
        const tw = ctx.measureText(name).width + 14;
        ctx.fillStyle = "rgba(7,11,20,.85)"; ctx.beginPath(); ctx.roundRect(hx - tw / 2, hy - 10, tw, 20, 10); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.fillText(name, hx, hy + 0.5);
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);
  return html`<canvas ref=${canvas}></canvas>`;
}

// Keeps the pitch at 105:68 and as large as the space allows, so the stats around it stay on screen.
function useFit(ref) {
  const [size, setSize] = useState(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      if (window.innerWidth <= 1000) return setSize(null);
      const w = el.clientWidth, hh = el.clientHeight;
      setSize({ width: Math.floor(Math.min(w, (hh * PL) / PW)), height: Math.floor(Math.min(hh, (w * PW) / PL)) });
    };
    const ro = new ResizeObserver(fit); ro.observe(el); fit();
    return () => ro.disconnect();
  }, []);
  return size;
}

function Broadcast({ s, colors, showLower = true, onProof }) {
  const wrap = useRef(null), size = useFit(wrap);
  const now = useTick(400);
  const meta = s.meta;
  const score = s.snapshot ? s.snapshot.match.score : {};
  const badges = s.badges.filter(b => b.until > now);
  const banner = s.banner && s.banner.until > now ? s.banner : null;
  const lower = showLower && s.lower && s.lower.until > now ? s.lower : null; // off air after ~10s, like TV
  return html`<div class="pitchwrap" ref=${wrap}><div class="frame" style=${size ? { width: size.width + "px", height: size.height + "px" } : {}}>
    <${Pitch} s=${s} colors=${colors} />
    ${meta && html`<div class="scorebug">
      <div class="team"><i style=${{ background: colors[meta.home.id] }}></i>${meta.home.short}</div>
      <div class="score">${score[meta.home.id] ?? 0}–${score[meta.away.id] ?? 0}</div>
      <div class="team away">${meta.away.short}<i style=${{ background: colors[meta.away.id] }}></i></div>
      <div class="clock">${s.clock}<small>${s.period === 1 ? "1H" : "2H"}</small></div>
    </div>`}
    <div class="badges">${badges.map(b => html`<div class="badge" key=${b.id}>
      <div class="v">${b.value}<small>${b.unit}</small></div>
      <div class="l"><b>${b.label}</b>${b.player ? b.player.name : ""}</div></div>`)}</div>
    ${banner && html`<div class="banner" style=${{ "--c": banner.color }}><div>${banner.label}<small>${banner.title}</small></div></div>`}
    ${lower && html`<div class="lower" key=${lower.moment.id + (lower.pending ? "p" : "")}>
      <div class=${"cap" + (lower.pending ? " pending" : "")}>${lower.caption}</div>
      <div class="line"><p>${lower.line || html`<span class="muted">The broadcast crew is on it…</span>`}</p>
        <${ProofChip} ai=${lower.ai} onProof=${ai => onProof(ai, lower.moment)} /></div></div>`}
  </div></div>`;
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

// ------------------------------------------------------------------ stats

// Neutral track; only the side that leads the stat is filled in its team colour.
function StatRow({ label, hv, av, hc, ac, lowWins, compact }) {
  const h = +hv || 0, a = +av || 0, tot = h + a;
  const has = hv != null && av != null && tot > 0;
  const lead = !has || h === a ? null : (h > a) !== !!lowWins ? "h" : "a";
  return html`<div class=${"stat" + (compact ? " sm" : "")}>
    <span class=${"n" + (lead === "h" ? " lead" : "")}>${hv ?? "–"}</span>
    <span class="lbl">${label}</span>
    <span class=${"n r" + (lead === "a" ? " lead" : "")}>${av ?? "–"}</span>
    <div class="bar">${has && html`
      <i class="hb" style=${{ width: `calc(${(h / tot) * 100}% - 1px)`, background: hc, opacity: lead === "a" ? 0.3 : 1 }}></i>
      <i class="ab" style=${{ width: `calc(${(a / tot) * 100}% - 1px)`, background: ac, opacity: lead === "h" ? 0.3 : 1 }}></i>`}</div>
  </div>`;
}

const STRIP = [["possession_pct", "Possession %"], ["shots", "Shots"], ["xg", "xG"], ["box_entries", "Box entries"], ["field_tilt_pct", "Field tilt %"]];
const DESK = [["possession_pct", "Possession %"], ["shots", "Shots"], ["on_target", "On target"], ["xg", "xG"], ["big_chances", "Big chances"],
  ["box_entries", "Box entries"], ["field_tilt_pct", "Field tilt %"], ["pass_accuracy_pct", "Pass accuracy %"],
  ["progressive_passes", "Progressive passes"], ["ppda", "PPDA · lower = more pressing", true], ["corners", "Corners"], ["fouls", "Fouls", true]];

function StatList({ s, colors, keys, compact }) {
  const snap = s.snapshot, meta = s.meta;
  if (!snap || !meta) return html`<div class="empty">Waiting for kick-off…</div>`;
  const [h, a] = [meta.home.id, meta.away.id];
  return keys.map(([k, label, low]) => html`<${StatRow} key=${k} label=${label} hv=${snap.teams[h][k]} av=${snap.teams[a][k]}
    hc=${colors[h]} ac=${colors[a]} lowWins=${low} compact=${compact} />`);
}

function StatStrip({ s, colors }) {
  const meta = s.meta;
  return html`<div class="strip">
    ${meta && html`<div class="teams"><span><i style=${{ background: colors[meta.home.id] }}></i>${meta.home.short}</span>
      <span>${meta.away.short}<i style=${{ background: colors[meta.away.id] }}></i></span></div>`}
    <div class="cells"><${StatList} s=${s} colors=${colors} keys=${STRIP} compact=${true} /></div>
    <div class="mini"><div class="mlbl">Momentum</div><${Momentum} s=${s} colors=${colors} H=${44} legend=${false} /></div>
  </div>`;
}

function Momentum({ s, colors, H = 90, legend = true }) {
  const snap = s.snapshot, meta = s.meta, max = s.durationMin;
  const tl = snap ? snap.momentum.timeline : [];
  const W = 340, mid = H / 2;
  const x = i => (i / Math.max(max, 1)) * W, y = v => mid - v * (mid - 4);
  const pts = tl.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = tl.length ? `M0,${mid} L${pts.split(" ").join(" L")} L${x(tl.length - 1)},${mid} Z` : "";
  const id = "m" + H;
  return html`<svg viewBox="0 0 ${W} ${H}" width="100%" height=${H} preserveAspectRatio="none" role="img" aria-label="momentum">
    <defs><clipPath id=${id + "t"}><rect x="0" y="0" width=${W} height=${mid} /></clipPath><clipPath id=${id + "b"}><rect x="0" y=${mid} width=${W} height=${mid} /></clipPath></defs>
    <line x1="0" x2=${W} y1=${mid} y2=${mid} stroke="#283654" />
    <line x1=${x(45)} x2=${x(45)} y1="0" y2=${H} stroke="#1d2842" stroke-dasharray="3 3" />
    ${area && meta && html`<path d=${area} fill=${colors[meta.home.id]} opacity=".6" clip-path=${`url(#${id}t)`} />
    <path d=${area} fill=${colors[meta.away.id]} opacity=".6" clip-path=${`url(#${id}b)`} />`}
  </svg>
  ${legend && meta && html`<div class="legend"><span><i style=${{ background: colors[meta.home.id] }}></i>${meta.home.name} on top</span>
    <span><i style=${{ background: colors[meta.away.id] }}></i>${meta.away.name} on top</span></div>`}`;
}

function XgRace({ s, colors }) {
  const snap = s.snapshot, meta = s.meta, max = s.durationMin;
  if (!snap || !meta) return null;
  const tl = snap.xg_timeline, W = 340, H = 100;
  const top = Math.max(1, ...tl.map(p => Math.max(p[meta.home.id], p[meta.away.id]))) * 1.1;
  const line = id => tl.map((p, i) => `${((i / Math.max(max, 1)) * W).toFixed(1)},${(H - (p[id] / top) * (H - 8)).toFixed(1)}`).join(" ");
  const goals = snap.moments.filter(m => m.type === "goal");
  return html`<svg viewBox="0 0 ${W} ${H}" width="100%" height=${H} preserveAspectRatio="none" role="img" aria-label="xG race">
    <line x1="0" x2=${W} y1=${H - 0.5} y2=${H - 0.5} stroke="#283654" />
    <polyline points=${line(meta.home.id)} fill="none" stroke=${colors[meta.home.id]} stroke-width="2.5" vector-effect="non-scaling-stroke" />
    <polyline points=${line(meta.away.id)} fill="none" stroke=${colors[meta.away.id]} stroke-width="2.5" vector-effect="non-scaling-stroke" />
    ${goals.map(g => html`<text x=${(minuteOf(g.clock) / max) * W} y="12" font-size="11" text-anchor="middle">⚽</text>`)}
  </svg>
  <div class="legend"><span><i style=${{ background: colors[meta.home.id] }}></i>${meta.home.short} ${snap.teams[meta.home.id].xg} xG</span>
    <span><i style=${{ background: colors[meta.away.id] }}></i>${meta.away.short} ${snap.teams[meta.away.id].xg} xG</span></div>`;
}

function Chaos({ snap }) {
  const v = snap ? snap.control_chaos.value : 0, label = snap ? snap.control_chaos.label : "–";
  const ang = Math.PI * (1 - v / 100), cx = 60, cy = 56, r = 46;
  const color = v < 35 ? "#7fd1ff" : v < 60 ? "#ffcf5c" : "#ff8a5c";
  return html`<div class="gauge">
    <svg width="120" height="66" viewBox="0 0 120 66" aria-label="control vs chaos">
      <path d="M14 56 A46 46 0 0 1 106 56" fill="none" stroke="#1d2842" stroke-width="10" stroke-linecap="round" />
      <path d=${`M14 56 A46 46 0 0 1 ${cx + r * Math.cos(ang)} ${cy - r * Math.sin(ang)}`} fill="none" stroke=${color} stroke-width="10" stroke-linecap="round" />
    </svg>
    <div><div class="big" style=${{ color }}>${v}</div><div class="lab">${label}</div>
      <div class="muted" style="font-size:12px">0 calm build-up · 100 end-to-end chaos</div></div></div>`;
}

function Analysis({ s, colors }) {
  const why = s.lastAI && s.lastAI.insight && s.lastAI.insight.why_it_matters;
  return html`<div class="pane">
    <section><h3>🧠 Why it matters ${s.lastAI && html`<span class="right">${s.lastAI.moment.clock}</span>`}</h3>
      ${why ? html`<p class="why">${why}</p>` : html`<p class="why empty">The Analyst agent explains each key moment here.</p>`}</section>
    <section><h3>Momentum <span class="right">threat, last ~10 min</span></h3><${Momentum} s=${s} colors=${colors} /></section>
    <section><h3>xG race</h3><${XgRace} s=${s} colors=${colors} /></section>
    <section><h3>Control vs chaos <span class="right">last 5 min</span></h3><${Chaos} snap=${s.snapshot} /></section>
    <section><h3>All stats</h3><${StatList} s=${s} colors=${colors} keys=${DESK} /></section>
  </div>`;
}

// ------------------------------------------------------------------ commentary feed

function MomentCard({ m, send, onCompare, onProof, yourTeam, waiting }) {
  const [open, setOpen] = useState(false);
  const ai = m.ai;
  const replay = e => { e && e.preventDefault(); send({ cmd: "seek", minute: Math.max(0, minuteOf(m.clock) - 1) }); };
  return html`<div class=${"fc" + (m.importance >= 0.6 ? " big" : "")}>
    <div class="h"><span>${icon(m)}</span><span>${m.clock} · ${m.type.replace(/_/g, " ")}</span>
      ${yourTeam && m.team_id === yourTeam && html`<span class="chip you">your team</span>`}
      <span class="spacer"></span><${ProofChip} ai=${ai} onProof=${a => onProof(a, m)} /></div>
    <div class="cap">${ai ? ai.caption : m.title}</div>
    ${ai ? html`<p>${ai.line}</p>`
      : waiting && m.past ? html`<p><span class="muted">You skipped past this one. </span><a href="#" onClick=${replay}>Replay it ↺</a></p>`
      : waiting ? html`<p class="muted">The crew is writing this…</p>` : null}
    <div class="acts">
      <button class="btn sm ghost" onClick=${replay}>↺ Replay</button>
      ${ai && ai.insight && ai.insight.why_it_matters && html`<button class="btn sm ghost" onClick=${() => setOpen(!open)}>${open ? "Hide why" : "Why?"}</button>`}
      ${ai && onCompare && html`<button class="btn sm ghost" onClick=${() => onCompare(m)}>Other fans' view ↔</button>`}
    </div>
    ${open && html`<div class="whytext">🧠 ${ai.insight.why_it_matters}</div>`}
  </div>`;
}

function RecapCard({ recap }) {
  const r = recap.recap;
  return html`<div class="fc recap"><div class="h"><span>📰</span><span>${recap.when === "full-time" ? "Full-time" : "Half-time"} recap</span>
    <span class="spacer"></span><span class="chip ai">✓ Fact-checked</span></div>
    <h2>${r.headline}</h2>${r.summary.map(p => html`<p>${p}</p>`)}
    <div class="potm">⭐ <span><b>${r.player_of_the_match}</b><br/><span class="muted">${r.player_reason}</span></span></div></div>`;
}

function Feed({ s, send, profile, onCompare, onProof, all }) {
  // Studio lists every notable moment; Fan only the ones the crew writes for this viewer.
  const list = s.moments.filter(m => m.ai || (all ? m.importance >= 0.3 : crewCovers(profile, m))).slice(0, 40);
  return html`<div class="feed">
    ${s.recap && html`<${RecapCard} recap=${s.recap} />`}
    ${list.map(m => html`<${MomentCard} key=${m.id} m=${m} send=${send} onCompare=${onCompare} onProof=${onProof}
      yourTeam=${profile.favorite_team} waiting=${crewCovers(profile, m)} />`)}
    ${!list.length && !s.recap && html`<div class="empty">Key moments appear here as the match unfolds, each one written up by the AI crew.</div>`}
  </div>`;
}

// ------------------------------------------------------------------ control room (studio only)

function TraceSteps({ trace }) {
  return html`<div class="trace">${trace.map(st => {
    const [ic, name] = AGENT[st.agent] || ["•", st.agent];
    const bad = /^(rejected|failed|gave up)/.test(st.note), ok = st.note === "passed";
    return html`<div class=${"step" + (bad ? " rej" : ok ? " ok" : "")}><span>${ic}</span><b>${name}</b>
      <span class="ms">${st.ms ? (st.ms / 1000).toFixed(1) + "s" : ""}</span>${st.note && html`<span class="note">${st.note}</span>`}</div>`;
  })}</div>`;
}

const cueText = c => {
  const p = c.payload;
  if (c.type === "stat_badge") return `${p.label} ${p.value}${p.unit}${p.player ? " · " + p.player.name : ""}`;
  if (c.type === "moment_banner") return `${p.label} · ${p.title}`;
  if (c.type === "lower_third") return `"${p.caption}" (${p.source}, ${p.language})`;
  if (c.type === "score_bug") return `${p.home.short} ${p.home.goals}–${p.away.goals} ${p.away.short}`;
  return JSON.stringify(p);
};

function ControlRoom({ s, matchId }) {
  const ai = s.lastAI;
  return html`<div class="pane">
    <section><h3>Last crew run <span class="right">Agent Framework · Foundry</span></h3>
      ${ai ? html`<div class="muted" style="font-size:12px;margin-bottom:8px">${ai.moment.clock} · ${ai.moment.title} · ${(ai.trace.reduce((a, x) => a + x.ms, 0) / 1000).toFixed(1)}s</div>
        <${TraceSteps} trace=${ai.trace} />`
        : html`<div class="empty">Each agent's work shows up here when the crew covers a moment.</div>`}</section>
    <section><h3>Graphics out <span class="right"><a href=${`/api/matches/${matchId}/cues`} target="_blank">all cues as JSON ↗</a></span></h3>
      <div class="muted" style="font-size:12px;margin:-4px 0 8px">Timed overlay cues a broadcast graphics engine can render. The pitch draws the same feed.</div>
      <div class="cues">${s.cues.filter(c => c.type !== "player_tag").slice(0, 60).map(c => html`<div class="cue" key=${c.cue_id}>
        <span class="muted">${c.clock}</span><span class=${"k " + c.type}>${c.type}</span><span class="p">${cueText(c)}</span></div>`)}
        ${!s.cues.length && html`<div class="empty">Cues stream here during play.</div>`}</div></section>
  </div>`;
}

// ------------------------------------------------------------------ layouts

function Tabs({ tabs, tab, setTab }) {
  return html`<div class="tabs">${tabs.map(([id, label]) => html`<button class=${tab === id ? "on" : ""} onClick=${() => setTab(id)}>${label}</button>`)}</div>`;
}

function Stage({ s, send, colors, side, showLower, onProof }) {
  return html`<main class="stage">
    <div class="left">
      <${Broadcast} s=${s} colors=${colors} showLower=${showLower} onProof=${onProof} />
      <${Transport} s=${s} send=${send} />
      <${StatStrip} s=${s} colors=${colors} />
    </div>
    <aside class="side">${side}</aside>
  </main>`;
}

function Studio({ s, send, matchId, colors, onCompare, onProof }) {
  const [tab, setTab] = useState("live");
  return html`<${Stage} s=${s} send=${send} colors=${colors} showLower=${true} onProof=${onProof} side=${html`
    <${Tabs} tab=${tab} setTab=${setTab} tabs=${[["live", "Commentary"], ["analysis", "Analysis"], ["control", "Control room"]]} />
    <div class="tabbody">
      ${tab === "live" && html`<${Feed} s=${s} send=${send} profile=${STUDIO_PROFILE} onCompare=${onCompare} onProof=${onProof} all=${true} />`}
      ${tab === "analysis" && html`<${Analysis} s=${s} colors=${colors} />`}
      ${tab === "control" && html`<${ControlRoom} s=${s} matchId=${matchId} />`}
    </div>`} />`;
}

function YourPlayer({ s, id }) {
  const p = s.snapshot && s.snapshot.top_players.find(x => x.id === id);
  const name = playerName(s.meta, id);
  return html`<div class="yp">⭐ <b>${name}</b> <span class="muted">${p ? [p.goals && `${p.goals} goals`, p.assists && `${p.assists} assists`,
    `${p.passes} passes (${p.pass_accuracy_pct}%)`, p.key_passes && `${p.key_passes} key passes`, p.xg && `${p.xg} xG`].filter(Boolean).join(" · ")
    : "· moments involving them are always covered for you"}</span></div>`;
}

function Fan({ s, send, profile, colors, onEdit, onCompare, onProof }) {
  const [tab, setTab] = useState("feed");
  const team = teamOf(s.meta, profile.favorite_team);
  return html`<${Stage} s=${s} send=${send} colors=${colors} showLower=${false} onProof=${onProof} side=${html`
    <div class="me">${team ? html`<${Crest} club=${team} size=${22} />` : "👀"}
      <span><b>${team ? team.name + " fan" : "Neutral"}</b> · ${profile.persona === "analyst" ? "tactics" : "the story"} · ${LANGS[profile.language]}</span>
      <span class="spacer"></span><button class="btn sm" onClick=${onEdit}>Change</button></div>
    ${profile.favorite_player && html`<${YourPlayer} s=${s} id=${profile.favorite_player} />`}
    <${Tabs} tab=${tab} setTab=${setTab} tabs=${[["feed", "For you"], ["analysis", "Stats"]]} />
    <div class="tabbody">
      ${tab === "feed" && html`<${Feed} s=${s} send=${send} profile=${profile} onCompare=${onCompare} onProof=${onProof} />`}
      ${tab === "analysis" && html`<${Analysis} s=${s} colors=${colors} />`}
    </div>`} />`;
}

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

// ------------------------------------------------------------------ modals

function Modal({ onClose, wide, children }) {
  useEffect(() => {
    const k = e => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, []);
  return html`<div class="modal-bg" onClick=${onClose}><div class="modal" style=${wide ? {} : { maxWidth: "640px" }} onClick=${e => e.stopPropagation()}>
    <button class="btn sm x" onClick=${onClose}>Close ✕</button>${children}</div></div>`;
}

// "How was this made?": the agent trace behind one specific line, opened from its fact-check chip.
function ProofModal({ ai, moment, onClose }) {
  const total = ai.trace.reduce((a, x) => a + x.ms, 0);
  const caught = ai.trace.filter(x => x.agent.includes("fact_checker") && x.note.startsWith("rejected")).length;
  return html`<${Modal} onClose=${onClose}>
    <h2>How this line was made</h2>
    <div class="muted">${icon(moment)} ${moment.clock} · ${moment.title}</div>
    <div class="proof-line"><div class="cap">${ai.caption}</div><p>${ai.line}</p></div>
    <div class="proof-sum">
      <span><b>${ai.trace.filter(x => x.ms).length}</b> AI agent runs</span>
      <span><b>${(total / 1000).toFixed(1)}s</b> total</span>
      <span class=${caught ? "warn" : ""}><b>${caught}</b> ${caught === 1 ? "draft" : "drafts"} caught by the fact-checker</span>
      <span><b>${ai.source === "ai" ? "AI" : "Fallback"}</b> ${ai.source === "ai" ? "line, every number verified" : "verified stats-engine text used"}</span>
    </div>
    ${ai.insight && ai.insight.why_it_matters && html`<p class="insight">🧠 “${ai.insight.why_it_matters}”</p>`}
    <${TraceSteps} trace=${ai.trace} />
    <p class="muted" style="font-size:12px;margin-top:14px">Numbers come from code, words come from AI. The fact-checker is plain code:
      any number or player name that isn't in the stats engine's facts sends the draft back for a rewrite.</p>
  <//>`;
}

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
  return html`<${Modal} onClose=${onClose} wide=${true}>
    <h2>One moment. Every fan.</h2>
    <div class="muted">${icon(moment)} ${moment.clock} · ${moment.title}${res ? ` · tailored for ${res.profiles.length} viewers in ${(ms / 1000).toFixed(1)}s (Producer agents run in parallel)` : ""}</div>
    ${err && html`<div class="empty">${err}</div>`}
    <div class="cmp">${(res ? res.profiles : [0, 1, 2]).map((p, i) => html`<div class="col1">
      ${res ? html`<div class="who">${who(p)}</div><div class="cap">${res.coverage[i].caption}</div><div>${res.coverage[i].line}</div>
        <div style="margin-top:10px"><${ProofChip} ai=${res.coverage[i]} /></div>`
        : html`<div class="skeleton" style="width:50%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div>`}
    </div>`)}</div>
  <//>`;
}

function GenerateModal({ clubs, scenarios, onCreated, onClose }) {
  const [home, setHome] = useState(clubs[0] ? clubs[0].id : "kestrel"), [away, setAway] = useState(clubs[1] ? clubs[1].id : "marlowe");
  const [scenario, setScenario] = useState("standard"), [busy, setBusy] = useState(false), [err, setErr] = useState(null);
  const go = () => {
    setBusy(true);
    post("/api/matches", { home, away, scenario }).then(r => onCreated(r.match_id)).catch(e => { setErr(e.detail || "Failed"); setBusy(false); });
  };
  return html`<${Modal} onClose=${onClose} wide=${true}>
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
  <//>`;
}

// ------------------------------------------------------------------ app

function App() {
  const [mode, setMode] = useState(() => new URLSearchParams(location.search).get("mode") || "studio");
  const [matches, setMatches] = useState([]), [matchId, setMatchId] = useState(null);
  const [clubs, setClubs] = useState([]), [scenarios, setScenarios] = useState([]), [ai, setAi] = useState(null);
  const [fanProfile, setFanProfile] = useState({ persona: "casual", language: "en", favorite_team: null, favorite_player: null });
  const [fanReady, setFanReady] = useState(false), [compare, setCompare] = useState(null), [gen, setGen] = useState(false);
  const [proof, setProof] = useState(null);
  const profile = mode === "fan" && fanReady ? fanProfile : STUDIO_PROFILE;
  const [s, send] = useMatch(matchId, profile);
  const colors = useMemo(() => teamColors(s.meta), [s.meta]);
  const onProof = useCallback((a, m) => setProof({ ai: a, moment: m }), []);

  const loadMatches = sel => api("/api/matches").then(list => { setMatches(list); setMatchId(sel || (list[0] && list[0].match_id)); });
  useEffect(() => {
    loadMatches();
    api("/api/clubs").then(setClubs); api("/api/scenarios").then(setScenarios); api("/api/health").then(hh => setAi(hh.ai));
  }, []);
  useEffect(() => { // pause while the fan is choosing, so they don't miss anything
    if (mode === "fan" && !fanReady) send({ cmd: "pause" });
  }, [mode, fanReady, s.meta]);
  useEffect(() => { setFanReady(false); setFanProfile(p => ({ ...p, favorite_team: null, favorite_player: null })); }, [matchId]);

  return html`<div class="app">
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
    ${mode === "studio" && html`<${Studio} s=${s} send=${send} matchId=${matchId} colors=${colors} onCompare=${ai ? setCompare : null} onProof=${onProof} />`}
    ${mode === "fan" && !fanReady && html`<${FanSetup} meta=${s.meta} profile=${fanProfile} setProfile=${setFanProfile}
      onDone=${() => { setFanReady(true); setTimeout(() => send({ cmd: "play" }), 150); }} />`}
    ${mode === "fan" && fanReady && html`<${Fan} s=${s} send=${send} profile=${fanProfile} colors=${colors} onEdit=${() => setFanReady(false)}
      onCompare=${ai ? setCompare : null} onProof=${onProof} />`}
    ${proof && html`<${ProofModal} ai=${proof.ai} moment=${proof.moment} onClose=${() => setProof(null)} />`}
    ${compare && html`<${CompareModal} matchId=${matchId} moment=${compare} meta=${s.meta} onClose=${() => setCompare(null)} />`}
    ${gen && html`<${GenerateModal} clubs=${clubs} scenarios=${scenarios} onClose=${() => setGen(false)}
      onCreated=${id => { setGen(false); loadMatches(id); }} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("root"));
