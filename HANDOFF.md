# Vista Hills Fantasy Sportsbook — Architecture Handoff

Use this doc to spin up a new Claude Code session (e.g. for a fantasy football version).
Paste it in at the start of the session.

---

## What exists

A live fantasy baseball sportsbook at:
**https://rdulzo-svg.github.io/Vista-Hills/fantasy_lines.html**

Repo: `github.com/rdulzo-svg/Vista-Hills` (GitHub Pages, no build step — pure static HTML/JS/CSS)

The stack:
- **ESPN Fantasy API** — fetched server-side via Python, credentials from cookies
- **GitHub Actions** — runs `scripts/update_lines.py` daily at 1 AM ET, commits `live_data.json`
- **Supabase (Postgres)** — stores bets placed by league members
- **GitHub Pages** — serves everything statically; pages fetch `live_data.json` at load time

---

## Key files

| File | Role |
|------|------|
| `scripts/update_lines.py` | Fetches ESPN data, grades completed bets, writes `live_data.json` |
| `live_data.json` | Daily-updated data blob consumed by all HTML pages |
| `fantasy_lines.html` | Sportsbook: matchup lines, bet slip, live scores |
| `fantasy_bets.html` | Bet tracker leaderboard (W/L/P, net units, ROI) |
| `supabase_setup.sql` | One-time SQL to create the `bets` table |
| `.github/workflows/update_lines.yml` | Daily GitHub Actions workflow |
| `index.html` | Hub page linking all analytics tools |

Other pages (not needed for football port): `fantasy_playoffs.html`, `fantasy_heatmap.html`, `fantasy_luck.html`, `fantasy_breakdown.html`, `fantasy_scatter.html`, `fantasy_bump.html`, `fantasy_powerrank.html`, `fantasy_consistency.html`, `fantasy_leaders.html`, `fantasy_corr.html`, `fantasy_matchup_sim.html`, `fantasy_h2h.html`

---

## ESPN API

**Base URL:**
```
https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/2026/segments/0/leagues/{LEAGUE_ID}
```
For football, change `flb` → `ffl`.

**Auth:** Two cookies — `espn_s2` and `SWID`. Read from browser on espn.com, stored as GitHub Secrets.

**Views used:**
- `mMatchupScore` — schedule, scores, matchup periods, pitcher starts
- `mTeam` — W/L records

**Key findings from API exploration:**
- ESPN does NOT expose weekly projected fantasy points in any API view
- `totalPointsLive` == `totalPoints` (current accumulated score, not a projection)
- `mRoster` view has `playerPoolEntry.appliedStatTotal` (season fantasy pts per player) and raw stat splits — player-level projections are buildable from this but require some math
- `statSourceId: 1` = projected stats (raw counting stats, not fantasy pts)
- Pitcher starts per team: `cumulativeScore.statBySlot["22"].value` in `mMatchupScore`

**Gotcha — null cumulativeScore:** ESPN returns `cumulativeScore: null` (not missing, but explicit null) for some teams. Use `(side_data.get("cumulativeScore") or {})` not `.get("cumulativeScore", {})`.

---

## Supabase

**Project:** `akppyivrfgfcwskfazuf.supabase.co`
(For football, create a new Supabase project or add a new table)

**`bets` table schema:**
```sql
create table public.bets (
  id uuid default gen_random_uuid() primary key,
  created_at timestamptz default now(),
  bettor text not null,
  matchup_period integer not null,
  away_key text not null,
  home_key text not null,
  pick_type text not null check (pick_type in ('spread', 'total', 'ml')),
  pick_key text not null,
  pick_desc text not null,
  line numeric not null,
  odds integer not null,
  amount numeric not null check (amount > 0),
  status text not null default 'open' check (status in ('open', 'won', 'lost', 'push')),
  payout numeric,
  away_final numeric,
  home_final numeric
);
alter table public.bets enable row level security;
create policy "place bets" on public.bets for insert to anon with check (true);
create policy "read all bets" on public.bets for select to anon using (true);
create index on public.bets (bettor);
create index on public.bets (status, matchup_period);
-- REQUIRED after creating table via SQL editor:
GRANT SELECT, INSERT ON public.bets TO anon;
GRANT SELECT, INSERT ON public.bets TO authenticated;
NOTIFY pgrst, 'reload schema';
```

