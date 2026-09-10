-- ============================================================================
-- Supabase schema for the Anak Krakatau community monitor
-- Free tier: 500 MB DB, 1 GB storage, 5 GB egress, 2 projects.
--            *** Free projects are PAUSED after 1 week of inactivity. ***
--            The 15-min insert below is what keeps it alive — do not remove it.
--
-- Design principle: WRITE from GitHub Actions using the service_role key.
-- The public (anon) can SELECT nothing but the two published views.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. readings — one row per poll. The archive / time series.
-- ----------------------------------------------------------------------------
create table if not exists public.readings (
  id            bigint generated always as identity primary key,
  observed_at   timestamptz not null default now(),
  volcano       text        not null default 'Anak Krakatau',
  -- primary: MAGMA / PVMBG
  magma_level   smallint,                      -- 1..4
  magma_level_name text,
  eruptions_today smallint,
  latest_vona_code text,                       -- RED / ORANGE / YELLOW / GREEN
  latest_vona_utc  timestamptz,
  report_period text,
  -- primary: Darwin VAAC
  vaac_state    text,                          -- advisory | nil | stale | error
  vaac_advisory_nr text,
  vaac_dtg      timestamptz,
  vaac_age_hours numeric,
  vaac_layers   jsonb,                         -- [{base,top,move_toward,speed_kt}, ...]
  -- secondary: our own estimate. Labelled as such, never published as fact.
  secondary_ash jsonb,
  -- provenance
  sources       jsonb not null default '{}'::jsonb,
  payload       jsonb,
  created_at    timestamptz not null default now()
);

create index if not exists readings_observed_at_idx on public.readings (observed_at desc);
create index if not exists readings_volcano_idx     on public.readings (volcano, observed_at desc);

-- ----------------------------------------------------------------------------
-- 2. events — only meaningful CHANGES. This is what gets pushed to Telegram.
--    Keeping it separate from readings means you push a handful of messages a
--    day instead of 96, which matters for both rate limits and trust.
-- ----------------------------------------------------------------------------
create table if not exists public.events (
  id            bigint generated always as identity primary key,
  occurred_at   timestamptz not null default now(),
  volcano       text not null default 'Anak Krakatau',
  kind          text not null,                 -- LEVEL_CHANGE | NEW_ERUPTION | NEW_VONA
                                               -- | VAAC_ADVISORY | VAAC_NIL | PIPELINE_DOWN
  severity      text not null default 'info',  -- critical | high | info
  title         text not null,
  body          text,
  url           text,
  attributed_to text,                          -- ALWAYS set. e.g. 'PVMBG / MAGMA Indonesia'
  payload       jsonb,
  published_at  timestamptz,                   -- null => not yet pushed to Telegram
  published_to  text[]                         default array[]::text[]
);

create index if not exists events_unpublished_idx on public.events (published_at) where published_at is null;
create index if not exists events_occurred_idx    on public.events (occurred_at desc);

-- ----------------------------------------------------------------------------
-- 3. pipeline_heartbeat — the watchdog. THE MOST IMPORTANT TABLE FOR SAFETY.
--    If this stops advancing, your monitor is dead and the community is getting
--    silence, which reads as "all clear". A silent monitor is more dangerous
--    than no monitor.
-- ----------------------------------------------------------------------------
create table if not exists public.pipeline_heartbeat (
  id           smallint primary key default 1,
  last_run_at  timestamptz not null default now(),
  last_ok_at   timestamptz,
  status       text not null default 'ok',     -- ok | degraded | failing
  detail       text,
  consecutive_failures int not null default 0,
  constraint singleton check (id = 1)
);
insert into public.pipeline_heartbeat (id) values (1)
  on conflict (id) do nothing;

