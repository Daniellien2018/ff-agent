# Build Log — Fantasy Football Agent

A detailed record of what was built, why, the statistical/logical reasoning
behind each piece, the technical integrations added, and the real bugs
found and fixed along the way. `README.md` covers how to *use* the system;
this covers how it came to exist and how it actually works underneath.

Built from `fantasy-agent-design.md`, a pre-written design doc that specified
the strategy, the build phases, and the file layout in advance. This log
follows that phase ordering.

---

## 1. Overview

The system watches a 14-team full-PPR Sleeper league, runs a season
simulator, and emails weekly recommendations. Every recommendation is scored
as **Δ P(title)** — the change in probability of winning the *league*, not
just this week — because the design doc's central thesis is that these two
diverge often enough to matter (e.g., a first-round playoff bye roughly
*doubles* championship odds in a 6-team, byes-for-top-2 bracket, which is a
bigger lever than almost any single weekly decision).

The system is read-only against Sleeper (never writes a lineup) and
human-in-the-loop by design: it recommends, a person sets the actual lineup.

---

## 2. Phase 0 — Recon

**Files:** `src/sleeper_client.py`, `src/players.py`, `scripts/recon.py`

Built a disk-cached, read-only wrapper around Sleeper's public REST API
(no auth token needed), and a fuzzy player-name resolver (`rapidfuzz`,
`token_sort_ratio`) to map free-text names to Sleeper's internal player IDs
— needed because every other data source (Vegas feeds, projections) refers
to players by name, and names collide across suffixes (Jr./Sr./III),
nicknames, and DST naming conventions ("Bears" vs "Chicago Bears" vs "CHI").

Then ran `recon.py` against the **live** league and found several things the
design doc had explicitly flagged as unresolved:

- **Redraft vs. carryover/keeper — resolved to REDRAFT.** The league
  object's `settings.draft_rounds` field said `3`, which can't possibly fill
  15-player rosters across 14 teams — that's what made this ambiguous in the
  first place. But the *draft* object's own `settings.rounds` field said
  `15`, and the actual pick count (210 = 14 × 15) matched exactly, with zero
  `is_keeper` flags on any pick. Verdict: `league.settings.draft_rounds` is
  stale/misleading; the draft object is the source of truth.
- **FLEX eligibility — confirmed RB/WR/TE.** Sleeper's `roster_positions`
  literally contains the token `"FLEX"` (not e.g. `"WRRB_FLEX"` or
  `"SUPER_FLEX"`), and that token has fixed platform-wide semantics.
- **Waiver timing — empirically confirmed, and it didn't match config.**
  Pulled all of 2025's transaction timestamps and found claims process
  **Tuesday ~07:00–08:00 UTC (~3am ET)**, not whatever the raw
  `waiver_day_of_week` setting implied read literally. This directly changed
  the FAAB-recap email's intended schedule (Monday afternoon, not "12h
  before" measured naively).
- **2025 league was only 12 teams, not 14.** The league expanded for 2026.
  Two managers (`bigasshamster`, `percmuncher1`) have no 2025 history —
  `league_intel.py` (Phase 2) handles this by simply omitting them rather
  than guessing.

---

## 3. Phase 1 — DEF/K Streaming + Email Pipeline

**Files:** `src/projections/vegas_source.py`, `src/streaming.py`,
`src/explain.py`, `src/email_report.py`

### The statistics: Vegas implied team totals

A defense's fantasy output is driven mostly by how many points the
*opposing* offense scores, and Vegas prices that far better than any
positional stat we could compute ourselves. The formula:

```
implied_team_total = game_total / 2 − team_spread / 2
```

where `team_spread` is that team's own signed point spread (negative =
favorite). E.g., total 48.5, home team favored by 3.5 (`spread = -3.5`):
`home_implied = 24.25 + 1.75 = 26.0`, `away_implied = 24.25 − 1.75 = 22.5`
(sums back to the game total, as it should).

