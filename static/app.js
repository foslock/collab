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
  const ACTIVITY_TIMEOUT = 10; // seconds — must match server's ACTIVITY_TIMEOUT

  // Drawing state
  let drawing = false;
  let currentPoints = [];

  // Persisted lines: array of { id, session_id, color, points }
  let lines = [];

  // In-progress remote draws: session_id -> { color, points }
  const remoteDraws = {};

  // Remote cursors: session_id -> { name, color, x, y, el }
  const remoteCursors = {};

  // Persistent pill elements: session_id -> pill DOM element (preserves animation state)
  const userPills = {};

  // Activity tracking: session_id -> last activity timestamp (Date.now())
  const userActivity = {};

  // Max visible user pills before showing "more" dropdown
  const MAX_VISIBLE_USERS = 5;

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
  function isActive(sid) {
    const last = userActivity[sid];
    if (!last) return false;
    return (Date.now() - last) < ACTIVITY_TIMEOUT * 1000;
  }

  function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function markActive(sid) {
    const wasActive = isActive(sid);
    userActivity[sid] = Date.now();
    // Only re-render when transitioning inactive -> active; avoids resetting the
    // CSS animation on every cursor-move message while the user is already active.
    if (!wasActive) {
      renderUsers(buildUserList());
    }
  }

  function buildUserList() {
    return Object.entries(remoteCursors).map(([sid, c]) => ({
      session_id: sid, name: c.name, color: c.color,
    }));
  }

  function renderUsers(userList) {
    const others = userList.filter(u => u.session_id !== sessionId);

    // Sort: active users first (most recently active first), then inactive
    others.sort((a, b) => {
      const aActive = isActive(a.session_id);
      const bActive = isActive(b.session_id);
      if (aActive && !bActive) return -1;
      if (!aActive && bActive) return 1;
      const aTime = userActivity[a.session_id] || 0;
      const bTime = userActivity[b.session_id] || 0;
      return bTime - aTime;
    });

    const visible = others.slice(0, MAX_VISIBLE_USERS);
    const overflow = others.slice(MAX_VISIBLE_USERS);

    // Drop pills for users no longer in the visible set (gone or moved to overflow).
    // We intentionally recreate overflow pills on demand so we only persist
    // animation state for the pills that are actually shown in the bar.
    const visibleSids = new Set(visible.map(u => u.session_id));
    for (const sid of Object.keys(userPills)) {
      if (!visibleSids.has(sid)) {
        userPills[sid].remove();
        delete userPills[sid];
      }
    }

    // Remove the overflow "more" button so we can re-append it at the end
    const existingMore = usersEl.querySelector(".user-more-btn");
    if (existingMore) existingMore.remove();

    // Update or create each visible pill, then move it to the end of usersEl.
    // appendChild on an already-attached node moves it without removing it first,
    // so the CSS animation is NOT reset when reordering existing pills.
    for (const u of visible) {
      let pill = userPills[u.session_id];
      if (!pill) {
        pill = createPill(u);
        userPills[u.session_id] = pill;
      } else {
        updatePillState(pill, u);
      }
      usersEl.appendChild(pill);
    }

    if (overflow.length > 0) {
      const moreBtn = document.createElement("div");
      moreBtn.className = "user-pill user-more-btn";
      moreBtn.textContent = "+" + overflow.length + " more";
      moreBtn.onclick = function (e) {
        e.stopPropagation();
        toggleOverflowDropdown(overflow, moreBtn);
      };
      usersEl.appendChild(moreBtn);
    }
  }

  function createPill(u) {
    const active = isActive(u.session_id);
    const pill = document.createElement("div");
    pill.className = "user-pill" + (active ? " pulse" : " faded");
    pill.style.setProperty("--pulse-color", hexToRgba(u.color, 0.45));
    pill.innerHTML = '<span class="dot" style="background:' + u.color + '"></span>' + u.name;
    return pill;
  }

  function updatePillState(pill, u) {
    const active = isActive(u.session_id);
    pill.classList.toggle("pulse", active);
    pill.classList.toggle("faded", !active);
  }

  function toggleOverflowDropdown(users, anchorEl) {
    var existing = document.getElementById("user-overflow-dropdown");
    if (existing) { existing.remove(); return; }

    var dropdown = document.createElement("div");
    dropdown.id = "user-overflow-dropdown";
    for (var i = 0; i < users.length; i++) {
      dropdown.appendChild(createPill(users[i]));
    }
    anchorEl.style.position = "relative";
    anchorEl.appendChild(dropdown);

    var close = function (e) {
      if (!dropdown.contains(e.target) && e.target !== anchorEl) {
        dropdown.remove();
        document.removeEventListener("click", close);
      }
    };
    setTimeout(function () { document.addEventListener("click", close); }, 0);
  }

  // Periodically re-render to update active/faded states
  setInterval(function () { renderUsers(buildUserList()); }, 3000);

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

  const helpOverlay = document.getElementById("help-overlay");
  document.getElementById("btn-help").onclick = () => helpOverlay.hidden = false;
  document.getElementById("help-close").onclick = () => helpOverlay.hidden = true;
  helpOverlay.addEventListener("click", (e) => { if (e.target === helpOverlay) helpOverlay.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") helpOverlay.hidden = true; });

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
          for (const u of msg.users) {
            if (u.session_id !== sessionId) {
              ensureCursorEl(u.session_id, u.name, u.color);
              updateCursorPos(u.session_id, u.x, u.y);
            }
          }
          renderUsers(buildUserList());
          break;

        case "user_joined":
          ensureCursorEl(msg.session_id, msg.name, msg.color);
          markActive(msg.session_id);
          break;

        case "user_left":
          removeCursor(msg.session_id);
          delete remoteDraws[msg.session_id];
          delete userActivity[msg.session_id];
          renderUsers(buildUserList());
          draw();
          break;

        case "cursor_move":
          ensureCursorEl(msg.session_id, msg.name, msg.color);
          updateCursorPos(msg.session_id, msg.x, msg.y);
          markActive(msg.session_id);
          break;

        case "draw_start":
          remoteDraws[msg.session_id] = {
            color: msg.color,
            points: [{ x: msg.x, y: msg.y }],
          };
          markActive(msg.session_id);
          draw();
          break;

        case "draw_move":
          if (remoteDraws[msg.session_id]) {
            remoteDraws[msg.session_id].points.push({ x: msg.x, y: msg.y });
            draw();
          }
          markActive(msg.session_id);
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
          markActive(msg.session_id);
          draw();
          break;

        case "lines_deleted":
          lines = lines.filter(l => l.session_id !== msg.session_id);
          draw();
          break;

        case "rate_limited":
          showRateLimitAlert(msg.retry_after);
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

  // ── Rate limit alert ──────────────────────────────────────────────────
  function showRateLimitAlert(retryAfter) {
    // Don't stack alerts
    if (document.getElementById("rate-limit-alert")) return;
    const alert = document.createElement("div");
    alert.id = "rate-limit-alert";
    alert.textContent = "You\u2019re drawing too fast! Please wait " + retryAfter + "s before drawing again.";
    document.body.appendChild(alert);
    setTimeout(function () { alert.remove(); }, 5000);
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
