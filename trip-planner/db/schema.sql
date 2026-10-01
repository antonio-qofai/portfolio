-- Trips and the answers friends submit to them. Applied by scripts/migrate.ts.
-- Every statement is idempotent, so running the migration twice is safe.

create table if not exists trips (
  id text primary key,
  admin_key text not null,
  name text not null,
  organizer_name text not null,
  date_from date not null,
  date_to date not null,
  is_sample boolean not null default false,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  plan jsonb,
  plan_at timestamptz
);

create table if not exists responses (
  id text primary key,
  trip_id text not null references trips(id) on delete cascade,
  edit_token text not null,
  traveler jsonb not null,
  is_sample boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists responses_trip_id on responses(trip_id);
create index if not exists trips_expires_at on trips(expires_at);

-- Fixed-window counters for rate limiting (per IP, per action).
-- window_start is in epoch seconds, so stale rows can be pruned by age.
create table if not exists rate_limits (
  key text not null,
  window_start bigint not null,
  count integer not null,
  primary key (key, window_start)
);

-- Results of slow or paid calls (Claude, flight and hotel prices, weather), keyed
-- by their inputs. A null expires_at keeps the entry forever (Claude's reading of
-- a set of notes never changes); prices and forecasts expire.
drop table if exists llm_cache;
create table if not exists cache (
  key text primary key,
  value jsonb not null,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);