Defense scoring then gets mapped from *the opponent's* implied total through
the league's points-allowed bands (`+10` for a shutout down to `-4` for
35+), using linear interpolation between band midpoints
(`numpy.interp`) rather than a hard step function — this turns a continuous
input (Vegas's number) into a continuous output instead of an arbitrary
cliff at each band boundary. A flat +3-point bonus approximates
average sacks/INTs/defensive TDs (a simplification, flagged for later
calibration against real data once `backtest.py` exists).

Kicker scoring uses a much rougher heuristic: baseline points at a
league-average implied total, a small linear scaling with the *kicking*
team's own implied total (more scoring drives → more FG/XP chances), and a
flat dome bonus (no weather suppression). Openly a placeholder pending real
calibration.

**Data source:** the-odds-api.com, free tier. Degrades **honestly** — if
`ODDS_API_KEY` isn't set or the request fails, `streaming.py` returns
"unavailable, here's why" rather than silently guessing or crashing.

### The email pipeline

`explain.py` classifies every recommendation into a leverage tier based on
its Δ P(title) magnitude (this exact threshold logic is reused everywhere
in the system):

| Tier | Δ P(title) | Treatment |
|---|---|---|
| Critical | > 1.0% | Full reasoning, leads the email |
| Moderate | 0.2% – 1.0% | One-line rationale |
| Marginal | < 0.2% | Folded into a single summary line, not listed individually |

`email_report.py` renders matched HTML/plaintext pairs (plaintext isn't an
afterthought — it's genuinely readable on a phone) and sends via **Resend**
(the only required secret in the whole system).

---

## 4. Phase 2 — Season Simulator + Manager Intel

**Files:** `src/season_sim.py`, `src/league_intel.py`, `src/lineup_math.py`

### The statistics: Monte Carlo season simulation

This is the control system everything else is scored against. Mechanically:

1. **Estimate each team's weekly scoring distribution** (mean + variance)
   using **empirical Bayes shrinkage** toward a league-wide prior:
   ```
   shrunk_mean = (shrinkage_weeks × prior_mean + n_played × observed_mean)
                 / (shrinkage_weeks + n_played)
   ```
   `shrinkage_weeks` is a pseudo-count of "prior observations" (currently
   4). Early in the season, `n_played` is small, so the shrunk estimate
   stays close to the prior — this is deliberate: the design doc explicitly
   warns that Week 1–4 team-strength signal is almost pure noise, and an
   unshrunk model would have the weekly "mode" (see below) whipsaw on small
   samples. The prior itself comes from pooling **every team's actual score,
   every week, from the 2025 season** — a reasonable stand-in for "what does
   a typical team in this scoring system put up" before this year's results
   exist.

2. **Simulate the remaining season** on the *actual* schedule (already
   fixed and knowable — Sleeper exposes future weeks' matchup pairings even
   before they're played), 10,000 times, by drawing each team's weekly score
   from `Normal(shrunk_mean, shrunk_variance)`, clipped at zero.

3. **Determine seeding** each trial by `(wins desc, points_for desc)`, then
   run the actual 6-team bracket: seeds 1–2 bye into the semifinal, seeds
   3–6 play a wild-card round (3v6, 4v5), and the bracket re-seeds properly
   (the stronger remaining seed plays the weaker one in each round).

4. **`P(playoffs)`, `P(bye)`, `P(seed=s)`, `P(title)`** are just the fraction
   of the 10,000 trials landing in each outcome for a given team.

**Validation:** ran the simulator on fully symmetric synthetic data (14
identical teams) and confirmed it reproduces the design doc's own stated
baseline math *exactly*: ~7.1%/team title odds (1/14), summing to 1.0 across
all teams; playoff probabilities summing to 6.0 (6 spots); bye probabilities
summing to 2.0 (2 byes). This is in the test suite
(`tests/test_season_sim.py`) as a standing regression check.

**The universal scoring mechanism — `weekly_mean_overrides`:** any decision
(stream a DEF, swap a lineup slot) gets scored by re-running the simulator
with that decision's point impact added to one team's mean for one specific
week, then diffing the resulting `P(title)` against a baseline run. This is
literally the "Δ P(title) for any decision = re-simulate with the change
applied" mechanism the design doc called for, and it's the same function
call every decision type in the codebase uses (`src/decisions.py`).

### Win-probability calculation

For the weekly `Win prob` display, treats the score difference between two
teams as approximately Normal (mean = difference of means, variance = sum
of variances, since the two teams' outcomes are modeled independently) and
computes `P(my_score > opp_score)` via the normal CDF (`math.erf`-based,
no `scipy` dependency needed for one calculation).

### Dynamic mode switching (§2.3)

`determine_mode(p_playoffs, week)` encodes:

| Condition | Mode | Why |
|---|---|---|
| P(playoffs) > 85% | Chase The Bye | The 1st/2nd seed bye is worth ~2× title odds — bigger than any other lever. Stop optimizing for this week; optimize for seeding/Weeks 15–17. |
| 30–85% | Win Now | In this band, Δ P(title) ≈ Δ P(win this week), so just maximize weekly win probability. |
| <30%, week ≥ 11 | All-In Variance | Probably not making it on the current trajectory with limited season left — need a tail outcome, not a good average week. |
| <15%, week ≥ 12 | Salvage | Nearly eliminated, late. No pick trading in this league means tanking earns nothing, so: keep playing, just with fully depleted expectations. |

Salvage is a *stricter subset* of All-In Variance's condition, so the code
checks it first rather than walking the table as independent branches.
Early-season combinations the table doesn't cover (e.g., 20% in Week 3)
default to Win Now — deliberately the boring, safe choice rather than
reacting to noise.

**Honest limitation:** mode is currently a *label*, not a *lever* — it's
shown in the email but doesn't yet change what streaming/start-sit/FAAB
actually pick. Making it change behavior (e.g., actually seeking correlated
variance via QB/WR stacking in All-In Variance mode) requires the
correlation/variance model below, which isn't built.

### Manager intel from 2025 (§2.6)

`league_intel.py` computes, per manager, from real 2025 transaction and
matchup data:

- **Lineup efficiency** — actual points scored vs. the mathematically
  optimal lineup they could have started that week (see the FLEX-solver
  discussion below — this reuses the same solver as the live optimizer).
- **Panic score** — the fraction of a manager's waiver adds that got dropped
  again within 2 weeks, a proxy for "bails after one bad week" (a source of
  cheap future pickups).
- **Transaction activity and trade count** — a rough proxy for engagement.

### The FLEX-eligible lineup solver — why a greedy algorithm is provably exact

`lineup_math.py`'s `optimal_lineup()` needs to assign players to slots where
most slots are position-exclusive (QB, RB, WR, TE, K, DEF) but FLEX accepts
RB/WR/TE. The implementation is a two-phase greedy:

1. Fill each exclusive slot with the top-N players of that exact position.
2. Fill FLEX slots with the top-N *leftover* players from any FLEX-eligible
   position.

This isn't just a heuristic — it's **exact** for this structure. Each
exclusive-position category is a *partition matroid* (fixed capacity per
position), and FLEX is a *uniform matroid* over the remaining FLEX-eligible
players. A weighted maximum-weight basis of a matroid is found exactly by
greedy selection (a standard result from matroid theory), and the union of
independent matroids is itself a matroid — so this greedy is provably
optimal here, not just "usually right." (It would need real matroid-union
augmentation, not this simple greedy, for a league with two *differently*
restricted flex categories at once — e.g. both a regular FLEX and a
SUPER_FLEX — which isn't this league's shape.)

This was tested against a deliberately adversarial case (three elite TEs
competing against a deep RB corps for FLEX slots) and matched the
hand-computed optimum exactly. It was originally attempted via a PuLP/CBC
integer program, but the bundled CBC solver binary turned out to be
x86_64-only and crashed (`Bad CPU type in executable`) on this Apple
Silicon machine — the from-scratch greedy replaced it entirely, with the
added benefit of no external solver dependency.

---

## 5. Phase 3 — Roster Discipline + FAAB

**Files:** `src/roster.py`, `src/faab.py`

Direct implementations of the design doc's §2.2 thesis: with 5 bench spots
across 14 teams, roster spots are the scarce resource, not FAAB dollars
($1,000 budget is comparatively "free").

- **IR flagging** — any rostered (non-reserve) player with a
  Doubtful/Out/IR/PUP/etc. designation gets flagged as IR-slot-eligible,
  since this league's settings confirm it accepts DOUBTFUL, not just OUT.
- **Backup QB/K/DEF detection** — flags any position where more than one is
  rostered (the doc's "never carry a backup" rule).
- **Bye-week crunch** — reuses the exact same FLEX-aware slot-filling logic
  from `lineup_math.py`, but for a *feasibility* question instead of an
  optimization one: remove bye-week players from the pool and check whether
  every required slot can still be filled. An unfillable slot is a real
  crunch, independent of who's actually best.
- **FAAB bid sizing** — maps the same critical/moderate/marginal leverage
  tiers onto a percentage of *remaining* budget (4% / 1.5% / 1% — streamers
  get a flat, lower 1% regardless of tier, since their edge decays next
  week when the matchup changes). A "walk away above" ceiling is set at 2×
  the suggested bid. This is an explicitly-flagged v1 heuristic, not a
  market-clearing model — pending calibration against real historical FAAB
  data once a backtest harness exists.

---

## 6. Phase 4 (Partial) — RB/WR/TE Start-Sit

**Files:** `src/optimizer.py`, `src/projections/sleeper_source.py`

### Finding a real per-player projections source

Discovered (by testing, not from documentation — it's unofficial) that
Sleeper exposes an undocumented endpoint:

```
GET https://api.sleeper.app/projections/nfl/{season}/{week}
    ?season_type=regular&position[]=QB&position[]=RB&...
```

This returns real, per-player PPR projections (`pts_ppr` field) sourced from
RotoWire and surfaced through Sleeper's own app — confirmed live for the
2026 season with meaningful data for ~580 of ~3,300 rostered-relevant
players (the rest are deep/irrelevant players with no real projection).

### What this fixed: identical Week 1 projections

The season simulator's team-strength model (§4 above) has **zero signal**
to distinguish teams before any games are played — every team collapses to
the exact same shrinkage-prior mean. This surfaced as a real, user-caught
bug: the Week 1 email showed **127.0 projected for both sides of the
matchup and an exact 50% win probability**, while Sleeper's own app showed
122/53% for the same matchup.

The fix: sum a team's actual **currently-SET starters'** `pts_ppr` from the
real per-player feed, and express the difference from that team's
historical-model mean as a **one-week-only override** fed into the same
`weekly_mean_overrides` mechanism every other decision already uses. This
means the real per-player signal flows into *everything* consistently — the
displayed `Projected`/`Opp` numbers, the win-probability calculation, and
the `P(playoffs)/P(title)` baseline all move together, rather than the
display line quietly disagreeing with the model underneath it. Verified
this restored numbers close to Sleeper's own (128.5/124.2, 55% — the
remaining few points of gap is expected, since it's a different projection
blend, not a bug).

### The optimizer itself

`optimizer.py` runs the *same* FLEX-aware solver from `lineup_math.py`
against a team's **entire roster** (not just current starters) using real
per-player projections, then diffs the resulting optimal assignment against
whatever's currently set. Each resulting swap is scored independently, using
the same `combined_overrides` composition pattern as DEF/K streaming, so a
start-sit call and a streaming call in the same email sit on the same
Δ P(title) footing rather than being computed from different baselines.

Per the design doc's §4.6 leverage rule, swaps below the "marginal"
threshold get folded into a single summary line (e.g. "Also near-ties
(<0.2%): ...") instead of listed as full decisions — most weeks, most
decisions really are near-ties, and presenting them as if they mattered
just trains a reader to stop reading.

**Validated against the live roster**: correctly found *no* recommended
swap when the current lineup was already near-optimal, and — when George
Kittle (flagged PUP elsewhere in the system) was double-checked by hand —
confirmed his own real projection (10.09) still legitimately beat the
bench alternative (8.42), i.e. the "no swap" call was a real signal from
the data, not a blind spot in the code.

### What Phase 4 still doesn't do

No correlation or variance modeling — no QB/WR stacking awareness, no
RB-committee anti-correlation, no boom/bust-vs-median distinction. That's
the design doc's §4.4 (bootstrap residuals + Gaussian copula), a genuinely
separate, harder piece of statistical work, not yet built. This is also
what would make the "mode" (§4 above) actually change behavior instead of
just being a label.

---

## 7. Scripts, Scheduling, and the Manual Workflow

**Files:** `scripts/weekly_lineup.py`, `scripts/recap.py`,
`scripts/inactives_check.py`, `scripts/whoami.py`, `run.sh`

Three weekly entrypoints implementing the design doc's §5 cadence:

- **`weekly_lineup.py`** (Sunday) — the main email: mode, season odds,
  DEF/K stream, start-sit, and roster-discipline watch items. Also writes an
  injury-status snapshot consumed by...
- **`inactives_check.py`** (optionally, later the same day) — a
  *conditional* alert that only sends an email if a starter's status has
  gotten worse since the morning snapshot. Silence is the expected, correct
  outcome most weeks — its arrival is itself the signal, per the design doc.
- **`recap.py`** (Monday afternoon) — FAAB targets ahead of the empirically
  confirmed Tuesday ~3am ET waiver processing (see Phase 0).
- **`whoami.py`** — a sanity-check script (not in the original design doc,
  added during this build) that prints the live roster in a human-readable
  form specifically so it can be eyeballed against the Sleeper app — this is
  what confirmed `MY_ROSTER_ID = 5` was correct.
- **`run.sh`** — a one-command wrapper (`./run.sh` or `./run.sh --dry-run`)
  running the lineup + recap emails together, added once manual (rather than
  scheduled) operation was the chosen workflow.

**Scheduling exists but is deliberately turned off.** Two paths were built:
a set of macOS `launchd` plists (`scripts/launchd/`) for local scheduling,
and a GitHub Actions workflow (`.github/workflows/ff-agent.yml`) for
cloud scheduling with a date-keyed cache to hand the injury snapshot between
the two same-day Sunday jobs (GitHub Actions runners are otherwise
stateless between separate workflow triggers). The Actions workflow's cron
schedule is currently **commented out**: it went live on push before the
required secrets (`RESEND_API_KEY`, `ODDS_API_KEY`) were added as repo
secrets, which would have caused it to fire and fail on its own schedule
without being asked to. `workflow_dispatch` (manual triggering from the
Actions tab) still works today; the cron block can be re-enabled once
secrets are configured.

---

## 8. Technical Integrations

| Integration | What it's for | Auth | Free tier |
|---|---|---|---|
| **Sleeper API** | League/roster/matchup/transaction/player data | None (public, read-only) | Unlimited (rate-limited, cached 24h for the player blob) |
| **The Odds API** (the-odds-api.com) | Vegas spreads/totals → implied team totals for DEF/K streaming | `ODDS_API_KEY` | 500 requests/month |
| **Sleeper's undocumented projections endpoint** | Real per-player PPR projections (RotoWire-sourced) for start-sit and this-week accuracy | None | Same as Sleeper API |
| **Resend** (resend.com) | Email delivery (HTML + plaintext) | `RESEND_API_KEY` | Free tier; shared test sender `onboarding@resend.dev` only delivers to your own registered Resend email |
| **GitHub** | Version control + optional cloud scheduling | SSH/HTTPS + `gh` CLI OAuth | N/A |

Both API keys live in `.env` (gitignored, never committed), loaded via
`python-dotenv`. Resend is the *only* required secret for the system to be
minimally useful (email); the Odds API key is required for DEF/K streaming
specifically but everything else degrades gracefully without it.

---

## 9. Real Bugs and Operational Issues Found (and Fixed)

Worth recording since some of these reflect real, non-obvious lessons:

1. **`league.settings.draft_rounds` was stale/wrong** — said 3 rounds, real
   draft was 15. Caught by cross-checking against the draft object's own
   settings and the actual pick count, not by trusting a single field.
2. **PuLP's bundled CBC solver doesn't run on Apple Silicon** — crashed with
   `Bad CPU type in executable`. Replaced with a from-scratch (and
   provably-exact, see Phase 4) greedy solver rather than fighting solver
   binaries.
3. **2025 league was 12 teams, not 14** — the assumption "same 14 managers
   both seasons" was wrong; two managers are new for 2026 with no history.
4. **GitHub SSH push failed with "Repository not found"** despite the repo
   existing — root cause was the local SSH key already being registered to
   a *different* GitHub account, so GitHub authenticated the SSH connection
   as the wrong identity and returned a private-repo-style 404 rather than a
   clear auth error. Fixed by switching to HTTPS and using `gh`'s own OAuth
   token as the git credential helper (`gh auth setup-git`) instead of the
   mismatched SSH key.
5. **GitHub push rejected `.github/workflows/*.yml`** with a scope error —
   pushing anything under `.github/workflows/` requires the OAuth
   `workflow` scope specifically, which the initial `gh auth login` grant
   didn't include. Fixed with `gh auth refresh -h github.com -s workflow`.
6. **`RESEND_API_KEY` was accidentally set to an email address** (the
   destination address, meant for a different config field) rather than an
   actual Resend API key — surfaced as a clean `403 API key is invalid`
   from Resend itself, not a code bug.
7. **Resend's sandbox sender rejected the configured recipient** — the
   `to` address used a Gmail `+alias` (`daniellien2018+fantasy@gmail.com`),
   but Resend's shared test sender only delivers to the *exact* address the
   account is registered with and doesn't resolve Gmail aliases. Fixed by
   using the bare address; the `[FF]` subject prefix already gives the same
   auto-labeling a Gmail filter would via the alias.
8. **Local pytest run failed to even *collect* tests** once a real `.env`
   file existed, with a sandbox-specific permission error trying to `stat()`
   it. Fixed with a root `conftest.py` (`collect_ignore`) — a sandbox-only
   issue, not something that affects real environments or CI, since `.env`
   is gitignored and never present in CI.

---

## 10. Testing

`tests/` — 47 tests, all against synthetic/mocked data, zero network calls,
safe to run anytime including in CI:

- **`test_season_sim.py`** — validates the Monte Carlo simulator reproduces
  the design doc's own baseline math on symmetric synthetic input, that
  stronger teams get higher title odds, that `weekly_mean_overrides` moves
  Δ P(title) in the correct direction, and that shrinkage actually pulls
  small samples toward the prior.
- **`test_lineup_math.py`** / **`test_optimizer.py`** — the FLEX-matroid
  edge case (three elite TEs vs. deep RBs) and real bench-upgrade scenarios.
- **`test_streaming.py`** — DEF/K scoring monotonicity and graceful
  degradation without an odds key.
- **`test_sleeper_client.py`** — disk-cache TTL behavior via a fake HTTP
  session (no real network).
- **`test_players.py`**, **`test_roster.py`**, **`test_faab.py`**,
  **`test_explain.py`**, **`test_sleeper_source.py`** — resolver accuracy,
  IR/backup/bye-crunch logic, FAAB tier scaling, leverage classification.

---

## 11. What's Not Built

Being explicit about scope boundaries, per the design doc's own phase
ordering (these were always later-phase/lower-priority, not oversights):

- **Correlation/variance modeling** (§4.4) — bootstrap residual
  distributions, Gaussian copula for stacking/anti-correlation awareness.
  This is what would make "mode" (§4/§2.3) actually change behavior instead
  of being a label.
- **Trade targeting** (`trades.py`, §2.5) — the Weeks 10–13 deadline
  acquisition-window logic.
- **Playoff-schedule targeting** (`playoff_schedule.py`, §2.4) — was meant
  to go live by Week 8, building the roster for Weeks 15–17 specifically.
- **Backtesting** (`backtest.py`) — replaying the 2025 season to calibrate
  the various heuristic constants currently flagged as placeholders (the
  DEF bonus-points estimate, the K heuristic coefficients, the FAAB bid
  percentages).
- **`bye_weeks` in `config.yaml`** — left empty; needs the official 2026
  NFL schedule filled in to activate the bye-week-crunch warning.
