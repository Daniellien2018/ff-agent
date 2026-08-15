# Fantasy Football Agent — Design Doc (v4, build-ready)

**League:** VG Premier — 14 teams, full PPR, `league_id 1370916469910540288`
**Season:** 2026, drafted, pre-Week-1 (today Aug 13, 2026; Week 1 ≈ Sept 10)
**Delivery:** email to `daniellien2018+fantasy@gmail.com`
**Human in the loop:** agent recommends, Daniel sets lineups manually

> ## North Star
> **Maximize P(win league).** Every recommendation is scored as **Δ P(title)**,
> not Δ P(win this week). These diverge often enough to matter, and where they
> diverge is where the edge lives.

---

## 0. Locked Configuration

### Identity
```
username:            wapen
user_id:             1129342415627833344
league_id:           1370916469910540288
draft_id:            1370916469918937088
previous_league_id:  1258264260069314560     # 2025 season — backtest + manager intel
league_name:         VG Premier
season:              2026
status:              in_season                # = draft complete
```

### League shape

| Setting | Value | Strategic consequence |
|---|---|---|
| Teams | **14** | Low replacement level; barren waivers; talent scarce |
| Starters | **10** | QB, RB, RB, WR, WR, TE, FLEX, FLEX, K, DEF |
| Bench | **5** | **The binding constraint.** See §2.2 |
| IR slots | **1** (accepts DOUBTFUL) | Effectively a free 16th spot if managed |
| FLEX | RB/WR/TE ×2 | Single QB, no superflex. *Verify eligibility in Phase 0* |
| Scoring | **Full PPR** | No TE premium |
| Passing | 4 pt TD, 1 pt/25 yd | QB compressed → **stream QB, don't pay for it** |
| Rush/Rec | 6 pt TD, 1 pt/10 yd | Volume > efficiency in 14-team PPR |
| Waivers | **FAAB $1000**, min bid 0 | Money is cheap; roster spots are not |
| Waiver day | `waiver_day_of_week: 1`, clears 2d | **Verify empirically** — drives email timing |
| Playoffs | **6 of 14**, Weeks 15–17 | 3 rounds, 1 wk each → **seeds 1–2 get byes** |
| Regular season | Weeks 1–14 | |
| Trade deadline | **Week 13** | Acquisition window is Weeks 10–13 |
| Pick trading | **Disabled** | Player-for-player only; tanking is worthless |
| Draft rounds | 3 | See "Unresolved" below |
| Trade veto | Needs 14 votes | Effectively no vetoes — deals go through |
| Median matchups | Off | Pure H2H → variance management matters more |

### Unresolved — Phase 0 must answer

**Redraft or carryover/keeper?** `settings.type: 0` says redraft, but `draft_rounds: 3` cannot fill 210 roster spots across 14 teams, and `max_keepers: 1` + `auto_continue: on` + `previous_league_id` + `copy_from_league_id` all point at rosters persisting year to year.

This determines whether trade valuation uses rest-of-season value or multi-year value with age curves. **Check:** pull `/draft/{draft_id}/picks` and `/league/{id}/rosters` — if rosters came in near-full off 3 rounds, it's a carryover league. Confirm with the commissioner; don't infer this one.

---

## 1. Feasibility

Sleeper's API is read-only, free for non-commercial use, no auth token. Reads cover everything needed: league, rosters, matchups, transactions, drafts, players, trending. Writes are unavailable and **out of scope by choice** — you set lineups manually.

Cache aggressively (reported limits range ~90–1000 req/min by source).

---

## 2. Winning the League

### 2.1 The objective decomposition

```
P(title) = P(make playoffs) × P(win 2 or 3 games | seed)
```

Six teams, three playoff weeks, one week per round → **2 teams get a first-round bye.**

| Seed | Games to win | P(title | seed), even odds |
|---|---|---|
| 1–2 | 2 | **~25%** |
| 3–6 | 3 | ~12.5% |

**A first-round bye doubles your championship odds.** The gap between finishing 2nd and 3rd is larger than any other single lever available to you.

Realistic trajectory:

| Scenario | P(playoffs) | P(title) |
|---|---|---|
| Average team | 43% | ~7% (= 1/14 baseline) |
| Well-managed, no bye | 85% | ~11% |
| Well-managed + real bye shot | 85% | **~18–20%** |

