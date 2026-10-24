// The 3D stadium view: the same match data as the 2D pitch, drawn as a stadium with broadcast cameras.
// Nothing here is invented: every player stands where shapeTargets() puts them and the ball goes where the
// event feed says, exactly like the 2D view. It only adds a third dimension and a camera director.
// Loaded on demand (dynamic import) so viewers who stay in 2D never download three.js.
import * as THREE from "three";

const PL = 105, PW = 68;
const X = x => x - PL / 2, Z = y => y - PW / 2;  // pitch metres -> scene (origin at the centre spot)
const GOAL_LINE = PL / 2 - 1.2;                  // the 2D markings sit 1.2 m inside the canvas edge
const BIG = new Set(["goal", "great_save", "big_chance_missed", "thunderbolt"]);
const ease = (dt, rate) => 1 - Math.exp(-dt * rate);

function canvasTexture(w, h, draw) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// A flat quad from four corners (inner-left, inner-right, outer-right, outer-left), for the sloped stands.
function quad(a, b, c, d, material) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute([...a, ...b, ...c, ...a, ...c, ...d], 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1], 2));
  g.computeVertexNormals();
  return new THREE.Mesh(g, material);
}

function crowdTexture(colors) {
  return canvasTexture(512, 128, (ctx, w, h) => {
    ctx.fillStyle = "#10141c"; ctx.fillRect(0, 0, w, h);
    const tones = ["#232a36", "#2b3240", "#1c212b", "#353b47", "#3d3530", ...colors];
    for (let row = 0; row < h; row += 6) {
      ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.fillRect(0, row + 5, w, 1);  // seat rows
      for (let x = 0; x < w; x += 4) {
        if (Math.random() < 0.18) continue;
        ctx.globalAlpha = 0.45 + Math.random() * 0.35;
        ctx.fillStyle = tones[(Math.random() * tones.length) | 0];
        ctx.fillRect(x, row + 1 + Math.random(), 3, 4);
      }
      ctx.globalAlpha = 1;
    }
  });
}

function boardTexture() {
  return canvasTexture(2048, 64, (ctx, w, h) => {
    ctx.fillStyle = "#070b14"; ctx.fillRect(0, 0, w, h);
    ctx.font = "800 40px 'Barlow Condensed', sans-serif"; ctx.textBaseline = "middle";
    const words = ["TOUCHLINE", "FACT-CHECKED AI COMMENTARY", "MICROSOFT FOUNDRY", "EVERY FAN, THEIR OWN BROADCAST"];
    let x = 20, i = 0;
    while (x < w) {
      ctx.fillStyle = i % 2 ? "#e6e8eb" : "#00e5a0";
      const word = words[i++ % words.length];
      ctx.fillText(word, x, h / 2 + 2);
      x += ctx.measureText(word).width + 70;
    }
  });
}

function netTexture() {
  const t = canvasTexture(64, 64, (ctx, w, h) => {
    ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 3;
    ctx.strokeRect(0, 0, w, h);
  });
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

function goal(side, netTex) {
  const g = new THREE.Group();
  const post = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.4 });
  const r = 0.12, hw = 3.66, ht = 2.44, depth = 2.2;
  for (const z of [-hw, hw]) {
    const p = new THREE.Mesh(new THREE.CylinderGeometry(r, r, ht, 12), post);
    p.position.set(0, ht / 2, z); p.castShadow = true; g.add(p);
  }
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(r, r, hw * 2 + r * 2, 12), post);
  bar.rotation.x = Math.PI / 2; bar.position.y = ht; bar.castShadow = true; g.add(bar);
  const net = (w, h, reps) => {
    const t = netTex.clone(); t.needsUpdate = true; t.repeat.set(...reps);
    return new THREE.MeshBasicMaterial({ map: t, transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false });
  };
  const back = new THREE.Mesh(new THREE.PlaneGeometry(hw * 2, ht), net(0, 0, [24, 8]));
  back.rotation.y = Math.PI / 2; back.position.set(depth, ht / 2, 0); g.add(back);
  const roof = new THREE.Mesh(new THREE.PlaneGeometry(depth, hw * 2), net(0, 0, [7, 24]));
  roof.rotation.x = Math.PI / 2; roof.position.set(depth / 2, ht, 0); g.add(roof);
  for (const z of [-hw, hw]) {
    const s = new THREE.Mesh(new THREE.PlaneGeometry(depth, ht), net(0, 0, [7, 8]));
    s.position.set(depth / 2, ht / 2, z); g.add(s);
  }
  g.position.x = side * GOAL_LINE;
  if (side < 0) g.rotation.y = Math.PI;
  return g;
}

