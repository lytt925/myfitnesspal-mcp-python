# MyFitnessPal MCP Server

An [MCP](https://modelcontextprotocol.io/) server that lets AI assistants such as Claude read and
write your MyFitnessPal data: food diary, exercise, body measurements, goals, water, steps and fasting.

It runs two ways:

| | Local | Remote |
|---|---|---|
| Transport | stdio, started by your MCP client | Streamable HTTP at `/mcp` |
| Works from | the machine it is installed on | any device that can reach the server |
| Good for | Claude Desktop, Claude Code on one computer | several devices, a phone, a hosted setup |
| Setup | [Run locally](#run-locally) | [Run remotely](#run-remotely) |

Either way you need **Python 3.10–3.12** (or Docker for remote) and a **logged-in MyFitnessPal session**.
MFP's form login no longer works and has no public API, so the server authenticates with your
session cookies. There is no username/password option.

## Quick start

1. Install: `git clone https://github.com/YOUR_USERNAME/myfitnesspal-mcp-python.git && cd myfitnesspal-mcp-python && uv sync --python 3.12`
   (needs [uv](https://docs.astral.sh/uv/); it picks a compatible Python for you).
2. [Get your cookies](#get-your-cookies).
3. Connect a client: [locally](#run-locally) or [remotely](#run-remotely).
4. Ask: *"Show my MyFitnessPal diary for today"*.

## Get your cookies

The server needs two cookies from a logged-in myfitnesspal.com session:
`__Secure-next-auth.session-token` (starts with `eyJ`) and `_mfp_session`. Treat them like a
password. Both commands check the session against MyFitnessPal before saving anything.

**Option A: read your own browser (macOS)**. Log into myfitnesspal.com in Chrome (or Arc, Edge,
Brave, ...), then:

```bash
uv run python -m mfp_mcp.server export-cookies --browser chrome | \
  uv run python -m mfp_mcp.server import-cookies
```

macOS asks for access to the browser's keychain entry: type your Mac password only in that system
dialog, and click **Allow** (not Always Allow) within 2 minutes. `--browser` also takes
`firefox`, `arc`, `edge`, `brave`, ...

**Option B: paste from DevTools (any OS)**. Log in, press F12 → Application (Firefox: Storage) →
Cookies → `https://www.myfitnesspal.com`, and copy the *Value* of those two cookies. Then:

```bash
uv run python -m mfp_mcp.server import-cookies
```

Paste, then press Ctrl-D:

```
__Secure-next-auth.session-token=eyJ...
_mfp_session=...
```

The whole `Cookie` request header (Network tab), JSON, and "Copy as cURL" are accepted too. Don't
use `document.cookie`: the login cookie is HttpOnly and won't be there. Paste into the terminal,
never into a chat or an issue.

Either way the session is saved to `~/.mfp_mcp/cookies.json`, which is where the server reads it.

### How the server finds the session

It reads, in order:

1. `cookies.json` in `~/.mfp_mcp/` (or `$MFP_CONFIG_DIR`): written by `import-cookies` and kept
   fresh automatically, so normally the newest.
2. The `MFP_COOKIES_JSON` environment variable: for hosted setups that cannot run the commands
   above (`export-cookies` without `| import-cookies` prints the value to use). It is also the
   fallback if the stored session has died.

MFP's login lasts about 30 days. While the server is in use it asks MFP to re-issue the cookie
(at most once per 12 hours) and saves it to `cookies.json`, so you normally import cookies **once**.
The session lapses if the server sits unused for ~30 days or you log out or change your password on
MFP; then import fresh cookies. The log line `MFP session re-issued; expires ...` shows the expiry.

## Run locally

Config file: macOS `~/Library/Application Support/Claude/claude_desktop_config.json`,
Windows `%APPDATA%\Claude\claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/yourname/myfitnesspal-mcp-python",
               "run", "python", "-m", "mfp_mcp.server"]
    }
  }
}
```

Use **absolute paths** (`which uv` for the first one); Claude Desktop does not inherit your shell
`PATH`. The server picks up the session you saved in [Get your cookies](#get-your-cookies), so
no secret goes in this file. Then **fully quit and restart Claude Desktop**.

For Claude Code: `claude mcp add myfitnesspal -- uv --directory /path/to/myfitnesspal-mcp-python run python -m mfp_mcp.server`

To check that the server starts, run `uv run python -m mfp_mcp.server`; it waits for stdio input
(`Ctrl+C` to stop).

## Run remotely

The server can run as a network service so Claude Code, Claude Desktop, Cursor, etc. can reach it
from any device. Three things differ from the local setup:

1. **Server authentication.** These tools can modify your diary, so HTTP mode refuses to start
   without `MFP_MCP_AUTH_TOKEN` (16+ chars). Clients send it as `Authorization: Bearer <token>`.
2. **MyFitnessPal session.** A server has no browser to read. [Get your cookies](#get-your-cookies)
   on a computer and pass them as `MFP_COOKIES_JSON`, or paste them at
   `https://<your-server>/session?token=<MFP_MCP_AUTH_TOKEN>` (works from a phone; cookies are
   verified against MFP before saving and never echoed back). Mount a persistent volume at
   `MFP_CONFIG_DIR` (the Docker image uses `/data`) so the refreshed session survives restarts.
3. **TLS.** Put the server behind HTTPS (Fly.io, Render, Cloud Run, Caddy, a Cloudflare Tunnel, ...).
   The bearer token travels in a header and must not go over plain HTTP.

### Start the server

```bash
# generate a token
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'

# directly
MFP_MCP_AUTH_TOKEN=<token> MFP_COOKIES_JSON='{"...":"..."}' \
  uv run python -m mfp_mcp.server --transport streamable-http --host 0.0.0.0 --port 8000

# or with Docker (image defaults to HTTP on :8000, cookies persisted in /data)
docker build -t mfp-mcp .
docker run -d -p 8000:8000 -v mfp-data:/data \
  -e MFP_MCP_AUTH_TOKEN=<token> -e MFP_COOKIES_JSON='{"...":"..."}' mfp-mcp
```

`GET /healthz` is unauthenticated and returns `ok`, for platform health checks.

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

For clients that cannot set headers, `https://mfp.example.com/mcp?token=<token>` is also accepted.
The secret is then part of the URL, so keep it private (the server disables access logging for this
reason, but proxies in front of it may log it). The server does not implement OAuth, so connector UIs
that require an OAuth flow cannot use it.

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

## Tools

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

Example prompts: *"What did I eat today?"*, *"Log 6 oz grilled chicken for lunch"*,
*"Log my weight as 232.5 pounds"*, *"How many calories do I have left today?"*,
*"Average protein this week?"*

Details for each tool are in the [Tool reference](#tool-reference).

## How it works

MyFitnessPal has no public API, so none of this is officially supported.

### How diary writes work

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

### How fasting works (write-only)

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

## Tool reference

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

## Security

Your session cookies give full access to your MyFitnessPal account, and this server can
**modify your diary** (food entries, goals, measurements, water, fasting).

- No MFP username or password is stored or used.
- The server writes one file, `cookies.json`, into `~/.mfp_mcp/` (or `$MFP_CONFIG_DIR`), with the
  directory mode `0700` and the file mode `0600`. Treat it as a password.
- Put `MFP_COOKIES_JSON` in your platform's secrets store, not in a committed file or on a command line.
- On macOS prefer **Allow** over **Always Allow** at the keychain prompt: "Always" lets that program
  read the browser's cookie-encryption key from then on. Only `myfitnesspal.com` cookies are read.
- The only outbound connections are to `myfitnesspal.com` and `api.myfitnesspal.com`.
- Over stdio the server runs on your machine. In HTTP mode it is reachable from the network and
  protected only by `MFP_MCP_AUTH_TOKEN`: use HTTPS and a long random token, and treat the token as
  full read/write access to your diary.

## Troubleshooting

**"No MyFitnessPal session cookies are configured" / "stored session no longer works"**
No cookies were provided, or MFP no longer accepts them (expired, logged out, password changed).
1. [Get fresh cookies](#get-your-cookies), then set `MFP_COOKIES_JSON` or, on a remote server, paste at `/session`.
2. If `cookies.json` holds a dead session and `MFP_COOKIES_JSON` is a good one, the server falls
   back to the variable automatically. If neither works, delete `~/.mfp_mcp/cookies.json` and import again.

**`export-cookies` says "No MyFitnessPal session found" (macOS)**
Click **Allow** on the keychain dialog within 2 minutes, and be logged into myfitnesspal.com in that
browser (try `--browser chrome|arc|...`, or use the paste method).

**Tools not appearing in Claude Desktop**
1. Check the config is valid JSON and uses absolute paths (no `~`).
2. Quit and relaunch Claude Desktop completely (Cmd+Q on macOS).
3. Read the logs: macOS `~/Library/Logs/Claude/`, Windows `%APPDATA%\Claude\logs\`.

**Logging food suddenly fails**
MFP's internal API is undocumented and can change without notice. That is the most likely cause.

**Empty responses**
Check that data is logged for that date (format `YYYY-MM-DD`) and try a recent one.

## Development

```bash
uv sync --python 3.12 --extra dev
uv run pytest
uv run black src/ && uv run isort src/ && uv run ruff check src/ && uv run mypy src/
```

```
├── Dockerfile              # Container deployment (HTTP server)
├── pyproject.toml          # Package configuration
└── src/mfp_mcp/
    ├── cookie_paste.py     # Parse cookies pasted from DevTools
    ├── remote.py           # HTTP transport, bearer auth, /session, cookie CLIs
    └── server.py           # MCP server and tools
```

## License

MIT License - See [LICENSE](LICENSE) file for details.

## Acknowledgments

- [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal) - The underlying library for MyFitnessPal access
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Model Context Protocol framework
- [Anthropic](https://anthropic.com) - Claude and the MCP specification