-- ----------------------------------------------------------------------------
-- 4. publish_cursor — dedupe so a retried job never double-posts to Telegram.
-- ----------------------------------------------------------------------------
create table if not exists public.publish_cursor (
  channel      text primary key,               -- 'telegram' | 'ntfy'
  last_event_id bigint not null default 0,
  updated_at   timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- 5. Row Level Security — anon may read published data ONLY, write NOTHING.
--    Every write happens from GitHub Actions with the service_role key, which
--    bypasses RLS. That key must never reach a browser.
-- ----------------------------------------------------------------------------
alter table public.readings          enable row level security;
alter table public.events            enable row level security;
alter table public.pipeline_heartbeat enable row level security;
alter table public.publish_cursor    enable row level security;

-- Public, read-only views. Exposing views rather than tables keeps internal
-- columns (payload, cursors) out of reach even if a policy is mis-set later.
create or replace view public.latest_reading as
  select * from public.readings order by observed_at desc limit 1;

create or replace view public.recent_events as
  select id, occurred_at, volcano, kind, severity, title, body, url, attributed_to
  from public.events order by occurred_at desc limit 100;

create or replace view public.status as
  select h.last_run_at, h.last_ok_at, h.status, h.detail, h.consecutive_failures,
         (extract(epoch from (now() - h.last_run_at)) / 60.0) as minutes_since_run
  from public.pipeline_heartbeat h where h.id = 1;

drop policy if exists "anon read latest"   on public.latest_reading;
drop policy if exists "anon read events"   on public.recent_events;
drop policy if exists "anon read status"   on public.status;

-- Views owned by postgres run with definer rights, but grant explicitly anyway.
grant usage on schema public to anon;
grant select on public.latest_reading, public.recent_events, public.status to anon;

-- Belt and braces: even if someone grants table access later, anon cannot write.
revoke insert, update, delete, truncate, references, trigger
  on public.readings, public.events, public.pipeline_heartbeat, public.publish_cursor
  from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 6. Retention — the free tier is 500 MB. Trim aggressively; you do not need
--    15-minute granularity from a year ago, and the raw MAGMA/VONA history is
--    already archived upstream.
-- ----------------------------------------------------------------------------
-- Run manually or from a monthly GitHub Action:
--   delete from public.readings where observed_at < now() - interval '45 days';
--   delete from public.events   where occurred_at < now() - interval '365 days';

-- ----------------------------------------------------------------------------
-- 7. Storage — generated SVG/HTML assets. 1 GB free.
--    Create the bucket in the dashboard (or:)
-- ----------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('assets', 'assets', true)
on conflict (id) do update set public = true;

-- Public read, no public write.
drop policy if exists "assets public read" on storage.objects;
create policy "assets public read" on storage.objects
  for select to anon using (bucket_id = 'assets');

-- ============================================================================
-- EGRESS WARNING — read before you build a dashboard on top of this
-- ----------------------------------------------------------------------------
-- Free Supabase gives 5 GB egress/month. A dashboard that polls `latest_reading`
-- once a minute with 100 concurrent visitors is ~360 MB/day => 10.8 GB/month.
-- That blows the free tier and Supabase will throttle you.
--
-- So: do NOT serve community traffic from Supabase.
--   * Generate static HTML/SVG in GitHub Actions
--   * Deploy to Cloudflare Pages (free tier has effectively unmetered bandwidth)
--   * Keep Supabase as the archive + the push-notification trigger only
-- Cloudflare fronts the readers; Supabase only ever talks to your CI job.
-- ============================================================================
-- ----------------------------------------------------------------------------
-- Part 2 of 2: human-in-the-loop workflow columns.
-- (Both parts are in this single file: paste once, run once.)
-- ----------------------------------------------------------------------------

alter table public.events
  add column if not exists routing      text not null default 'review',
                                        -- auto | review | quarantine
  add column if not exists status       text not null default 'pending',
                                        -- pending | approved | rejected | held
                                        -- | auto_published | quarantined
  add column if not exists approved_by  text,          -- telegram id or 'auto:<rule>'
  add column if not exists approved_at  timestamptz,
  add column if not exists confidence   jsonb,         -- corroboration summary
  add column if not exists checks       jsonb;         -- validate.py gate results

create index if not exists events_pending_idx
  on public.events (status) where status = 'pending';

-- Which channel each event was drafted to / published to is already in
-- published_to (text[]). Add the editor channel concept:
--   published_to contains 'editor:<chat_id>' for drafts and
--   'public:<chat_id>' for community posts.

-- A tiny view the responder job and the dashboard use:
create or replace view public.newsroom as
  select id, occurred_at, volcano, kind, severity, title, routing, status,
         approved_by, approved_at,
         (confidence->>'agreement') as direction_agreement,
         (select count(*) from jsonb_array_elements(coalesce(checks,'[]'::jsonb)) c
           where (c->>'ok')::boolean = false and (c->>'severity') = 'fail') as failed_checks
  from public.events
  order by occurred_at desc
  limit 200;

grant select on public.newsroom to anon;

-- ============================================================================
-- WORKFLOW (enforced by convention in publish.py, auditable here)
-- ----------------------------------------------------------------------------
--   routing=auto        -> status='auto_published' immediately. Only verbatim
--                          official text (MAGMA level/VONA, Darwin VAA) ever
--                          gets this routing, and only with all gates green.
--   routing=review      -> drafted to the private EDITOR channel with
--                          [Publish] [Hold] [Kill] buttons. A human tap sets
--                          status='approved'|'held'|'rejected' + approved_by.
--                          The next publisher run posts approved ones publicly.
--   routing=quarantine  -> status='quarantined', never published anywhere
--                          public; operator gets an ntfy ping with the failed
--                          checks so a broken parser can't reach an audience.
--
-- Audit trail: every public row has approved_by ('tg:<id>' for a human,
-- 'auto:<rule>' otherwise). If approved_by is null on a public post,
-- something bypassed the workflow — treat that as an incident.
-- ============================================================================
