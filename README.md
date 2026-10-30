# MyFitnessPal MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that enables AI assistants like Claude to interact with your MyFitnessPal data, including food diary, exercises, body measurements, nutrition goals, and water intake.

## Features

| Tool | Type | Description |
|------|------|-------------|
| `mfp_get_diary` | Read | Get food diary entries for any date |
| `mfp_search_food` | Read | Search the MyFitnessPal food database |
| `mfp_get_food_details` | Read | Get detailed nutrition info for a food item |
| `mfp_add_food_to_diary` | Write | Add a food item to your diary for a specific meal and date |
| `mfp_remove_food_from_diary` | Write | Remove a logged entry from your diary |
| `mfp_create_custom_food` | Write | Create a private custom food with a full nutrition panel |
| `mfp_list_own_foods` | Read | List your own custom foods (private ones do not appear in search) |
| `mfp_delete_custom_food` | Write | Delete one of your custom foods |
| `mfp_get_measurements` | Read | Get weight/body measurement history |
| `mfp_set_measurement` | Write | Log a new weight or body measurement |
| `mfp_get_exercises` | Read | Get logged exercises (cardio & strength) |
| `mfp_get_goals` | Read | Get daily nutrition goals |
| `mfp_set_goals` | Write | Update daily nutrition goals |
| `mfp_get_water` | Read | Get water intake for a date |
| `mfp_set_water` | Write | Log water intake for a date |
| `mfp_get_steps` | Read | Get daily step counts synced from Apple Health / devices |
| `mfp_log_fast` | Write | Log a completed intermittent fasting window (start + end) |
| `mfp_update_fast` | Write | Update the start/end times of an existing fasting entry |
| `mfp_delete_fast` | Write | Delete a fasting entry by id |
| `mfp_get_report` | Read | Get nutrition reports over a date range |

## How diary writes work

