#!/usr/bin/env python3
"""
Fetches current ESPN fantasy data and writes live_data.json.
Reads ESPN_S2 and ESPN_SWID from environment variables (GitHub Secrets).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

LEAGUE_ID = 13910
SEASON    = 2026
BASE_URL  = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
    f"/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# ESPN team ID → our short key (stable for this league)
ID_TO_KEY = {
    1: "WG", 2: "BB", 4: "RM", 6: "TB",
    7: "AA", 8: "JK", 9: "MN", 10: "SK",
    11: "SP", 12: "IR", 13: "DH", 14: "MM",
}

# Static display data — only changes if teams rename themselves
TEAM_META = {
    "IR": {"name": "Ima Ride For My MF McGGonicle", "color": "#e63946"},
    "MN": {"name": "MANNY MEN",                     "color": "#f77f00"},
    "WG": {"name": "WITTnessing Greatness",          "color": "#2a9d8f"},
    "MM": {"name": "Mike and Mike In The Mornin'",   "color": "#457b9d"},
    "AA": {"name": "All ARRAEZ for the JUDGE",       "color": "#6a4c93"},
    "SP": {"name": "16 percent",                     "color": "#90be6d"},
    "JK": {"name": "Jung H King",                    "color": "#ff6b6b"},
    "SK": {"name": "Stop looking at me Kwan",        "color": "#4cc9f0"},
    "BB": {"name": "Back 2 Back CY Young",           "color": "#e9c46a"},
    "TB": {"name": "Thicc Bois",                     "color": "#f4a261"},
    "DH": {"name": "Dom's House of Fun",             "color": "#a8dadc"},
    "RM": {"name": "Raleighing My Way",              "color": "#52b788"},
}

# Anchor for scoring-period → calendar-date conversion
# Period 48 = May 11, 2026 (confirmed from live testing)
ANCHOR_PERIOD = 48
ANCHOR_DATE   = datetime(2026, 5, 11)


def espn_get(view, cookies):
    r = requests.get(BASE_URL, params={"view": view}, cookies=cookies, timeout=15)
    r.raise_for_status()
    return r.json()


def period_to_date(period):
    return ANCHOR_DATE + timedelta(days=period - ANCHOR_PERIOD)


def fmt_date_range(start: datetime, end: datetime) -> str:
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}, {end.year}"
    return f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}, {end.year}"


def main():
    espn_s2 = os.environ.get("ESPN_S2", "")
    swid    = os.environ.get("ESPN_SWID", "")
    if not espn_s2 or not swid:
        print("ERROR: ESPN_S2 and ESPN_SWID environment variables required", file=sys.stderr)
        sys.exit(1)

    cookies = {"espn_s2": espn_s2, "SWID": swid}

    print("Fetching ESPN data…")
    score_data = espn_get("mMatchupScore", cookies)
    team_data  = espn_get("mTeam",         cookies)

    status                = score_data["status"]
    current_matchup       = status["currentMatchupPeriod"]
    current_scoring_period = status["latestScoringPeriod"]

    print(f"  Current matchup period : {current_matchup}")
    print(f"  Current scoring period : {current_scoring_period}")

    # ── Build team W/L records ────────────────────────────────────────────────
    team_records = {}
    for t in team_data.get("teams", []):
        key = ID_TO_KEY.get(t["id"])
        if not key:
            continue
        rec = t.get("record", {}).get("overall", {})
        team_records[key] = {
            "wins":   rec.get("wins",   0),
            "losses": rec.get("losses", 0),
        }

    # ── Extract weekly totals from all matchup periods ────────────────────────
    # team_weekly[key][matchupPeriod] = total score that week
    team_weekly = {k: {} for k in ID_TO_KEY.values()}
    all_periods_seen = set()

    for m in score_data.get("schedule", []):
        mp = m.get("matchupPeriodId")
        if mp is None:
            continue
        all_periods_seen.add(mp)
        for side in ("away", "home"):
            side_data = m.get(side, {})
            tid = side_data.get("teamId")
            key = ID_TO_KEY.get(tid)
            if not key:
                continue
            total = side_data.get("totalPoints")
            if total is not None:
                # For the current week keep updating; for past weeks it's final
                if mp not in team_weekly[key] or mp == current_matchup:
                    team_weekly[key][mp] = total

    # Completed weeks: matchup periods strictly before current week
    completed = sorted(p for p in all_periods_seen if p < current_matchup)
    print(f"  Completed matchup periods: {completed}")

    # ── Compute all-play wins (12-team league → 11 possible per week) ─────────
    ap_wins = {k: 0 for k in ID_TO_KEY.values()}
    for period in completed:
        scores = [(k, team_weekly[k].get(period, 0)) for k in ID_TO_KEY.values()]
        scores.sort(key=lambda x: x[1], reverse=True)
        for rank, (key, _) in enumerate(scores):
            ap_wins[key] += len(scores) - 1 - rank   # beats every team below

    ap_total = len(completed) * 11

    # ── Build ordered score history arrays ────────────────────────────────────
    max_completed = max(completed) if completed else 0
    team_scores = {}
    for key in ID_TO_KEY.values():
        team_scores[key] = [
            team_weekly[key].get(p, 0)
            for p in range(1, max_completed + 1)
            if p in team_weekly[key]
        ]

    # ── Current week live data ────────────────────────────────────────────────
    current_matchups = [
        m for m in score_data.get("schedule", [])
        if m.get("matchupPeriodId") == current_matchup
    ]

    # Find week start period
    week_periods: set[int] = set()
    for m in current_matchups:
        for side in ("away", "home"):
            week_periods.update(
                int(p) for p in m.get(side, {}).get("pointsByScoringPeriod", {})
            )
    week_start_period = min(week_periods) if week_periods else current_scoring_period

    # Current scores + SP starts used per team
    team_live: dict[str, dict] = {}
    for m in current_matchups:
        for side in ("away", "home"):
            side_data = m.get(side, {})
            tid = side_data.get("teamId")
            key = ID_TO_KEY.get(tid)
            if not key:
                continue
            score  = side_data.get("totalPoints") or 0
            starts = (
                side_data.get("cumulativeScore", {})
                         .get("statBySlot", {})
                         .get("22", {})
                         .get("value") or 0
            )
            team_live[key] = {"score": round(float(score), 1), "starts": int(starts)}

    # Matchup pairings for current week
    matchup_pairs = []
    for m in current_matchups:
        away_key = ID_TO_KEY.get(m.get("away", {}).get("teamId"))
        home_key = ID_TO_KEY.get(m.get("home", {}).get("teamId"))
        if away_key and home_key:
            matchup_pairs.append({"away": away_key, "home": home_key})

    # ── Build history (W/L results for completed weeks) ───────────────────────
    history = []
    for m in score_data.get("schedule", []):
        mp = m.get("matchupPeriodId")
        if not mp or mp >= current_matchup:
            continue
        away_key = ID_TO_KEY.get(m.get("away", {}).get("teamId"))
        home_key = ID_TO_KEY.get(m.get("home", {}).get("teamId"))
        if not away_key or not home_key:
            continue
        away_score = m.get("away", {}).get("totalPoints") or 0
        home_score = m.get("home", {}).get("totalPoints") or 0
        winner = away_key if away_score > home_score else home_key
        history.append([away_key, home_key, winner, mp,
                        round(float(away_score), 1), round(float(home_score), 1)])

    # ── Date range ────────────────────────────────────────────────────────────
    week_start_date = period_to_date(week_start_period)
    week_end_date   = week_start_date + timedelta(days=6)
    date_range      = fmt_date_range(week_start_date, week_end_date)

    # ── Assemble output ───────────────────────────────────────────────────────
    now_et = datetime.now(timezone.utc) - timedelta(hours=5)
    updated_str = now_et.strftime("%b %-d, %-I:%M %p ET")

    output = {
        "updated":          updated_str,
        "matchupPeriod":    current_matchup,
        "currentPeriod":    current_scoring_period,
        "weekStartPeriod":  week_start_period,
        "totalPeriods":     7,
        "spCap":            13,
        "weekLabel":        str(current_matchup),
        "dateRange":        date_range,
        "apTotal":          ap_total,
        "matchups":         matchup_pairs,
        "history":          history,
        "teams":            {},
    }

    for key in ID_TO_KEY.values():
        live = team_live.get(key, {"score": 0, "starts": 0})
        output["teams"][key] = {
            **TEAM_META[key],
            "wins":   team_records.get(key, {}).get("wins",   0),
            "losses": team_records.get(key, {}).get("losses", 0),
            "apW":    ap_wins.get(key, 0),
            "scores": team_scores.get(key, []),
            "score":  live["score"],
            "starts": live["starts"],
        }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote live_data.json — Matchup {current_matchup}, {updated_str}")
    print(f"  Matchups: {[(m['away'], m['home']) for m in matchup_pairs]}")


if __name__ == "__main__":
    main()
