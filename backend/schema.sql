-- Paste into Supabase SQL Editor and run once.
create table if not exists queries (
  id uuid primary key default gen_random_uuid(),
  product_query text not null,
  session_id text,
  created_at timestamptz not null default now()
);

create table if not exists serpapi_cache (
  id bigint generated always as identity primary key,
  engine text not null,
  query_params_hash text not null,
  response_json jsonb not null,
  fetched_at timestamptz not null default now(),
  pinned boolean not null default false,  -- demo fixture: never expires (see demo_fixtures.py)
  unique (engine, query_params_hash)
);
-- For a database created before `pinned` existed:
alter table serpapi_cache add column if not exists pinned boolean not null default false;
-- For a database created before "timing not judged" (2026-09-30):
alter table timing_results alter column confidence drop not null;

create table if not exists trust_results (
  query_id uuid primary key references queries(id) on delete cascade,
  verdict text not null check (verdict in ('pass', 'fail')),
  signals_json jsonb not null,
  reasons jsonb not null
);

create table if not exists timing_results (
  query_id uuid primary key references queries(id) on delete cascade,
  verdict text not null check (verdict in ('buy', 'wait')),
  confidence real check (confidence between 0 and 1),  -- null = timing not judged (no data for any component)
  signals_json jsonb not null
);

create table if not exists verdicts (
  query_id uuid primary key references queries(id) on delete cascade,
  final_verdict text not null check (final_verdict in ('walk_away', 'wait', 'buy_now')),
  reasoning_chain_json jsonb not null,
  created_at timestamptz not null default now()
);

-- Append-only price history (serpapi_cache overwrites, so it can't hold history). One row per real SerpApi
-- observation: observed_at is when SerpApi returned the price, so cache replays hit the unique key and are skipped.
create table if not exists price_snapshots (
  id bigint generated always as identity primary key,
  product_id text not null,   -- the listing's ASIN / eBay item id
  source text not null,       -- 'amazon listing' / 'ebay listing', or a retailer from the product's store list
  price numeric not null,
  observed_at timestamptz not null,
  unique (product_id, source, observed_at)
);
alter table price_snapshots enable row level security;

-- Owner receipts ("What owners say", 2026-10-01): context only, weight 0. One row per query whose receipts ran
-- (pipeline run from cache, or the live "See what owners say" run). Forum sources hold title/site/date/link only.
create table if not exists receipts_results (
  query_id uuid primary key references queries(id) on delete cascade,
  status text not null check (status in ('found', 'none', 'unavailable')),
  known_issues_json jsonb not null,
  known_praise_json jsonb not null,
  sources_json jsonb not null,  -- forum_reported, mentioned_once, forum_only, videos, engines, audit, mismatch
  created_at timestamptz not null default now()
);
alter table receipts_results enable row level security;

-- Backend uses the secret key (bypasses RLS); RLS on with no policies keeps the publishable key locked out.
alter table queries enable row level security;
alter table serpapi_cache enable row level security;
alter table trust_results enable row level security;
alter table timing_results enable row level security;
alter table verdicts enable row level security;