MyFitnessPal has no public API. Reads here are scraped from the website via
[`python-myfitnesspal`](https://github.com/coddingtonbear/python-myfitnesspal), and **diary
writes go through MFP's internal v2 JSON API** — the same one their web client uses,
authenticated with your existing session token.

**Custom-food writes** (`mfp_create_custom_food`, `mfp_list_own_foods`,
`mfp_delete_custom_food`) use a different endpoint family: MFP's v2 API exposes no
custom-food create, so these call the same cookie-authenticated web endpoints their
website uses (`/api/auth/csrf`, `/api/services/users/foods/mine`, `/api/services/foods`).
No browser needs to be running — the stored session cookies are sufficient.

That interface is undocumented and was determined by observing the web client. It works today,
but MyFitnessPal can change it without notice. They have already done so once: this server
originally posted to `/food/diary/{user}/add`, which now returns 404, leaving food logging
broken. If logging starts failing, that is the most likely cause.

## How fasting works (write-only)

**Fasting tools** (`mfp_log_fast`, `mfp_update_fast`, `mfp_delete_fast`) POST / PATCH /
DELETE against `/v2/diary/fasting_entry` on the same v2 JSON API. Payloads match the
shape captured from the iOS app:

```json
{"items": [{
  "type": "fasting_entry",
  "id": "UPPERCASE-UUID",
  "fast_started": "2026-08-06T13:00:00Z",
  "fast_ended":   "2026-08-07T05:00:00Z"
}]}
```

Ids are client-generated UUIDv4s in uppercase (the iOS convention). The MCP auto-generates
one when you omit `id` from `mfp_log_fast`; save the returned id if you plan to update or
delete the entry.

**There is no read endpoint.** MFP exposes no GET for fasting entries — `GET /v2/diary/fasting_entry`
returns `405 Method Not Allowed`. The mobile app populates its Fasting History screen via a
delta-sync channel (`mobile-sync-api.myfitnesspal.com/v2.1/sync`) that requires a pre-issued
sync token, is scoped to the mobile OAuth client, and rejects the web-session bearer this
server authenticates with. You'll continue to read fasting history in the MFP app itself
until Under Armour publishes something.

The write tools are still useful for automating log-entry (e.g. inferring fasts from your
Garmin sleep window + first-meal timestamp) or correcting entries programmatically.

## Prerequisites

- **Python 3.10–3.12** (check with `python3 --version`)

  Not 3.13+: `lxml`, pulled in by `myfitnesspal`, has no wheels for it and fails to build
  against the 3.14 C API. On macOS, `brew install python@3.12`.

- **pip 21.3+** (for pyproject.toml support; upgrade with `pip install --upgrade pip`)
- **MyFitnessPal account**
- **A logged-in MyFitnessPal session**, passed to the server as cookies. See
  [Authentication](#authentication); MFP's form login no longer works, so there is
  no username/password option.

## Installation

### Option 1: Install from Source with uv (Recommended)

```bash
git clone https://github.com/YOUR_USERNAME/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python

# Creates .venv with a compatible Python (3.10-3.12) and installs the package.
# Add --extra login for the browser-window login helper, --extra dev for tests.
uv sync --python 3.12
```

Run anything with `uv run ...` (e.g. `uv run python -m mfp_mcp.server`). Without uv:
`python3.12 -m venv venv && source venv/bin/activate && pip install -e .`

### Option 2: Install with pip (when published)

```bash
pip install mfp-mcp
```

> **Note**: Option 2 requires the package to be published to PyPI. For now, use Option 1.

### Verify Installation

After installation, verify the server can start:

```bash
# With venv activated
python -m mfp_mcp.server
```

You should see the server waiting for input (it communicates via stdio). Press `Ctrl+C` to stop.

To test that your session cookies work, see [Authentication](#authentication): the
`export-cookies` and `import-cookies` commands verify against MyFitnessPal before printing.

## Authentication

MyFitnessPal has no public API and its form login no longer works, so the server uses your
logged-in **session cookies**. It reads them from, in order:

1. `cookies.json` in `~/.mfp_mcp/` (or `$MFP_CONFIG_DIR`). Written by the import tools and
   kept fresh automatically (see below), so it is normally the newest.
2. The `MFP_COOKIES_JSON` environment variable: the starting point you provide, and the
   fallback if the stored session has died.

### Get your cookies

Pick whichever is easiest. Each prints a one-line JSON value for `MFP_COOKIES_JSON`
(after checking it against MyFitnessPal) and treats it like a password, because it is one.

| Method | Command | Notes |
|--------|---------|-------|
| **Read your own browser** | `uv run python -m mfp_mcp.server export-cookies` | You must be logged into myfitnesspal.com. macOS reads Arc/Chrome/Edge/Brave/etc. through the keychain (click **Allow**, not Always Allow); `--browser chrome\|firefox\|arc\|...` picks one |
| **Open a login window** | `uv run --extra login python -m mfp_mcp.server export-cookies --login` | Opens a browser, you log in, it captures the session. Google/Apple sign-in may be refused in that automated window; use another method then |
| **Paste from DevTools (F12)** | `uv run python -m mfp_mcp.server import-cookies` | Log in, F12 → Network, reload, click a request to `www.myfitnesspal.com`, copy the whole `Cookie` request header, paste, Ctrl-D |

For the paste methods: Cookie-header text, JSON, and rows copied from Application → Cookies are
all accepted. Don't use `document.cookie`: the login cookie is HttpOnly and won't be there.
On a hosted server you can also paste at `https://<server>/session?token=<token>` or
`curl -X POST https://<server>/session -H "Authorization: Bearer <token>" --data-binary @cookies.txt`
(see [Remote Server](#remote-server-use-it-from-anywhere)).

### The session keeps itself alive

MFP's login lasts about 30 days. While the server is in use it asks MFP to re-issue the
session cookie (at most once per 12 hours) and saves the new one to `cookies.json`, so you
normally import cookies **once**. `MFP_COOKIES_JSON` then only matters for the first start.
The session still lapses if the server sits unused for ~30 days or you log out or change your
password on MFP; then import fresh cookies again. The log line
`MFP session re-issued; expires ...` shows the current expiry.

## Configuration for Claude Desktop

Config file: macOS `~/Library/Application Support/Claude/claude_desktop_config.json`,
Windows `%APPDATA%\Claude\claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/yourname/myfitnesspal-mcp-python",
               "run", "python", "-m", "mfp_mcp.server"],
      "env": {
        "MFP_COOKIES_JSON": "{\"__Secure-next-auth.session-token\":\"...\"}"
      }
    }
  }
}
```

Use **absolute paths** (`which uv` for the first one); Claude Desktop does not inherit your shell
`PATH`. Paste the value `export-cookies` printed, with the quotes escaped as above. After the
first successful call the session is saved to `~/.mfp_mcp/cookies.json` and refreshed there.
Then **fully quit and restart Claude Desktop** and try: *"Show my MyFitnessPal diary for today"*.

## Security Note on Cookies

Your session cookies give full access to your MyFitnessPal account, and this server can
**modify your diary** (food entries, goals, measurements, water, fasting).

- Files this server writes to `~/.mfp_mcp/` (directory mode `0700`):

  | File | Contents | Mode |
  |------|----------|------|
  | `cookies.json` | Session cookies: treat as a password | `0600` |

- Put `MFP_COOKIES_JSON` in your platform's secrets store, not in a committed file or a
  command line. On macOS prefer **Allow** over **Always Allow** at the keychain prompt:
  "Always" lets that program read the browser's cookie-encryption key from then on.
- Only cookies for `myfitnesspal.com` are read from your browser.

## Usage Examples

Once configured, you can interact with your MyFitnessPal data through Claude:

### Food Diary
```
"Show me what I ate today"
"Get my food diary for 2026-01-05"
"What meals did I log yesterday?"
```

### Logging and Correcting Food
```
"Log a grilled chicken breast, 6 oz, for lunch"
"Add 2 cups of oatmeal to breakfast"
```

### Track Weight Progress
```
"Show my weight history for the past 30 days"
"Log my weight as 232.5 pounds"
"What's my weight trend this month?"
```

### Search Foods
```
"Search MyFitnessPal for chicken breast"
"Find nutrition info for Greek yogurt"
"Look up calories in a banana"
```

### Check Goals vs Actual
```
"Compare my nutrition goals to what I actually ate today"
"Am I on track with my protein intake?"
"How many calories do I have left today?"
```

### Exercise Log
```
"What exercises did I log today?"
"Show my workout from yesterday"
```

### Nutrition Reports
```
"Show my calorie intake over the past week"
"What's my average protein intake this week?"
"Generate a nutrition report for January"
```

## Project Structure

```
myfitnesspal-mcp-python/
├── Dockerfile              # Container deployment (HTTP server)
├── pyproject.toml          # Python package configuration
├── README.md               # This file
└── src/
    └── mfp_mcp/
        ├── __init__.py     # Package initialization
        ├── cookie_paste.py # Parse cookies pasted from DevTools
        ├── remote.py       # HTTP transport, bearer auth, /session, cookie CLIs
        └── server.py       # MCP server implementation
```

## Development

### Setup Development Environment

```bash
# Clone and enter directory
git clone https://github.com/YOUR_USERNAME/myfitnesspal-mcp-python.git
cd myfitnesspal-mcp-python

# Create virtual environment (Python 3.10+ required)
python3 -m venv venv
source venv/bin/activate

# Upgrade pip and install with dev dependencies
pip install --upgrade pip
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Code Formatting

```bash
black src/
isort src/
ruff check src/
```

### Type Checking

```bash
mypy src/
```

## Remote Server (use it from anywhere)

By default the server speaks stdio and only works for a client on the same machine. It can
also run as a network service (MCP **Streamable HTTP**, endpoint `/mcp`) so Claude Code,
Claude Desktop, Cursor, etc. can reach it from any device.

Three things differ from the local setup:

1. **Authentication to the server.** These tools can modify your diary, so HTTP mode refuses
   to start without `MFP_MCP_AUTH_TOKEN` (16+ chars). Clients send it as
   `Authorization: Bearer <token>`.
2. **MyFitnessPal session.** A server has no browser to read. Get your cookies on a computer
   (see [Authentication](#authentication)) and pass them as `MFP_COOKIES_JSON`, or paste them at
   `https://<your-server>/session?token=<MFP_MCP_AUTH_TOKEN>` (works from a phone; the cookies
   are verified against MFP before saving and never echoed back). Mount a persistent volume at
   `MFP_CONFIG_DIR` (the Docker image uses `/data`) so the refreshed session survives restarts.
3. **TLS.** Put the server behind HTTPS (Fly.io, Render, Cloud Run, Caddy, a Cloudflare
   Tunnel, ...). The bearer token travels in a header and must not go over plain HTTP.

### Run it

```bash
# generate a token
python -c 'import secrets; print(secrets.token_urlsafe(32))'

# directly
MFP_MCP_AUTH_TOKEN=<token> MFP_COOKIES_JSON='{"...":"..."}' \
  python -m mfp_mcp.server --transport streamable-http --host 0.0.0.0 --port 8000

# or with Docker (image defaults to HTTP on :8000, cookies persisted in /data)
docker build -t mfp-mcp .
docker run -d -p 8000:8000 -v mfp-data:/data \
  -e MFP_MCP_AUTH_TOKEN=<token> -e MFP_COOKIES_JSON='{"...":"..."}' mfp-mcp
```

`GET /healthz` is unauthenticated and returns `ok`, for platform health checks.

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MFP_MCP_TRANSPORT` | `stdio` (`streamable-http` in the Docker image) | `stdio` or `streamable-http`; same as `--transport` |
| `MFP_MCP_HOST` | `127.0.0.1` | Bind address (`--host`) |
| `MFP_MCP_PORT` / `PORT` | `8000` | Bind port (`--port`) |
| `MFP_MCP_AUTH_TOKEN` | — | Shared secret; required for HTTP |
| `MFP_MCP_ALLOWED_HOSTS` | any | Comma-separated `Host` headers to accept, e.g. `mfp.example.com` |
| `MFP_MCP_STATELESS` | `1` | Stateless sessions, so restarts don't invalidate clients |
| `MFP_MCP_ALLOW_NO_AUTH` | `0` | Skip the token check — only if an authenticating proxy sits in front |
| `MFP_COOKIES_JSON` | — | MFP session cookies (output of `export-cookies`) |
| `MFP_CONFIG_DIR` | `~/.mfp_mcp` | Where `cookies.json` is kept; mount a volume here |

### Connect a client

```bash
# Claude Code
claude mcp add --transport http myfitnesspal https://mfp.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

Clients that only speak stdio (e.g. Claude Desktop) can bridge with
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mfp.example.com/mcp",
               "--header", "Authorization: Bearer ${MFP_TOKEN}"],
      "env": { "MFP_TOKEN": "<token>" }
    }
  }
}
```

For clients that cannot set headers, `https://mfp.example.com/mcp?token=<token>` is also
accepted. The secret is then part of the URL, so keep that URL private (access logging is
disabled in the server for this reason, but proxies in front of it may log it). The server
does not implement OAuth, so connector UIs that require an OAuth flow cannot use it.

