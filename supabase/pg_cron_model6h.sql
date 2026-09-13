-- ============================================================================
-- pg_cron: schedule the 6-hourly MODEL run + let the build's own verdict
-- pause/resume it. Anak Krakatau community monitor — companion to
-- pg_cron_watchdog.sql
--
-- WHAT THIS ADDS (on top of the 15-min watchdog file you already ran):
--   * job 'trigger-krakatau-model'         — every 6 h, fires workflow_dispatch
--     on .github/workflows/model-6h.yml (the same PAT already in Vault).
--   * job 'krakatau-model-scheduler-sync'  — every 5 min, reads the activity
--     verdict the 15-min build just COMMITTED to
--     site/data/anak-krakatau/snapshot.json and pauses/resumes the
--     model job to match: quiet (VAAC terminated / no real-time MAGMA+VAAC)
--     -> pause, active (fresh advisory / VONA / eruption) -> resume.
--   * public.model_scheduler_state / ..._log — the current flag + an audit
--     trail of every pause/resume (pruned to 90 days).
--
-- FAIL-SAFE PROPERTIES:
--   * set_model_scheduler() is idempotent — a 5-min heartbeat of "still
--     quiet" flips nothing and writes no log row;
--   * a failed snapshot fetch RAISES, so pg_cron records it in
--     cron.job_run_details, and the scheduler state stays as it was;
--   * trigger_model_run() re-checks the state flag before dispatching, so
--     even a missed alter_job cannot fire the model while paused;
--   * worst case (an advisory terminated right before a 6-h boundary): one
--     extra dispatch goes through, hits the build-side quiet gate, and does
--     no model work at all.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. State + audit tables
-- ----------------------------------------------------------------------------
create table if not exists public.model_scheduler_state (
  id          int primary key default 1 check (id = 1),
  active      boolean not null default true,
  reason      text,
  changed_at  timestamptz not null default now()
);
insert into public.model_scheduler_state (id, active)
values (1, true)
on conflict (id) do nothing;

create table if not exists public.model_scheduler_log (
  ts      timestamptz not null default now(),
  action  text not null,          -- 'pause' | 'resume'
  reason  text,
  changed boolean not null
);

-- ----------------------------------------------------------------------------
-- 2. Trigger function: fire GitHub workflow_dispatch on model-6h.yml.
--    Guarded by the state flag: even if the job somehow fires while paused
--    (manual resume, alter_job hiccup), it dispatches nothing.
-- ----------------------------------------------------------------------------
create or replace function public.trigger_model_run()
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_token text;
  v_active boolean;
