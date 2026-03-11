// ── Collab Canvas — client ────────────────────────────────────────────────

(() => {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────────
  let sessionId = null;
  let myName = "";
  let myColor = "#ffffff";
  let ws = null;

  // Canvas transform (pan / zoom)
  let camX = 0, camY = 0, zoom = 1;
  const MIN_ZOOM = 0.1, MAX_ZOOM = 5;

  // Drawing state
  let drawing = false;
  let currentPoints = [];

  // Persisted lines: array of { id, session_id, color, points }
  let lines = [];

  // In-progress remote draws: session_id -> { color, points }
  const remoteDraws = {};

  // Remote cursors: session_id -> { name, color, x, y, el }
  const remoteCursors = {};

  // Panning
  let panning = false;
  let panStartX = 0, panStartY = 0;

  // ── DOM refs ───────────────────────────────────────────────────────────
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  const cursorLayer = document.getElementById("cursor-layer");
  const nameEl = document.getElementById("user-name");
  const dotEl = document.getElementById("color-dot");
  const usersEl = document.getElementById("active-users");

  // ── Resize ─────────────────────────────────────────────────────────────
  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    draw();
  }
  window.addEventListener("resize", resize);
  resize();

  // ── Coordinate helpers ────────────────────────────────────────────────
  function screenToWorld(sx, sy) {
    return { x: (sx - camX) / zoom, y: (sy - camY) / zoom };
  }
  function worldToScreen(wx, wy) {
    return { x: wx * zoom + camX, y: wy * zoom + camY };
  }

  // ── Drawing ────────────────────────────────────────────────────────────
  function drawLine(pts, color) {
    if (pts.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(pts[0].x * zoom + camX, pts[0].y * zoom + camY);
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(pts[i].x * zoom + camX, pts[i].y * zoom + camY);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(2, 3 * zoom);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  function drawGrid() {
    const step = 50 * zoom;
    const offX = camX % step;
    const offY = camY % step;

    ctx.strokeStyle = "rgba(255,255,255,0.04)";
    ctx.lineWidth = 1;

    for (let x = offX; x < canvas.width; x += step) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }
    for (let y = offY; y < canvas.height; y += step) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(canvas.width, y);
      ctx.stroke();
    }
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // background
    ctx.fillStyle = "#1a1a2e";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    drawGrid();

    // persisted lines
    for (const line of lines) {
      drawLine(line.points, line.color);
    }

    // remote in-progress draws
    for (const sid of Object.keys(remoteDraws)) {
      const rd = remoteDraws[sid];
      drawLine(rd.points, rd.color);
    }

    // current local draw
    if (drawing && currentPoints.length > 0) {
      drawLine(currentPoints, myColor);
    }
  }

  // ── Remote cursors (DOM) ──────────────────────────────────────────────
  function ensureCursorEl(sid, name, color) {
    if (remoteCursors[sid]) return remoteCursors[sid].el;
    const el = document.createElement("div");
    el.className = "remote-cursor";
    el.innerHTML = `
      <svg viewBox="0 0 24 24" fill="${color}" stroke="#000" stroke-width="1">
        <path d="M5 3l14 8-6.5 1.5L11 19z"/>
      </svg>
      <span class="cursor-label" style="background:${color}">${name}</span>`;
    cursorLayer.appendChild(el);
    remoteCursors[sid] = { name, color, x: 0, y: 0, el };
    return el;
  }

  function updateCursorPos(sid, wx, wy) {
    const c = remoteCursors[sid];
    if (!c) return;
    c.x = wx;
    c.y = wy;
    const s = worldToScreen(wx, wy);
    c.el.style.left = s.x + "px";
    c.el.style.top = s.y + "px";
  }

  function removeCursor(sid) {
    const c = remoteCursors[sid];
    if (c) {
      c.el.remove();
      delete remoteCursors[sid];
    }
  }

  function repositionAllCursors() {
    for (const sid of Object.keys(remoteCursors)) {
      const c = remoteCursors[sid];
      const s = worldToScreen(c.x, c.y);
      c.el.style.left = s.x + "px";
      c.el.style.top = s.y + "px";
    }
  }

  // ── User list UI ──────────────────────────────────────────────────────
  function renderUsers(userList) {
    usersEl.innerHTML = "";
    for (const u of userList) {
      if (u.session_id === sessionId) continue;
      const pill = document.createElement("div");
      pill.className = "user-pill";
      pill.innerHTML = `<span class="dot" style="background:${u.color}"></span>${u.name}`;
      usersEl.appendChild(pill);
    }
  }

  // ── Input handling ────────────────────────────────────────────────────

  // Throttle cursor sends
  let lastCursorSend = 0;
  const CURSOR_THROTTLE = 30; // ms

  canvas.addEventListener("pointerdown", (e) => {
    if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
      // middle-click or shift+click to pan
      panning = true;
      panStartX = e.clientX - camX;
      panStartY = e.clientY - camY;
      canvas.style.cursor = "grabbing";
      return;
    }

    if (e.button === 0) {
      drawing = true;
      const pt = screenToWorld(e.clientX, e.clientY);
      currentPoints = [pt];
      wsSend({ type: "draw_start", x: pt.x, y: pt.y });
    }
  });

  canvas.addEventListener("pointermove", (e) => {
    if (panning) {
      camX = e.clientX - panStartX;
      camY = e.clientY - panStartY;
      repositionAllCursors();
      draw();
      return;
    }

    const pt = screenToWorld(e.clientX, e.clientY);

    // throttle cursor broadcasts
    const now = performance.now();
    if (now - lastCursorSend > CURSOR_THROTTLE) {
      wsSend({ type: "cursor_move", x: pt.x, y: pt.y });
      lastCursorSend = now;
    }

    if (drawing) {
      currentPoints.push(pt);
      wsSend({ type: "draw_move", x: pt.x, y: pt.y });
      draw();
    }
  });

  function endDraw() {
    if (panning) {
      panning = false;
      canvas.style.cursor = "crosshair";
      return;
    }
    if (!drawing) return;
    drawing = false;

    if (currentPoints.length >= 2) {
      // Add to local lines immediately so the line stays visible
      lines.push({
        id: null,
        session_id: sessionId,
        color: myColor,
        points: currentPoints,
      });
      wsSend({ type: "draw_end", points: currentPoints });
    }
    currentPoints = [];
    draw();
  }

  canvas.addEventListener("pointerup", endDraw);
  canvas.addEventListener("pointerleave", endDraw);

  // Zoom with scroll wheel
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const oldZoom = zoom;
    const delta = -e.deltaY * 0.001;
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom + delta * zoom));

    // Zoom toward pointer position
    const mx = e.clientX, my = e.clientY;
    camX = mx - (mx - camX) * (zoom / oldZoom);
    camY = my - (my - camY) * (zoom / oldZoom);

    repositionAllCursors();
    draw();
  }, { passive: false });

  // Button controls
  document.getElementById("btn-zoom-in").onclick = () => {
    const cx = canvas.width / 2, cy = canvas.height / 2;
    const oldZoom = zoom;
    zoom = Math.min(MAX_ZOOM, zoom * 1.3);
    camX = cx - (cx - camX) * (zoom / oldZoom);
    camY = cy - (cy - camY) * (zoom / oldZoom);
    repositionAllCursors();
    draw();
  };

  document.getElementById("btn-zoom-out").onclick = () => {
    const cx = canvas.width / 2, cy = canvas.height / 2;
    const oldZoom = zoom;
    zoom = Math.max(MIN_ZOOM, zoom / 1.3);
    camX = cx - (cx - camX) * (zoom / oldZoom);
    camY = cy - (cy - camY) * (zoom / oldZoom);
    repositionAllCursors();
    draw();
  };

  document.getElementById("btn-reset").onclick = () => {
    zoom = 1; camX = 0; camY = 0;
    repositionAllCursors();
    draw();
  };

  document.getElementById("btn-delete").onclick = () => {
    if (!confirm("Delete all your drawings?")) return;
    wsSend({ type: "delete_my_lines" });
  };

  // ── WebSocket ─────────────────────────────────────────────────────────

  function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws?session_id=${sessionId}`);

    ws.onopen = () => console.log("WS connected");

    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);

      switch (msg.type) {
        case "users_list":
          renderUsers(msg.users);
          for (const u of msg.users) {
            if (u.session_id !== sessionId) {
              ensureCursorEl(u.session_id, u.name, u.color);
              updateCursorPos(u.session_id, u.x, u.y);
            }
          }
          break;

        case "user_joined":
          ensureCursorEl(msg.session_id, msg.name, msg.color);
          // refresh pill list
          renderUsers(
            Object.entries(remoteCursors).map(([sid, c]) => ({
              session_id: sid, name: c.name, color: c.color,
            }))
          );
          break;

        case "user_left":
          removeCursor(msg.session_id);
          delete remoteDraws[msg.session_id];
          renderUsers(
            Object.entries(remoteCursors).map(([sid, c]) => ({
              session_id: sid, name: c.name, color: c.color,
            }))
          );
          draw();
          break;

        case "cursor_move":
          ensureCursorEl(msg.session_id, msg.name, msg.color);
          updateCursorPos(msg.session_id, msg.x, msg.y);
          break;

        case "draw_start":
          remoteDraws[msg.session_id] = {
            color: msg.color,
            points: [{ x: msg.x, y: msg.y }],
          };
          draw();
          break;

        case "draw_move":
          if (remoteDraws[msg.session_id]) {
            remoteDraws[msg.session_id].points.push({ x: msg.x, y: msg.y });
            draw();
          }
          break;

        case "draw_end":
          delete remoteDraws[msg.session_id];
          if (msg.points && msg.points.length >= 2) {
            lines.push({
              id: msg.line_id,
              session_id: msg.session_id,
              color: msg.color,
              points: msg.points,
            });
          }
          draw();
          break;

        case "lines_deleted":
          lines = lines.filter(l => l.session_id !== msg.session_id);
          draw();
          break;
      }
    };

    ws.onclose = () => {
      console.log("WS closed, reconnecting in 2s…");
      setTimeout(connectWS, 2000);
    };

    ws.onerror = (err) => {
      console.error("WS error", err);
      ws.close();
    };
  }

  // ── Bootstrap ─────────────────────────────────────────────────────────

  async function init() {
    // Check for existing session
    const stored = localStorage.getItem("collab_session_id");
    const params = new URLSearchParams();
    if (stored) params.set("session_id", stored);

    const res = await fetch(`/api/session?${params}`);
    const data = await res.json();

    sessionId = data.session_id;
    myName = data.name;
    myColor = data.color;

    localStorage.setItem("collab_session_id", sessionId);

    nameEl.textContent = myName;
    dotEl.style.background = myColor;

    // Load persisted lines
    const linesRes = await fetch("/api/lines");
    lines = await linesRes.json();
    draw();

    // Connect WebSocket
    connectWS();
  }

  init();
})();
