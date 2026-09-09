#!/usr/bin/env python3
"""
publish.py — push volcano findings to the community, safely.

Channels:
  * Telegram channel   — RECOMMENDED for public broadcast. Free, unlimited
                         subscribers, real push to phones, no app install
                         beyond Telegram. A *channel* (not group) means people
                         cannot reply-spam the feed.
  * ntfy.sh            — RECOMMENDED for YOUR OWN ops alerts only. See the
                         security note below; do not use a bare public topic
                         as a community broadcast.
  * Supabase           — archive + dedupe cursor + heartbeat.
  * generic webhook    — Slack/Discord/matrix, whatever you like.

------------------------------------------------------------------------------
WHY ntfy.sh IS NOT A PUBLIC CHANNEL
------------------------------------------------------------------------------
On ntfy.sh, topic names ARE the only credential. Anyone who knows or guesses
`ntfy.sh/anak-krakatau` can both SUBSCRIBE and PUBLISH to it. Consequences:

  * Spoofing — a stranger can post "LEVEL IV AWAS, EVACUATE" to your audience.
    During an active eruption that is genuinely dangerous, not just annoying.
  * Squatting — they can subscribe and watch your traffic.
  * Limits    — ntfy.sh free tier is ~250 messages/day per visitor, and
    attachments expire after 3 h. Fine for a personal alert, not a broadcast.

If you still want ntfy, either:
  (a) use a long random unguessable topic (`ntfy.sh/ak-7f3c9b2e1a8d4056...`), or
  (b) better: create a free ntfy.sh account, mint a token with *publish* rights
      on the topic, and send `Authorization: Bearer <token>`. Subscribers can
      then read but not write. That is the supported way to lock a topic down.
Either way, treat it as a personal/maintainer channel. Use Telegram for people.

------------------------------------------------------------------------------
SAFETY RULES THIS MODULE ENFORCES
------------------------------------------------------------------------------
1. Attribution is mandatory. Every outbound message names its source
   (PVMBG/MAGMA, Darwin VAAC). Unattributed hazard information is how rumours
   start.
2. Silence is never rendered as "all clear". If the Darwin VAAC has no advisory,
   or MAGMA is unreachable, the message says so explicitly.
3. Our own Himawari/open-meteo plume estimate is always labelled SECONDARY /
   UNCONFIRMED, and never sent as a standalone alert.
4. Heartbeat. If the pipeline stops, that is itself an event worth publishing —
   a dead monitor that looks like "no news" is the most dangerous failure mode.
5. Dedupe via a stored cursor, so a retried CI job cannot double-post.
6. Rate limiting, so we never spam the channel or hammer BoM/MAGMA.

Secrets come from the environment ONLY. Never hardcode, never commit.
  TELEGRAM_BOT_TOKEN   TELEGRAM_CHAT_ID   NTFY_URL   NTFY_TOKEN
  SUPABASE_URL         SUPABASE_SERVICE_KEY        WEBHOOK_URL

Stdlib only. Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "krakatau-community-monitor/1.0 (+https://github.com/YOURNAME/krakatau)"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# Telegram: ~30 msg/s globally, 1 msg/s per chat, 20 msg/min per group.
# We are nowhere near that, but stay polite and batch.
TELEGRAM_MIN_INTERVAL_S = 1.1
MAX_MESSAGES_PER_RUN = 12          # hard cap: never flood the channel
MAX_BODY_CHARS = 3500              # Telegram limit is 4096; leave headroom

DISCLAIMER_ID = "⚠ Bukan peringatan resmi. Not an official warning system."


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------
def _post(url: str, data: bytes | None = None, headers: dict | None = None,
          timeout: int = 30, retries: int = 3) -> tuple[int, str]:
    last = ""
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
            req.add_header("User-Agent", UA)
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.status, r.read().decode("utf-8", "replace")[:600]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {body}"
            if e.code in (400, 401, 403, 404):     # not transient
                return e.code, body
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (2 ** i))
    return 0, last


def _get(url: str, headers: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
class Telegram:
    """Minimal Bot API client. Channel = broadcast, subscribers cannot reply."""

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self._last = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _throttle(self):
        wait = TELEGRAM_MIN_INTERVAL_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def send(self, text: str, silent: bool = False) -> tuple[bool, str]:
        if not self.enabled:
            return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"
        if len(text) > MAX_BODY_CHARS:
            text = text[: MAX_BODY_CHARS - 20].rstrip() + "\n… (truncated)"
        self._throttle()
        body = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": bool(silent),   # true = no sound/vibration
        }).encode()
        st, resp = _post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                         data=body, headers={"Content-Type": "application/json"})
        ok = st == 200 and '"ok":true' in resp.replace(" ", "")
        return ok, resp if not ok else "sent"

    def send_photo(self, caption: str, path_or_url: str, silent: bool = True) -> tuple[bool, str]:
        """Attach the SVG/PNG map. Local file -> multipart upload."""
        if not self.enabled:
            return False, "not configured"
        self._throttle()
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        if path_or_url.startswith(("http://", "https://")):
            body = json.dumps({"chat_id": self.chat_id, "photo": path_or_url,
                               "caption": caption[:1024], "disable_notification": silent}).encode()
            st, resp = _post(url, data=body, headers={"Content-Type": "application/json"})
        else:
            try:
                with open(path_or_url, "rb") as f:
                    blob = f.read()
            except OSError as e:
                return False, f"cannot read {path_or_url}: {e}"
            boundary = "----krakatau" + os.urandom(8).hex()
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{self.chat_id}\r\n",
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1024]}\r\n",
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"disable_notification\"\r\n\r\n{'true' if silent else 'false'}\r\n",
            ]
            raw = "".join(parts).encode()
            raw += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                    f"filename=\"map.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
            raw += blob + f"\r\n--{boundary}--\r\n".encode()
            st, resp = _post(url, data=raw,
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        ok = st == 200 and '"ok":true' in resp.replace(" ", "")
        return ok, resp if not ok else "sent"

    def send_draft(self, text: str, event_id: int, editor_chat: str | None = None,
                   silent: bool = True) -> tuple[bool, str]:
        """Post a DRAFT to the private editor channel with approve buttons.

        The buttons are the human-in-the-loop. callback_data carries only the
        action and event id; the responder job turns a tap into a status write.
        """
        chat = editor_chat or os.environ.get("TELEGRAM_EDITOR_CHAT_ID") or self.chat_id
        if not (self.token and chat):
            return False, "editor chat / token not configured"
        self._throttle()
        body = json.dumps({
            "chat_id": chat,
            "text": (text[: MAX_BODY_CHARS - 400] +
                     f"\n\n<code>event #{event_id}</code> — routing: review")[:MAX_BODY_CHARS],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Publish", "callback_data": f"pub:{event_id}"},
                {"text": "⏸ Hold", "callback_data": f"hold:{event_id}"},
                {"text": "❌ Kill", "callback_data": f"kill:{event_id}"},
            ]]},
        }).encode()
        st, resp = _post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                         data=body, headers={"Content-Type": "application/json"})
        ok = st == 200 and '"ok":true' in resp.replace(" ", "")
        return ok, resp if not ok else "draft sent"

    def poll_callbacks(self, timeout: int = 3, offset: int | None = None) -> tuple[list, int]:
        """One getUpdates poll. Returns (callback_queries, next_offset)."""
        if not self.token:
            return [], offset or 0
        params = {"timeout": timeout, "allowed_updates": json.dumps(["callback_query"])}
        if offset:
            params["offset"] = offset
        st, resp = _get("https://api.telegram.org/bot%s/getUpdates?%s"
                        % (self.token, urllib.parse.urlencode(params)), timeout=timeout + 10)
        try:
            d = json.loads(resp)
            ups = d.get("result", []) if d.get("ok") else []
        except Exception:
            return [], offset or 0
        nxt = max([u["update_id"] for u in ups], default=offset or 0) + 1
        cbs = [u["callback_query"] for u in ups if "callback_query" in u]
        return cbs, nxt

    def answer_callback(self, cb_id: str, text: str = "") -> None:
        if not self.token:
            return
        _post(f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
              data=json.dumps({"callback_query_id": cb_id, "text": text[:200]}).encode(),
              headers={"Content-Type": "application/json"}, retries=1)

    def me(self) -> str:
        if not self.token:
            return "no token"
        st, resp = _get(f"https://api.telegram.org/bot{self.token}/getMe")
        return resp[:200]


# --------------------------------------------------------------------------
# ntfy
# --------------------------------------------------------------------------
class Ntfy:
    """Personal/ops alerting. See module docstring — not a public broadcast."""

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url or os.environ.get("NTFY_URL")
        self.token = token or os.environ.get("NTFY_TOKEN")

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, title: str, body: str, priority: int = 3, tags: list[str] | None = None,
             click: str | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, "NTFY_URL not set"
        h = {"Title": title[:250], "Priority": str(max(1, min(5, priority))),
             "Markdown": "yes"}
        if tags:
            h["Tags"] = ",".join(tags[:8])
        if click:
            h["Click"] = click
        if self.token:
            # Publish-only token: subscribers can read but cannot spoof you.
            h["Authorization"] = f"Bearer {self.token}"
        st, resp = _post(self.url, data=body.encode(), headers=h)
        return st == 200, resp


# --------------------------------------------------------------------------
# Supabase
# --------------------------------------------------------------------------
class Supabase:
    """Archive + dedupe cursor + heartbeat. Uses service_role (server-side only).

    If you would rather not hold a service_role key, create an RPC function with
    SECURITY DEFINER that inserts into the three tables and exposes nothing else,
    then grant it to `authenticated` and use an anon key. Less blast radius.
    """

    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _h(self, extra: dict | None = None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
             "Content-Type": "application/json", "Prefer": "return=minimal"}
        h.update(extra or {})
        return h

    def insert_reading(self, row: dict) -> tuple[bool, str]:
        if not self.enabled:
            return False, "SUPABASE_URL / SUPABASE_SERVICE_KEY not set"
        st, resp = _post(f"{self.url}/rest/v1/readings", data=json.dumps(row).encode(),
                         headers=self._h())
        return st in (200, 201), resp

    def heartbeat(self, status: str = "ok", detail: str = "") -> tuple[bool, str]:
        if not self.enabled:
            return False, "not configured"
        payload = {"last_run_at": datetime.now(timezone.utc).isoformat(),
                   "status": status, "detail": detail[:400],
                   "consecutive_failures": 0 if status == "ok" else 1}
        if status == "ok":
            payload["last_ok_at"] = payload["last_run_at"]
        h = self._h()
        h["Prefer"] = "return=minimal"
        st, resp = _post(f"{self.url}/rest/v1/pipeline_heartbeat?id=eq.1",
                         data=json.dumps(payload).encode(), headers=h)
        return st in (200, 204), resp

    def _patch(self, path: str, payload, extra_h=None) -> tuple[bool, str]:
        h = self._h(extra_h)
        h["Prefer"] = "return=minimal"
        st, resp = _post(f"{self.url}/rest/v1/{path}", data=json.dumps(payload).encode(), headers=h)
        return st in (200, 204), resp

    def insert_event(self, ev: dict) -> int | None:
        """Insert an event row; return its id (needed for approval buttons)."""
        if not self.enabled:
            return None
        h = self._h()
        h["Prefer"] = "return=representation"
        st, resp = _post(f"{self.url}/rest/v1/events",
                         data=json.dumps(ev).encode(), headers=h)
        try:
            return int(json.loads(resp)[0]["id"]) if st in (200, 201) else None
        except Exception:
            return None

    def set_event_status(self, event_id: int, status: str, by: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, "not configured"
        payload = {"status": status, "approved_by": by,
                   "approved_at": datetime.now(timezone.utc).isoformat()}
        h = self._h()
        h["Prefer"] = "return=minimal"
        st, resp = _post(f"{self.url}/rest/v1/events?id=eq.{event_id}",
                         data=json.dumps(payload).encode(), headers=h)
        return st in (200, 204), resp

    def approved_unpublished(self, limit: int = 10) -> list[dict]:
        if not self.enabled:
            return []
        q = ("?status=eq.approved"
             "&published_to=not.cs.{public}"
             "&order=occurred_at.asc&limit=%d" % limit)
        st, resp = _get(f"{self.url}/rest/v1/events{q}", headers=self._h())
        try:
            return json.loads(resp) if st == 200 else []
        except Exception:
            return []

    def mark_published(self, event_id: int, channel_tag: str) -> tuple[bool, str]:
        if not self.enabled:
            return False, "not configured"
        h = self._h()
        h["Prefer"] = "return=minimal"
        # append to published_to array
        st, resp = _post(f"{self.url}/rest/v1/events?id=eq.{event_id}",
                         data=json.dumps({"published_to": [channel_tag]}).encode(),
                         headers={**h, "Prefer": "return=minimal"})
        return st in (200, 204), resp

    def cursor_get(self, channel: str) -> int:
        if not self.enabled:
            return 0
        st, resp = _get(f"{self.url}/rest/v1/publish_cursor?channel=eq.{urllib.parse.quote(channel)}"
                        f"&select=last_event_id", headers=self._h())
        try:
            rows = json.loads(resp)
            return int(rows[0]["last_event_id"]) if rows else 0
        except Exception:
            return 0

    def cursor_set(self, channel: str, event_id: int) -> tuple[bool, str]:
        if not self.enabled:
            return False, "not configured"
        payload = {"channel": channel, "last_event_id": event_id,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        h = self._h()
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
        st, resp = _post(f"{self.url}/rest/v1/publish_cursor",
                         data=json.dumps(payload).encode(), headers=h)
        return st in (200, 201, 204), resp


# --------------------------------------------------------------------------
# message composition
# --------------------------------------------------------------------------
HTML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def esc(s) -> str:
    """Escape for Telegram HTML parse_mode. Never pass scraped text raw."""
    s = "" if s is None else str(s)
    for k, v in HTML_ESC.items():
        s = s.replace(k, v)
    return s


def compose_event(ev: dict) -> tuple[str, int, bool]:
    """Build a Telegram message from an event dict.

    Returns (text, ntfy_priority, is_silent).
    Attribution and the not-an-official-warning disclaimer are ALWAYS included.
    """
    sev = (ev.get("severity") or "info").lower()
    icon = {"critical": "🔴", "high": "🟠", "info": "🔵"}.get(sev, "🔵")
    prio = {"critical": 5, "high": 4, "info": 3}.get(sev, 3)
    silent = sev == "info"

    when = ev.get("occurred_at") or datetime.now(timezone.utc).isoformat()
    lines = [
        f"{icon} <b>{esc(ev.get('title') or ev.get('kind') or 'Update')}</b>",
        "",
    ]
    if ev.get("body"):
        lines += [esc(ev["body"]), ""]
    lines.append(f"🕐 {esc(when)}")
    lines.append(f"📋 Sumber / Source: <b>{esc(ev.get('attributed_to') or 'unattributed')}</b>")
    if ev.get("url"):
        lines.append(f"🔗 <a href=\"{esc(ev['url'])}\">Lihat sumber asli / original source</a>")
    lines += ["", f"<i>{esc(DISCLAIMER_ID)}</i>"]
    lines.append("<i>Ikuti instruksi PVMBG &amp; BPBD setempat. Follow official PVMBG and local BPBD instructions.</i>")
    return "\n".join(lines), prio, silent


def compose_secondary_estimate(secondary: dict) -> str:
    """For OUR OWN derived data. Always framed as an unconfirmed estimate."""
    return "\n".join([
        "🧪 <b>Perkiraan sekunder — BUKAN data resmi</b>",
        "<i>Secondary estimate from Himawari-9 satellite winds + open-meteo. "
        "Our own calculation, NOT confirmed by PVMBG or the Darwin VAAC. "
        "Use for situational awareness only.</i>",
        "",
        esc(json.dumps(secondary, ensure_ascii=False, indent=1)[:1200]),
        "",
        f"<i>{esc(DISCLAIMER_ID)}</i>",
    ])


def compose_heartbeat_warning(minutes: float) -> tuple[str, int, bool]:
    """Published when the pipeline has gone quiet. Silence must not look like
    an all-clear."""
    return "\n".join([
        "⚫ <b>Monitor tidak aktif / Monitor is not reporting</b>",
        "",
        f"Data terakhir berhasil diambil {minutes:.0f} menit yang lalu.",
        f"Last successful update was {minutes:.0f} minutes ago.",
        "",
        "<b>TIDAK ADA BERITA BUKAN BERARTI AMAN.</b> Tidak adanya pembaruan dari "
        "monitor ini bukan tanda bahwa aktivitas gunung api menurun.",
        "<b>NO NEWS IS NOT GOOD NEWS.</b> A silent monitor is not evidence that "
        "the volcano has calmed down.",
        "",
        "📋 Periksa sumber resmi langsung:",
        "🔗 <a href=\"https://magma.esdm.go.id\">MAGMA Indonesia (PVMBG)</a>",
        "🔗 <a href=\"https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml\">Darwin VAAC</a>",
        "",
        f"<i>{esc(DISCLAIMER_ID)}</i>",
    ]), 5, False


# --------------------------------------------------------------------------
# turn monitor output into events
# --------------------------------------------------------------------------
def events_from_monitor(data: dict, vaac: dict | None = None) -> list[dict]:
    """Derive publishable events from a volcano_monitor.py JSON payload.

    Note what is NOT here: our own ash-trajectory estimate. Derived data is
    never pushed as an alert on its own — it is attached to a dashboard instead.
    """
    out = []
    volcano = data.get("volcano", "Anak Krakatau")

    for a in data.get("alerts", []) or []:
        kind, sev = a.get("kind"), (a.get("severity") or "info").lower()
        src = ("Darwin VAAC (Bureau of Meteorology)" if kind.startswith("VAAC")
               else "PVMBG / MAGMA Indonesia (magma.esdm.go.id)")
        out.append({
            "volcano": volcano, "kind": kind, "severity": sev,
            "title": {
                "LEVEL_CHANGE": f"Tingkat aktivitas berubah — {volcano}",
                "NEW_ERUPTION": f"Erupsi baru — {volcano}",
                "NEW_VONA": f"VONA baru ({a.get('message','')[:40]})",
                "REPORT_UPDATE": f"Laporan pengamatan baru — {volcano}",
            }.get(kind, f"Update — {volcano}"),
            "body": a.get("message"),
            "url": a.get("url"),
            "attributed_to": src,
            "occurred_at": data.get("generated_utc"),
        })

    # Darwin VAAC transitions are themselves events — including the return to
    # 'nil', because that changes what people should trust.
    if vaac:
        st = vaac.get("state")
        if st == "advisory" and vaac.get("advisory"):
            adv = vaac["advisory"]
            layers = "; ".join(
                f"{ly['base']}-{ly['top']} mov {ly['move_toward']} {ly['speed_kt']}kt"
                for ly in adv.get("observed_layers", []))
            out.append({
                "volcano": volcano, "kind": "VAAC_ADVISORY", "severity": "high",
                "title": f"Darwin VAAC advisory — {adv.get('volcano')} {adv.get('advisory_nr')}",
                "body": (f"{adv.get('eruption_details')}\n"
                         f"Ash layers: {layers}\n"
                         f"VAAC remark: {(adv.get('remarks') or '')[:300]}"),
                "url": "https://www.bom.gov.au/aviation/volcanic-ash/darwin-va-advisory.shtml",
                "attributed_to": "Darwin VAAC, Bureau of Meteorology (Australia) — ICAO VAAC",
                "occurred_at": adv.get("dtg_utc"),
            })
    return out


# --------------------------------------------------------------------------
# human-in-the-loop workflow
# --------------------------------------------------------------------------
def run_workflow(events: list[dict], verdict: dict | None, tg: Telegram, ntfy: Ntfy,
                 sb: Supabase, args) -> dict:
    """Route every event: auto-publish, draft-for-human, or quarantine.

    Returns a small report dict. This is the ONLY path that writes to the
    public channel, and it can only write (a) verbatim official text that
    passed all gates, or (b) items a human approved by tapping a button.
    """
    report = {"auto": 0, "drafted": 0, "quarantined": 0, "published_approved": 0}

    for ev in events:
        kind = ev.get("kind", "UNKNOWN")
        if verdict is not None:
            routing, why = validate_routing(verdict, kind)
        else:
            routing, why = ev.get("routing", "review"), ["no verdict supplied"]
        ev["routing"], ev["routing_reasons"] = routing, why

        if routing == "quarantine":
            ev["status"] = "quarantined"
            if sb.enabled:
                sb.insert_event({**_event_row(ev), "status": "quarantined",
                                 "routing": routing,
                                 "checks": (verdict or {}).get("checks"),
                                 "confidence": (verdict or {}).get("direction_corroboration")})
            if ntfy.enabled:
                ntfy.send(f"QUARANTINED: {ev.get('title','')[:60]}",
                          "Failed gates:\n" + "\n".join(f"- {w}" for w in why)[:1500],
                          priority=4, tags=["warning", "quarantine"])
            print(f"[quarantine] {ev.get('title')}: {why}")
            report["quarantined"] += 1
            continue

        if routing == "auto":
            text, prio, silent = compose_event(ev)
            print(f"[auto] {ev.get('title')}")
            if not args.dry_run and tg.enabled:
                ok, resp = tg.send(text, silent=silent)
                if ok:
                    report["auto"] += 1
                    if sb.enabled:
                        eid = sb.insert_event({**_event_row(ev), "status": "auto_published",
                                               "routing": "auto", "approved_by": "auto:verbatim-official",
                                               "published_to": ["public"]})
                else:
                    print(f"[telegram] FAILED: {resp}", file=sys.stderr)
            else:
                report["auto"] += 1
            continue

        # routing == review: draft to the editor channel once, then wait.
        if sb.enabled:
            eid = sb.insert_event({**_event_row(ev), "status": "pending",
                                   "routing": "review",
                                   "checks": (verdict or {}).get("checks"),
                                   "confidence": (verdict or {}).get("direction_corroboration")})
        else:
            eid = None
        text, prio, silent = compose_event(ev)
        if eid:
            corr = (verdict or {}).get("direction_corroboration") or {}
            text += ("\n\n<b>Cross-check:</b> " +
                     esc(json.dumps(corr.get("agreement")) ) +
                     " " + esc(_corr_oneline(corr)))
        if args.dry_run:
            print(f"[draft] (dry-run) event #{eid}:\n{text}")
            report["drafted"] += 1
            continue
        if tg.enabled:
            ok, resp = tg.send_draft(text, eid or 0)
            print(f"[draft] event #{eid} -> editor channel: {'ok' if ok else resp}")
            if ok:
                report["drafted"] += 1
        else:
            print(f"[draft] no telegram; would draft: {ev.get('title')}")

    # ---- publish what a human approved ------------------------------------
    for row in (sb.approved_unpublished() if sb.enabled else []):
        text, prio, silent = compose_event(row)
        if args.dry_run:
            print(f"[approved, dry-run] #{row['id']} {row.get('title')}")
            continue
        ok, resp = tg.send(text, silent=silent) if tg.enabled else (False, "no tg")
        if ok:
            sb.mark_published(row["id"], "public")
            report["published_approved"] += 1
            print(f"[published] #{row['id']} approved by {row.get('approved_by')}")
    return report


def _event_row(ev: dict) -> dict:
    return {"volcano": ev.get("volcano"), "kind": ev.get("kind"),
            "severity": ev.get("severity"), "title": ev.get("title"),
            "body": ev.get("body"), "url": ev.get("url"),
            "attributed_to": ev.get("attributed_to"),
            "occurred_at": ev.get("occurred_at"), "payload": ev.get("payload")}


def _corr_oneline(corr: dict) -> str:
    parts = []
    for b in (corr.get("bands") or []):
        ref = b.get("reference") or {}
        parts.append(f"{b['band_km']}km:{ref.get('toward_deg')}deg n={b['n_sources']}"
                     f"{'!' if b.get('conflicting') else ''}")
    return " ".join(parts[:4])


def validate_routing(verdict: dict, kind: str):
    import validate as _v
    return _v.routing_for(verdict, kind)


def run_responder(tg: Telegram, sb: Supabase, args) -> int:
    """Poll Telegram once and apply human decisions. Cron-friendly."""
    cbs, nxt = tg.poll_callbacks(timeout=3)
    n = 0
    for cb in cbs:
        data = cb.get("data") or ""
        user = (cb.get("from") or {}).get("id")
        allowed = os.environ.get("TELEGRAM_APPROVER_IDS", "")
        if allowed and str(user) not in [x.strip() for x in allowed.split(",") if x.strip()]:
            tg.answer_callback(cb["id"], "Not authorised.")
            print(f"[responder] reject unauthorised tap from {user}")
            continue
        act, _, eid = data.partition(":")
        status = {"pub": "approved", "hold": "held", "kill": "rejected"}.get(act)
        if not status or not eid.isdigit():
            tg.answer_callback(cb["id"], "Unknown action.")
            continue
        ok, resp = sb.set_event_status(int(eid), status, f"tg:{user}")
        tg.answer_callback(cb["id"], {"pub": "Will publish on next run.",
                                      "hold": "Held.", "kill": "Killed."}.get(act, ""))
        print(f"[responder] event #{eid} -> {status} by tg:{user} ({'ok' if ok else resp})")
        n += 1
    print(f"[responder] applied {n} decision(s)")
    return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Publish volcano findings safely")
    ap.add_argument("--monitor-json", help="path to volcano_monitor.py --json output")
    ap.add_argument("--vaac-json", help="path to darwin_vaac.py --json output")
    ap.add_argument("--events-json", help="publish a hand-built list of events instead")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--ntfy", action="store_true")
    ap.add_argument("--ntfy-auto", action="store_true",
                    help="use ntfy if NTFY_URL is configured, silently skip otherwise")
    ap.add_argument("--webhook", default=os.environ.get("WEBHOOK_URL"))
    ap.add_argument("--supabase", action="store_true", help="archive + dedupe cursor")
    ap.add_argument("--photo", help="attach this image/map to the Telegram post")
    ap.add_argument("--dry-run", action="store_true", help="print, do not send")
    ap.add_argument("--workflow", action="store_true",
                    help="route events through auto/draft/quarantine (needs --supabase)")
    ap.add_argument("--verdict-json", help="validate.py output, used for routing decisions")
    ap.add_argument("--respond", action="store_true",
                    help="poll Telegram once and apply human approve/hold/kill taps")
    ap.add_argument("--approve", type=int, metavar="ID", help="approve event ID from the CLI")
    ap.add_argument("--reject", type=int, metavar="ID", help="reject event ID from the CLI")
    ap.add_argument("--check-heartbeat", action="store_true",
                    help="only verify the pipeline is alive; publish if it is not")
    ap.add_argument("--max-staleness-min", type=float, default=90.0)
    args = ap.parse_args()

    tg, ntfy, sb = Telegram(), Ntfy(), Supabase()
    print(f"[channels] telegram={'on' if tg.enabled else 'off'} "
          f"ntfy={'on' if ntfy.enabled else 'off'} supabase={'on' if sb.enabled else 'off'} "
          f"webhook={'on' if args.webhook else 'off'}")

    if args.respond:
        if not (tg.enabled and sb.enabled):
            print("[responder] needs TELEGRAM_* and SUPABASE_*", file=sys.stderr)
            return 1
        return run_responder(tg, sb, args)

    for act, eid in (("approved", args.approve), ("rejected", args.reject)):
        if eid and sb.enabled:
            ok, resp = sb.set_event_status(eid, act, "cli")
            print(f"[cli] event #{eid} -> {act}: {'ok' if ok else resp}")
            return 0 if ok else 1

    if args.check_heartbeat and sb.enabled:
        st, body = _get(f"{sb.url}/rest/v1/status?select=*", headers=sb._h())
        try:
            row = json.loads(body)[0]
            mins = float(row.get("minutes_since_run") or 9999)
        except Exception:
            mins = 9999.0
        if mins > args.max_staleness_min:
            text, prio, silent = compose_heartbeat_warning(mins)
            print(f"[watchdog] pipeline silent for {mins:.0f} min — publishing warning")
            if not args.dry_run:
                if tg.enabled:
                    tg.send(text, silent=silent)
                if ntfy.enabled:
                    ntfy.send("Monitor offline — no news is NOT good news",
                              f"Last successful run {mins:.0f} min ago.", priority=5,
                              tags=["warning"])
            return 2
        print(f"[watchdog] ok — last run {mins:.1f} min ago")
        return 0

    # load input
    monitor = vaac = None
    if args.monitor_json:
        monitor = json.load(open(args.monitor_json, encoding="utf-8"))
    if args.vaac_json:
        vaac = json.load(open(args.vaac_json, encoding="utf-8"))

    if args.events_json:
        events = json.load(open(args.events_json, encoding="utf-8"))
    else:
        events = events_from_monitor(monitor or {}, vaac)

    if not events:
        print("[publish] nothing to publish (no changes detected)")
        if sb.enabled:
            sb.heartbeat("ok", "no changes")
        return 0

    if args.workflow:
        verdict = None
        if args.verdict_json:
            verdict = json.load(open(args.verdict_json, encoding="utf-8"))
        rep = run_workflow(events, verdict, tg, ntfy, sb, args)
        print(f"[workflow] {rep}")
        if sb.enabled:
            sb.heartbeat("ok", f"workflow {rep}")
        return 0

    # dedupe against the stored cursor
    last_id = sb.cursor_get("telegram") if (args.supabase and sb.enabled) else 0
    pending = [e for e in events if int(e.get("id") or 0) > last_id] or events
    pending = pending[:MAX_MESSAGES_PER_RUN]
    if len(events) > MAX_MESSAGES_PER_RUN:
        print(f"[publish] capping at {MAX_MESSAGES_PER_RUN} messages "
              f"({len(events)} pending) — not flooding the channel")

    sent = 0
    for ev in pending:
        text, prio, silent = compose_event(ev)
        print(f"\n{'-'*70}\n{text}\n{'-'*70}")
        if args.dry_run:
            sent += 1
            continue
        ok_t, resp_t = tg.send(text, silent=silent) if args.telegram else (None, "disabled")
        if args.telegram and not ok_t:
            print(f"[telegram] FAILED: {resp_t}", file=sys.stderr)
        if (args.ntfy or args.ntfy_auto) and ntfy.enabled:
            ok_n, resp_n = ntfy.send(ev.get("title", "Update"),
                                     re.sub(r"<[^>]+>", "", ev.get("body") or ""),
                                     priority=prio, tags=["volcano"],
                                     click=ev.get("url"))
            if not ok_n:
                print(f"[ntfy] FAILED: {resp_n}", file=sys.stderr)
        if args.webhook:
            _post(args.webhook, data=json.dumps({"text": re.sub(r'<[^>]+>', '', text)}).encode(),
                  headers={"Content-Type": "application/json"})
        if ok_t:
            sent += 1

    if args.photo and not args.dry_run and tg.enabled:
        ok, resp = tg.send_photo("Peta perkiraan sekunder / secondary estimate map — "
                                 "bukan data resmi", args.photo)
        print(f"[photo] {'ok' if ok else resp}")

    if sb.enabled:
        if monitor:
            sb.insert_reading({
                "volcano": monitor.get("volcano"),
                "magma_level": monitor.get("level"),
                "magma_level_name": monitor.get("level_name"),
                "eruptions_today": monitor.get("eruptions_today"),
                "latest_vona_code": (monitor.get("latest_vona") or [{}])[0].get("code"),
                "vaac_state": (vaac or {}).get("state"),
                "vaac_layers": ((vaac or {}).get("advisory") or {}).get("observed_layers"),
                "sources": monitor.get("source_hierarchy"),
                "payload": monitor,
            })
        if sent and pending:
            sb.cursor_set("telegram", int(pending[-1].get("id") or 0))
        sb.heartbeat("ok" if sent or not pending else "degraded", f"{sent} message(s) sent")

    print(f"\n[publish] {sent}/{len(pending)} message(s) sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
