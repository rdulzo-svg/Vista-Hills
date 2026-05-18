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

# Lineup slot IDs treated as bench (not counted for fantasy points)
BENCH_SLOTS = {15, 16, 17}   # 15=BE, 16=IL, 17=NA/TS

# ESPN raw stat ID → our category key
ESPN_STAT_TO_CAT = {
    6:  "r",    13: "s",   11: "d",   12: "t",    5: "hr",
    7:  "rbi",  15: "gw",   3: "bbB", 14: "kB",   4: "hbp",
    8:  "sac",   9: "sb",  10: "cs",
    17: "ip",   18: "hP",  21: "er",  19: "bbP",  20: "hb",
    24: "kP",   25: "qs",  30: "so",  26: "w",    27: "l",
    28: "sv",   29: "bs",  31: "hd",
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


def fetch_cat_stats(cookies):
    """
    Fetch per-category raw stat totals per team using the mRoster view.
    Sums season-to-date actual stats (scoringPeriodId=0, statSourceId=0)
    for active (non-bench) roster players only.
    Returns {team_key: {stat_key: value, ...}} or {} on failure.
    """
    try:
        resp = requests.get(
            BASE_URL, params={"view": "mRoster"}, cookies=cookies, timeout=20
        )
        resp.raise_for_status()
        roster_json = resp.json()
    except Exception as exc:
        print(f"  Warning: mRoster fetch failed — {exc}", file=sys.stderr)
        return {}

    cat_stats = {}
    for t in roster_json.get("teams", []):
        key = ID_TO_KEY.get(t["id"])
        if not key:
            continue

        cats = {c: 0.0 for c in ESPN_STAT_TO_CAT.values()}

        for entry in (t.get("roster") or {}).get("entries", []):
            if entry.get("lineupSlotId") in BENCH_SLOTS:
                continue  # skip bench / IL / NA slots

            ppe = entry.get("playerPoolEntry") or {}
            for se in (ppe.get("stats") or []):
                # Season-to-date actual stats entry
                if se.get("scoringPeriodId") == 0 and se.get("statSourceId") == 0:
                    for sid_str, val in (se.get("stats") or {}).items():
                        cat = ESPN_STAT_TO_CAT.get(int(sid_str))
                        if cat and val:
                            cats[cat] += float(val)
                    break  # one matching entry per player is enough

        # Normalise IP: ESPN may return total outs (>1000) or decimal innings
        ip_val = cats.get("ip", 0.0)
        if ip_val > 1000:                       # stored as total outs
            full_inn  = int(ip_val) // 3
            rem_outs  = int(ip_val) % 3
            cats["ip"] = round(full_inn + rem_outs / 10, 1)
        elif ip_val > 0:                        # decimal innings → X.Y notation
            full_inn = int(ip_val)
            frac_inn = ip_val - full_inn
            thirds   = round(frac_inn * 3)
            cats["ip"] = round(full_inn + thirds / 10, 1)

        cat_stats[key] = {k: round(v, 1) for k, v in cats.items()}

    return cat_stats


def grade_single(bet, away_final, home_final):
    """Returns (status, payout) for a single bet given final scores."""
    pick_type = bet["pick_type"]
    pick_key  = bet["pick_key"]
    line      = float(bet["line"])
    odds      = int(bet["odds"])
    amount    = float(bet["amount"])
    away_key  = bet["away_key"]

    if pick_type == "spread":
        if pick_key == away_key:
            margin = (away_final + line) - home_final
        else:
            margin = (home_final + line) - away_final
        if abs(margin) < 0.01:
            status = "push"
        elif margin > 0:
            status = "won"
        else:
            status = "lost"

    elif pick_type == "total":
        total = away_final + home_final
        diff  = total - line
        if abs(diff) < 0.01:
            status = "push"
        elif (pick_key == "over" and diff > 0) or (pick_key == "under" and diff < 0):
            status = "won"
        else:
            status = "lost"

    elif pick_type == "ml":
        diff = away_final - home_final
        away_won = diff > 0.01
        home_won = diff < -0.01
        if abs(diff) < 0.01:
            status = "push"
        elif (pick_key == away_key and away_won) or (pick_key != away_key and home_won):
            status = "won"
        else:
            status = "lost"

    else:
        return "open", None

    if status == "won":
        profit = amount * 100 / abs(odds) if odds < 0 else amount * odds / 100
        payout = round(amount + profit, 2)
    elif status == "push":
        payout = float(amount)
    else:
        payout = 0.0

    return status, payout


def _to_decimal(american):
    return (american / 100) + 1 if american > 0 else (100 / abs(american)) + 1


def _to_american(decimal):
    if decimal >= 2:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def grade_bets(supabase_url, service_key, history, current_matchup):
    """Grade open bets (straight and parlay) for any completed matchup periods."""
    headers = {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
    }

    r = requests.get(
        f"{supabase_url}/rest/v1/bets",
        params={"status": "eq.open", "matchup_period": f"lt.{current_matchup}", "select": "*"},
        headers=headers,
        timeout=15,
    )
    if not r.ok:
        print(f"  Supabase fetch failed: {r.status_code} {r.text}", file=sys.stderr)
        return
    open_bets = r.json()
    if not open_bets:
        print("  No open bets to grade.")
        return

    # Build score lookup: (away_key, home_key, matchup_period) -> (away_final, home_final)
    result_map = {}
    for row in history:
        away_key, home_key, _winner, mp, away_score, home_score = row
        result_map[(away_key, home_key, mp)] = (float(away_score), float(home_score))

    # Separate straight bets from parlay legs
    straight_bets = [b for b in open_bets if not b.get("parlay_id")]
    parlay_legs   = [b for b in open_bets if b.get("parlay_id")]

    graded = 0

    # ── Grade straight bets ───────────────────────────────────────────────────
    for bet in straight_bets:
        key = (bet["away_key"], bet["home_key"], bet["matchup_period"])
        if key not in result_map:
            continue
        away_final, home_final = result_map[key]
        status, payout = grade_single(bet, away_final, home_final)
        patch = {"status": status, "payout": payout,
                 "away_final": away_final, "home_final": home_final}
        r2 = requests.patch(
            f"{supabase_url}/rest/v1/bets?id=eq.{bet['id']}",
            headers={**headers, "Prefer": "return=minimal"},
            json=patch, timeout=15,
        )
        if r2.ok:
            graded += 1
            print(f"  Graded {bet['bettor']} ({bet['pick_desc']}): {status}")
        else:
            print(f"  Failed to grade {bet['id']}: {r2.status_code}", file=sys.stderr)

    # ── Grade parlays ─────────────────────────────────────────────────────────
    from collections import defaultdict
    parlays = defaultdict(list)
    for leg in parlay_legs:
        parlays[leg["parlay_id"]].append(leg)

    for parlay_id, legs in parlays.items():
        legs.sort(key=lambda x: x.get("leg_num") or 0)

        # Grade each leg individually; skip parlay if any leg's result is missing
        graded_legs = []
        can_grade = True
        for leg in legs:
            key = (leg["away_key"], leg["home_key"], leg["matchup_period"])
            if key not in result_map:
                can_grade = False
                break
            away_final, home_final = result_map[key]
            leg_status, _ = grade_single(leg, away_final, home_final)
            graded_legs.append((leg, leg_status, away_final, home_final))

        if not can_grade:
            continue

        statuses = [g[1] for g in graded_legs]

        # Determine parlay outcome
        if "lost" in statuses:
            parlay_status = "lost"
            primary_payout = 0.0
        elif all(s == "push" for s in statuses):
            parlay_status = "push"
            primary_payout = float(legs[0]["amount"])
        else:
            # All won, or mix of won+push → recalculate odds using only winning legs
            winning_odds = [g[0]["odds"] for g in graded_legs if g[1] == "won"]
            if not winning_odds:
                parlay_status = "push"
                primary_payout = float(legs[0]["amount"])
            elif len(winning_odds) == 1:
                # Single winning leg after pushes — pay at that leg's odds
                wo = winning_odds[0]
                amt = float(legs[0]["amount"])
                profit = amt * wo / 100 if wo > 0 else amt * 100 / abs(wo)
                parlay_status = "won"
                primary_payout = round(amt + profit, 2)
            else:
                # Multiple winning legs — recalculate combined parlay odds
                combined = 1.0
                for wo in winning_odds:
                    combined *= _to_decimal(wo)
                recalc_odds = _to_american(combined)
                amt = float(legs[0]["amount"])
                profit = amt * recalc_odds / 100 if recalc_odds > 0 else amt * 100 / abs(recalc_odds)
                parlay_status = "won"
                primary_payout = round(amt + profit, 2)

        # Patch all legs in the DB
        for i, (leg, _leg_status, away_final, home_final) in enumerate(graded_legs):
            is_primary = (i == 0)
            payout = primary_payout if is_primary else 0.0
            patch = {"status": parlay_status, "payout": payout,
                     "away_final": away_final, "home_final": home_final}
            r2 = requests.patch(
                f"{supabase_url}/rest/v1/bets?id=eq.{leg['id']}",
                headers={**headers, "Prefer": "return=minimal"},
                json=patch, timeout=15,
            )
            if r2.ok and is_primary:
                graded += 1
                pid_short = parlay_id[:8]
                print(f"  Graded parlay {pid_short}… ({len(legs)}-leg, {leg['bettor']}): {parlay_status}")
            elif not r2.ok:
                print(f"  Failed to grade parlay leg {leg['id']}: {r2.status_code}", file=sys.stderr)

    print(f"  Graded {graded} bets ({len(straight_bets)} straight, {len(parlays)} parlays).")


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
    cat_stats  = fetch_cat_stats(cookies)
    if cat_stats:
        print(f"  Got category stats for {len(cat_stats)} teams.")
    else:
        print("  Warning: no category stats retrieved.", file=sys.stderr)

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
            cum    = side_data.get("cumulativeScore") or {}
            starts = int(((cum.get("statBySlot") or {}).get("22") or {}).get("value") or 0)
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
        "catStats":         cat_stats,
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

    # ── Supabase config ───────────────────────────────────────────────────────
    supabase_url  = os.environ.get("SUPABASE_URL", "")
    supabase_anon = os.environ.get("SUPABASE_ANON_KEY", "")
    service_key   = os.environ.get("SUPABASE_SERVICE_KEY", "")
    output["supabaseUrl"]     = supabase_url
    output["supabaseAnonKey"] = supabase_anon

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote live_data.json — Matchup {current_matchup}, {updated_str}")
    print(f"  Matchups: {[(m['away'], m['home']) for m in matchup_pairs]}")

    # ── Grade completed bets ──────────────────────────────────────────────────
    if supabase_url and service_key:
        print("Grading open bets…")
        grade_bets(supabase_url, service_key, history, current_matchup)
    elif supabase_url:
        print("  Skipping grading: SUPABASE_SERVICE_KEY not set", file=sys.stderr)


if __name__ == "__main__":
    main()