So the ceiling of good management is roughly **3x baseline — and about half of that gain comes from seeding**, which most managers treat as a consolation prize rather than the prize.

**Consequence:** regular-season wins have *increasing* marginal value near the top of the standings. Coasting after clinching is a real mistake in this format, unlike in a 4-team-playoff league.

**Consequence:** `pick_trading: 0` and `draft_rounds: 3` mean draft position is nearly worthless, so **tanking has no value**. Always play to win.

### 2.2 Bench spots are the scarce resource, not FAAB

$1000 budget, 5 bench slots, 14 teams. Money is nearly free. Roster spots are brutally constrained. This inverts standard FAAB advice.

**Every add/drop must price the roster spot explicitly.** The question isn't "is this player worth $40," it's "is this player worth more than the best alternative use of a bench slot for the rest of the season."

Direct implications:

- **Never carry a backup QB, K, or DEF.** That's 12 of 15 spots on RB/WR/TE.
- **Stream QB.** 14 rostered starters against ~32 NFL starters means good QBs go unowned, and 4-pt passing TDs compress the elite-vs-streamer gap. Don't spend draft capital, FAAB, or a bench spot on QB depth.
- **Bid aggressively on players who will start.** Money is not the constraint; hoarding FAAB is a mistake.
- **Almost never roster a speculative stash.** The lottery ticket has to beat a startable body, and in a 14-team league it usually doesn't.
- **Use the IR slot relentlessly.** It accepts DOUBTFUL, not just OUT. An unused IR slot is a wasted roster spot — flag it every single week.

### 2.3 Dynamic mode switching

The right strategy depends on where you stand, and it changes across the season. This requires a live season simulator (§4.1) as the control system.

| P(playoffs) | Mode | Behavior |
|---|---|---|
| > 85% | **Chase the bye** | Max Δ P(title). Optimize roster for Weeks 15–17, not this week. Buy at the deadline. Reduce weekly variance to protect seeding. |
| 30–85% | **Win now** | Max P(win) each week. Δ P(title) ≈ Δ P(win week) in this band. |
| < 30%, Week ≥ 11 | **All-in variance** | Maximize weekly variance — you need tail outcomes. If carryover league, pivot to selling. |
| < 15%, Week ≥ 12 | **Salvage** | Redraft: keep playing (tanking worthless). Carryover: sell aging assets. |

The mode should be stated in every email so you always know what the model is optimizing for.

### 2.4 Build the roster for Weeks 15–17, not for September

Underexploited, because it requires thinking about it before it's too late.

- **Target favorable Week 15–17 matchups** when making adds and trades from ~Week 8 onward. A WR3 with three elite playoff matchups beats a WR2 with three brutal ones.
- **Weather is a real factor**, more than resting is. Weeks 15–17 are mid-December to early January — outdoor cold-weather games suppress passing and kicking. Dome and warm-weather teams get a quiet boost.
- **Snap-limiting risk** from clinched teams is modest in Week 17 (NFL Week 18 is where resting bites), but worth flagging for players on teams likely to have locked their seed.
- If you're chasing a bye (seeds 1–2 skip Week 15), **your roster only needs to peak Weeks 16–17.** That changes which players you want.

### 2.5 The acquisition window: Weeks 10–13

With 6 of 14 making the playoffs, roughly 5–6 teams are practically dead by Week 11. Trade deadline is Week 13. That's your window, and it's the single biggest available roster upgrade.

**Consolidation trades are structurally favored here.** No pick trading means player-for-player only. A 2-for-1 that upgrades a starting slot *also frees a bench spot* — you capture value twice, and bench spots are your scarce resource (§2.2). Actively seek quantity-for-quality.

Also: `veto_votes_needed: 14` means vetoes effectively never happen. Deals you agree on will go through.

### 2.6 Exploit your leaguemates — you have the data

`previous_league_id: 1258264260069314560` is the 2025 season with the same 14 managers and the same scoring. Compute, per manager:

- **Lineup efficiency** — actual points scored vs. their optimal possible lineup, weekly. Identifies who leaves points on the bench.
- **Transaction frequency and recency** — identifies checked-out managers.
- **Panic behavior** — do they drop players after one bad week? Those are free acquisitions.
- **Trade history** — who trades at all, and who systematically overpays for name value or for RBs.

