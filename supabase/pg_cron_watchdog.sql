-- ============================================================================
-- pg_cron: trigger GitHub Actions build + watchdog for pipeline_heartbeat
-- Anak Krakatau community monitor
--
-- Prereqs (do once, in this order):
--   1. Dashboard -> Database -> Extensions -> enable "pg_cron", "pg_net",
--      and "http" (the last one lets Postgres read HTTP response bodies
--      synchronously, which pg_net alone cannot do).
--   2. Create a fine-grained PAT (repo: krakatau-monitor only,
--      permission: Actions = Read and write, expiration = never)
--   3. Store it in Vault (run manually, do NOT commit this line anywhere):
--        select vault.create_secret('ghp_xxxxxxxxxxxx', 'example-PAT-name');
--   4. Store your Discord webhook URL in Vault too (also run manually):
--        select vault.create_secret(
--          'https://discord.com/api/webhooks/<id>/<token>',
--          'example-discord-webhook');
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Trigger function: fires GitHub's workflow_dispatch on pages.yml
-- ----------------------------------------------------------------------------
create or replace function public.trigger_github_build()
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_token text;
begin
  select decrypted_secret into v_token
  from vault.decrypted_secrets
  where name = 'example-PAT-name';

  if v_token is null then
    raise exception 'example-PAT-name secret not found in Vault';
  end if;

  perform net.http_post(
    url     := 'https://api.github.com/repos/hv2henry/krakatau-monitor/actions/workflows/pages.yml/dispatches',
    headers := jsonb_build_object(
                 'Authorization', 'Bearer ' || v_token,
                 'Accept',        'application/vnd.github+json',
                 'X-GitHub-Api-Version', '2022-11-28'
               ),
    body    := jsonb_build_object('ref', 'main')
  );
end;
$$;

-- ----------------------------------------------------------------------------
-- 2. Heartbeat sync: asks GitHub what actually happened to the
--    last pages.yml run, and writes that into pipeline.
--
--    NOTE ON PRECISION: this polls "the latest run" every cycle. If a run is
--    still in_progress when polled, it's treated as 'ok' (not yet judged) so
--    a normal ~2-5 min build doesn't false-alarm mid-flight. consecutive_
--    failures increments once per poll where the run had already completed
--    unsuccessfully -- if the SAME failed run is still the latest on two
--    consecutive polls, it counts twice. Fine for alerting purposes (it only
--    makes failing look more urgent, never less); add a run_id column later
--    if you want exact per-run counting.
-- ----------------------------------------------------------------------------
create or replace function public.sync_heartbeat_from_github()
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_token text;
  v_resp  extensions.http_response;
  v_run   jsonb;
  v_status text;
  v_concl  text;
begin
  select decrypted_secret into v_token
  from vault.decrypted_secrets
  where name = 'example-PAT-name';

  if v_token is null then
    raise exception 'example-PAT-name secret not found in Vault';
  end if;

  select * into v_resp from extensions.http((
    'GET',
    'https://api.github.com/repos/hv2henry/krakatau-monitor/actions/workflows/pages.yml/runs?per_page=1',
    ARRAY[
      extensions.http_header('Authorization', 'Bearer ' || v_token),
      extensions.http_header('Accept', 'application/vnd.github+json'),
      extensions.http_header('User-Agent', 'krakatau-heartbeat-sync')
    ],
    NULL, NULL
  )::extensions.http_request);

  if v_resp.status <> 200 then
    raise exception 'GitHub API returned %', v_resp.status;
  end if;

  v_run    := v_resp.content::jsonb -> 'workflow_runs' -> 0;
  v_status := v_run ->> 'status';       -- queued | in_progress | completed
  v_concl  := v_run ->> 'conclusion';   -- success | failure | cancelled | null

  insert into public.pipeline_heartbeat
    (id, last_run_at, last_ok_at, status, detail, consecutive_failures)
  values (
    1,
    coalesce((v_run ->> 'run_started_at')::timestamptz, now()),
    case when v_concl = 'success'
         then coalesce((v_run ->> 'updated_at')::timestamptz, now()) end,
    case when v_status <> 'completed' or v_concl = 'success' then 'ok'
         else 'failing' end,
    format('run %s: status=%s conclusion=%s',
           v_run ->> 'id', v_status, coalesce(v_concl, 'pending')),
    case when v_status <> 'completed' or v_concl = 'success' then 0 else 1 end
  )
  on conflict (id) do update set
    last_run_at = excluded.last_run_at,
    last_ok_at  = coalesce(excluded.last_ok_at, public.pipeline_heartbeat.last_ok_at),
    status      = excluded.status,
    detail      = excluded.detail,
    consecutive_failures = case when excluded.status = 'ok' then 0
                                 else public.pipeline_heartbeat.consecutive_failures + 1 end;