**Gotchas:**
- Creating a table via SQL editor does NOT auto-grant to `anon` — you must run the `GRANT` statements manually or you get `PGRST125` (Invalid path in URL) on every POST
- `NOTIFY pgrst, 'reload schema'` forces PostgREST to pick up the new table immediately
- The `SUPABASE_URL` secret should be the bare project URL: `https://xxxx.supabase.co` — NOT with `/rest/v1/` appended. If a user sets it with the path, the JS normalizes it: `.replace(/\/rest\/v1\/?$/, '')`
- Anon key = "publishable key" in Supabase dashboard; service key = "secret key"

**GitHub Secrets needed:**
```
ESPN_S2
ESPN_SWID
SUPABASE_URL          # https://xxxx.supabase.co
SUPABASE_ANON_KEY     # publishable key
SUPABASE_SERVICE_KEY  # secret key (for grading/UPDATE only)
```

---

## live_data.json structure

```json
{
  "updated": "May 12, 11:57 AM ET",
  "matchupPeriod": 7,
  "currentPeriod": 49,
  "weekStartPeriod": 48,
  "totalPeriods": 7,
  "spCap": 13,
  "weekLabel": "7",
  "dateRange": "May 11–17, 2026",
  "apTotal": 66,
  "supabaseUrl": "https://xxxx.supabase.co",
  "supabaseAnonKey": "eyJ...",
  "matchups": [
    { "away": "RM", "home": "TB" }
  ],
  "history": [
    ["RM", "TB", "TB", 1, 505.0, 506.0]
  ],
  "teams": {
    "IR": {
      "name": "Ima Ride For My MF McGGonicle",
      "color": "#e63946",
      "wins": 6, "losses": 0,
      "apW": 48,
      "scores": [755.0, 538.5, 554.0, 411.0, 409.5, 443.5],
      "score": 26.0,
      "starts": 2
    }
  }
}
```

`history` rows: `[away_key, home_key, winner_key, matchup_period, away_score, home_score]`

---

## Current odds methodology (baseball)

In `fantasy_lines.html`, `computeLines(keyA, keyB)`:

```js
function projScore(key) {
  const t = TEAMS[key];
  return 0.4 * avg(t.scores) + 0.6 * avg(t.scores.slice(-2));
}

function computeLines(keyA, keyB) {
  const projA = projScore(keyA), projB = projScore(keyB);
  const apRatioA  = (tA.apW / AP_TOTAL) / (tA.apW/AP_TOTAL + tB.apW/AP_TOTAL);
  const projRatioA = projA / (projA + projB);
  const pA = 0.70 * apRatioA + 0.30 * projRatioA;
  const spreadMag = Math.floor(Math.abs(projA - projB)) + 0.5;
  const totalLine = Math.round((projA + projB) * 2) / 2;
  // ML from toAmericanML(pA/pB) with ~4.8% overround
}
```

**Known weaknesses (planned improvements):**
1. Spread ignores score variance — JK has σ≈186, IR has σ≈130; a 20-pt edge means very different things
2. ML is independently computed from spread — they can be inconsistent
3. Fix: compute `σ_combined = √(σA²+σB²)`, then `P(A) = Φ(spreadDiff / σ_combined)` (normal CDF) so all three markets are self-consistent
4. Longer-term: player-level projections via `mRoster` (hitter daily avg × days left + SP per-start avg × projected starts)

---

## GitHub Actions workflow

`.github/workflows/update_lines.yml` — triggers daily at 6 AM UTC (1 AM ET) + manual dispatch.
Runs `scripts/update_lines.py`, commits `live_data.json` if changed.
The Python script also grades any open bets for completed matchup periods via Supabase REST API.

---

## Bet grading logic (`update_lines.py`)

`grade_single(bet, away_final, home_final)` → `(status, payout)`:
- **Spread**: `(favored_score + line) - opponent_score` → positive = won, zero = push, negative = lost
- **Total**: `away + home` vs `line` → over/under
- **ML**: straight winner comparison
- **Payout**: `amount * 100/|odds|` if favorite wins; `amount * odds/100` if underdog wins

Grading runs automatically when `current_matchup > bet.matchup_period`.

---

## For a fantasy football port

Key things to change:
1. ESPN URL: `flb` → `ffl`
2. Scoring period anchor date/period (football weeks start on different days)
3. Pitcher starts concept doesn't exist — replace with QB starts or just drop the starts cap
4. Score ranges are totally different (football fantasy scores ~80–180 pts/week vs baseball's 300–800)
5. The spread/total math is the same conceptually, just different magnitudes
6. Matchup period structure is similar (weekly matchups)
7. Team count may differ (10 or 12 teams)

The Supabase `bets` table schema works as-is for football — it's sport-agnostic.