begin
  select coalesce(active, true) into v_active
  from public.model_scheduler_state where id = 1;

  if not v_active then
    raise notice 'model scheduler paused — dispatch skipped';
    return;
  end if;

  select decrypted_secret into v_token
  from vault.decrypted_secrets
  where name = 'example-PAT-name';

  if v_token is null then
    raise exception 'example-PAT-name secret not found in Vault';
  end if;

  perform net.http_post(
    url     := 'https://api.github.com/repos/hv2henry/krakatau-monitor/actions/workflows/model-6h.yml/dispatches',
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
-- 3. Pause/resume — the single place that flips the job.
--    Idempotent: when the job already matches the requested state it changes
--    nothing and writes no log row, so a 5-min heartbeat of "still quiet"
--    stays silent.
-- ----------------------------------------------------------------------------
create or replace function public.set_model_scheduler(p_active boolean,
                                                       p_reason text default null)
returns jsonb
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_job   bigint;
  v_now   boolean;
  v_action text;
begin
  select jobid into v_job from cron.job where jobname = 'trigger-krakatau-model';
  if v_job is null then
    return jsonb_build_object('ok', false, 'error', 'job trigger-krakatau-model not found');
  end if;

  select active into v_now from cron.job where jobid = v_job;

  if v_now is distinct from p_active then
    perform cron.alter_job(job_id => v_job, active => p_active);
    v_action := case when p_active then 'resume' else 'pause' end;
    insert into public.model_scheduler_log (action, reason, changed)
    values (v_action, left(coalesce(p_reason, ''), 120), true);
    update public.model_scheduler_state
      set active = p_active, reason = left(coalesce(p_reason, ''), 120),
          changed_at = now()
      where id = 1;
  end if;

  -- housekeeping: keep 90 days of audit
  delete from public.model_scheduler_log where ts < now() - interval '90 days';

  return jsonb_build_object('ok', true, 'active', p_active,
                            'changed', v_now is distinct from p_active);
end;
$$;

-- ----------------------------------------------------------------------------
-- 4. The pull side: read the build's activity verdict straight out of the
--    committed snapshot.json and align the model job with it.
--
--    activity.state = 'active' -> resume; 'quiet' -> pause (reason_code
--    rides along into the audit log). A snapshot without an activity field is NOT
--    evidence either way: the scheduler keeps its current state.
-- ----------------------------------------------------------------------------
create or replace function public.sync_model_scheduler_from_github()
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_resp  extensions.http_response;
  v_snap  jsonb;
  v_state text;
  v_code  text;
begin
  select * into v_resp from extensions.http((
    'GET',
    'https://raw.githubusercontent.com/hv2henry/krakatau-monitor/main/site/data/anak-krakatau/snapshot.json',
    ARRAY[
      extensions.http_header('Accept', 'application/json'),
      extensions.http_header('User-Agent', 'krakatau-model-scheduler-sync')
    ],
    NULL, NULL
  )::extensions.http_request);

  if v_resp.status <> 200 then
    raise exception 'snapshot.json fetch returned %', v_resp.status;
  end if;

  v_snap  := v_resp.content::jsonb;
  v_state := v_snap -> 'activity' ->> 'state';

  if v_state is null then
    raise notice 'snapshot.json carries no activity verdict — scheduler unchanged';
    return;
  end if;

  v_code := coalesce(v_snap -> 'activity' ->> 'reason_code', v_state);

  perform public.set_model_scheduler(
    p_active => (v_state = 'active'),
    p_reason => 'github: ' || v_code
  );
end;
$$;

-- ----------------------------------------------------------------------------
-- 5. Lock down EXECUTE: all three functions touch Vault secrets (or write
--    heartbeat state) and must never be reachable via the auto-exposed
--    /rest/v1/rpc/<fn> endpoint. security invoker alone isn't enough (anon
--    could still call it) -- revoke explicitly so the RPC 404s outright.
-- ----------------------------------------------------------------------------
revoke execute on function public.trigger_model_run()                from public, anon, authenticated;
revoke execute on function public.set_model_scheduler(boolean, text) from public, anon, authenticated;
revoke execute on function public.sync_model_scheduler_from_github() from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 6. Schedule the jobs (unschedule first so re-running this file is
--    idempotent):
--      'trigger-krakatau-model'         0 */6 * * *  — 00,06,12,18 UTC =
--      07,13,19,01 WIB, the cadence model-6h.yml used to carry natively.
--      'krakatau-model-scheduler-sync'  */5 * * * *  — cheap, idempotent,
--      and matched to raw.githubusercontent.com's ~5-min cache TTL; keeps
--      the pause window ahead of the 6-h boundaries tight.
-- ----------------------------------------------------------------------------
select cron.unschedule(jobid)
from cron.job
where jobname in ('trigger-krakatau-model', 'krakatau-model-scheduler-sync');

select cron.schedule(
  'trigger-krakatau-model',
  '0 */6 * * *',
  $$select public.trigger_model_run();$$
);

select cron.schedule(
  'krakatau-model-scheduler-sync',
  '*/5 * * * *',
  $$select public.sync_model_scheduler_from_github();$$
);

-- ----------------------------------------------------------------------------
-- 7. Sanity check: all jobs registered, model job active by default
-- ----------------------------------------------------------------------------
select jobid, jobname, schedule, active
from cron.job
where jobname in ('trigger-krakatau-build', 'trigger-krakatau-model',
                  'krakatau-model-scheduler-sync')
order by jobname;
