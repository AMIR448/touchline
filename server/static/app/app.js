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
  recap_fact_checker: ["✅", "Fact-checker", "Checks the recap"],
  match_qa: ["🔎", "Match Q&A", "Decides which stats to look up, then answers"],
  budget: ["⛔", "Budget guard", "Today's AI budget for this demo is used up"] };
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
  trail: [], last: null, lineups: {}, snapshot: null, moments: [], cues: [], badges: [], banner: null, lower: null, recap: null, lastAI: null,
  qa: [] });

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
      return { ...s, moments: [{ ...m, at: now }, ...s.moments], lower };
    }
    case "commentary": {
      const c = msg.coverage;
      if (c.profile_key !== key) return s;
      const moments = s.moments.map(m => (m.id === msg.moment.id ? { ...m, ai: c } : m));
      if (msg.replay) return { ...s, moments };  // re-sent after a profile change: refill the feed quietly
      const m = moments.find(x => x.id === msg.moment.id) || msg.moment;
      return { ...s, moments, lower: { moment: m, caption: c.caption, line: c.line, ai: c, until: now + 10000 }, lastAI: { ...c, moment: msg.moment } };
    }
    case "state":
      return { ...s, snapshot: msg.state };
    case "reset": {
      const d = msg.event.display;
      const last = d ? { ...d, team: msg.event.team_id, player: msg.event.player_id, type: msg.event.type } : null;
      return { ...s, trail: last ? [last] : [], last, lineups: msg.lineups || s.lineups, badges: [], banner: null, lower: null, cues: [],
        recap: null, snapshot: msg.state, moments: [...msg.state.moments].reverse().map((m, i) => ({ ...m, past: true, at: -i })), clock: msg.event.clock };
    }
    case "recap":
      return msg.profile_key === key && msg.recap ? { ...s, recap: msg } : s;
    case "profile":  // new language/club/style: drop lines written for the old one; the server re-sends what it has
      return { ...s, moments: s.moments.map(m => ({ ...m, ai: undefined, past: true })), recap: null, lower: null };
    case "answer":
      return { ...s, qa: s.qa.map(q => (q.id === msg.id ? { ...q, ...msg, pending: false } : q)) };
    case "error":
      return { ...s, qa: s.qa.map(q => (q.pending ? { ...q, pending: false, answer: msg.message, source: "fallback", tools: [], trace: [] } : q)) };
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
    const q = new URLSearchParams({ speed: 10, autoplay: true, persona: profile.persona, language: profile.language });
    if (profile.favorite_team) q.set("team", profile.favorite_team);
    if (profile.favorite_player) q.set("player", profile.favorite_player);
    const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/matches/${matchId}?${q}`);
    ws.current = sock;
    sock.onmessage = e => { const msg = JSON.parse(e.data); setS(prev => reduce(prev, msg, keyRef.current)); };
    return () => sock.close();
  }, [matchId]);
  // Profile changes are sent live, so the match keeps playing from where it is.
  useEffect(() => { send({ cmd: "profile", ...profile }); }, [keyRef.current]);
  const ask = useCallback(question => {
    const id = "q" + Date.now();
    setS(prev => ({ ...prev, qa: [...prev.qa, { id, question, pending: true, clock: prev.clock, at: performance.now() }] }));
    send({ cmd: "ask", id, question });
  }, []);
  return [s, send, ask];
}

// The commentator's voice: plays each new line for this viewer; if lines pile up (fast replay),
// only the newest waits, so the voice never lags far behind the picture.
function useVoice(matchId, lastAI, on) {
  const audio = useRef(null), queued = useRef(null);
  const [speaking, setSpeaking] = useState(null);
  const stop = () => { if (audio.current) audio.current.pause(); audio.current = null; queued.current = null; setSpeaking(null); };
  const next = () => {
    const item = queued.current;
    queued.current = null;
    if (!item) { audio.current = null; setSpeaking(null); return; }
    const a = new Audio(item.url);
    audio.current = a;
    setSpeaking(item.momentId);
    a.onended = a.onerror = next;
    a.play().catch(next);
  };
  useEffect(() => {
    if (!on || !lastAI || !matchId) return;
    queued.current = { momentId: lastAI.moment.id,
      url: `/api/matches/${matchId}/voice/${lastAI.moment.id}?profile=${encodeURIComponent(lastAI.profile_key)}` };
    if (!audio.current) next();
  }, [lastAI, on]);
  useEffect(() => { if (!on) stop(); }, [on]);
  useEffect(() => stop, [matchId]);
  return speaking;
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
  // two opponents on the same spot (a No. 10 against a holding midfielder) would hide each other
  const all = Object.values(out);
  for (const a of all) for (const b of all)
    if (a.team === meta.home.id && b.team !== a.team && Math.hypot(a.pos[0] - b.pos[0], a.pos[1] - b.pos[1]) < 3.5) {
      a.pos = [a.pos[0] - 1.2, clampY(a.pos[1] - 1.8)]; b.pos = [b.pos[0] + 1.2, clampY(b.pos[1] + 1.8)];
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

// Sizes the left column to the largest 105:68 pitch that fits the height, so the pitch, timeline and
// stats strip line up and the side panel takes the rest of the width.
function useStageFit(ref) {
  const [w, setW] = useState(null);
  useEffect(() => {
    const st = ref.current;
    if (!st) return;
    const fit = () => {
      const left = st.querySelector(".left"), tr = st.querySelector(".transport"), strip = st.querySelector(".strip");
      if (window.innerWidth <= 1000 || !left || !tr || !strip) return setW(null);
      const availW = st.clientWidth - 36 - 16 - 340; // padding, gap, narrowest side panel
      const availH = left.clientHeight - tr.offsetHeight - strip.offsetHeight - 20; // two 10px gaps
      setW(Math.floor(Math.min(availW, (availH * PL) / PW)));
    };
    const ro = new ResizeObserver(fit); ro.observe(st); fit();
    return () => ro.disconnect();
  }, []);
  return w;
}

const Eq = () => html`<span class="eq" aria-label="speaking"><i></i><i></i><i></i><i></i></span>`;

function Broadcast({ s, colors, showLower = true, onProof, speaking }) {
  const now = useTick(400);
  const meta = s.meta;
  const score = s.snapshot ? s.snapshot.match.score : {};
  const badges = s.badges.filter(b => b.until > now);
  const banner = s.banner && s.banner.until > now ? s.banner : null;
  const lower = showLower && s.lower && s.lower.until > now ? s.lower : null; // off air after ~10s, like TV
  return html`<div class="pitchwrap"><div class="frame">
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
      <div class=${"cap" + (lower.pending ? " pending" : "")}>${speaking === lower.moment.id && html`<${Eq} />`}${lower.caption}</div>
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
      ${marks.map(m => html`<span class=${"mk mk-" + m.type} title=${m.title} style=${{ left: `${(minuteOf(m.clock) / max) * 100}%` }}>
        ${m.type === "goal" ? "⚽" : m.type === "card" ? html`<i></i>` : "HT"}</span>`)}
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

