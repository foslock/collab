# Collab Canvas

A real-time collaborative drawing application where multiple users draw on a shared infinite canvas. Each user gets a unique name and color, and can see other users' cursors and drawings live.

## Features

- **Real-time collaborative drawing** — multiple users draw simultaneously via WebSocket
- **Infinite canvas** — pan (middle-click or shift+click drag) and zoom (scroll wheel) with grid overlay
- **Live cursors** — see other users' cursor positions in real-time with name labels
- **Persistent drawings** — lines are saved to the database and loaded on reconnect
- **User activity indicators** — active users (within 10s) show a pulsing color dot; inactive users appear faded and sort to the end of the list
- **Overflow user list** — when more than 5 users are connected, a "+N more" dropdown shows the rest
- **Rate limiting** — each user can draw up to 100 lines per minute; exceeding the limit shows a gentle alert
- **Session persistence** — sessions are stored in localStorage and resumed on return
- **Delete own drawings** — users can delete all their drawings with a single click

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Uvicorn
- **Frontend**: Vanilla JavaScript, Canvas API
- **Database**: PostgreSQL (production via asyncpg), SQLite (development/testing via aiosqlite)
- **Deployment**: Docker + Docker Compose

## Quick Start

### Local Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/). Install it once with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install the project and run the dev server:

```bash
uv sync          # creates .venv and installs all deps (including dev)
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 in multiple browser tabs to test collaboration.

### Docker

```bash
docker-compose up --build
```

This starts PostgreSQL and the web server on port 8000.

### Running Tests

```bash
uv run pytest tests/ -v
```

Tests use an in-memory SQLite database and require no external services.

## Architecture

```
app/
  main.py        # FastAPI server, WebSocket handler, ConnectionManager, rate limiting
  models.py      # SQLAlchemy models (Session, Line)
  database.py    # Database engine and session factory
  names.py       # Random "Adjective Animal" name + color generator
static/
  index.html     # Single-page HTML
  app.js         # Client-side drawing, WebSocket, UI logic
  style.css      # Dark theme styling, animations
tests/
  conftest.py    # Shared test fixtures (async DB, HTTP client)
  test_api.py    # REST endpoint tests
  test_websocket.py         # WebSocket integration tests
  test_connection_manager.py # ConnectionManager unit tests
  test_rate_limiting.py     # Rate limiting and activity tracking unit tests
  test_rate_limit_ws.py     # Rate limiting WebSocket integration tests
  test_models.py            # Database model tests
  test_names.py             # Name generator tests
```

## API

### REST

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serves the app HTML |
| `/api/session?session_id=UUID` | GET | Get or create a session |
| `/api/lines` | GET | Get all persisted lines |

### WebSocket (`/ws?session_id=UUID`)

**Client sends**: `cursor_move`, `draw_start`, `draw_move`, `draw_end`, `delete_my_lines`

**Server sends**: `users_list`, `user_joined`, `user_left`, `cursor_move`, `draw_start`, `draw_move`, `draw_end`, `lines_deleted`, `rate_limited`

## Configuration

| Constant | Default | Description |
|---|---|---|
| `SESSION_EXPIRY_DAYS` | 30 | Days before inactive sessions are cleaned up |
| `RATE_LIMIT_LINES` | 100 | Max lines per user per window |
| `RATE_LIMIT_WINDOW` | 60 | Rate limit window in seconds |
| `ACTIVITY_TIMEOUT` | 10 | Seconds before a user is considered inactive |
