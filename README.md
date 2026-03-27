# EVA — Executive Function Assistant

A self-hosted Discord bot that acts as an executive function prosthetic. It sends proactive structured check-ins, task suggestions, and energy-aware nudges throughout the day, using Claude as its language backend.

**Stack:** Python 3.12 · discord.py 2.x · APScheduler · Anthropic Claude API · Joplin · Google Calendar · Docker Compose

---

> ⚠️ **Early-stage software — use with realistic expectations.**
>
> EVA is a personal project under active development. It is not thoroughly tested in all configurations and has known rough edges (see [Known Issues](#known-issues)). Scheduled messages occasionally misfire after a bot restart. The LLM responses are non-deterministic by nature. Calendar write access requires re-running OAuth if the token was created before write scope was added. If you run this, expect to occasionally need to clear state files or restart the stack.

---

## Features

### Proactive daily schedule

EVA fires messages on a configurable schedule without any user action:

| Time (default) | What happens |
|---|---|
| **07:30** (weekdays) | Morning interview — three questions, one at a time: energy level, main goal, likely blockers. EVA generates a sharp day-plan summary when all three are answered. A retry nudge fires 90 minutes later if there's no response. |
| **09:15** (weekdays) | Day kick-off — calendar summary, free windows, top 2–3 task suggestions with a concrete first action for each. |
| **13:00** (weekdays) | Midday check-in — names the single most important thing to get done before end of day. Includes [All good / I'm struggling / Skip] buttons. |
| **20:30** (weekdays + optional weekend) | Evening check-in — couch-compatible tasks only, 15-minute max commitment, buttons. |
| **22:30** (every day) | End-of-day review — LLM micro-review of the day's interactions. Skipped if `off today` was declared. |
| **23:00** (every day) | Bedtime reminder — fixed message, only suppressed by `off today full silence`. |

All times and days are configurable in `config.json`. Weekend suppression is built into the scheduler triggers — changing a time doesn't require code changes.

### On-demand messages

Send any of the following at any time (channel or DM):

| Message | Effect |
|---|---|
| `off today` | Suppresses all proactive messages for the rest of the day. Bedtime still fires. |
| `off today full silence` | Suppresses everything, including bedtime. |
| `done` / `I finished` / `I'm done with X` | Acknowledges completion, cancels any pending follow-up timer. |
| `done: <task description>` | Marks the matching task as done in Joplin. Example: `done: fix login bug` — EVA finds the closest matching task and checks it off. |
| `done <task description>` | Same as above without the colon. Example: `done send invoice to Müller`. |
| `I'm stuck` / `struggling` | EVA names the most likely blocker based on your task list and recent context, gives the single smallest action to break the freeze, then offers a commitment timer. |
| `skip` | Dismisses the current suggestion. |
| `add: <task>` | Appends the task as a checklist item to the Joplin inbox note (`99 - added by eva` by default). Example: `add: call Cristian about Certificat Energetic`. |
| `schedule: <description>` | Creates a Google Calendar event. Example: `schedule: dentist on Thursday at 14:00 for 1 hour`. EVA extracts the details and confirms. |
| `add event: <description>` | Same as `schedule:`. |
| `I need <N> minutes to <task>` | Sets a commitment timer. EVA confirms and checks back in exactly N minutes. Example: `I need 17 minutes to finish the proposal`. |
| `give me <N> min` | Same — short form. Example: `give me 25 min`. |
| `<N> min` | Bare timer. Falls back to last suggestion as the task description. Example: `20 min`. |
| `<USE_OPUS>` | Switches to `claude-opus-4-6` for the session (reverts after 10 messages or end of day). |
| anything else | General LLM response in the current mode, with full task and calendar context. |

### Manual routine triggers

Fire any scheduled routine immediately (replies to wherever the command came from — channel or DM):

```
!morning    !kickoff    !midday    !evening    !eod    !bedtime    !retry
```

### Commitment timer

After EVA suggests something and you say `I'm stuck`, a timer picker appears:

```
[10 min]  [20 min]  [30 min]  [45 min]  [No timer]
```

Click a button and EVA schedules a real APScheduler one-shot job. When it fires:

> Gabriell — 17 minutes are up. Did you do it or not?

with [Done ✅ / Still working on it / Skipped it] buttons.

You can also commit proactively at any time:
> `I need 17 minutes to finish the report`
> → *Committed — 17 minutes for: finish the report. I'll check back at 10:47.*

### Joplin task integration

EVA reads your tasks from the `00_TODO` notebook (configurable). Tags are detected automatically from note titles — no special syntax needed:

| Tag | Meaning | Detected from |
|---|---|---|
| `[today]` | Must be done today — shown first | `today` · `by EOD` · `by EOB` · `urgent/today` |
| `[urgent]` | Drop everything | `urgent` · `asap` |
| `[this-week]` | Sometime this week | `this week` · `by EOW` |
| `[high]` | Important, not time-bound | `important` · `high priority` |
| `[low-energy]` | Can do when tired | `low energy` · `couch` · `[couch]` |
| `[easy]` | Quick win | `easy` · `quick win` · `quick` |

Multiple tags on one task are fine: *"Fix login bug urgent/today"* → `[today]` + `[urgent]`.

**Write-back:** `add:` appends checklist items to a dedicated inbox note (`99 - added by eva`). `done: <task>` checks off the matching item. The inbox note must exist in Joplin before first use.

### Google Calendar integration

EVA reads today's events and free windows and uses them to schedule nudges. It also creates events on request via `schedule:`. All calendars in your Google account are included unless excluded via `excluded_calendar_ids` in `config.json`.

### Conversation memory

EVA maintains context across messages. The last 20 interactions (both channel and DM, in chronological order) are sent to Claude as actual conversation turns — not just a text summary. This means EVA remembers what you said in the morning interview when generating the afternoon check-in.

### Tone

Hard accountability by default. EVA names the cost of delay directly, gives the first concrete physical action (never abstract suggestions), and does not offer soft exits. Work mode = maximum pressure. Recovery mode = still pushes, couch-compatible tasks only. This is intentional and configurable via `llm/prompts.py`.

---

## Known Issues

- **Duplicate messages after restart.** If the bot restarts within 60 seconds of a scheduled job time, APScheduler may fire that job again on startup (misfire grace window). A `restart: always` policy in Docker Compose combined with `up -d --build` can briefly run two containers simultaneously — both will connect to Discord and both will fire. `deploy.sh` mitigates this with an explicit `stop` before rebuild, but brief overlap is still possible.

- **Morning routine not firing.** If the daily state file is corrupted or left in an inconsistent state (e.g. after the midnight duplicate-message incident), the morning routine may appear to skip. Fix: clear `data/state.json` after a bad deploy. The bot reinitialises on next startup.

- **LLM day/time references.** Without an explicit date in the trigger string, Claude may hallucinate the day of the week based on the operating mode (e.g. infer "it's Sunday" because mode=WEEKEND). All trigger strings now include full date+time, but if you add a new handler or trigger, include `clock.now().strftime("%A %Y-%m-%d %H:%M")` explicitly.

- **Calendar write requires OAuth re-auth.** If your `google_token.json` was created before write scope (`calendar.events`) was added, creating events will fail silently. Re-run `setup_calendar.py` after deleting the old token (see setup instructions).

- **Joplin inbox note must exist.** `add:` writes to a specific note (`99 - added by eva` by default). If this note doesn't exist in Joplin, the operation falls back to a local state queue that is not visible in Joplin. Create the note manually first.

- **`done: <task>` matching is fuzzy.** EVA uses the LLM to match your description to a Joplin task. If the description is ambiguous or the task list is large, it may match the wrong task. Always check Joplin after using this command.

- **No persistent timer across restarts.** Commitment timers are in-memory APScheduler `date` jobs. If the bot restarts while a timer is running, the timer is lost. EVA will not check back.

- **Monthly spend tracking is approximate.** Token prices are hardcoded in `llm/client.py` and may drift from Anthropic's actual pricing. The cap is enforced but the dollar figure shown is an estimate.

---

## Prerequisites

- Docker + Docker Compose
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications)) with `MESSAGE CONTENT INTENT` enabled
- An Anthropic API key
- Joplin desktop app syncing to Dropbox (the bot container runs its own Joplin CLI that syncs from the same Dropbox)
- A Google account with Calendar access (OAuth2 setup — one-time, see below)

---

## First-time setup

### 1. Clone and create config files

```sh
git clone https://github.com/galyron/exec_func_assist.git
cd exec_func_assist

cp .env.example .env
cp config.example.json config.json
```

Edit `.env`:

```
DISCORD_BOT_TOKEN=      # from Discord Developer Portal
ANTHROPIC_API_KEY=      # sk-ant-...
JOPLIN_API_TOKEN=       # any random string, e.g.: openssl rand -hex 32
JOPLIN_DROPBOX_AUTH=    # see Joplin setup below
```

Edit `config.json` — at minimum set these three:

```json
"discord_channel_id": 123456789,
"discord_user_id":    123456789,
"user_name":          "YourName"
```

### 2. Joplin one-time Dropbox auth

```sh
docker compose run --rm --entrypoint sh joplin
# inside the container:
joplin config sync.target 7
joplin sync          # prints a browser auth URL — open it on your machine
joplin config sync.7.auth   # copy the output token string
exit
```

Paste the copied token into `.env` as `JOPLIN_DROPBOX_AUTH='<token>'` (keep the single quotes).

Create the EVA inbox note in Joplin:
- Open Joplin on your desktop
- Inside `00_TODO` (or your configured `todo_notebook`), create a note titled `99 - added by eva`
- Leave it empty — EVA will append checklist items to it

### 3. Google Calendar OAuth (run on a machine with a browser)

```sh
python3 -m venv .venv-setup
.venv-setup/bin/pip install google-api-python-client google-auth-oauthlib
.venv-setup/bin/python setup_calendar.py
```

This opens a browser consent screen and writes `secrets/google_token.json`. If running on a headless machine, run this step on your laptop and copy the token:

```sh
scp secrets/google_token.json user@mbox:~/services/exec_func_assist/secrets/
```

**To exclude calendars** (e.g. birthdays, holidays):

```sh
docker compose run --rm bot python -m connectors.calendar
# lists all calendar IDs — paste any to exclude into config.json → excluded_calendar_ids
```

---

## Configuration reference

All settings live in `config.json` (committed, no secrets). Secrets are in `.env` (gitignored).

| Key | Default | Description |
|---|---|---|
| `discord_channel_id` | — | Channel ID where EVA posts and listens |
| `discord_user_id` | — | Your Discord user ID — all other users are silently dropped |
| `user_name` | `"Gabriell"` | Your name, used in all messages |
| `security_alerts_channel_id` | `null` | Channel ID for unauthorized-user alerts; `null` = log only |
| `todo_notebook` | `"00_TODO"` | Joplin notebook to read tasks from |
| `todo_inbox_note` | `"99 - added by eva"` | Note title for `add:` writes |
| `timezone` | `"Europe/Berlin"` | IANA timezone for all scheduling |
| `morning_routine` | `"07:30"` | Time of morning interview (weekdays) |
| `morning_routine_retry_window_min` | `90` | Minutes after morning_routine to send retry nudge |
| `work_start` | `"09:15"` | Start of work mode + day kick-off |
| `work_end` | `"16:00"` | End of work mode |
| `midday_checkin` | `"13:00"` | Midday check-in time |
| `evening_start` | `"20:30"` | Start of recovery mode + evening check-in |
| `end_of_day_review` | `"22:30"` | End-of-day review (all days) |
| `bedtime` | `"23:00"` | Bedtime reminder (all days) |
| `nudge_cooldown_min` | `45` | Minimum minutes between unsolicited messages |
| `min_gap_for_nudge_min` | `30` | Minimum calendar free-window size to trigger a nudge |
| `followup_default_min` | `20` | Default follow-up timer when no duration is specified |
| `monthly_cost_limit_usd` | `10.0` | Anthropic API spend cap per calendar month |
| `opus_session_max_messages` | `10` | Number of messages before Opus session reverts to Sonnet |
| `weekend_evening_nudge` | `true` | Whether to send the evening check-in on weekends |
| `excluded_calendar_ids` | `[]` | Google Calendar IDs to ignore |

---

## Running in development

```sh
docker compose up --build
```

Both containers start: `eva-bot-dev` (the bot) and `eva-joplin-dev` (Joplin CLI + Dropbox sync).

**Time-accelerated debug run** — compress the whole day's schedule to verify all handlers fire:

```sh
docker compose run --rm bot python bot.py --debug --debug-time "2026-03-24 07:25" --debug-multiplier 60
```

60× = 1 real second ≈ 1 simulated minute. The full schedule plays out in ~20 real minutes.

**Clear state between test runs:**

```sh
rm -rf data/
```

**Run the test suite:**

```sh
python -m pytest tests/ -q
```

**Verify connectors independently:**

```sh
docker compose run --rm bot python -m connectors.joplin
docker compose run --rm bot python -m connectors.calendar
```

---

## Deploying to production

### First deploy

On the server:

```sh
git clone https://github.com/galyron/exec_func_assist.git ~/services/exec_func_assist
cd ~/services/exec_func_assist
cp .env.example .env            # fill in production values
cp config.example.json config.json
mkdir -p secrets data
```

From your laptop, copy the Google Calendar token:

```sh
scp secrets/google_token.json user@mbox:~/services/exec_func_assist/secrets/
```

Start the stack:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Subsequent deploys

```sh
./deploy.sh
```

This SSHs to the server, stops the old bot container explicitly (to prevent overlapping instances), pulls latest code, and rebuilds. Each deploy appends a timestamped entry to `deploy.log` on the server — check it to confirm when the last deploy completed:

```sh
ssh user@mbox "tail ~/services/exec_func_assist/deploy.log"
```

### Logs

```sh
ssh user@mbox
docker logs eva-bot-prod -f
docker logs eva-joplin-prod -f
```

---

## Repository layout

```
bot.py                  Entry point and Discord client (EFABot)
config.py               Config loader — reads .env + config.json
scheduler.py            APScheduler cron job registration (C14)
deploy.sh               SSH deploy script — stop, pull, rebuild, log
setup_calendar.py       One-time Google OAuth flow

handlers/
  base.py               BaseHandler — _log_bot(), _log_user(), SendFn type
  morning.py            Morning interview, stateful 3-question flow (C8)
  kickoff.py            Day kick-off briefing at work_start (C9)
  checkin.py            Midday + evening check-ins with Discord buttons (C10)
  bedtime.py            End-of-day review + bedtime reminder (C11)
  on_demand.py          On-demand message routing + intent detection (C12)
  followup.py           Commitment timer — schedule, fire, TimerPickerView (C13)

connectors/
  joplin.py             Joplin REST API — task read + checklist write-back
  calendar.py           Google Calendar — event read + event creation
  models.py             Shared Task / CalendarEvent / FreeWindow types

context/
  assembler.py          Context assembly, mode + energy determination (C5)

llm/
  client.py             Anthropic SDK wrapper — multi-turn, spend tracking (C6)
  prompts.py            System prompts keyed by Mode

state/
  manager.py            JSON state read/write — atomic writes, daily rollover
  models.py             TypedDicts for all state shapes

utils/
  clock.py              Clock abstraction — RealClock + DebugClock

joplin/
  Dockerfile            Joplin CLI container
  entrypoint.sh         Runs Joplin sync loop + socat forwarder

secrets/                gitignored — google_token.json, google_client_secret.json
data/                   gitignored — state.json, interactions.json, memory.json
```

---

## License

Personal project — no license. Do not distribute.