// ------------------------------------------------------------------ the live feed: commentary + your questions

const TAGLINE = { standard: "Clash of styles", late_comeback: "The late comeback", red_card: "Down to ten men",
  high_press: "A pressing war", dominant_home: "One-way traffic" };
const tagline = sc => TAGLINE[sc] || (sc || "").replace(/_/g, " ");

function MomentCard({ m, send, onCompare, onProof, yourTeam, waiting, speaking }) {
  const [open, setOpen] = useState(false);
  const ai = m.ai;
  const replay = e => { e && e.preventDefault(); send({ cmd: "seek", minute: Math.max(0, minuteOf(m.clock) - 1) }); };
  return html`<div class=${"fc" + (m.importance >= 0.6 ? " big" : "") + (yourTeam && m.team_id === yourTeam ? " mine" : "")}>
    <div class="h"><span class="ic">${icon(m)}</span><span>${m.clock}</span>
      <span class="spacer"></span>${speaking === m.id && html`<${Eq} />`}<${ProofChip} ai=${ai} onProof=${a => onProof(a, m)} /></div>
    <div class="cap">${ai ? ai.caption : m.title}</div>
    ${ai ? html`<p>${ai.line}</p>`
      : waiting && m.past ? html`<p class="muted"><a href="#" onClick=${replay}>Replay this moment ↺</a></p>`
      : waiting ? html`<p class="muted writing">The crew is writing this<span class="dots"><i></i><i></i><i></i></span></p>` : null}
    ${ai && html`<div class="acts">
      ${ai.insight && ai.insight.why_it_matters && html`<button class="btn sm ghost" onClick=${() => setOpen(!open)}>${open ? "Hide" : "Why?"}</button>`}
      ${onCompare && html`<button class="btn sm ghost" onClick=${() => onCompare(m)}>Other fans' view ↔</button>`}
      <button class="btn sm ghost" onClick=${replay}>Replay ↺</button>
    </div>`}
    ${open && html`<div class="whytext">🧠 ${ai.insight.why_it_matters}</div>`}
  </div>`;
}