In a 14-team league you almost certainly have 2–3 disengaged managers. Their good players are acquirable cheaply, and their weekly lineups are exploitable when you face them (they inflate your win probability, which the season simulator should account for rather than assuming all opponents are equally strong).

### 2.7 Variance as a deliberate lever

Direction of the right bet flips by situation:

- **Regular season, likely playoff team:** *reduce* variance. You're protecting a seed, and seeding is worth 2x.
- **Regular season, bubble team:** *increase* variance. You need upside.
- **Playoffs, any round, underdog:** *maximize* variance. Single elimination — only P(win) matters.
- **Playoffs, favorite:** *minimize* variance. Don't hand away an edge.

Mechanically, the sharpest and least-used tool here is **stacking**. Deliberately starting a QB with his own WR/TE raises correlation and therefore variance; splitting them lowers it. As a playoff underdog, stack. As a favorite protecting a seed, de-stack. The copula model (§4.4) makes this computable rather than a vibe.

### 2.8 Summary — ranked by expected Δ P(title) per hour of build

1. **DEF streaming** — biggest per-hour edge, fully computable from Vegas lines (§4.2)
2. **Season simulator** — the control system; without it you can't score anything as Δ P(title)
3. **Roster-spot discipline** — IR usage, no backup QB/K/DEF, bye planning
4. **FAAB with shadow pricing** — spend freely, protect slots
5. **Manager intel from 2025** — cheap to compute, identifies soft targets
6. **Deadline consolidation trades** — biggest single upgrade, narrow window
7. **Playoff-weeks roster targeting** — must be live by ~Week 8
8. **Weekly lineup optimization** — real but modest; 5 bench spots means most weeks are forced
9. **K streaming** — small, nearly free once DEF streaming exists

---

## 3. Why lineup optimization ranks low here

Worth being explicit, because it's counterintuitive and it protects your build schedule.

15 spots, 10 starting. Carrying 1 QB, 1 K, 1 DEF leaves 12 RB/WR/TE, of which 7 start. So **5 bench RB/WR/TE** competing for 2 FLEX slots plus injury fill-ins. Most weeks that's 3–5 live decisions, several of them near-ties.

A heavy Monte Carlo engine is a lot of machinery aimed at a small decision space. Build it — but build it after the things above. Don't let it eat the schedule.

**Corollary for the emails:** many weeks the honest answer is "your lineup is forced, nothing to decide." Say that. If the emails manufacture analysis for forced decisions, you'll stop reading them by Week 4 — and then the system has negative value on the week it finally has something urgent to tell you.

---

## 4. Analytical Design

### 4.1 Season simulator — `season_sim.py` (the control system)

Everything else is scored against this, so it's foundational.

Monte Carlo the remainder of the season:
1. Estimate each of the 14 teams' weekly scoring distribution (mean + variance) from roster strength and observed results, shrunk toward the league mean early in the year
2. Adjust for **manager lineup efficiency** (§2.6) — a disengaged manager's realized distribution is worse than their roster implies
3. Simulate remaining games on the actual schedule, 10k times
4. Output **P(playoffs)**, **P(bye)**, **P(seed = s)**, **P(title)**

Then Δ P(title) for any decision = re-simulate with the change applied. That's the universal metric, and it's what makes "should I stream a DEF" and "should I make this trade" comparable on one scale.

Also gives you remaining strength-of-schedule for free, which is what turns a record into an actual playoff probability.

### 4.2 DEF streaming — `streaming.py` (build first)

Your scoring makes defense enormously matchup-dependent:
```
pts_allow_0:     +10        pts_allow_21_27:   0
pts_allow_1_6:    +7        pts_allow_28_34:  -1
pts_allow_7_13:   +4        pts_allow_35p:    -4
pts_allow_14_20:  +1        sack +1, int +2, def_td +6
```
A ~14-point swing driven mostly by opposing offense quality — which Vegas prices accurately via implied team totals (`implied ≈ total/2 − spread/2`). Stream the defense facing the lowest implied total.

DEF is 10% of your starting lineup, costs one roster spot you must hold anyway, and is nearly free to churn. Highest edge per hour in the build.

Same machinery, applied to K: target high implied *team* totals and domes.

### 4.3 Projections