function label(text, { bg = null, fg = "#ffffff", size = 1, font = "800 92px 'Barlow Condensed', sans-serif" } = {}) {
  const probe = document.createElement("canvas").getContext("2d"); probe.font = font;
  const w = Math.ceil(probe.measureText(text).width) + (bg ? 64 : 24), h = 128;
  const tex = canvasTexture(w, h, (ctx) => {
    ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    if (bg) { ctx.fillStyle = bg; ctx.beginPath(); ctx.roundRect(4, 14, w - 8, h - 28, 50); ctx.fill(); }
    else { ctx.lineWidth = 14; ctx.strokeStyle = "rgba(7,11,20,.9)"; ctx.strokeText(text, w / 2, h / 2 + 4); }
    ctx.fillStyle = fg; ctx.fillText(text, w / 2, h / 2 + 4);
  });
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  sprite.scale.set((w / h) * size, size, 1);
  sprite.renderOrder = 10;
  return sprite;
}

export function createStadium(canvas, { live, drawLines }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.3;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#05080f");
  scene.fog = new THREE.Fog("#05080f", 150, 300);
  const camera = new THREE.PerspectiveCamera(30, PL / PW, 0.5, 700);

  // floodlit night match
  scene.add(new THREE.HemisphereLight("#dfe9ff", "#0b2a18", 1.1));
  const sun = new THREE.DirectionalLight("#ffffff", 2.2);
  sun.position.set(-30, 90, 45); sun.castShadow = true;
  Object.assign(sun.shadow.camera, { left: -70, right: 70, top: 50, bottom: -50, near: 10, far: 220 });
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0005;
  scene.add(sun);

  // pitch: the very same markings as the 2D view, painted onto the grass
  const pitchTex = canvasTexture(2100, 1360, (ctx, w, h) => drawLines(ctx, w, h, false));
  pitchTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  const pitch = new THREE.Mesh(new THREE.PlaneGeometry(PL, PW), new THREE.MeshStandardMaterial({ map: pitchTex, roughness: 0.95 }));
  pitch.rotation.x = -Math.PI / 2; pitch.receiveShadow = true; scene.add(pitch);
  const apron = new THREE.Mesh(new THREE.PlaneGeometry(PL + 50, PW + 46), new THREE.MeshStandardMaterial({ color: "#0b3a22", roughness: 1 }));
  apron.rotation.x = -Math.PI / 2; apron.position.y = -0.03; apron.receiveShadow = true; scene.add(apron);

  // advertising boards around the pitch
  const boardTex = boardTexture(); boardTex.wrapS = THREE.RepeatWrapping;
  const boardMat = (len) => { const t = boardTex.clone(); t.needsUpdate = true; t.repeat.set(len / 32, 1);  // keep the 32:1 artwork unstretched on a 1 m board
    return new THREE.MeshBasicMaterial({ map: t }); };
  for (const [len, x, z, ry] of [[PL + 8, 0, -(PW / 2 + 4), 0], [PL + 8, 0, PW / 2 + 4, Math.PI],
                                  [PW + 8, -(PL / 2 + 5), 0, Math.PI / 2], [PW + 8, PL / 2 + 5, 0, -Math.PI / 2]]) {
    const b = new THREE.Mesh(new THREE.PlaneGeometry(len, 1), boardMat(len));
    b.position.set(x, 0.5, z); b.rotation.y = ry; scene.add(b);
  }

  // stands: four sloped banks of crowd
  let crowdColors = null;
  const standMats = [];
  const stands = new THREE.Group(); scene.add(stands);
  const buildStands = colors => {
    stands.clear(); standMats.forEach(m => { m.map.dispose(); m.dispose(); }); standMats.length = 0;
    const mat = (rep) => { const t = crowdTexture(colors); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(rep, 3);
      const m = new THREE.MeshStandardMaterial({ map: t, roughness: 1, side: THREE.DoubleSide }); standMats.push(m); return m; };
    const lx = PL / 2 + 12, lz = PW / 2 + 8;
    // sides (along the touchlines), then ends (behind the goals)
    for (const s of [-1, 1]) {
      stands.add(quad([-lx, 1.5, s * lz], [lx, 1.5, s * lz], [lx, 24, s * (lz + 26)], [-lx, 24, s * (lz + 26)], mat(5)));
      stands.add(quad([s * (PL / 2 + 9), 1.5, -lz], [s * (PL / 2 + 9), 1.5, lz], [s * (PL / 2 + 33), 22, lz], [s * (PL / 2 + 33), 22, -lz], mat(3)));
    }
    // floodlight towers in the corners
    const pole = new THREE.MeshStandardMaterial({ color: "#3a4150" });
    const lamp = new THREE.MeshBasicMaterial({ color: "#fffbe8" });
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      const p = new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.9, 46, 8), pole);
      p.position.set(sx * (PL / 2 + 30), 23, sz * (PW / 2 + 28)); stands.add(p);
      const l = new THREE.Mesh(new THREE.BoxGeometry(7, 4, 1), lamp);
      l.position.set(sx * (PL / 2 + 30), 46, sz * (PW / 2 + 28)); l.lookAt(0, 0, 0); stands.add(l);
    }
  };

  const netTex = netTexture();
  scene.add(goal(1, netTex), goal(-1, netTex));

  // ball, with a little shadow-catching flight
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.32, 20, 14), new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.35, emissive: "#333333" }));
  ball.castShadow = true; scene.add(ball);
  const flight = { from: [PL / 2, PW / 2], to: [PL / 2, PW / 2], t: 1, dur: 1, peak: 0, key: "" };

  // recent ball path, faint, on the grass (like the 2D trail)
  const trail = new THREE.Group(); scene.add(trail);
  let trailRef = null;

  // players
  const bodyGeo = new THREE.CapsuleGeometry(0.42, 0.75, 4, 12);
  const shortsGeo = new THREE.CylinderGeometry(0.43, 0.4, 0.5, 12);
  const headGeo = new THREE.SphereGeometry(0.25, 14, 10);
  const skin = new THREE.MeshStandardMaterial({ color: "#c99a78", roughness: 0.8 });
  const ringGeo = new THREE.RingGeometry(0.95, 1.25, 32); ringGeo.rotateX(-Math.PI / 2);
  const ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.85 }));
  ring.position.y = 0.03; scene.add(ring);
  const avatars = {};
  const pickables = [];
  let holderTag = null, holderId = null;

  const avatar = (id, who, col, gk) => {
    const g = new THREE.Group();
    const shirt = new THREE.MeshStandardMaterial({ color: col, roughness: 0.6 });
    const body = new THREE.Mesh(bodyGeo, shirt); body.position.y = 1.35; body.castShadow = true;
    const dark = new THREE.Color(col).getHSL({}).l > 0.6;
    const shorts = new THREE.Mesh(shortsGeo, new THREE.MeshStandardMaterial({ color: dark ? "#1b2333" : "#f2f2f2", roughness: 0.7 }));
    shorts.position.y = 0.75; shorts.castShadow = true;
    const legs = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.22, 0.6, 8), skin); legs.position.y = 0.3; legs.castShadow = true;
    const head = new THREE.Mesh(headGeo, skin); head.position.y = 2.2; head.castShadow = true;
    const num = label(String(who.number), { size: 1.0 }); num.position.y = 2.95;
    g.add(body, shorts, legs, head, num);
    g.scale.setScalar(1.2);
    for (const m of [body, shorts, legs, head]) { m.userData.id = id; pickables.push(m); }
    scene.add(g);
    return { g, shirt, col, x: null, z: null, gk, phase: Math.random() * 6 };
  };

  // camera director
  const cam = { pos: new THREE.Vector3(0, 30, 70), look: new THREE.Vector3(), fov: 30, mode: null, cut: null, lastMoment: null, side: 1, snap: true };

  const shot = (mode, bx, bz, poss, s) => {
    const homeAttacks = s.meta && poss === s.meta.home.id ? 1 : -1;
    switch (mode) {
      case "goal": {
        const side = cam.cut ? cam.cut.side : cam.side;
        return { pos: [side * (PL / 2 + 17), 9, bz * 0.1], look: [side * 30, 0, bz * 0.3], fov: 40 };
      }
      case "follow":
        return { pos: [bx - homeAttacks * 19, 12, bz * 0.8], look: [bx + homeAttacks * 10, 0, bz * 0.9], fov: 48 };
      case "top":
        return { pos: [0, 88, 50], look: [0, 0, 1], fov: 45 };
      default:  // tv: the main broadcast camera high on the halfway line, panning with play
        return { pos: [bx * 0.55, 33, PW / 2 + 40], look: [bx * 0.85, 0, bz * 0.3 + 1], fov: 33 };
    }
  };

  const raycaster = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const onClick = e => {
    const { onPlayer } = live();
    if (!onPlayer) return;
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    if (hit) onPlayer(hit.object.userData.id);
  };
  canvas.addEventListener("click", onClick);

  let raf, prev = performance.now(), clock = 0;
  const frame = now => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min((now - prev) / 1000, 0.1); prev = now; clock += dt;
    const { s, colors, people, targets, camera: wanted } = live();
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    const size = renderer.getSize(new THREE.Vector2());
    if (size.x !== w || size.y !== h) { renderer.setSize(w, h, false); camera.aspect = w / h; }

    if (s.meta) {
      const cc = [colors[s.meta.home.id], colors[s.meta.away.id]];
      if (!crowdColors || cc.join() !== crowdColors) { crowdColors = cc.join(); buildStands(cc.filter(Boolean)); }
    }

    // players ease toward their targets, exactly like the 2D dots
    const k = ease(dt, 3.2);
    for (const [id, a] of Object.entries(avatars)) if (!targets[id]) a.g.visible = false;
    for (const [id, tg] of Object.entries(targets)) {
      const who = people[id];
      if (!who) continue;
      const col = tg.gk ? "#e8e8e8" : colors[tg.team] || "#ffffff";
      let a = avatars[id];
      if (!a) a = avatars[id] = avatar(id, who, col, tg.gk);
      if (a.col !== col) { a.shirt.color.set(col); a.col = col; }
      a.g.visible = true;
      const tx = X(tg.pos[0]), tz = Z(tg.pos[1]);
      if (a.x === null) { a.x = tx; a.z = tz; }
      const ox = a.x, oz = a.z;
      a.x += (tx - a.x) * k; a.z += (tz - a.z) * k;
      const speed = Math.hypot(a.x - ox, a.z - oz) / Math.max(dt, 1e-3);
      a.phase += dt * (2 + Math.min(speed, 8) * 1.6);
      const sway = 0.3;  // idle drift, same as the 2D view
      a.g.position.set(a.x + Math.sin(clock * 0.9 + (who.number || 0)) * sway, Math.abs(Math.sin(a.phase)) * Math.min(speed, 6) * 0.025,
                       a.z + Math.cos(clock * 0.7 + (who.number || 0) * 1.7) * sway);
    }

    // the ball flies from where it was to where the latest event put it
    const last = s.last;
    const bt = last ? (last.end || last.start) : [PL / 2, PW / 2];
    const key = bt.join(",") + (last ? last.type : "");
    if (key !== flight.key) {
      const from = flight.t >= 1 ? flight.to : [ball.position.x + PL / 2, ball.position.z + PW / 2];
      const d = Math.hypot(bt[0] - from[0], bt[1] - from[1]);
      const ground = last && ["carry", "dribble", "pressure", "ball_recovery", "tackle", "interception"].includes(last.type);
      Object.assign(flight, { key, from, to: bt, t: flight.key ? 0 : 1, dur: Math.min(Math.max(d / 26, 0.25), 1.1),
        peak: ground ? 0 : d > 28 ? Math.min(d * 0.13, 7) : last && last.type === "shot" ? 0.8 : 0.15 });
      // Auto: every shot cuts to the camera behind the goal it is aimed at, so the finish is seen live
      if (last && last.type === "shot" && flight.t === 0) {
        const until = now + 4500;
        if (!cam.cut || cam.cut.until < until) cam.cut = { side: bt[0] > PL / 2 ? 1 : -1, until };
      }
    }
    flight.t = Math.min(1, flight.t + dt / flight.dur);
    const f = 1 - (1 - flight.t) ** 2;
    const bx = X(flight.from[0] + (flight.to[0] - flight.from[0]) * f), bz = Z(flight.from[1] + (flight.to[1] - flight.from[1]) * f);
    ball.position.set(bx, 0.32 + 4 * flight.peak * flight.t * (1 - flight.t), bz);
    ball.rotation.z -= dt * 6; ball.rotation.x += dt * 4;

    if (s.trail !== trailRef) {
      trailRef = s.trail;
      trail.children.forEach(c => { c.geometry.dispose(); c.material.dispose(); });
      trail.clear();
      s.trail.forEach((t, i) => {
        if (!t.end) return;
        const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(X(t.start[0]), 0.06, Z(t.start[1])), new THREE.Vector3(X(t.end[0]), 0.06, Z(t.end[1]))]);
        trail.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: colors[t.team] || "#ffffff", transparent: true, opacity: 0.15 + (0.5 * (i + 1)) / s.trail.length })));
      });
    }

    // the player on the ball: a ring at their feet and their name overhead
    const holder = last && avatars[last.player] && avatars[last.player].g.visible ? avatars[last.player] : null;
    ring.visible = !!holder;
    if (holder) ring.position.set(holder.g.position.x, 0.03, holder.g.position.z);
    if ((holder ? last.player : null) !== holderId) {
      if (holderTag) { scene.remove(holderTag); holderTag.material.map.dispose(); holderTag.material.dispose(); holderTag = null; }
      holderId = holder ? last.player : null;
      if (holder) {
        holderTag = label(people[holderId].name.split(" ").slice(-1)[0],
          { bg: "rgba(7,11,20,.88)", size: 1.5, font: "600 64px Inter, sans-serif" });
        scene.add(holderTag);
      }
    }
    if (holderTag && holder) holderTag.position.set(holder.g.position.x, 5.1, holder.g.position.z);

    // director: in Auto, big moments cut to the camera behind that goal for a few seconds
    const top = s.moments[0];
    if (top && top.id !== cam.lastMoment) {
      const fresh = cam.lastMoment !== null && !top.past;
      cam.lastMoment = top.id;
      if (fresh && BIG.has(top.type) && s.meta) {
        const attacking = top.team_id === s.meta.home.id ? 1 : -1;  // home attacks to the right
        cam.cut = { side: top.type === "great_save" ? -attacking : attacking,  // a save belongs to the keeper's team
                     until: now + (top.type === "goal" ? 8000 : 5000) };
      }
    }
    if (cam.cut && now > cam.cut.until) cam.cut = null;
    if (Math.abs(bx) > 12) cam.side = Math.sign(bx);  // behind-the-goal view picks the end the ball is at
    const mode = wanted === "auto" ? (cam.cut ? "goal" : "tv") : wanted;
    if (mode !== cam.mode) { cam.snap = cam.mode === null || wanted === "auto"; cam.mode = mode; }  // Auto cuts like TV; buttons glide
    const poss = last ? last.team : s.meta && s.meta.home.id;
    const want = shot(mode, bx, bz, poss, s);
    const ce = cam.snap ? 1 : ease(dt, mode === "follow" ? 3 : 2);
    cam.pos.lerp(new THREE.Vector3(...want.pos), ce);
    cam.look.lerp(new THREE.Vector3(...want.look), cam.snap ? 1 : ease(dt, 3));
    cam.fov += (want.fov - cam.fov) * ce;
    cam.snap = false;
    camera.position.copy(cam.pos); camera.lookAt(cam.look);
    if (Math.abs(camera.fov - cam.fov) > 0.01) { camera.fov = cam.fov; }
    camera.updateProjectionMatrix();

    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  return {
    dispose() {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("click", onClick);
      scene.traverse(o => {
        if (o.geometry) o.geometry.dispose();
        const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
        mats.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
      });
      renderer.dispose();
    },
  };
}
