// ── Collab Canvas — client ────────────────────────────────────────────────

(() => {
  "use strict";

  // ── State ──────────────────────────────────────────────────────────────
  let sessionId = null;
  let myName = "";
  let myColor = "#ffffff";
  let ws = null;

  // Canvas (multi-canvas)
  let currentCanvasHash = null;
  let isCanvasOwner = false;
  let ownerSessionId = null;

  // Canvas transform (pan / zoom)
  let camX = 0, camY = 0, zoom = 1;
  const MIN_ZOOM = 0.1, MAX_ZOOM = 5;
  const ACTIVITY_TIMEOUT = 10; // seconds — must match server's ACTIVITY_TIMEOUT

  // Drawing state
  let drawing = false;
  let currentPoints = [];

  // Persisted lines: array of { id, session_id, color, points }
  let lines = [];

  // Lines currently fading out: { color, points, start, duration }
  let fadingLines = [];
  const FADE_DURATION = 350; // ms

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
  const canvasHashLabel = document.getElementById("canvas-hash-label");
  const canvasHashItem = document.getElementById("canvas-hash-item");
  const clearCanvasBtn = document.getElementById("btn-clear-canvas");

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
  function fadeOutLines(toFade) {
    if (toFade.length === 0) return;
    const now = performance.now();
    for (const line of toFade) {
      fadingLines.push({ color: line.color, points: line.points, start: now, duration: FADE_DURATION });
    }
    requestAnimationFrame(animateFades);
  }

  function animateFades() {
    if (fadingLines.length === 0) return;
    const now = performance.now();
    fadingLines = fadingLines.filter(f => now - f.start < f.duration);
    draw();
    if (fadingLines.length > 0) requestAnimationFrame(animateFades);
  }

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

    // fading lines
    const now = performance.now();
    for (const f of fadingLines) {
      const elapsed = now - f.start;
      const alpha = Math.max(0, 1 - elapsed / f.duration);
      ctx.globalAlpha = alpha;
      drawLine(f.points, f.color);
      ctx.globalAlpha = 1;
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

  function removeAllCursors() {
    for (const sid of Object.keys(remoteCursors)) {
      remoteCursors[sid].el.remove();
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

    // Sort: canvas owner first, then active users (most recently active first), then inactive
    others.sort((a, b) => {
      const aOwner = a.session_id === ownerSessionId;
      const bOwner = b.session_id === ownerSessionId;
      if (aOwner && !bOwner) return -1;
      if (!aOwner && bOwner) return 1;
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
    const ownerIcon = u.session_id === ownerSessionId
      ? '<span class="owner-badge" title="Canvas owner">&#9733;</span>'
      : '';
    pill.innerHTML = '<span class="dot" style="background:' + u.color + '"></span>' + u.name + ownerIcon;
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

  function rebuildAllPills() {
    for (const sid of Object.keys(userPills)) {
      userPills[sid].remove();
      delete userPills[sid];
    }
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

  document.getElementById("btn-undo").onclick = () => {
    wsSend({ type: "undo_last_line" });
    // Optimistic: fade out the last line by this user
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].session_id === sessionId) {
        fadeOutLines([lines[i]]);
        lines.splice(i, 1);
        draw();
        break;
      }
    }
  };

  document.getElementById("btn-delete").onclick = () => {
    if (!confirm("Delete all your drawings?")) return;
    wsSend({ type: "delete_my_lines" });
    // Optimistic: fade out all lines by this user
    const mine = lines.filter(l => l.session_id === sessionId);
    lines = lines.filter(l => l.session_id !== sessionId);
    fadeOutLines(mine);
    draw();
  };

  const helpOverlay = document.getElementById("help-overlay");
  document.getElementById("btn-help").onclick = () => helpOverlay.hidden = false;
  document.getElementById("help-close").onclick = () => helpOverlay.hidden = true;
  helpOverlay.addEventListener("click", (e) => { if (e.target === helpOverlay) helpOverlay.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { helpOverlay.hidden = true; joinOverlay.hidden = true; } });

  // ── Canvas menu ────────────────────────────────────────────────────────

  const canvasMenuBtn = document.getElementById("canvas-menu-btn");
  const canvasDropdown = document.getElementById("canvas-dropdown");

  canvasMenuBtn.onclick = (e) => {
    e.stopPropagation();
    canvasDropdown.hidden = !canvasDropdown.hidden;
  };

  document.addEventListener("click", (e) => {
    if (!canvasDropdown.contains(e.target) && e.target !== canvasMenuBtn) {
      canvasDropdown.hidden = true;
    }
  });

  document.getElementById("btn-new-canvas").onclick = async () => {
    canvasDropdown.hidden = true;
    const res = await fetch(`/api/canvas?session_id=${sessionId}`, { method: "POST" });
    const data = await res.json();
    if (data.hash_id) {
      navigateToCanvas(data.hash_id);
    }
  };

  // Join canvas modal
  const joinOverlay = document.getElementById("join-overlay");
  const joinInput = document.getElementById("join-input");
  const joinError = document.getElementById("join-error");

  document.getElementById("btn-join-canvas").onclick = () => {
    canvasDropdown.hidden = true;
    joinOverlay.hidden = false;
    joinInput.value = "";
    joinError.hidden = true;
    joinInput.focus();
  };

  document.getElementById("join-close").onclick = () => { joinOverlay.hidden = true; };
  document.getElementById("join-cancel").onclick = () => { joinOverlay.hidden = true; };
  joinOverlay.addEventListener("click", (e) => { if (e.target === joinOverlay) joinOverlay.hidden = true; });

  document.getElementById("join-confirm").onclick = () => attemptJoinCanvas();
  joinInput.addEventListener("keydown", (e) => { if (e.key === "Enter") attemptJoinCanvas(); });

  async function attemptJoinCanvas() {
    const raw = joinInput.value.trim();
    if (!raw) return;

    // Extract hash from URL or use as-is
    let hash = raw;
    const urlMatch = raw.match(/\/canvas\/([a-f0-9]{8})/i);
    if (urlMatch) {
      hash = urlMatch[1];
    }
    // Validate it looks like a hash (8 hex chars)
    if (!/^[a-f0-9]{8}$/i.test(hash)) {
      joinError.textContent = "Invalid canvas ID. Expected 8-character hex code.";
      joinError.hidden = false;
      return;
    }

    // Check canvas exists
    const res = await fetch(`/api/canvas/${hash}`);
    const data = await res.json();
    if (data.error) {
      joinError.textContent = "Canvas not found. Check the ID and try again.";
      joinError.hidden = false;
      return;
    }

    joinOverlay.hidden = true;
    navigateToCanvas(hash);
  }

  // Clear canvas (owner only)
  clearCanvasBtn.onclick = () => {
    if (!confirm("Clear ALL drawings on this canvas from ALL users?")) return;
    wsSend({ type: "clear_canvas" });
    // Optimistic: fade out all lines immediately
    fadeOutLines(lines);
    lines = [];
    draw();
  };

  // Canvas hash in dropdown — click to copy URL
  canvasHashItem.onclick = (e) => {
    e.stopPropagation();
    if (!currentCanvasHash) return;
    const url = `${location.origin}/canvas/${currentCanvasHash}`;
    navigator.clipboard.writeText(url).then(() => {
      const orig = canvasHashLabel.textContent;
      canvasHashLabel.textContent = "Copied!";
      setTimeout(() => { canvasHashLabel.textContent = orig; }, 1500);
    });
  };

  // ── Canvas navigation ──────────────────────────────────────────────────

  function navigateToCanvas(hash) {
    // Close existing WebSocket
    if (ws) {
      ws.onclose = null; // prevent auto-reconnect
      ws.close();
      ws = null;
    }

    // Clear canvas state
    lines = [];
    fadingLines = [];
    for (const sid of Object.keys(remoteDraws)) delete remoteDraws[sid];
    removeAllCursors();
    for (const sid of Object.keys(userPills)) {
      userPills[sid].remove();
      delete userPills[sid];
    }
    for (const sid of Object.keys(userActivity)) delete userActivity[sid];
    drawing = false;
    currentPoints = [];

    // Reset view
    camX = 0; camY = 0; zoom = 1;
    ownerSessionId = null;
    isCanvasOwner = false;
    clearCanvasBtn.hidden = true;

    currentCanvasHash = hash;
    history.pushState(null, "", `/canvas/${hash}`);
    canvasHashLabel.textContent = hash;
    localStorage.setItem("collab_canvas_hash", hash);

    // Load lines and connect
    loadCanvasAndConnect(hash);
  }

  async function loadCanvasAndConnect(hash) {
    // Load canvas info
    const canvasRes = await fetch(`/api/canvas/${hash}`);
    const canvasData = await canvasRes.json();
    if (canvasData.error) {
      console.error("Canvas not found:", hash);
      return;
    }

    // Load persisted lines
    const linesRes = await fetch(`/api/canvas/${hash}/lines`);
    lines = await linesRes.json();
    draw();

    // Connect WebSocket
    connectWS();
  }

  // Handle browser back/forward
  window.addEventListener("popstate", () => {
    const hash = getCanvasHashFromURL();
    if (hash && hash !== currentCanvasHash) {
      navigateToCanvas(hash);
    }
  });

  // ── WebSocket ─────────────────────────────────────────────────────────

  function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function connectWS() {
    if (!currentCanvasHash || !sessionId) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws?session_id=${sessionId}&canvas_hash=${currentCanvasHash}`);

    ws.onopen = () => console.log("WS connected to canvas", currentCanvasHash);

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

        case "canvas_info":
          isCanvasOwner = msg.is_owner;
          ownerSessionId = msg.owner_session_id;
          clearCanvasBtn.hidden = !isCanvasOwner;
          // Re-render user pills so owner badge appears
          rebuildAllPills();
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

        case "draw_confirmed":
          // Assign the server-issued ID to our most recent unconfirmed local line
          for (let i = lines.length - 1; i >= 0; i--) {
            if (lines[i].session_id === sessionId && lines[i].id === null) {
              lines[i].id = msg.line_id;
              break;
            }
          }
          break;

        case "line_deleted": {
          const removed = lines.filter(l => l.id === msg.line_id);
          lines = lines.filter(l => l.id !== msg.line_id);
          fadeOutLines(removed);
          draw();
          break;
        }

        case "lines_deleted": {
          const removed = lines.filter(l => l.session_id === msg.session_id);
          lines = lines.filter(l => l.session_id !== msg.session_id);
          fadeOutLines(removed);
          draw();
          break;
        }

        case "canvas_cleared": {
          fadeOutLines(lines);
          lines = [];
          draw();
          break;
        }

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

  // ── URL helpers ────────────────────────────────────────────────────────

  function getCanvasHashFromURL() {
    const match = location.pathname.match(/^\/canvas\/([a-f0-9]{8})$/i);
    return match ? match[1] : null;
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

    // Determine canvas: from URL, from localStorage, or create default
    let canvasHash = getCanvasHashFromURL();

    if (canvasHash) {
      // Validate the canvas exists
      const canvasRes = await fetch(`/api/canvas/${canvasHash}`);
      const canvasData = await canvasRes.json();
      if (canvasData.error) {
        // Canvas doesn't exist, fall back to default
        canvasHash = null;
      }
    }

    if (!canvasHash) {
      // Check localStorage for last used canvas
      const storedCanvas = localStorage.getItem("collab_canvas_hash");
      if (storedCanvas) {
        const checkRes = await fetch(`/api/canvas/${storedCanvas}`);
        const checkData = await checkRes.json();
        if (!checkData.error) {
          canvasHash = storedCanvas;
        }
      }
    }

    if (!canvasHash) {
      // Create or get default canvas for this user
      const defaultRes = await fetch(`/api/default-canvas?session_id=${sessionId}`);
      const defaultData = await defaultRes.json();
      canvasHash = defaultData.hash_id;
    }

    currentCanvasHash = canvasHash;
    localStorage.setItem("collab_canvas_hash", canvasHash);
    canvasHashLabel.textContent = canvasHash;

    // Update URL if not already on a canvas URL
    if (!getCanvasHashFromURL()) {
      history.replaceState(null, "", `/canvas/${canvasHash}`);
    }

    // Load persisted lines for this canvas
    const linesRes = await fetch(`/api/canvas/${canvasHash}/lines`);
    lines = await linesRes.json();
    draw();

    // Connect WebSocket
    connectWS();
  }

  init();
})();