function QACard({ q, onProof }) {
  return html`<div class="fc qa-card">
    <div class="h"><span class="ic">💬</span><span>${q.clock} · you asked</span><span class="spacer"></span>
      ${!q.pending && html`<${ProofChip} ai=${{ ...q, caption: q.question, line: q.answer }} onProof=${a => onProof(a, { type: "qa", clock: q.clock, title: q.question })} />`}</div>
    <div class="question">${q.question}</div>
    ${q.pending ? html`<p class="muted writing">Looking it up<span class="dots"><i></i><i></i><i></i></span></p>` : html`<p>${q.answer}</p>
      ${q.tools && q.tools.length > 0 && html`<div class="tools">${q.tools.map(t => html`<span class="tool">🔧 ${t.tool.replace(/_/g, " ")}${t.args ? " · " + t.args : ""}</span>`)}</div>`}`}
  </div>`;
}

function RecapCard({ recap }) {
  const r = recap.recap;
  return html`<div class="fc recap"><div class="h"><span class="ic">📰</span><span>${recap.when === "full-time" ? "Full-time" : "Half-time"} story</span>
    <span class="spacer"></span><span class="chip ai">✓ Fact-checked</span></div>
    <h2>${r.headline}</h2>${r.summary.map(p => html`<p>${p}</p>`)}
    <div class="potm">⭐ <span><b>${r.player_of_the_match}</b><br/><span class="muted">${r.player_reason}</span></span></div></div>`;
}

// How a club likes to play, in words (from its tactics, 0..1).
function style(t) {
  const { press, directness, tempo } = t.tactics || {};
  if (press >= 0.75) return "press high and hunt the ball";
  if (directness >= 0.7) return "go long and direct";
  if (press <= 0.3) return "sit deep and counter";
  if (directness <= 0.35 && tempo >= 0.5) return "keep the ball and build patiently";
  return "mix it up";
}

function KickoffCard({ meta }) {
  if (!meta) return null;
  const { home, away } = meta;
  return html`<div class="fc kickoff"><div class="h"><span class="ic">🏟️</span><span>00:00 · kick-off</span></div>
    <div class="cap">${home.name} v ${away.name}</div>
    <p>${home.name} (${home.formation}) like to ${style(home)}. ${away.name} (${away.formation}) like to ${style(away)}.</p></div>`;
}

function suggestions(s) {
  const out = ["Who's on top right now, and why?"];
  const top = s.snapshot && s.snapshot.top_players[0];
  if (top && top.touches) out.push(`How is ${top.name} playing?`);
  out.push("What changed in the last 10 minutes?");
  return out;
}

function AskBar({ s, ask }) {
  const [text, setText] = useState("");
  const busy = s.qa.some(q => q.pending);
  const last = s.qa[s.qa.length - 1];
  const chips = (last && !last.pending && last.follow_ups && last.follow_ups.length ? last.follow_ups : suggestions(s)).slice(0, 3);
  const go = q => { q = (q || "").trim(); if (q && !busy) { ask(q); setText(""); } };
  return html`<div class="askbox">
    <div class="chips">${chips.map(q => html`<button class="sugg" disabled=${busy} onClick=${() => go(q)}>${q}</button>`)}</div>
    <form class="askbar" onSubmit=${e => { e.preventDefault(); go(text); }}>
      <input value=${text} onInput=${e => setText(e.target.value)} maxLength="300" placeholder="Ask anything about this match…" aria-label="Ask about this match" />
      <button class="btn primary" disabled=${busy || !text.trim()}>Ask</button>
    </form>
  </div>`;
}

