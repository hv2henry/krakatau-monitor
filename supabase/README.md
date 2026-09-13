# supabase/

Scheduler + watchdog layer for **[krakatau-monitor](https://github.com/hv2henry/krakatau-monitor#readme)**—*Phase 2, live.*

GitHub Actions does the heavy lifting—collecting the official feeds, running
the ash-transport model, publishing the site. This folder holds the Supabase
side of the deal: a handful of `pg_cron` jobs that **fire those workflows on
schedule**, **record what actually happened to them**, and **ping a Discord
webhook the moment the pipeline goes quiet**.

> **No news is not good news.** The watchdog treats a stale pipeline exactly
> like a failing one—silence must never look like calm.

The 15-minute watchdog stack is running; `pg_cron_model6h.sql` is the newest
addition—apply it right after the watchdog file if you haven't yet.

| File | Role |
|------|------|
| [`pg_cron_watchdog.sql`](./pg_cron_watchdog.sql) | The 15-minute stack: build trigger, heartbeat sync, Discord watchdog |
| [`pg_cron_model6h.sql`](./pg_cron_model6h.sql) | Companion file: 6-hourly model trigger + activity-driven auto pause/resume |

Both files are **idempotent**—re-running them in the Supabase SQL editor
simply unschedules and re-schedules their own jobs. Nothing duplicates.

## What runs, and when

| pg_cron job | Schedule (UTC) | Function | What it does |
|-------------|----------------|----------|--------------|
| `trigger-krakatau-build` | `*/15 * * * *` | `trigger_github_build()` | fires `workflow_dispatch` on `pages.yml`—the 15-minute site build |
| `krakatau-heartbeat-sync` | `2-59/15 * * * *` | `sync_heartbeat_from_github()` | asks GitHub for the latest `pages.yml` run and writes the verdict into heartbeat |
| `krakatau-watchdog` | `3-59/15 * * * *` | `check_pipeline_watchdog()` | pings Discord if the last run is > 30 min old, unknown, or `failing` |
| `trigger-krakatau-model` | `0 */6 * * *` | `trigger_model_run()` | fires `workflow_dispatch` on `model-6h.yml`; skips dispatch while the scheduler is paused |
| `krakatau-model-scheduler-sync` | `*/5 * * * *` | `sync_model_scheduler_from_github()` | reads `activity.state` and pauses/resumes the model job to match the volcano `activity` |

The offsets are deliberate: the build fires at :00, the heartbeat sync reads it
at :02 (giving the just-triggered run time to register with GitHub's API), and
the watchdog judges at :03—reading a heartbeat sync just wrote, not last
cycle's. Model runs land at 00/06/12/18 UTC = 07/13/19/01 WIB, the cadence
`model-6h.yml` used to carry natively. The 5-minute model sync is matched to
raw.githubusercontent.com's ~5-minute cache TTL.

**How the auto pause/resume works:** each 15-minute build commits an
`activity` verdict into `snapshot.json`—fresh advisory/VONA/eruption →
`active`; VAAC terminated/no real-time MAGMA+VAAC → `quiet`. The sync job
reads that field every 5 minutes: `active` → resume the 6-hourly model job,
`quiet` → pause it, field missing → change nothing. Every actual flip is
logged with its reason; the current flag lives in `model_scheduler_state`.

## Setup

Do these once, in order:

1. **Enable the extensions**—Dashboard → Database → Extensions → enable
   `pg_cron`, `pg_net`, and `http`. `http` is what lets Postgres read HTTP response bodies synchronously.

2. **Create a fine-grained Personal Access Token (PAT)**—scope it to the `krakatau-monitor` repo
   only, permission **Actions: Read and write**, expiration *never*. Expiration *never* isn't a best pratice, but you can't have your model fail on `t=0`. Thus, make sure to rotate your PAT once in a while.

3. **Store both secrets in Vault**—run these manually in the SQL editor.
   **Never commit these lines anywhere:**

   ```sql
   -- GitHub PAT (used by both files)
   select vault.create_secret('ghp_xxxxxxxxxxxx', 'example-PAT-name');

   -- Discord webhook URL (used by the watchdog)
   select vault.create_secret(
     'https://discord.com/api/webhooks/<id>/<token>',
     'example-discord-webhook'
   );
   ```

   The secret names are arbitrary—if you rename them, rename the lookups
   inside the SQL files too. Both files read the PAT as
   `example-PAT-name`; the watchdog reads the webhook as
   `example-discord-webhook`.

4. **Run the SQL files, in order**—`pg_cron_watchdog.sql` first, then
   `pg_cron_model6h.sql`. The model file is a companion that assumes the
   watchdog file already ran. Paste each whole file into the SQL editor and
   run once.

5. **Verify**—each file ends with a sanity-check query against `cron.job`.
   Expect three jobs after the watchdog file, five after the model file, with
   `trigger-krakatau-model` active.

   > **Schema note:** `sync_heartbeat_from_github()` and
   > `check_pipeline_watchdog()` expect `public.pipeline_heartbeat` and the
   > `public.status` view to exist already—they belong to the publishing
   > side (`src/publish.py`), which writes heartbeats through the Supabase
   > REST endpoint.

## Security notes

- **Secrets only live in Vault.** The PAT and the Discord webhook URL are
  stored with `vault.create_secret()` and fetched at call time from
  `vault.decrypted_secrets`. They are never hardcoded, never written into a
  table, and never committed.
- **Least-privilege token.** The PAT is fine-grained: it can only touch
  `krakatau-monitor` and only the Actions API (read + write)—it cannot
  read code or secrets. The Discord webhook can only post to its channel.
- **The RPC surface is closed.** Every function here touches Vault secrets or
  writes pipeline state. Each one is `SECURITY INVOKER`, pins
  `search_path = public`, **and** carries an explicit
  `REVOKE EXECUTE ... FROM public, anon, authenticated`. `security invoker`
  alone is not enough—`anon` could still call the function through
  Supabase's auto-exposed `/rest/v1/rpc/<fn>` endpoint; the explicit revoke
  makes it 404 outright. Double check on *WARN* tab on Security Advisor.

## Everyday operations

```sql
-- current scheduler flag + when/why it last changed
select * from public.model_scheduler_state;

-- recent pause/resume history
select * from public.model_scheduler_log order by ts desc limit 20;

-- manual pause/resume of the 6-hourly model job
select public.set_model_scheduler(false, 'manual: maintenance');
select public.set_model_scheduler(true,  'manual: resume');

-- job run history, including failures
select * from cron.job_run_details order by start_time desc limit 20;
```

Fail-safe behaviour worth knowing: `set_model_scheduler()` is idempotent, so a
5-minute heartbeat of "still quiet" flips nothing and logs nothing; a failed
snapshot fetch raises, which `cron.job_run_details` records while the
scheduler state stays as it was; and `trigger_model_run()` re-checks the state
flag before dispatching, so even a missed `alter_job` cannot fire the model
while paused. Worst case—an advisory terminates right before a 6-hour
boundary—one extra dispatch goes through and hits the build-side quiet gate,
doing no model work at all.