end;
$$;

-- ----------------------------------------------------------------------------
-- 3. Watchdog function: reads the existing public.status view and pings
--    a Discord webhook if stale. "No news is not good news" silence must
--    never look like calm.
-- ----------------------------------------------------------------------------
create or replace function public.check_pipeline_watchdog()
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_minutes numeric;
  v_status  text;
  v_webhook text;
begin
  select minutes_since_run, status
  into v_minutes, v_status
  from public.status;

  -- schedule runs every 15 min; alert if we've missed more than one cycle
  if v_minutes is null or v_minutes > 30 or v_status = 'failing' then
    select decrypted_secret into v_webhook
    from vault.decrypted_secrets
    where name = 'example-discord-webhook';

    if v_webhook is null then
      raise exception 'example-discord-webhook secret not found in Vault';
    end if;

    perform net.http_post(
      url     := v_webhook,
      headers := jsonb_build_object('Content-Type', 'application/json'),
      body    := jsonb_build_object(
                   'content', format(
                     '🌋 **KRAKATAU MONITOR STALE** — last run %s min ago, status=%s. NO NEWS IS NOT GOOD NEWS.',
                     coalesce(round(v_minutes, 1)::text, 'unknown'),
                     coalesce(v_status, 'unknown')
                   )
                 )
    );
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 4. Lock down EXECUTE: all three functions touch Vault secrets (or write
--    heartbeat state) and must never be reachable via the auto-exposed
--    /rest/v1/rpc/<fn> endpoint. security invoker alone isn't enough (anon
--    could still call it) -- revoke explicitly so the RPC 404s outright.
-- ----------------------------------------------------------------------------
revoke execute on function public.trigger_github_build()      from public, anon, authenticated;
revoke execute on function public.sync_heartbeat_from_github() from public, anon, authenticated;
revoke execute on function public.check_pipeline_watchdog()   from public, anon, authenticated;

-- pg_cron jobs run as the role that scheduled them (postgres by default),
-- which already owns these functions and needs no extra grant. If you ever
-- schedule via a different role, grant execute to that role explicitly here.

-- ----------------------------------------------------------------------------
-- 5. Schedule all three jobs
--    (unschedule first so re-running this file is idempotent)
-- ----------------------------------------------------------------------------
select cron.unschedule(jobid)
from cron.job
where jobname in ('trigger-krakatau-build', 'krakatau-heartbeat-sync', 'krakatau-watchdog');

select cron.schedule(
  'trigger-krakatau-build',
  '*/15 * * * *',
  $$select public.trigger_github_build();$$
);

-- offset +2 min so the just-triggered run has had a moment to register with
-- GitHub's API before we poll it
select cron.schedule(
  'krakatau-heartbeat-sync',
  '2-59/15 * * * *',
  $$select public.sync_heartbeat_from_github();$$
);

-- offset +3 min so it reads a heartbeat that sync just wrote, not last cycle's
select cron.schedule(
  'krakatau-watchdog',
  '3-59/15 * * * *',
  $$select public.check_pipeline_watchdog();$$
);

-- ----------------------------------------------------------------------------
-- 6. Sanity check: confirm all three jobs are registered
-- ----------------------------------------------------------------------------
select jobid, jobname, schedule, active
from cron.job
where jobname in ('trigger-krakatau-build', 'krakatau-heartbeat-sync', 'krakatau-watchdog');
