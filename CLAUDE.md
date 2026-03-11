# CLAUDE.md

## Project Overview

Collab Canvas is a real-time collaborative drawing app. FastAPI backend with WebSocket for live communication, vanilla JS frontend with Canvas API, PostgreSQL/SQLite for persistence.

## Commands

- **Run server**: `uvicorn app.main:app --reload`
- **Run tests**: `python -m pytest tests/ -v`
- **Run single test file**: `python -m pytest tests/test_rate_limiting.py -v`
- **Docker**: `docker-compose up --build`

## Project Structure

- `app/main.py` — Main server file. Contains `ConnectionManager` (WebSocket connection tracking, activity timestamps, rate limiting), REST endpoints (`/api/session`, `/api/lines`), and the WebSocket handler (`/ws`). This is the most important file.
- `app/models.py` — SQLAlchemy models: `Session` (user identity) and `Line` (persisted drawings with JSON points).
- `app/database.py` — Database engine setup. Supports PostgreSQL (production) and SQLite (testing). Detects `DATABASE_URL` env var.
- `app/names.py` — Generates unique "Adjective Animal" names and assigns colors from a fixed palette.
- `static/app.js` — All client-side logic in a single IIFE. Handles canvas drawing, pan/zoom, WebSocket communication, remote cursor rendering, user list UI with activity-based sorting/fading/overflow, and rate limit alerts.
- `static/style.css` — Dark theme CSS. Includes pulse animation for active users, faded state for inactive users, overflow dropdown, and rate limit toast.
- `static/index.html` — Single-page HTML shell.

## Key Architecture Decisions

- **Single module-level `ConnectionManager`** instance (`manager`) holds all WebSocket state. Tests must clear `manager.active`, `manager.cursors`, `manager.last_activity`, and `manager.line_timestamps` between runs.
- **Rate limiting** is in-memory per-session via sliding window (timestamps list). Not persisted — resets on server restart.
- **Activity tracking** uses `time.monotonic()` on the server and `Date.now()` on the client. The server includes `last_active` (seconds since last action) in `active_users()` responses.
- **Frontend is vanilla JS** — no build step, no framework. All in one IIFE in `app.js`.
- **Tests use two patterns**: async `httpx.AsyncClient` for REST API tests (in `conftest.py`), and sync `starlette.TestClient` for WebSocket tests (fixture in each WS test file). Both patch `app.main.engine` and `app.main.async_session` to use in-memory SQLite.

## Testing Notes

- Tests use in-memory SQLite via `aiosqlite`. No external services needed.
- pytest config is in `pytest.ini` with `asyncio_mode = auto`.
- WebSocket tests that need to verify server-side state changes after `send_text` should either: (a) receive a broadcast on a second WebSocket to synchronize, or (b) send a follow-up message to force processing. Direct assertion after `send_text` can be racy.
- When adding new state to `ConnectionManager`, update the clear calls in `tests/conftest.py` (`client` fixture) and `tests/test_websocket.py` (`sync_client` fixture) and `tests/test_rate_limit_ws.py` (`ws_client` fixture).

## Important: Paired Constants

`ACTIVITY_TIMEOUT` is defined in both `app/main.py` and `static/app.js` and must stay in sync. The server uses it for `last_active` in user lists; the client uses it to decide pulse vs faded styling.

## Common Patterns

- **Adding a new WebSocket message type**: Handle in the `while True` loop in `websocket_endpoint` in `app/main.py`, add corresponding `case` in the `switch` block in `static/app.js`.
- **Adding a new REST endpoint**: Add to `app/main.py` before the static files mount (must be last).
- **Modifying user list behavior**: `renderUsers()` and related functions in `app.js` control sorting, pill creation, and overflow dropdown.
