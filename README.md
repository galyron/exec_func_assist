# EVA — Executive Function Assistant

A self-hosted Discord bot that acts as an executive function prosthetic. It sends proactive structured check-ins, task suggestions, and energy-aware nudges throughout the day, using Claude as its language backend.

Messages are routed based on time of day and user intent. The morning starts with a structured interview, work hours get assertive task nudges, evenings shift to low-pressure couch-compatible suggestions. The user can also send messages on demand — "I'm stuck", "done", "add: buy milk", "off today" — and the bot responds appropriately.

**Stack:** Python 3.12 · discord.py 2.x · APScheduler · Anthropic Claude API · Joplin (task source) · Google Calendar · Docker Compose

---

## Prerequisites

- Docker + Docker Compose
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An Anthropic API key
- Joplin desktop app syncing to Dropbox (the bot container runs its own Joplin CLI that syncs from the same Dropbox)
- A Google Calendar account (OAuth2 setup — one-time, see below)

---

## First-time setup

### 1. Clone and create config files

```sh
git clone https://github.com/galyron/exec_func_assist.git
cd exec_func_assist

cp .env.example .env
cp config.example.json config.json
```

Edit `.env` — fill in all four values:

```
DISCORD_BOT_TOKEN=      # from Discord Developer Portal
ANTHROPIC_API_KEY=      # sk-ant-...
JOPLIN_API_TOKEN=       # any random string, e.g.: openssl rand -hex 32
JOPLIN_DROPBOX_AUTH=    # see Joplin setup below
```

Edit `config.json` — at minimum set:

```json
"discord_channel_id": 123456789,     // right-click channel → Copy Channel ID (needs Developer Mode)
"discord_user_id":    123456789,     // right-click your name → Copy User ID
"user_name":          "YourName"
```

Optional: set `security_alerts_channel_id` to a Discord channel ID to receive alerts when unexpected users message the bot (leave `null` to log only).

### 2. Joplin one-time Dropbox auth

The bot reads your notes via the Joplin REST API. The Joplin container needs a Dropbox OAuth token to sync your notes.

```sh
docker compose run --rm --entrypoint sh joplin
# inside the container:
joplin config sync.target 7
joplin sync          # prints a browser auth URL — open it on your machine
joplin config sync.7.auth   # copy the output (token string)
exit
```

Paste the copied token into `.env`:

```
JOPLIN_DROPBOX_AUTH='<paste token here>'
```

Keep the single quotes in case the token contains special characters.

### 3. Google Calendar OAuth (MacBook only — requires a browser)

This step must be run on your MacBook, not on mbox. It opens a browser consent screen and writes `secrets/google_token.json`, which you then copy to mbox.

```sh
python3 -m venv .venv-setup
.venv-setup/bin/pip install google-api-python-client google-auth-oauthlib
.venv-setup/bin/python setup_calendar.py
```

The venv is throwaway — you can delete it after. `secrets/google_token.json` is gitignored; copy it to mbox manually (see the prod deploy section below).

To see which calendars are visible and exclude any unwanted ones:

```sh
docker compose run --rm bot python -m connectors.calendar
# then edit config.json → excluded_calendar_ids with any IDs to skip
```

---

## Running in development (MacBook)

```sh
docker compose up --build
```

Both containers start: `eva-bot-dev` (the bot) and `eva-joplin-dev` (Joplin CLI + Dropbox sync). The bot connects to Discord and begins its schedule.

**Time-accelerated debug run** — compress the day's schedule to verify all handlers fire in sequence (60× = 1 real second ≈ 1 simulated minute):

```sh
docker compose run --rm bot python bot.py --debug --debug-time "2026-03-24 07:25" --debug-multiplier 60
```

State files are written to `./data/` on the host (mounted as a volume). Clear them between test runs if needed:

```sh
rm -rf data/
```

**Verify connectors independently:**

```sh
docker compose run --rm bot python -m connectors.joplin
docker compose run --rm bot python -m connectors.calendar
```

**Run the test suite:**

```sh
python -m pytest tests/ -q
```

---

## Deploying to production (mbox)

### First deploy

On mbox, clone the repo and set up config:

```sh
ssh gabriell@192.168.178.24
git clone https://github.com/galyron/exec_func_assist.git ~/exec_func_assist
cd ~/exec_func_assist
cp .env.example .env            # fill in with production values
cp config.example.json config.json
mkdir -p secrets
exit
```

From MacBook, copy the Google Calendar token:

```sh
scp secrets/google_token.json gabriell@192.168.178.24:~/exec_func_assist/secrets/
```

Then start the stack on mbox:

```sh
ssh gabriell@192.168.178.24
cd ~/exec_func_assist
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The prod override sets `restart: always` and binds the Joplin port to `127.0.0.1` only (not exposed externally).

### Subsequent deploys

From MacBook, after pushing your changes:

```sh
./deploy.sh
```

This SSHs to mbox, runs `git pull --ff-only`, and restarts the stack with `docker compose up -d --build`.

### Check logs on production

```sh
ssh gabriell@192.168.178.24
docker logs eva-bot-prod -f
docker logs eva-joplin-prod -f
```

---

## Joplin task tags

The bot reads tasks exclusively from the `00_TODO` notebook (configurable via `todo_notebook` in `config.json`).

Tags are detected automatically from note titles. You don't need special syntax — write whatever feels natural. All of the following are recognised:

| Tag | What the bot sees | Write any of these in the note title |
|-----|-------------------|--------------------------------------|
| `[today]` | Must be done today — shown first in nudges | `today` · `by EOD` · `by EOB` · `do it today` · `must do today` · `urgent/today` |
| `[urgent]` | Drop everything | `urgent` · `asap` |
| `[this-week]` | Sometime this week | `this week` · `by EOW` · `by end of week` |
| `[high]` | Important, not time-bound | `important` · `high priority` · `[high]` |
| `[low-energy]` | Can do when tired or on the couch | `low energy` · `couch` · `[low-energy]` · `[couch]` |
| `[easy]` | Quick win, minimal effort | `easy` · `quick win` · `quick` · `[easy]` |

Multiple tags on one task are fine: *"Fix login bug urgent/today"* → `[today]` + `[urgent]`.

---

## Bot behaviour reference

| Time | Mode | Behaviour |
|------|------|-----------|
| 07:30 | Morning | Structured interview — one question at a time, max 5 follow-ups |
| 09:15–16:00 | Work | Assertive nudges, task triage, concrete first actions |
| 16:00–20:30 | General | Lighter check-ins |
| 20:30+ | Recovery | Couch-compatible tasks only, 15-min max commitments |
| Weekend | Weekend | Silent unless user initiates; evening nudge optional |

**On-demand commands:**

| Message | Effect |
|---------|--------|
| `off today` | Suppresses all proactive messages for the day; bedtime still fires |
| `off today full silence` | Suppresses everything including bedtime |
| `done` / `I finished X` | Acknowledges completion; cancels pending follow-up |
| `I'm stuck` | LLM suggests smallest next step; schedules a 20-min follow-up |
| `skip` | Dismisses current suggestion |
| `add: <task>` | Appends task to local queue in state |
| `<USE_OPUS>` | Switches to claude-opus-4-6 for the session |
| anything else | General LLM response in current mode |

---

## Repository layout

```
bot.py                  Entry point and Discord client
config.py               Config loader
scheduler.py            APScheduler job registration
handlers/
  base.py               BaseHandler superclass
  morning.py            Morning interview (C8)
  kickoff.py            Day kick-off briefing (C9)
  checkin.py            Midday + evening check-ins (C10)
  bedtime.py            End-of-day review + bedtime reminder (C11)
  on_demand.py          On-demand message routing (C12)
  followup.py           20-min follow-up scheduler (C13)
connectors/
  joplin.py             Joplin REST API client
  calendar.py           Google Calendar client
  models.py             Shared Task / CalendarEvent / FreeWindow types
context/
  assembler.py          Context assembly + mode/energy determination
llm/
  client.py             Anthropic SDK wrapper + spend tracking
  prompts.py            System prompts keyed by Mode
state/
  manager.py            JSON state read/write
utils/
  clock.py              Clock abstraction — RealClock + DebugClock
joplin/
  Dockerfile            Joplin CLI container
  entrypoint.sh         Configures Joplin, runs sync loop + socat forwarder
secrets/                gitignored — google_token.json, google_client_secret.json
data/                   gitignored — state.json, interactions.json, memory.json
```
