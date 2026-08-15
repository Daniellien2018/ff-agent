# Fantasy Football Agent

A personal decision-support system for a 14-team full-PPR Sleeper league
("VG Premier"). It pulls live league data, runs a Monte Carlo season
simulator, checks DEF/K streaming against Vegas lines, finds RB/WR/TE
start-sit upgrades from real per-player projections, flags roster-discipline
issues (IR, bye-week crunches, backup QB/K/DEF), and emails the results.

**You set your lineup manually in the Sleeper app.** This system only reads
data and recommends — it never writes to Sleeper and never sets a lineup
for you. Every recommendation is scored as **Δ P(title)** (the change in
your probability of winning the league), not just this week's win
probability — see `fantasy-agent-design.md` for the full reasoning.

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Recon: league settings, redraft-vs-keeper, FLEX eligibility, waiver timing | ✅ Done |
| 1 | DEF/K streaming (Vegas implied totals) + email pipeline | ✅ Done |
| 2 | Season simulator (P(playoffs)/P(bye)/P(title)) + 2025 manager intel | ✅ Done |
| 3 | Roster discipline: IR flagging, backup QB/K/DEF, bye-week crunch, FAAB sizing | ✅ Done |
| 4 | RB/WR/TE start-sit | ⚠️ Partial — mean-projection optimizer live; correlation/variance modeling (stacking, boom-bust) not built |
| 5 | Trade targeting, playoff-schedule targeting, backtesting | ❌ Not built |

Read `fantasy-agent-design.md` for the design rationale — it's the source of
truth this whole system was built from, including why each phase is ordered
the way it is.

## Requirements