function Feed({ s, send, ask, profile, onCompare, onProof, speaking }) {
  const items = [
    ...s.moments.filter(m => m.ai || crewCovers(profile, m)).slice(0, 40).map(m => ({ kind: "m", at: m.at ?? 0, m })),
    ...s.qa.map(q => ({ kind: "q", at: q.at ?? 0, q })),
  ].sort((a, b) => b.at - a.at);
  return html`<div class="feedpanel">
    <div class="feedhead"><b>Live commentary</b><span class="muted">${LANGS[profile.language]} · ${profile.persona === "analyst" ? "tactics" : "the story"}</span></div>
    <div class="feed">
      ${s.recap && html`<${RecapCard} recap=${s.recap} />`}
      ${items.map(it => it.kind === "m"
        ? html`<${MomentCard} key=${it.m.id} m=${it.m} send=${send} onCompare=${onCompare} onProof=${onProof}
            yourTeam=${profile.favorite_team} waiting=${crewCovers(profile, it.m)} speaking=${speaking} />`
        : html`<${QACard} key=${it.q.id} q=${it.q} onProof=${onProof} />`)}
      <${KickoffCard} meta=${s.meta} />
      ${!items.length && !s.recap && html`<div class="empty">The big moments appear here as they happen, written by the AI crew for you.
        Or ask a question below.</div>`}
    </div>
    <${AskBar} s=${s} ask=${ask} />
  </div>`;
}

// ------------------------------------------------------------------ "How it works" (for the curious, and for judges)

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

function TrustReport() {
  const [t, setT] = useState(null);
  useEffect(() => { api("/static/app/trust.json").then(setT).catch(() => {}); }, []);
  if (!t) return null;
  const c = t.commentary;
  const tiles = [
    [`${t.unverified_ai_lines_shown} / ${t.ai_lines_audited}`, "AI lines shown that fail an independent re-check"],
    [`${c.drafts_caught ?? "–"}`, "bad drafts caught and fixed before anyone saw them"],
    [`${t.ask.verified_answers_pct}%`, `of ${t.ask.questions} fan questions answered and verified`],
    [`$${t.cost_per_match_usd}`, "AI cost per match"],
  ];
  return html`<section><h3>Measured, not claimed <span class="right">${t.matches} fresh matches</span></h3>
    <div class="trust">${tiles.map(([v, l]) => html`<div><b>${v}</b><span>${l}</span></div>`)}</div></section>`;
}

const PIPE = [["📡", "Event Hubs", "match events stream in"], ["📊", "Stats engine", "numbers & key moments (code)"],
  ["🧠", "Analyst", "why it matters", "analyst"], ["🎙️", "Commentator", "the line", "commentator"],
  ["✅", "Fact-checker", "every number verified", "fact_checker"], ["🎛️", "Producer", "made for you", "producer"],
  ["🔊", "Voice", "Azure AI Speech"]];