| Source | Role |
|---|---|
| **Vegas lines** | Implied team totals. **Priority — powers DEF/K streaming** |
| `nfl_data_py` / nflverse | Historical weekly stats, snap share, target share, PBP. Source of truth. |
| Sleeper projections (undocumented) | Baseline; matches the app |
| FantasyPros ECR | Consensus tiebreaker |

Equal weights to start; tune against 2025 once the backtest harness exists.

Feature priority: (1) snap + target share trailing 3 weeks — usage is far stickier than fantasy points; (2) implied team total; (3) opponent defensive efficiency vs. position, schedule-adjusted; (4) injury designation + practice participation; (5) pace; (6) weather.

### 4.4 Variance — `variance.py`

Everyone sees the same projections. Almost nobody models spread correctly.

**Do not assume normal distributions.** RB/WR outcomes are strongly right-skewed — median sits well below mean, which is dragged up by TD spikes. Bootstrap instead:

1. Compute `actual − projected` residuals across all available player-weeks
2. Bucket by position × projection tier (e.g. WR projected 8–12)
3. Store the **empirical residual distribution** per bucket
4. Simulate by sampling a residual and adding it to the projection

Preserves real skew and fat tails, no distributional assumptions, and directly validatable: does the actual land below your 20th percentile ~20% of the time?

**Correlation** — where naive sims fail, and the mechanism behind the stacking lever (§2.7):
- Positive: QB ↔ his own WR/TE; all players in a high-total game
- Negative: two RBs in one backfield; your player vs. an opposing DEF you're starting
- Gaussian copula: correlate on quantiles, invert through each player's empirical distribution. ~30 lines, materially better calibration.

### 4.5 Optimizer — `optimizer.py`

Small search space, so keep it simple. ILP (`PuLP`) over `roster_positions` maximizing expected points, iteratively excluding to enumerate the top ~50 lineups. Then 10k correlated sims per candidate against the opponent's simulated total.

Select on **Δ P(title)** via `season_sim`, which in the 30–85% playoff-probability band reduces to P(win) but correctly diverges at the extremes.

Opponent model: simulate their set lineup if available; otherwise their expected-points-optimal lineup, adjusted by their historical lineup efficiency (§2.6).

### 4.6 Decision leverage classification

| Tag | Δ P(title) | Email treatment |
|---|---|---|
| **Critical** | > 1.0% | Leads the email, full reasoning |
| **Moderate** | 0.2–1.0% | One-line rationale |
| **Marginal** | < 0.2% | Grouped: "these barely matter" |
| **Conditional** | pending news | Flagged for Sunday recheck |

Note the thresholds are in Δ P(title), which is much smaller in magnitude than Δ P(win week) — a 5% weekly win-prob swing in Week 3 is worth well under 1% of title odds. Calibrate these against Phase 2 output rather than trusting my numbers.

---

## 5. Email Delivery

### Transport
- **Resend or SendGrid free tier (recommended)** — API key, one POST, good deliverability, no SMTP quirks
- **Gmail SMTP + app password** — requires 2FA on the sending account

That credential is the **only** secret in the system. Sleeper needs none.