- Python 3.11+ (developed/tested on 3.13)
- A [Resend](https://resend.com) account (free tier) — the only required secret, for email delivery
- A [The Odds API](https://the-odds-api.com) account (free tier: 500 requests/month) — powers DEF/K streaming off Vegas lines
- No Sleeper account/token needed — its API is public and read-only

## Installation

```bash
git clone git@github.com:Daniellien2018/ff-agent.git
cd ff-agent

python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp env.template .env
```

Then edit `.env` and fill in both keys:

```
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ODDS_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- **Resend key:** resend.com/api-keys → Create API Key. The default sender
  (`onboarding@resend.dev`, Resend's shared test sender) only delivers to the
  exact email address your Resend account is registered with — see the
  comments in `config.yaml`'s `email:` block if you want to send to a
  different address (requires verifying your own domain in Resend).
- **Odds API key:** the-odds-api.com → sign up → copy your key from the
  dashboard. Free tier is one weekly pull, plenty for this.

Both keys are read from `.env` via `python-dotenv` — nothing needs to be
exported into your shell manually.

## Configuration

Everything league-specific lives in `config.yaml`, with inline comments
noting what's confirmed via live data vs. still assumed. Two things worth
knowing before your first real week:

- **`bye_weeks` is empty.** Fill it in from the official 2026 NFL schedule
  (team abbreviation → bye week number) to activate the bye-week-crunch
  warning — it silently no-ops until then, it doesn't guess.
- **`email.to`** must be deliverable by your Resend setup (see the comment
  above it) — it currently doesn't use a Gmail `+alias` because Resend's
  shared test sender doesn't resolve those.
- **`MY_ROSTER_ID = 5`** is hardcoded at the top of each `scripts/*.py` file
  (confirmed once via `scripts/recon.py` against the live league). It won't
  change season to season for the same team, but if you ever swap Sleeper
  accounts/teams, update it in all three weekly scripts.

## Running it

The whole weekly routine in one command:

```bash
./run.sh --dry-run   # preview both emails without sending anything
./run.sh              # send both emails for real
```

Nothing runs automatically by default — see "Automating it" below if you
want that later. For now, just run `./run.sh` whenever you want to check in
(Sunday morning is the natural time).

Individual scripts, if you want to run just one:

```bash
.venv/bin/python scripts/weekly_lineup.py [--dry-run]   # lineup + DEF/K stream + start-sit, main weekly email
.venv/bin/python scripts/recap.py [--dry-run]            # FAAB targets -- run before Tuesday's waiver processing
.venv/bin/python scripts/inactives_check.py              # only emails if a starter's status changed since the last lineup run
.venv/bin/python scripts/whoami.py                       # sanity check: prints your live roster to eyeball against the Sleeper app
.venv/bin/python scripts/recon.py                        # one-time league discovery (already run -- see data/recon/*.json)
```

Suggested cadence, confirmed against this league's real waiver timing
(`data/recon/waiver_timing.json` — waivers clear Tuesday ~3am ET, not
whatever the raw `waiver_day_of_week` setting implies on its own):

| When | What | Why |
|---|---|---|
| Sunday morning | `weekly_lineup.py` | Lineup, DEF/K stream, start-sit, roster flags |
| Sunday ~11:45am (optional) | `inactives_check.py` | Only sends if a status changed — silence is the expected outcome most weeks |
| Monday afternoon | `recap.py` | FAAB targets, comfortably ahead of Tuesday's ~3am ET waiver run |

## Automating it (optional)

Both paths below are already built, just not turned on:

**Locally, via `launchd` (macOS)** — runs while your Mac is on:

```bash
cp scripts/launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ff-agent.weekly-lineup.plist
launchctl load ~/Library/LaunchAgents/com.ff-agent.inactives-check.plist
launchctl load ~/Library/LaunchAgents/com.ff-agent.recap.plist
```

Logs land in `data/logs/`. Uninstall any time with
`launchctl unload ~/Library/LaunchAgents/<name>.plist`. The schedule uses
your Mac's local system timezone — adjust the `Hour`/`Minute` in the plists
if it's not set to US Eastern.

**Cloud, via GitHub Actions** — runs regardless of whether your laptop's on:

`.github/workflows/ff-agent.yml` exists with its cron schedule **commented
out** (it was turned off deliberately — see the comment at the top of that
file for why). To enable:

1. Add `RESEND_API_KEY` and `ODDS_API_KEY` under the repo's
   Settings → Secrets and variables → Actions
2. Uncomment the `schedule:` block in `ff-agent.yml`

Until then, you can still trigger any of the three scripts manually from
GitHub's Actions tab → "Fantasy Football Agent" → "Run workflow" — no setup
needed for that.

## Testing

```bash
.venv/bin/python -m pytest
```

47 tests, all against synthetic/mocked data — no network calls, safe to run
anytime, including in CI. Notably: the season simulator's symmetric-input
test reproduces the design doc's own baseline math exactly (~7.1%/team title
odds pre-season, 6/14 playoff spots, 2/14 byes), and the lineup-math tests
cover the FLEX-eligibility edge case that a naive greedy gets wrong.

## Project layout

```
config.yaml              # all league-specific settings, with provenance comments
env.template              # copy to .env and fill in
run.sh                    # one-command weekly routine
src/
  sleeper_client.py        # read-only, disk-cached Sleeper API wrapper
  players.py                # player cache + fuzzy name -> player_id resolver
  season_sim.py              # Monte Carlo season simulator -- the control system
  league_intel.py             # 2025 manager efficiency/panic/trade intel
  league_data.py               # shared live-data glue (free agents, priors, projections)
  streaming.py                  # DEF/K streaming off Vegas implied totals
  roster.py                      # IR flagging, backup QB/K/DEF, bye-week crunch
  faab.py                         # FAAB bid sizing
  optimizer.py                     # RB/WR/TE start-sit (mean-projection slice)
  lineup_math.py                    # FLEX-aware optimal-lineup solver, shared by the above two
  decisions.py                       # scores streaming + start-sit swaps as Δ P(title)
  explain.py                          # leverage classification + rationale formatting
  email_report.py                      # HTML/plaintext email rendering + Resend delivery
  projections/
    vegas_source.py                     # Vegas implied team totals (the-odds-api.com)
    sleeper_source.py                    # real per-player PPR projections (Sleeper/RotoWire)
scripts/
  recon.py                # one-time league discovery
  weekly_lineup.py          # Sunday lineup email (main entrypoint)
  recap.py                   # pre-waiver FAAB recap email
  inactives_check.py           # conditional same-day injury-status alert
  whoami.py                     # roster sanity check
  launchd/                       # optional local scheduling (macOS)
.github/workflows/ff-agent.yml  # optional cloud scheduling (GitHub Actions)
tests/                    # pytest suite, no network calls
data/
  cache/                   # disk cache, gitignored (24h TTL for players, shorter for odds/projections)
  recon/                    # one-time recon output, gitignored
  logs/                       # launchd logs if you enable local scheduling, gitignored
```

## Design doc

`fantasy-agent-design.md` is the source of truth this was built from —
league strategy reasoning, the full build-phase plan, and the "things that
will bite you" list. Read it first if you're picking this back up after time
away.
