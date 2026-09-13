#!/usr/bin/env python3
"""
activity_state.py — is there a LIVE ash episode right now, or is the volcano
back to normal?

WHY THIS MODULE EXISTS (the 2026-09-12 lesson)
-----------------------------------------------
When Darwin VAAC stops issuing advisories (ash no longer identifiable) and
MAGMA has no fresh VONA/eruption rows, the pipeline used to keep rendering the
LAST data as if it were today's: the plume-top fallback happily quoted a
VONA issued 19 days earlier as "Puncak awan abu resmi hari ini". That is the
most dangerous failure mode a monitor can have — presenting the memory of an
eruption as the present.

The maintainer rule, agreed in advance:

    no real-time data from MAGMA *and* no real-time data from Darwin VAAC
    => NORMAL, not "fail", and never a silent fallback to old data.

This module turns that rule into one pure function. It never touches the
network: callers pass what the fetchers already produced, so it is fully
unit-testable offline (tests/test_quiet_state.py).

STATE MACHINE
-------------
    "active"  a live signal exists:
                - a CURRENT, non-terminated VAAC advisory, or
                - a VONA issued within VONA_FRESH_H, or
                - an eruption row within ERUPTION_FRESH_H.
    "quiet"   BOTH sources were reached successfully AND none of the live
              signals exists. Two sub-reasons:
                - "vaac_terminated": the VAAC bulletin explicitly says
                  ADVISORY TERMINATED / NO FURTHER ADVISORIES / CANCELLED —
                  the authoritative end of the episode.
                - "no_realtime_data": sources reachable, but nothing fresh.
    (held)    if a source is UNREACHABLE we cannot prove "normal" from it, so
              the previous state is carried forward (hysteresis). Absence of
              data is not evidence of absence — the 2026-09-08 lesson, when
              VAAC said "nil" while MAGMA recorded five eruptions that day.

The output dict rides along in snapshot.json as `activity`, drives the site's
normal-mode banner, gates the plume-top fallbacks, and pauses the 6-hourly
model (auto-publish + archive) while the volcano is quiet.

Stdlib only. Python 3.8+.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

ACTIVITY_VERSION = "activity/1.0"

# Freshness windows. Kept deliberately wider than validate.py's per-source SLA
# (7 h warn / 13 h fail): those gates grade a single record's hygiene, while
# THESE windows decide whether an episode is LIVE at all. A VONA arriving
# within 24 h is a real eruption signal even if the 6-hourly VAAC window has
# lapsed; an eruption row is "live" for 6 h.
VONA_FRESH_H = 24.0
ERUPTION_FRESH_H = 6.0
# Mirrors darwin_vaac.fetch()'s is_current (advisory age <= 12 h).
VAAC_CURRENT_H = 12.0


def _parse_iso(s):
    """Parse the timestamp shapes this pipeline actually meets:
      ISO-8601 (with Z or +00:00 or an explicit offset) — VAAC DTG,
      eruption rows, our own outputs;
      MAGMA's "2026-09-08 04:34:00 UTC" — VONA issued_utc straight off the
      timeline HTML. Missing the second format would silently drop every
      VONA age to None, and a FRESH VONA would then fail to flip the state
      machine to active — the exact class of bug this module exists to kill.
    ISO is tried FIRST so an explicit non-UTC offset is never flattened.
    """
    if not s:
        return None
    txt = str(s).strip()
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*(?:UTC)?$", txt)
    if m:
        try:
            return datetime.fromisoformat(m.group(1) + "T" + m.group(2)) \
                .replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes").replace("+00:00", "Z") if dt else None


def _age_h(iso, now):
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def newest_age(entries, key):
    """Age in hours (float, may be negative-free) of the newest entry that
    carries `key`, or None when nothing usable is present."""
    ages = [_age_h(e.get(key), datetime.now(timezone.utc)) for e in entries or []]
    ages = [a for a in ages if a is not None]
    return min(ages) if ages else None


def vona_plume_top(vona, now=None, max_age_h=VONA_FRESH_H):
    """The freshest VONA entry carrying an ash-top estimate, ONLY if it was
    issued within max_age_h. Returns the entry dict or None.

    This is the age gate the old code lacked: on 2026-09-12 the newest VONA
    with ash_top_m was 19 days old (557 m, 24 Aug) and was rendered as the
    OFFICIAL ash-cloud top "today". Now: no fresh VONA -> no anchor -> the
    site says there is no official top today, and in a quiet state it says
    why (normal activity) instead.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    best = None
    best_dt = None
    for v in vona or []:
        if not v.get("ash_top_m"):
            continue
        dt = _parse_iso(v.get("issued_utc"))
        if dt is None:
            continue
        age = (now - dt).total_seconds() / 3600.0
        if age > max_age_h:
            continue
        if best_dt is None or dt > best_dt:
            best, best_dt = v, dt
    return best


