-- Run this once in your Supabase SQL Editor (dashboard.supabase.com → SQL Editor)
-- Creates the bets table, enables row-level security, and sets anon read/write policies.
-- The grading script uses the service role key, which bypasses RLS automatically.

create table if not exists public.bets (
  id             uuid        default gen_random_uuid() primary key,
  created_at     timestamptz default now(),
  bettor         text        not null,
  matchup_period integer     not null,
  away_key       text        not null,
  home_key       text        not null,
  pick_type      text        not null check (pick_type in ('spread', 'total', 'ml')),
  pick_key       text        not null,   -- team key, 'over', or 'under'
  pick_desc      text        not null,   -- human-readable, e.g. "16 percent -33.5"
  line           numeric     not null,   -- spread number, total line, or ML odds
  odds           integer     not null,   -- American odds used for payout calc
  amount         numeric     not null check (amount > 0),
  status         text        not null default 'open' check (status in ('open', 'won', 'lost', 'push')),
  payout         numeric,                -- null while open; set on grading
  away_final     numeric,               -- null while open; final score on grading
  home_final     numeric,
  -- Parlay columns (null for straight bets)
  parlay_id      uuid,                  -- shared UUID links all legs of one parlay
  parlay_odds    integer,               -- combined American odds for the whole parlay
  leg_num        integer                -- 1 = primary leg (holds amount/payout); 2+ = other legs
);

-- ── Parlay migration (run this if table already exists) ─────────────────────
-- ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS parlay_id    uuid;
-- ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS parlay_odds  integer;
-- ALTER TABLE public.bets ADD COLUMN IF NOT EXISTS leg_num      integer;

alter table public.bets enable row level security;

-- Anon key: anyone with the public URL can place bets and read the leaderboard
create policy "place bets"
  on public.bets for insert
  to anon
  with check (true);

create policy "read all bets"
  on public.bets for select
  to anon
  using (true);

-- Index for the leaderboard query (group by bettor) and grading query
create index if not exists bets_bettor_idx       on public.bets (bettor);
create index if not exists bets_status_period_idx on public.bets (status, matchup_period);