## Troubleshooting

### "python: command not found" or wrong Python version

**Problem**: Python is not in PATH or you need to specify version.

**Solutions**:
1. On macOS/Linux, use `python3` instead of `python`
2. Check your version: `python3 --version` (must be 3.10+)
3. If needed, install Python 3.12 via Homebrew: `brew install python@3.12`
4. Then create venv with: `python3.12 -m venv venv`

### "pip install -e ." fails with "setup.py not found"

**Problem**: Your pip version is too old to support pyproject.toml builds.

**Solution**: Upgrade pip first:
```bash
pip install --upgrade pip
pip install -e .
```

### "No MyFitnessPal session cookies are configured" / "stored session no longer works"

**Problem**: No cookies were provided, or MFP no longer accepts them (expired, or you logged
out / changed your password).

**Solutions**:
1. Import fresh cookies: see [Authentication](#authentication). Then either set
   `MFP_COOKIES_JSON` or, on a hosted server, paste at `/session`.
2. If `cookies.json` holds a dead session and `MFP_COOKIES_JSON` is set to a good one, the
   server falls back to the variable automatically. If neither works, delete
   `~/.mfp_mcp/cookies.json` and import again.
3. **`export-cookies` says "No MyFitnessPal session found" (macOS)**: you must click
   **Allow** on the keychain dialog within 2 minutes, and be logged into myfitnesspal.com in
   that browser (try `--browser chrome|arc|...`, or use the paste method instead).

### "No module named 'mfp_mcp'"

**Problem**: Package not installed or wrong Python environment.

**Solutions**:
1. Ensure you're using the correct Python from your virtual environment
2. Reinstall the package: `pip install -e .`
3. Verify the path in your Claude Desktop config points to the venv Python:
   ```
   /path/to/project/venv/bin/python  # macOS/Linux
   C:\path\to\project\venv\Scripts\python.exe  # Windows
   ```

### Tools not appearing in Claude Desktop

**Problem**: MCP server not connecting.

**Solutions**:
1. Check the config file syntax (must be valid JSON - use a JSON validator)
2. Use **absolute paths** in the configuration (no `~` or relative paths)
3. Restart Claude Desktop completely (Cmd+Q on macOS, then relaunch)
4. Check Claude Desktop logs:
   - macOS: `~/Library/Logs/Claude/`
   - Windows: `%APPDATA%\Claude\logs\`

### Empty responses or no data

**Problem**: Authentication works but no data returned.

**Solutions**:
1. Verify you have data logged in MyFitnessPal for the requested date
2. Check the date format (YYYY-MM-DD)
3. Try a recent date where you know you have entries

### Double parentheses in terminal prompt like "((venv) )"

**Problem**: VS Code/Cursor Python extension bug with venv prompt.

**Solutions**:
1. Update the Python extension in VS Code/Cursor
2. Or manually fix the venv activate script - change line ~70 in `venv/bin/activate`:
   ```bash
   # Change from:
   PS1="("'(venv) '") ${PS1:-}"
   # To:
   PS1="(venv) ${PS1:-}"
   ```

## API Reference

### mfp_get_diary
Get food diary for a specific date.
- `date` (optional): YYYY-MM-DD format, defaults to today
- `response_format`: "markdown" or "json"

### mfp_search_food
Search the MyFitnessPal food database.
- `query` (required): Search term
- `limit` (optional): Max results (default 10, max 50)
- `response_format`: "markdown" or "json"

### mfp_get_food_details
Get detailed nutrition for a food item.
- `mfp_id` (required): MyFitnessPal food ID from search results
- `response_format`: "markdown" or "json"

### mfp_add_food_to_diary
Add a food item to your diary for a specific meal and date.
- `mfp_id` (required): MyFitnessPal food ID from search results (use `mfp_search_food` first)
- `meal` (optional): Meal name - "Breakfast", "Lunch", "Dinner", or "Snacks" (default: "Breakfast")
- `date` (optional): YYYY-MM-DD format (default: today)
- `quantity` (optional): Number of servings (default: 1.0)
- `unit` (optional): Unit/serving size description (e.g., "1 cup", "100g")

**Example workflow:**
1. Use `mfp_search_food` to find a food item and get its `mfp_id`
2. Use `mfp_add_food_to_diary` with the `mfp_id` to add it to your diary

### mfp_create_custom_food
Create a private custom food in your account. Returns the new food's `id`, which
`mfp_add_food_to_diary` accepts.
- `description` (required): Food name as it appears in MFP
- `calories` (required): Calories per serving
- `brand_name` (optional): Brand; packaged = label brand, restaurant = venue, homemade = "Generic" (default: "Generic")
- `serving_amount` (optional): Serving size number (default: 100)
- `serving_unit` (optional): Serving unit, e.g. "g", "ml", "piece" (default: "g")
- Nutrients (all optional): `carbs`, `fiber`, `sugar`, `protein`, `fat`, `saturated_fat`,
  `polyunsaturated_fat`, `monounsaturated_fat`, `trans_fat`, `cholesterol` (mg), `sodium` (mg),
  `potassium` (mg), `vitamin_a`, `vitamin_c`, `calcium`, `iron` (last four are %DV)
- `country_code` (optional): Label convention (default: "NL") — **see the carbs note below**
- `public` (optional): Share publicly (default: false)
- `response_format`: "markdown" or "json"

**`carbs` is NET carbs, and `country_code` is what makes it so.** The field selects which
label convention your number follows, so it changes the meaning of `carbs`:

| `country_code` | `carbs` is read as | MFP stores |
|------|------|-------------|
| `"NL"` and other EU codes (labels exclude fibre) | **NET** | `net_carbs = carbs`, `carbohydrates = carbs + fiber` |
| omitted / US (labels include fibre) | TOTAL | `carbohydrates = carbs`, `net_carbs = carbs − fiber` |

Sending `carbs=42, fiber=8` stores `50/42` under `"NL"` but `42/34` without it. Pass the number
straight off the label and leave `country_code` matching that label's origin — do not pre-subtract
fibre.

**MyFitnessPal has no custom-food update endpoint.** To correct a food, create the corrected
version and then `mfp_delete_custom_food` the old one.

### mfp_list_own_foods
List your own custom foods, newest first. Private custom foods do not reliably appear in
`mfp_search_food`, so this is how to find the `id` of something you created earlier.
- `search` (optional): Substring filter on the food name
- `limit` (optional): Max foods to return (default: 25)
- `response_format`: "markdown" or "json"

### mfp_delete_custom_food
Delete one of your custom foods. Destructive and not recoverable; MyFitnessPal may refuse if the
food is referenced by a logged diary entry.
- `food_id` (required): Food id from `mfp_create_custom_food` or `mfp_list_own_foods`

### mfp_remove_food_from_diary
Remove a logged entry from your diary.
- `entry_id` (required): Diary entry id (from `mfp_get_diary`)

### mfp_get_measurements
Get body measurement history.
- `measurement` (optional): "Weight", "Body Fat", "Waist", etc.
- `start_date` (optional): YYYY-MM-DD (default 30 days ago)
- `end_date` (optional): YYYY-MM-DD (default today)
- `response_format`: "markdown" or "json"

### mfp_set_measurement
Log a body measurement for today.
- `measurement` (optional): Type (default "Weight")
- `value` (required): Numeric value

### mfp_get_exercises
Get exercise log for a date.
- `date` (optional): YYYY-MM-DD (default today)
- `response_format`: "markdown" or "json"

### mfp_get_goals
Get daily nutrition goals.
- `date` (optional): YYYY-MM-DD (default today)
- `response_format`: "markdown" or "json"

### mfp_set_goals
Update nutrition goals.
- `calories` (optional): Daily calorie goal
- `protein` (optional): Daily protein in grams
- `carbohydrates` (optional): Daily carbs in grams
- `fat` (optional): Daily fat in grams

### mfp_get_water
Get water intake for a date.
- `date` (optional): YYYY-MM-DD (default today)

### mfp_set_water
Log water intake for a date.
- `cups` (required): Number of cups of water (e.g., 2.5 for 2.5 cups). Note: MyFitnessPal uses cups as the unit (1 cup = ~237ml)
- `date` (optional): YYYY-MM-DD format (default: today)

### mfp_get_steps
Get daily step counts synced into MyFitnessPal (Apple Health, Google Fit, connected devices). Days with no synced data return `steps: null`.
- `start_date` (optional): YYYY-MM-DD (default 6 days before end_date)
- `end_date` (optional): YYYY-MM-DD (default today)
- `response_format`: "markdown" or "json"

Maximum 31 days per call (MFP's API returns one day per request).

### mfp_get_report
Get nutrition report over a date range.
- `report_name` (optional): "Net Calories", "Protein", "Fat", "Carbs"
- `start_date` (optional): YYYY-MM-DD (default 7 days ago)
- `end_date` (optional): YYYY-MM-DD (default today)
- `response_format`: "markdown" or "json"

## Security & Privacy

- **Session cookies only**: no MyFitnessPal username or password is stored or used. See
  [Security Note on Cookies](#security-note-on-cookies).
- **Cookie file**: `~/.mfp_mcp/cookies.json` (or `$MFP_CONFIG_DIR`) is created with owner-only
  permissions.
- **Network**: the only outbound connections are to `myfitnesspal.com` and
  `api.myfitnesspal.com`. No third-party services receive your data.
- **Local by default**: over stdio the server runs on your machine. If you choose the HTTP
  mode, it is reachable from the network and protected only by `MFP_MCP_AUTH_TOKEN`: use HTTPS
  and a long random token, and treat the token as full read/write access to your diary.

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Acknowledgments

- [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal) - The underlying library for MyFitnessPal access
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Model Context Protocol framework
- [Anthropic](https://anthropic.com) - Claude and the MCP specification