def assess_activity(vaac, vona, eruptions=None, now=None,
                    previous=None, magma_ok=True):
    """Decide `active` vs `quiet` from what the fetchers just produced.

    vaac      dict as returned by darwin_vaac.fetch() (state/terminated/age)
    vona      list of VONA entries (issued_utc, ash_top_m, ...) — may be []
    eruptions list of eruption rows (utc, ...) — may be [] or None
    now       aware UTC datetime (defaults to real now; tests pin it)
    previous  the previous snapshot's `activity` dict, for hysteresis and
              `since_utc` carry-forward. None on the very first run.
    magma_ok  False when the MAGMA collect errored (mon["error"] set):
              an unreachable source can never vote for "quiet".

    Returns the activity dict written to snapshot.json:
      state          "active" | "quiet"
      reason_code    machine code (see _REASONS)
      reason         {"id","en"} human sentences, source-labelled
      evidence       the numbers behind the decision (for the ledger)
      since_utc      when this state began (carried when unchanged)
      assessed_utc   this assessment's timestamp
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)

    vstate = (vaac or {}).get("state")
    vterm = bool((vaac or {}).get("terminated"))
    vage = (vaac or {}).get("age_hours")
    vaac_ok = vstate in ("advisory", "nil", "stale")   # endpoint answered
    vaac_current = bool(vstate == "advisory" and not vterm
                        and vage is not None and vage <= VAAC_CURRENT_H)

    vona_age = None
    newest_vona = None
    for v in vona or []:
        a = _age_h(v.get("issued_utc"), now)
        if a is None:
            continue
        if vona_age is None or a < vona_age:
            vona_age, newest_vona = a, v
    fresh_vona = bool(vona_age is not None and vona_age <= VONA_FRESH_H)

    erup_age = None
    for e in eruptions or []:
        a = _age_h(e.get("utc"), now)
        if a is not None and (erup_age is None or a < erup_age):
            erup_age = a
    fresh_eruption = bool(erup_age is not None and erup_age <= ERUPTION_FRESH_H)

    evidence = {
        "vaac_state": vstate,
        "vaac_terminated": vterm or None,
        "vaac_age_hours": (round(vage, 1) if isinstance(vage, (int, float)) else None),
        "newest_vona_utc": (newest_vona or {}).get("issued_utc"),
        "vona_age_hours": (round(vona_age, 1) if vona_age is not None else None),
        "newest_eruption_age_hours": (round(erup_age, 1) if erup_age is not None else None),
    }

    prev_state = (previous or {}).get("state")
    prev_since = (previous or {}).get("since_utc")

    if vaac_current:
        state, code = "active", "vaac_current"
    elif fresh_vona:
        state, code = "active", "fresh_vona"
    elif fresh_eruption:
        state, code = "active", "fresh_eruption"
    elif vaac_ok and magma_ok:
        # both sources answered; nothing live anywhere => NORMAL.
        # A terminated bulletin says so explicitly; otherwise it is simply
        # "no real-time data from either authority".
        state = "quiet"
        code = "vaac_terminated" if vterm else "no_realtime_data"
    else:
        # a source is unreachable: absence is not evidence of absence.
        # Carry the previous state forward (default: keep watching).
        state = prev_state or "active"
        code = ("held_vaac_unreachable" if not vaac_ok
                else "held_magma_unreachable")
        evidence["held"] = True

    _REASONS = {
        "vaac_current": {
            "id": "Advisori Darwin VAAC masih berlaku (umur {vaac_age} jam).",
            "en": "Darwin VAAC advisory still current (age {vaac_age} h)."},
        "fresh_vona": {
            "id": "VONA MAGMA terbaru terbit {vona_age} jam lalu.",
            "en": "Latest MAGMA VONA was issued {vona_age} h ago."},
        "fresh_eruption": {
            "id": "Erupsi tercatat MAGMA dalam {erup_age} jam terakhir.",
            "en": "MAGMA recorded an eruption within the last {erup_age} h."},
        "vaac_terminated": {
            "id": ("Darwin VAAC telah menghentikan advisori—abu tidak lagi "
                   "teridentifikasi; tidak ada VONA/erupsi baru dari MAGMA."),
            "en": ("Darwin VAAC has terminated its advisory—ash no longer "
                   "identifiable; no fresh VONA/eruption from MAGMA.")},
        "no_realtime_data": {
            "id": ("Tidak ada data real-time dari MAGMA maupun Darwin VAAC—"
                   "tidak ada episode abu yang berlangsung."),
            "en": ("No real-time data from either MAGMA or Darwin VAAC—"
                   "no ash episode in progress.")},
        "held_vaac_unreachable": {
            "id": "Darwin VAAC tidak terjangkau; status sebelumnya dipertahankan.",
            "en": "Darwin VAAC unreachable; previous state held."},
        "held_magma_unreachable": {
            "id": "MAGMA tidak terjangkau; status sebelumnya dipertahankan.",
            "en": "MAGMA unreachable; previous state held."},
    }
    reason = dict(_REASONS[code])
    for k in ("vaac_age", "vona_age", "erup_age"):
        val = {"vaac_age": evidence.get("vaac_age_hours"),
               "vona_age": evidence.get("vona_age_hours"),
               "erup_age": evidence.get("newest_eruption_age_hours")}[k]
        reason["id"] = reason["id"].replace("{" + k + "}",
                                            "?" if val is None else str(val))
        reason["en"] = reason["en"].replace("{" + k + "}",
                                            "?" if val is None else str(val))

    since = prev_since if (prev_state == state and prev_since) else _iso(now)

    return {
        "version": ACTIVITY_VERSION,
        "state": state,
        "reason_code": code,
        "reason": reason,
        "evidence": evidence,
        "since_utc": since,
        "assessed_utc": _iso(now),
    }