function HowItWorks({ s, matchId, onClose }) {
  const ai = s.lastAI, ran = new Set(ai ? ai.trace.map(x => x.agent) : []);
  const mcp = `${location.origin}/mcp/`;
  return html`<div class="drawer-bg" onClick=${onClose}><aside class="drawer" onClick=${e => e.stopPropagation()}>
    <button class="btn sm x" onClick=${onClose}>Close ✕</button>
    <h2>How Touchline works</h2>
    <p class="lead">Numbers come from code. Words come from AI. Every number the AI writes is checked against the stats before you see it.</p>
    <section><h3>The AI crew <span class="right">Microsoft Agent Framework on Foundry</span></h3>
      <div class="pipe" key=${ai ? ai.moment.id : "idle"}>${PIPE.map(([ic, name, what, agent], i) => html`
        <div class=${"node" + (agent && ran.has(agent) ? " lit" : "")} style=${{ "--d": `${i * 120}ms` }}><span>${ic}</span><b>${name}</b><small>${what}</small></div>`)}</div>
      ${ai ? html`<div class="muted" style="font-size:12px;margin:10px 0 6px">Last run: ${ai.moment.clock} · ${ai.moment.title} · ${(ai.trace.reduce((a, x) => a + x.ms, 0) / 1000).toFixed(1)}s</div>
        <${TraceSteps} trace=${ai.trace} />` : html`<div class="muted" style="font-size:12px;margin-top:8px">Play the match: each agent's step shows here as the crew covers a moment.</div>`}
      <p class="muted" style="font-size:12px">If a draft fails the fact-check, it goes back to its writer with the exact problem. After repeated failures the verified stats text is shown instead: the show goes on.</p>
    </section>
    <${TrustReport} />
    <section><h3>Ask the match</h3>
      <p class="muted" style="font-size:13px;margin:0 0 8px">A separate agent decides which stats to look up for each question, then the same fact-checker verifies its answer. It only knows the match up to now: no spoilers.</p>
      <div class="tools">${["match situation", "team stats", "player stats", "top players", "key moments"].map(t => html`<span class="tool">🔧 ${t}</span>`)}</div>
      <p class="muted" style="font-size:13px;margin:10px 0 4px">The same tools are open to any AI assistant over MCP:</p>
      <div class="copy"><code>${mcp}</code><button class="btn sm" onClick=${() => navigator.clipboard && navigator.clipboard.writeText(mcp)}>Copy</button></div>
    </section>
    <section><h3>For broadcasters <span class="right"><a href=${`/api/matches/${matchId}/cues`} target="_blank">all cues as JSON ↗</a></span></h3>
      <p class="muted" style="font-size:13px;margin:0 0 8px">The overlays on the pitch come from timed graphics cues a TV graphics engine can render.</p>
      <div class="cues">${s.cues.filter(c => c.type !== "player_tag").slice(0, 12).map(c => html`<div class="cue" key=${c.cue_id}>
        <span class="muted">${c.clock}</span><span class=${"k " + c.type}>${c.type}</span><span class="p">${cueText(c)}</span></div>`)}
        ${!s.cues.length && html`<div class="empty">Cues stream here during play.</div>`}</div>
    </section>
    <section><h3>Built with</h3><div class="built">${["Microsoft Foundry · gpt-5-mini, gpt-4.1-mini", "Microsoft Agent Framework", "Azure AI Speech",
      "Azure Event Hubs", "Azure App Service", "Managed identity (no keys)", "Model Context Protocol"].map(b => html`<span>${b}</span>`)}</div>
      <p class="muted" style="font-size:12px">All matches, clubs and players are synthetic and fictional.</p></section>
  </aside></div>`;
}

// ------------------------------------------------------------------ start screen

function Start({ matches, clubs, matchId, setMatchId, profile, setProfile, onGenerate, onKickOff }) {
  const club = id => clubs.find(c => c.id === id);
  const sel = matches.find(m => m.match_id === matchId);
  const home = sel && club(sel.home_id), away = sel && club(sel.away_id);
  const set = patch => setProfile({ ...profile, ...patch });
  return html`<main class="start">
    <div class="brand logo"><b>TOUCH<i>LINE</i></b></div>
    <div class="hero">
      <h1>Watch any match with an <em>AI broadcast crew</em> made for you.</h1>
      <p>Live commentary, voice and answers, in your language and from your side. Every number is fact-checked.</p>
    </div>
    <div class="q">1 · Pick a match</div>
    <div class="matches">
      ${matches.map(m => { const h = club(m.home_id), a = club(m.away_id); return html`
        <button class=${"mcard" + (m.match_id === matchId ? " on" : "")} onClick=${() => { setMatchId(m.match_id); set({ favorite_team: null }); }}>
          <div class="crests"><${Crest} club=${h} size=${34} /><span>v</span><${Crest} club=${a} size=${34} /></div>
          <b>${m.home} v ${m.away}</b><small>${tagline(m.scenario)}</small></button>`; })}
      <button class="mcard new" onClick=${onGenerate}><div class="plus">＋</div><b>Simulate a new match</b><small>any two clubs, any storyline</small></button>
    </div>
    <div class="q">2 · Watch as</div>
    <div class="choices">
      ${[home, away].filter(Boolean).map(t => html`<button class=${"choice" + (profile.favorite_team === t.id ? " on" : "")}
        onClick=${() => set({ favorite_team: t.id })}><${Crest} club=${t} size=${24} />${t.name} fan</button>`)}
      <button class=${"choice" + (!profile.favorite_team ? " on" : "")} onClick=${() => set({ favorite_team: null })}>👀 Neutral</button>
    </div>
    <div class="row2">
      <div><div class="q">3 · Language</div>
        <div class="choices">${Object.entries(LANGS).map(([k, v]) => html`<button class=${"choice" + (profile.language === k ? " on" : "")} onClick=${() => set({ language: k })}>${v}</button>`)}</div></div>
      <div><div class="q">4 · Style</div>
        <div class="choices">
          <button class=${"choice" + (profile.persona === "casual" ? " on" : "")} onClick=${() => set({ persona: "casual" })}><span>Just the story<small>drama and people</small></span></button>
          <button class=${"choice" + (profile.persona === "analyst" ? " on" : "")} onClick=${() => set({ persona: "analyst" })}><span>The tactics<small>xG, pressing, patterns</small></span></button>
        </div></div>
    </div>
    <button class="btn primary kick" disabled=${!matchId} onClick=${onKickOff}>▶ Kick off <small>with sound</small></button>
    <div class="foot">Synthetic matches between fictional clubs · Microsoft Foundry · Agent Framework · Azure AI Speech · Event Hubs</div>
  </main>`;
}

// ------------------------------------------------------------------ watch screen

function ProfileMenu({ meta, profile, setProfile, onClose }) {
  const set = patch => setProfile({ ...profile, ...patch });
  return html`<div class="pop-bg" onClick=${onClose}><div class="pop" onClick=${e => e.stopPropagation()}>
    <div class="q">Watch as</div><div class="choices">
      ${meta && [meta.home, meta.away].map(t => html`<button class=${"choice sm" + (profile.favorite_team === t.id ? " on" : "")} onClick=${() => set({ favorite_team: t.id })}>
        <${Crest} club=${t} size=${18} />${t.short}</button>`)}
      <button class=${"choice sm" + (!profile.favorite_team ? " on" : "")} onClick=${() => set({ favorite_team: null })}>Neutral</button></div>
    <div class="q">Language</div><div class="choices">${Object.entries(LANGS).map(([k, v]) => html`<button class=${"choice sm" + (profile.language === k ? " on" : "")} onClick=${() => set({ language: k })}>${v}</button>`)}</div>
    <div class="q">Style</div><div class="choices">
      <button class=${"choice sm" + (profile.persona === "casual" ? " on" : "")} onClick=${() => set({ persona: "casual" })}>Story</button>
      <button class=${"choice sm" + (profile.persona === "analyst" ? " on" : "")} onClick=${() => set({ persona: "analyst" })}>Tactics</button></div>
    <p class="muted" style="font-size:12px;margin:10px 0 0">Changes apply instantly, to the whole feed.</p>
  </div></div>`;
}

function Watch({ s, send, ask, matchId, summary, profile, setProfile, colors, voice, voiceOn, setVoiceOn, speaking, onBack, onCompare, onProof }) {
  const [how, setHow] = useState(false), [menu, setMenu] = useState(false);
  const meta = s.meta, team = teamOf(meta, profile.favorite_team);
  return html`<div class="app">
    <header class="top">
      <button class="brand" onClick=${onBack} title="Back to matches"><b>TOUCH<i>LINE</i></b></button>
      ${meta && html`<div class="mtitle"><${Crest} club=${meta.home} size=${22} /><b>${meta.home.name}</b><span class="muted">v</span>
        <b>${meta.away.name}</b><${Crest} club=${meta.away} size=${22} />${summary && html`<span class="muted tl">· ${tagline(summary.scenario)}</span>`}</div>`}
      <span class="spacer"></span>
      <button class="btn pchip" onClick=${() => setMenu(true)} title="Change how you watch">
        ${team ? html`<${Crest} club=${team} size=${18} />` : "👀"}<span>${team ? team.short + " fan" : "Neutral"} · ${LANGS[profile.language]} · ${profile.persona === "analyst" ? "Tactics" : "Story"}</span> ▾</button>
      ${voice && html`<button class=${"btn voice" + (voiceOn ? " on" : "")} onClick=${() => setVoiceOn(!voiceOn)} aria-pressed=${voiceOn}>${voiceOn ? "🔊 Voice" : "🔈 Voice off"}</button>`}
      <button class="btn how" onClick=${() => setHow(true)}>⚙ How it works</button>
    </header>
    <${Stage} s=${s} send=${send} colors=${colors} showLower=${true} onProof=${onProof} speaking=${speaking} side=${html`
      <${Feed} s=${s} send=${send} ask=${ask} profile=${profile} onCompare=${onCompare} onProof=${onProof} speaking=${speaking} />`} />
    ${menu && html`<${ProfileMenu} meta=${meta} profile=${profile} setProfile=${setProfile} onClose=${() => setMenu(false)} />`}
    ${how && html`<${HowItWorks} s=${s} matchId=${matchId} onClose=${() => setHow(false)} />`}
  </div>`;
}

function Stage({ s, send, colors, side, showLower, onProof, speaking }) {
  const ref = useRef(null), w = useStageFit(ref);
  return html`<main class="stage" ref=${ref} style=${w ? { gridTemplateColumns: `${w}px minmax(340px, 620px)` } : {}}>
    <div class="left">
      <${Broadcast} s=${s} colors=${colors} showLower=${showLower} onProof=${onProof} speaking=${speaking} />
      <${Transport} s=${s} send=${send} />
      <${StatStrip} s=${s} colors=${colors} />
    </div>
    <aside class="side">${side}</aside>
  </main>`;
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

function ProofModal({ ai, moment, onClose }) {
  const total = ai.trace.reduce((a, x) => a + x.ms, 0);
  const caught = ai.trace.filter(x => x.agent.includes("fact_checker") && x.note.startsWith("rejected")).length;
  return html`<${Modal} onClose=${onClose}>
    <h2>How this ${moment.type === "qa" ? "answer" : "line"} was made</h2>
    <div class="muted">${moment.type === "qa" ? "💬" : icon(moment)} ${moment.clock} · ${moment.title}</div>
    <div class="proof-line"><div class="cap">${ai.caption}</div><p>${ai.line}</p></div>
    <div class="proof-sum">
      <span><b>${ai.trace.filter(x => x.ms).length}</b> AI agent runs</span>
      <span><b>${(total / 1000).toFixed(1)}s</b> total</span>
      <span class=${caught ? "warn" : ""}><b>${caught}</b> ${caught === 1 ? "draft" : "drafts"} caught by the fact-checker</span>
      <span><b>${ai.source === "ai" ? "AI" : "Fallback"}</b> ${ai.source === "ai" ? "every number verified" : "verified stats text used"}</span>
    </div>
    ${ai.insight && ai.insight.why_it_matters && html`<p class="insight">🧠 “${ai.insight.why_it_matters}”</p>`}
    <${TraceSteps} trace=${ai.trace} />
    <p class="muted" style="font-size:12px;margin-top:14px">The fact-checker is plain code: any number or player name that isn't in the
      stats engine's facts sends the draft back for a rewrite.</p>
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
    return html`${t ? html`<${Crest} club=${t} size=${20} />` : "👀"}<span><b>${t ? t.name + " fan" : "Neutral"}</b> · ${p.persona === "analyst" ? "Tactics" : "Story"} · ${LANGS[p.language]}</span>`;
  };
  return html`<${Modal} onClose=${onClose} wide=${true}>
    <h2>One moment. Every fan.</h2>
    <div class="muted">${icon(moment)} ${moment.clock} · ${moment.title}${res ? ` · tailored for ${res.profiles.length} fans in ${(ms / 1000).toFixed(1)}s` : ""}</div>
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
    <h2>Simulate a new match</h2>
    <div class="muted">A full 90 minutes in under a second: realistic events calibrated to top-flight averages, between fictional clubs.</div>
    <div class="form">
      <label>Home<select class="select" value=${home} onChange=${e => setHome(e.target.value)}>${clubs.map(c => html`<option value=${c.id}>${c.name} · ${c.formation}</option>`)}</select></label>
      <label>Away<select class="select" value=${away} onChange=${e => setAway(e.target.value)}>${clubs.map(c => html`<option value=${c.id}>${c.name} · ${c.formation}</option>`)}</select></label>
    </div>
    <div class="scen">${scenarios.map(sc => html`<button class=${scenario === sc.id ? "on" : ""} onClick=${() => setScenario(sc.id)}>
      <b>${tagline(sc.id)}</b><small>${sc.description}</small></button>`)}</div>
    ${err && html`<div class="empty" style="color:var(--danger)">${err}</div>`}
    <div style="margin-top:18px"><button class="btn primary" disabled=${busy || home === away} onClick=${go}>${busy ? "Simulating…" : home === away ? "Pick two different clubs" : "Simulate ▶"}</button></div>
  <//>`;
}