### Format
Multipart MIME, HTML primary + genuinely readable plaintext (you'll read this on a phone Sunday morning). Scannable subjects:
```
[FF] Week 3 Lineup — 2 changes · 61% win · P(title) 9.2%
[FF] Week 3 ALERT — Kincaid OUT, change needed
[FF] Week 11 DEADLINE — 3 buy targets, window closes Wk13
```

### Sunday lineup email
```
WEEK 3 vs. TeamName                          MODE: Win Now
Projected 121.4 · Opp 116.8 · Win prob 58%
Season: P(playoffs) 61% · P(bye) 14% · P(title) 9.2%

── CHANGES (2) ─────────────────────────────

1. START Kenneth Walker over Charbonnet      [+0.9% P(title)]
   Walker practiced fully Friday. Charbonnet only has volume if
   Walker sits — same backfield, negatively correlated, so starting
   both caps your ceiling instead of raising your floor.

2. STREAM DEF: Titans over Bears             [+0.5% P(title)]
   Titans face CLE, implied total 16.5 — lowest on the slate.
   FAAB bid $12 (1.2% of remaining $980). Walk away above $25.
   Costs no roster spot — straight swap for Bears.

── FORCED / NO DECISION ───────────────────

Rest of lineup is forced. Three near-ties (<0.2%): FLEX2, TE, K.

── WATCH ITEMS ────────────────────────────

⚠ Kincaid QUESTIONABLE. If out, start Ferguson [+0.6% P(title)].
  Recheck email lands 11:45am only if something changed.

⚠ IR SLOT EMPTY. Hall is DOUBTFUL and IR-eligible — moving him
  frees a bench spot worth ~$40 of FAAB flexibility.

⚠ WEEK 6 BYE CRUNCH: 3 starters out (Jefferson, Walker, Kincaid).
  Start bidding on a flex body now, not in Week 5.

── MODE NOTE ──────────────────────────────

Win Now: maximizing this week's win prob. If P(playoffs) clears 85%
I'll switch to Chase The Bye and start optimizing for Weeks 15-17
instead of the current week.
```

### Cadence — 3 emails/week
| When | Email | Condition |
|---|---|---|
| ~12h before waivers process | Recap + FAAB targets & bids + mode update | Always |
| Sun 10am | Lineup + DEF/K stream | Always |
| Sun 11:45am | Inactives alert | **Only if something changed** |

Seasonal additions: Week 8 playoff-schedule review; Weeks 10–13 deadline buy/sell targets.

⚠ **Timing dependency:** `waiver_day_of_week: 1` + `waiver_clear_days: 2` is ambiguous. Pull 2025 transactions and read actual processing timestamps. A FAAB email arriving after claims lock is worthless — and it's a silent failure you'd only notice by losing a player.

The 11:45am conditional is the highest-value job in the system. Inactive surprises decide more matchups than optimization does, and because it only fires when it matters, its arrival is itself the signal.

Off by default: Thu TNF check, Mon MNF scenarios, live in-game monitoring.

---

## 6. Repo Structure

```
fantasy-agent/
├── config.yaml
├── .env                     # email credential only — gitignored
├── requirements.txt
├── src/
│   ├── sleeper_client.py    # read-only wrapper, disk cache w/ TTL
│   ├── players.py           # player cache + fuzzy name→id resolver
│   ├── season_sim.py        # P(playoffs) / P(bye) / P(title) — CONTROL SYSTEM
│   ├── league_intel.py      # manager efficiency, dead teams, roster needs
│   ├── streaming.py         # DEF + K — BUILD FIRST
│   ├── roster.py            # IR eligibility, bye crunch, bench shadow price
│   ├── faab.py              # bid sizing w/ roster-spot pricing
│   ├── playoff_schedule.py  # Weeks 15-17 targeting
│   ├── projections/
│   │   ├── vegas_source.py       # implied totals — priority
│   │   ├── nflverse_source.py
│   │   ├── sleeper_source.py
│   │   └── ensemble.py
│   ├── variance.py          # empirical residuals + copula
│   ├── optimizer.py         # ILP candidates + MC, scored as ΔP(title)
│   ├── explain.py           # decision → rationale (load-bearing)
│   ├── trades.py            # consolidation-focused
│   ├── email_report.py
│   └── backtest.py          # replay 2025, check calibration
├── data/{cache,residuals.parquet}
├── scripts/{recon,weekly_lineup,inactives_check,recap}.py
└── tests/
```

Python throughout, commented. `explain.py` is load-bearing — since a human reads every output, the system's job is to be **legible**, not autonomous.

---

## 7. Scheduling

**Start: cron / launchd** (launchd on macOS). One entrypoint, dispatch on job name.

**Season: GitHub Actions.** Cron in a workflow, email credential in repo secrets, nothing needs to be powered on. Actions cron drifts 5–15 min — schedule the 11:45am sweep at 11:30am and the FAAB email with hours of slack.

**Skip Temporal.** It earned consideration only for durably waiting on a human approval signal. You set lineups manually, so there's no signal to wait on — the job is "compute, email, exit."

**If Actions drift bothers you:** Modal or Cloud Run + Cloud Scheduler.

---

## 8. Build Phases

Ordered by Δ P(title) per hour. ~4 weeks to Week 1.

**Phase 0 — Recon (1 evening).** `sleeper_client.py`, `players.py`, `scripts/recon.py`. Dump league/rosters/draft. **Resolve redraft-vs-keeper.** Probe undocumented endpoints. Verify FLEX eligibility. Read 2025 transactions for real waiver timing. Fuzzy name resolver tested against 20 known players.

**Phase 1 — DEF streaming + email pipeline (1 weekend).** `vegas_source.py`, `streaming.py`, `email_report.py`, `explain.py`. A weekly email recommending a DEF stream with reasoning. Highest per-hour edge, and it proves out the delivery path end to end before anything depends on it.

**Phase 2 — Season simulator + league intel (1 weekend).** `season_sim.py`, `league_intel.py`. Unlocks Δ P(title) as the universal metric and identifies soft managers. Naive team-strength estimates are fine to start.

**Phase 3 — Roster discipline (1 weekend).** `roster.py`, basic `faab.py`. IR flagging, bye-crunch lookahead, bench shadow pricing. Cheap, high return.

**Phase 4 — Lineup engine (2–3 weekends).** Ensemble projections, empirical variance, copula, optimizer scored as Δ P(title), leverage tagging. Backtest against 2025 and check calibration.

**Phase 5 — Playoff targeting + trades.** `playoff_schedule.py` live by **Week 8**; `trades.py` live by **Week 10** for the deadline window.

**Target for Week 1: Phases 0–3.** Phase 4 during the season. A crude working system beats a half-finished good one, and Phases 1–3 already capture most of the available edge.

---

## 9. Things That Will Bite You

- **Player ID mismatches** across sources. Fuzzy resolver in Phase 0. Suffixes, Jr./Sr., DST naming.
- **The 5MB `/players/nfl` blob.** Cache 24h minimum; Sleeper's docs ask you not to hammer it.
- **Bye weeks.** A bye player has no distribution, not a zero-mean one. Especially dangerous with 5 bench spots.
- **Empty starter slots** encoded as `"0"`.
- **FLEX eligibility** — verify, don't hardcode.
- **Backtest leakage.** Use only data that existed before that week's kickoff. Easy to accidentally use end-of-season stats and build a model that looks brilliant and isn't.
- **Season-sim overconfidence early.** In Weeks 1–4 team strength estimates are almost pure noise. Shrink hard toward the league mean or the mode switcher will whipsaw. P(title) shouldn't move much on a single Week 2 result.
- **Timezones.** NFL scheduling is ET. `zoneinfo`, store UTC, handle the early-November DST shift.
- **Overfitting to 17 games.** Little data per player. Simple and calibrated beats clever.
- **Silent email failure.** Log locally, exit non-zero so Actions surfaces it.
- **FAAB email after claims lock.** Verify processing time empirically.

---

## Appendix: Notes for Claude Code

Suggested first prompt:

> Read `fantasy-agent-design.md`. Implement **Phase 0 only** — `scripts/recon.py`,
> `src/sleeper_client.py`, `src/players.py`. Python, commented.
>
> The recon script should:
> 1. Pull league `1370916469910540288` + rosters, users, and draft
>    `1370916469918937088` picks → `data/recon/*.json`
> 2. Report roster sizes vs. draft rounds and assess whether this is redraft
>    or carryover/keeper (see §0 "Unresolved")
> 3. Probe each undocumented endpoint and report which respond for 2026
> 4. Pull 2025 league `1258264260069314560` transactions and report actual
>    waiver processing timestamps
> 5. Confirm what positions the two FLEX slots accept
> 6. Build and test a fuzzy name→player_id resolver against 20 known players
>
> Do not build the optimizer, streaming, season sim, or email yet.

Review recon output before Phase 1 — several §4 choices depend on whether Sleeper's projections endpoint is live for 2026, which determines how much of the projection layer comes from nflverse instead.

**Constraints to restate, easy to lose in a long session:**
- **Read-only Sleeper access.** Never attempt a write. Never store Sleeper credentials.
- Cache `/players/nfl` for 24h minimum.
- **North star is Δ P(title)**, not Δ P(win this week).
- **Bench spots are scarcer than FAAB dollars.** Price the roster spot in every add/drop.
- **Never roster a backup QB, K, or DEF.** Stream all three.
- **A first-round bye doubles title odds.** Seeding is not a consolation prize.
- Every recommendation carries a rationale — `explain.py` is not optional.
- **DEF streaming before the lineup optimizer.** 10 starters / 5 bench / 14 teams means lineup decisions are largely forced. Don't let the optimizer eat the schedule.