// ------------------------------------------------------------------ app

function App() {
  const params = new URLSearchParams(location.search);
  const [screen, setScreen] = useState("start");
  const [matches, setMatches] = useState([]), [matchId, setMatchId] = useState(params.get("match"));
  const [clubs, setClubs] = useState([]), [scenarios, setScenarios] = useState([]), [voice, setVoice] = useState(false);
  const [profile, setProfile] = useState({ persona: params.get("style") === "tactics" ? "analyst" : "casual",
    language: LANGS[params.get("lang")] ? params.get("lang") : "en", favorite_team: params.get("team"), favorite_player: null });
  const [compare, setCompare] = useState(null), [gen, setGen] = useState(false), [proof, setProof] = useState(null), [voiceOn, setVoiceOn] = useState(false);
  const [s, send, ask] = useMatch(screen === "watch" ? matchId : null, profile);
  const colors = useMemo(() => teamColors(s.meta), [s.meta]);
  const speaking = useVoice(matchId, s.lastAI, voiceOn && screen === "watch");
  const onProof = useCallback((a, m) => setProof({ ai: a, moment: m }), []);
  const loadMatches = sel => api("/api/matches").then(list => { setMatches(list); setMatchId(id => sel || (list.find(m => m.match_id === id) ? id : list[0] && list[0].match_id)); });
  useEffect(() => {
    loadMatches();
    api("/api/clubs").then(setClubs); api("/api/scenarios").then(setScenarios); api("/api/health").then(hh => setVoice(hh.voice));
  }, []);
  useEffect(() => { // a shareable link to exactly this view
    const q = new URLSearchParams({ match: matchId || "", lang: profile.language, style: profile.persona === "analyst" ? "tactics" : "story" });
    if (profile.favorite_team) q.set("team", profile.favorite_team);
    history.replaceState(null, "", "?" + q);
  }, [matchId, profileKey(profile)]);
  const summary = matches.find(m => m.match_id === matchId);
  return html`<div>
    ${screen === "start" && html`<${Start} matches=${matches} clubs=${clubs} matchId=${matchId} setMatchId=${setMatchId} profile=${profile}
      setProfile=${setProfile} onGenerate=${() => setGen(true)} onKickOff=${() => { setVoiceOn(voice); setScreen("watch"); }} />`}
    ${screen === "watch" && html`<${Watch} s=${s} send=${send} ask=${ask} matchId=${matchId} summary=${summary} profile=${profile} setProfile=${setProfile}
      colors=${colors} voice=${voice} voiceOn=${voiceOn} setVoiceOn=${setVoiceOn} speaking=${speaking} onBack=${() => setScreen("start")}
      onCompare=${setCompare} onProof=${onProof} />`}
    ${proof && html`<${ProofModal} ai=${proof.ai} moment=${proof.moment} onClose=${() => setProof(null)} />`}
    ${compare && html`<${CompareModal} matchId=${matchId} moment=${compare} meta=${s.meta} onClose=${() => setCompare(null)} />`}
    ${gen && html`<${GenerateModal} clubs=${clubs} scenarios=${scenarios} onClose=${() => setGen(false)}
      onCreated=${id => { setGen(false); loadMatches(id); }} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("root"));
