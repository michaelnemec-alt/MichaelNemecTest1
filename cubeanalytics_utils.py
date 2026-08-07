"""CubeAnalytics API helpers."""

import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import raw_events

BASE_URL = "https://api.cubeanalytics.autostoresystem.com/v1"

# --- Per-day on-disk cache -------------------------------------------------
# The API is queried by a date range and returns one immutable result object
# per past day. Re-downloading the whole range on every view / restart is the
# main bottleneck when self-hosting (slow uplink). So we cache each day's raw
# response on disk (keyed by installation + endpoint + day) and, on the next
# request, fetch only the days we don't have yet — typically just the new tail.
#
# The cache lives on the Streamlit disk-cache volume (persisted across restarts
# in the container); override with CUBE_DAY_CACHE_DIR.
_DAY_CACHE_DIR = Path(
    os.environ.get("CUBE_DAY_CACHE_DIR", str(Path.home() / ".streamlit" / "cube_day_cache"))
)
# Days this recent are still accumulating (may change), so their cached copy is
# only trusted for _FRESH_TTL_SECONDS before being re-fetched. Older days are
# immutable and cached permanently. Recent days are still written to disk so
# that rapidly changing the date range (e.g. 30d -> 14d, both ending today)
# reuses what was just downloaded instead of re-hitting the network every time.
_REFRESH_TAIL_DAYS = 1
_FRESH_TTL_SECONDS = int(os.environ.get("CUBE_FRESH_TTL_SECONDS", "900"))  # 15 min
_ENDPOINT_RE = re.compile(r"/installations/([^/]+)/([^/]+)/?$")

# Cap per-function in-memory cache entries. Without this, @st.cache_data grows
# unbounded — every (site, from, to) keeps a full DataFrame in RAM forever, so
# browsing many sites/ranges slowly exhausts memory on a small NAS. ~10 sites ×
# a few ranges fits comfortably; older entries are evicted (and, thanks to the
# per-day disk cache above, cheaply rebuilt without re-downloading history).
_CACHE_MAX_ENTRIES = int(os.environ.get("CUBE_CACHE_MAX_ENTRIES", "40"))


@st.cache_resource
def _session():
    """Shared HTTP session that transparently retries transient failures
    (502/503/504 gateway errors and connection drops) with backoff, so a
    flaky upstream API recovers instead of failing the whole load."""
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=1,
        status=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=64,
        pool_maxsize=64,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _token():
    """Current CubeAnalytics API token from secrets, or '' when absent."""
    try:
        return st.secrets["cubeanalytics"]["token"] or ""
    except (KeyError, FileNotFoundError):
        return ""


def _token_fingerprint():
    """Short, non-reversible hash of the token, used only as a cache key so a
    token swap (e.g. broader site access) invalidates token-scoped caches like
    the installation list instead of serving the previous token's result."""
    tok = _token()
    return hashlib.sha256(tok.encode()).hexdigest()[:12] if tok else ""


def is_api_configured():
    """Return True when a CubeAnalytics API token is present in secrets."""
    return bool(_token())


def _headers():
    return {"API-Authorization": f"Token {_token()}"}


# Installations whose name contains any of these (case-insensitive) are hidden
# everywhere — e.g. the AutoStore demo site, which isn't a real warehouse.
# Override with a comma-separated CUBE_EXCLUDE_INSTALLATIONS.
_EXCLUDE_INSTALLATIONS = [
    s.strip().lower()
    for s in os.environ.get("CUBE_EXCLUDE_INSTALLATIONS", "demo").split(",")
    if s.strip()
]


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def _get_installations_cached(token_fp):
    """Token-scoped installation fetch. `token_fp` is only a cache key (see
    _token_fingerprint) so switching tokens returns the new token's sites."""
    del token_fp  # used solely as the cache key
    resp = _session().get(f"{BASE_URL}/installations/", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    installations = []
    for r in data.get("results", []):
        name = r["name"]
        if any(term in name.lower() for term in _EXCLUDE_INSTALLATIONS):
            continue
        installations.append({
            "id": r["id"],
            "name": name,
            "city": r.get("city", ""),
            "country": r.get("country", ""),
        })
    return installations


def get_installations():
    """Installations the current token can access (id, name, city, country).

    Excludes _EXCLUDE_INSTALLATIONS (e.g. the demo site). The result is cached
    per token, so a token change (e.g. gaining more sites) refreshes the list
    rather than serving the previous token's installations from disk cache.
    """
    return _get_installations_cached(_token_fingerprint())


# Short-code / common-name aliases for CubeAnalytics sites, matched as a
# substring against the installation city or name (case-insensitive). Used to
# label filters and tables so operators recognise the FC (e.g. the two Pragues:
# Praha = PRG2, Chrášťany = PRG3).
_SITE_ALIASES = (
    ("Bischofsheim", "FRA / Frankfurt"),
    ("Chrášťany", "PRG3 / Prague 3"),
    ("Praha", "PRG2 / Prague 2"),
    ("Garching", "MUC / Munich"),
    ("Schönefeld", "BER / Berlin"),
    ("Vienna", "VIE / Vienna"),
    ("Biatorbágy", "BUD / Budapest"),
)


def site_alias(text):
    """Short code / common name for a site given its city or installation name,
    or None when unknown."""
    low = (text or "").lower()
    for key, alias in _SITE_ALIASES:
        if key.lower() in low:
            return alias
    return None


def site_display_label(text):
    """`<name> · <alias>` when a short code is known, else the name unchanged."""
    alias = site_alias(text)
    return f"{text} · {alias}" if alias else text


def _fetch_all_pages(url, params):
    """Follow pagination and collect all results."""
    all_results = []
    while url:
        resp = _session().get(url, headers=_headers(), params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        all_results.extend(data.get("results", []))
        url = data.get("next")
        params = None  # next URL already contains params
    return all_results


def _iter_days(date_from_str, date_to_str):
    d0 = datetime.strptime(date_from_str, "%Y-%m-%d").date()
    d1 = datetime.strptime(date_to_str, "%Y-%m-%d").date()
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _contiguous_groups(days):
    """Split a sorted list of dates into runs of consecutive days."""
    if not days:
        return []
    days = sorted(days)
    groups = [[days[0]]]
    for d in days[1:]:
        if (d - groups[-1][-1]).days == 1:
            groups[-1].append(d)
        else:
            groups.append([d])
    return groups


def _day_cache_path(installation_id, endpoint, day):
    return _DAY_CACHE_DIR / str(installation_id) / endpoint / f"{day.isoformat()}.json"


def _read_day(path):
    """Return (objs, fetched_epoch) for a cached day, or None if absent/unreadable.

    Tolerates the legacy on-disk format (a bare list with no timestamp), which
    is treated as immutable (fetched_epoch = 0)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and "objs" in data:
        return data["objs"], float(data.get("fetched", 0) or 0)
    if isinstance(data, list):
        return data, 0.0
    return None


def _write_day(path, objs):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"objs": objs, "fetched": time.time()}, f)
        os.replace(tmp, path)
    except OSError:
        pass  # cache is best-effort; a write failure just means a re-fetch later


def clear_day_cache(day=None):
    """Delete per-day disk-cache files so the next query re-fetches from the API.

    The Streamlit ``@st.cache_data`` memo is only the outer layer; ``_fetch_days``
    keeps its own on-disk per-day cache that ``.clear()`` does NOT touch. A day
    first fetched while the API had no data for it (e.g. it was still "today")
    gets stored empty and, once older than ``_REFRESH_TAIL_DAYS``, is treated as
    immutable and served empty forever. The Recalculate button must call this to
    genuinely force a fresh pull.

    day : a ``date`` to clear across every installation/endpoint; None wipes the
          whole per-day cache.
    """
    if not _DAY_CACHE_DIR.exists():
        return 0
    removed = 0
    if day is None:
        pattern = "*.json"
    else:
        pattern = f"{day.isoformat()}.json"
    for path in _DAY_CACHE_DIR.rglob(pattern):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _day_of(obj):
    d = obj.get("date")
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_days(url, params):
    """Range fetch with a per-day disk cache in front of _fetch_all_pages.

    Returns the same list of per-day result objects _fetch_all_pages would,
    but reads immutable past days from disk and only hits the network for the
    days that are missing or still recent. Falls back to a plain range fetch if
    the URL/params don't look like a dated installation query.
    """
    m = _ENDPOINT_RE.search(url)
    if not m or not params or "after" not in params or "before" not in params:
        return _fetch_all_pages(url, params)
    installation_id, endpoint = m.group(1), m.group(2)
    try:
        days = _iter_days(params["after"], params["before"])
    except (ValueError, TypeError):
        return _fetch_all_pages(url, params)
    if not days:
        return _fetch_all_pages(url, params)

    fresh_cutoff = datetime.now(timezone.utc).date() - timedelta(days=_REFRESH_TAIL_DAYS)
    now = time.time()

    cached = {}
    missing = []
    for d in days:
        entry = _read_day(_day_cache_path(installation_id, endpoint, d))
        if entry is None:
            missing.append(d)
            continue
        objs, fetched_at = entry
        if d < fresh_cutoff or (now - fetched_at) < _FRESH_TTL_SECONDS:
            # Immutable past day, or a recent day whose copy is still fresh.
            cached[d] = objs
        else:
            missing.append(d)  # recent day, cached copy is stale — re-fetch

    fetched = {}
    for group in _contiguous_groups(missing):
        raw = _fetch_all_pages(
            url, {"after": group[0].isoformat(), "before": group[-1].isoformat()}
        )
        by_day = {}
        for obj in raw:
            od = _day_of(obj)
            if od is not None:
                by_day.setdefault(od, []).append(obj)
        for d in group:
            objs = by_day.get(d, [])
            fetched[d] = objs
            _write_day(_day_cache_path(installation_id, endpoint, d), objs)

    out = []
    for d in days:
        out.extend(cached.get(d) if d in cached else fetched.get(d, []))
    return out


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_system_health(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/system-health/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        h = day_result.get("result", {})
        rows.append({
            "date": day_result.get("date"),
            "health_index": h.get("health_index"),
            "health_bucket": h.get("health_bucket"),
            "uptime": h.get("uptime"),
            "wait_bin": h.get("wait_bin"),
            "waste_time": h.get("waste_time"),
            "average_battery_score": h.get("average_battery_score"),
            "mtbf_h": h.get("mtbf_h"),
            "packet_loss": h.get("packet_loss"),
            "mbbd": h.get("mbbd"),
            "uptime_score": h.get("uptime_score"),
            "wait_time_score": h.get("wait_time_score"),
            "waste_time_score": h.get("waste_time_score"),
            "battery_score": h.get("battery_score"),
            "mtbf_score": h.get("mtbf_score"),
            "packet_loss_score": h.get("packet_loss_score"),
            "mbbd_score": h.get("mbbd_score"),
            "mtbf_error_count": h.get("mtbf_error_count"),
            "mtbf_operational_hours": h.get("mtbf_operational_hours"),
            "mbbd_bin_count": h.get("mbbd_bin_count"),
            "mbbd_port_downtime_count": h.get("mbbd_port_downtime_count"),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_uptime(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        r = day_result.get("result", {})
        rows.append({
            "date": day_result.get("date"),
            "up_ratio": r.get("up_ratio"),
            "recovery_up_ratio": r.get("recovery_up_ratio"),
            "up_seconds": r.get("up_seconds"),
            "down_seconds": r.get("down_seconds"),
            "recovery_seconds": r.get("recovery_seconds"),
            "total_seconds": r.get("total_seconds"),
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_system_mode_periods(installation_id, date_from_str, date_to_str):
    """Uptime/downtime periods (system mode periods) read from the uptime endpoint.

    Returns one row per period with the same columns the CubeAnalytics portal
    shows in "System mode periods". Times are recorded within the same calendar
    day, so a stop that persists overnight appears twice (once per day).

    Columns: date, mode ('Up'|'Down'), start_at, end_at, module, module_error,
             stop_code, up_seconds, down_seconds.
    """
    url = f"{BASE_URL}/installations/{installation_id}/uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        day = day_result.get("date")
        for p in day_result.get("result", {}).get("periods", []):
            is_up = p.get("mode") == "uptime"
            robot_id = p.get("stop_robot_id")
            err_code = p.get("stop_error_code")
            err_name = p.get("stop_error_name")
            module_error = (
                f"{err_code} {err_name}".strip()
                if (err_code is not None or err_name) else ""
            )
            rows.append({
                "date": day,
                "mode": "Up" if is_up else "Down",
                "start_at": p.get("start_at"),
                "end_at": p.get("end_at"),
                "module": f"Robot {robot_id}" if robot_id is not None else "",
                "module_error": module_error,
                "stop_code": p.get("stop_code_name") or "",
                "up_seconds": p.get("up_seconds", 0) or 0,
                "down_seconds": p.get("down_seconds", 0) or 0,
            })

    if not rows:
        return pd.DataFrame(
            columns=["date", "mode", "start_at", "end_at", "module",
                     "module_error", "stop_code", "up_seconds", "down_seconds"]
        )
    df = pd.DataFrame(rows)
    df["start_at"] = pd.to_datetime(df["start_at"], errors="coerce")
    df["end_at"] = pd.to_datetime(df["end_at"], errors="coerce")
    df = df.sort_values("start_at").reset_index(drop=True)
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_robot_state(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/robot-state/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        robot_states = day_result.get("result", {}).get("robot_states", {})
        all_robots = []
        if isinstance(robot_states, dict):
            for hour_robots in robot_states.values():
                if isinstance(hour_robots, list):
                    all_robots.extend(hour_robots)
        elif isinstance(robot_states, list):
            all_robots = robot_states

        total_time = sum(r.get("total_time_s", 0) for r in all_robots)
        if total_time == 0:
            continue

        avail = sum(r.get("available", 0) for r in all_robots)
        charging_avail = sum(r.get("charging_available", 0) for r in all_robots)
        working = sum(r.get("working", 0) for r in all_robots)
        recovery = sum(r.get("recovery", 0) for r in all_robots)
        unavailable = sum(r.get("unavailable", 0) for r in all_robots)
        charging_unavail = sum(r.get("charging_unavailable", 0) for r in all_robots)
        service_on = sum(r.get("service_on_grid", 0) for r in all_robots)
        service_off = sum(r.get("service_off_grid", 0) for r in all_robots)

        rows.append({
            "date": day_result.get("date"),
            "robot_availability_pct": (avail + charging_avail) / total_time * 100,
            "working_pct": working / total_time * 100,
            "charging_available_pct": charging_avail / total_time * 100,
            "available_pct": avail / total_time * 100,
            "recovery_pct": recovery / total_time * 100,
            "unavailable_pct": unavailable / total_time * 100,
            "charging_unavailable_pct": charging_unavail / total_time * 100,
            "service_on_grid_pct": service_on / total_time * 100,
            "service_off_grid_pct": service_off / total_time * 100,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_robot_state_per_robot(installation_id, date_from_str, date_to_str):
    """Per (date, robot) uptime metrics for a single installation.

    query_robot_state sums every robot into one site figure; this keeps each
    robot separate so a site can be broken down robot by robot. Down time =
    recovery + unavailable + service on/off grid, matching the site robot-uptime
    definition; uptime_pct = (total_time - down) / total_time x 100.
    """
    url = f"{BASE_URL}/installations/{installation_id}/robot-state/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        robot_states = day_result.get("result", {}).get("robot_states", {})
        all_robots = []
        if isinstance(robot_states, dict):
            for hour_robots in robot_states.values():
                if isinstance(hour_robots, list):
                    all_robots.extend(hour_robots)
        elif isinstance(robot_states, list):
            all_robots = robot_states
        for r in all_robots:
            rows.append({
                "date": d,
                "robot_id": r.get("robot_id"),
                "robot_type": r.get("robot_type", ""),
                "total_time_s": r.get("total_time_s", 0) or 0,
                "working": r.get("working", 0) or 0,
                "available": r.get("available", 0) or 0,
                "charging_available": r.get("charging_available", 0) or 0,
                "charging_unavailable": r.get("charging_unavailable", 0) or 0,
                "recovery": r.get("recovery", 0) or 0,
                "unavailable": r.get("unavailable", 0) or 0,
                "service_on_grid": r.get("service_on_grid", 0) or 0,
                "service_off_grid": r.get("service_off_grid", 0) or 0,
                "battery_pct_avg": r.get("battery_pct_avg"),
            })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_robot_movement(installation_id, date_from_str, date_to_str):
    """Per (date, robot) physical movement for a single installation.

    Distance (x+y+z, converted to km) and lift count per robot per day, from
    the /robot-movement endpoint. The endpoint currently returns two API
    versions per day (2.0.0 current, 1.3.0 deprecated) for the same data;
    only 2.0.0 is kept to avoid double-counting.
    """
    url = f"{BASE_URL}/installations/{installation_id}/robot-movement/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        if day_result.get("version") != "2.0.0":
            continue
        d = day_result.get("date")
        movements = day_result.get("result", {}).get("robot_movements", {})
        if not isinstance(movements, dict):
            continue
        for robot_id, m in movements.items():
            dist_m = (m.get("distance_x_m", 0) or 0) + (m.get("distance_y_m", 0) or 0) + (m.get("distance_z_m", 0) or 0)
            try:
                rid = int(robot_id)
            except (TypeError, ValueError):
                rid = robot_id
            rows.append({
                "date": d,
                "robot_id": rid,
                "distance_km": dist_m / 1000.0,
                "total_lifts": m.get("total_lifts", 0) or 0,
            })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_robot_state_hourly(installation_id, date_from_str, date_to_str):
    """Per (date, hour) robot working vs total time for one installation.

    Keeps the hourly resolution the robot-state endpoint reports (keys like
    '08:00:00'), summing seconds across all robots present that hour:
      working_s = time robots spent working, total_s = total robot time,
      charging_unavailable_s = time robots were charging and unable to work
      (the uncontrollable battery constraint), utilization = working_s / total_s.
    total_s / 3600 is the robot count; charging_unavailable_s / 3600 is the
    robots kept off work by charging. Used to build the throughput ceiling.
    """
    url = f"{BASE_URL}/installations/{installation_id}/robot-state/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        robot_states = day_result.get("result", {}).get("robot_states", {})
        if not isinstance(robot_states, dict):
            continue
        for hour_key, hour_robots in robot_states.items():
            if not isinstance(hour_robots, list) or not hour_robots:
                continue
            try:
                hour = int(str(hour_key).split(":")[0])
            except ValueError:
                continue
            working_s = sum(r.get("working", 0) or 0 for r in hour_robots)
            total_s = sum(r.get("total_time_s", 0) or 0 for r in hour_robots)
            charging_unavailable_s = sum(
                r.get("charging_unavailable", 0) or 0 for r in hour_robots
            )
            if total_s == 0:
                continue
            rows.append({
                "date": d,
                "hour": hour,
                "working_s": working_s,
                "total_s": total_s,
                "charging_unavailable_s": charging_unavailable_s,
                "n_robots": len(hour_robots),
            })

    if not rows:
        return pd.DataFrame(columns=[
            "date", "hour", "working_s", "total_s",
            "charging_unavailable_s", "n_robots",
        ])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_bin_presentations(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/bin-presentations/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        bp_list = day_result.get("result", {}).get("bin_presentations", [])
        total_count = sum(bp.get("count", 0) for bp in bp_list)
        total_picks = sum(bp.get("picks", 0) for bp in bp_list)
        total_goods_in = sum(bp.get("goods_in", 0) for bp in bp_list)
        total_count_all = sum(bp.get("count_all_bins", 0) for bp in bp_list)

        w_bins = [bp.get("average_wait_bin", 0) * bp.get("count", 1) for bp in bp_list if bp.get("count", 0) > 0]
        w_users = [bp.get("average_wait_user", 0) * bp.get("count", 1) for bp in bp_list if bp.get("count", 0) > 0]
        w_wastes = [bp.get("average_waste_time", 0) * bp.get("count", 1) for bp in bp_list if bp.get("count", 0) > 0]

        rows.append({
            "date": day_result.get("date"),
            "bin_presentations": total_count,
            "picks": total_picks,
            "goods_in": total_goods_in,
            "all_bins": total_count_all,
            "avg_wait_bin": sum(w_bins) / total_count if total_count else 0,
            "avg_wait_user": sum(w_users) / total_count if total_count else 0,
            "avg_waste_time": sum(w_wastes) / total_count if total_count else 0,
            "num_ports": len(bp_list),
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_bins_above(installation_id, date_from_str, date_to_str):
    """Digging depth per day: average number of bins that had to be moved to
    reach a requested bin. Weighted mean of the bins_above distribution.

    avg_digging_depth        = Σ(bins_above × tasks) / total_tasks
    avg_digging_depth_unique = Σ(bins_above × unique_tasks) / total_unique_tasks
    """
    url = f"{BASE_URL}/installations/{installation_id}/bins-above/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        res = day_result.get("result", {})
        dist = res.get("bins_above_list", []) or []
        total_tasks = res.get("total_tasks", 0) or 0
        total_unique = res.get("total_unique_tasks", 0) or 0
        weighted = sum(b.get("bins_above", 0) * b.get("tasks", 0) for b in dist)
        weighted_u = sum(b.get("bins_above", 0) * b.get("unique_tasks", 0) for b in dist)
        rows.append({
            "date": day_result.get("date"),
            "avg_digging_depth": weighted / total_tasks if total_tasks else None,
            "avg_digging_depth_unique": weighted_u / total_unique if total_unique else None,
            "total_tasks": total_tasks,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_bin_usage(installation_id, date_from_str, date_to_str):
    """Bin-usage efficiency for category 1 & 2 picks, per day.

    picks         = number of category-1/2 pick presentations
    unique_bins   = distinct task-groups presented (proxy for distinct bins —
                    the daily API exposes no bin_id; task-group is the finest
                    per-bin identifier available in port-bin-wait-time)
    picks_per_bin = picks / unique_bins (higher = better bin reuse, less digging)
    """
    url = f"{BASE_URL}/installations/{installation_id}/port-bin-wait-time/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        port_data = day_result.get("result", {}).get("port_hour_wait_time", {})
        picks = 0
        bins = set()
        for records in port_data.values():
            for rec in records:
                if rec.get("subtype") != "BIN_PRESENTATIONS" or rec.get("pick_type") != "picks":
                    continue
                if rec.get("category") not in (1, 2):
                    continue
                picks += rec.get("count", 0) or 0
                tg = rec.get("taskgroup")
                if tg is not None:
                    bins.add(tg)
        rows.append({
            "date": day_result.get("date"),
            "picks": picks,
            "unique_bins": len(bins),
            "picks_per_bin": picks / len(bins) if bins else None,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_port_uptime(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/port-uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        pm = day_result.get("result", {}).get("port_metrics", {})
        if not pm:
            continue
        ports = list(pm.values() if isinstance(pm, dict) else pm)
        total_open = sum(p.get("open_seconds", 0) for p in ports)
        total_closed = sum(p.get("closed_seconds", 0) for p in ports)
        total_down = sum(p.get("downtime_seconds", 0) for p in ports)
        utils = [p.get("utilization", 0) for p in ports if p.get("utilization") is not None]
        # Port uptime = (open + closed) / (open + closed + error downtime), matching the
        # CubeAnalytics portal: only error downtime counts, manual stopped/disabled does not.
        per_port_uptime = []
        for p in ports:
            o = p.get("open_seconds", 0) or 0
            c = p.get("closed_seconds", 0) or 0
            d = p.get("downtime_seconds", 0) or 0
            denom = o + c + d
            if denom:
                per_port_uptime.append((o + c) / denom * 100)

        rows.append({
            "date": day_result.get("date"),
            "uptime_pct": (sum(per_port_uptime) / len(per_port_uptime)) if per_port_uptime else 0,
            "utilization_pct": (sum(utils) / len(utils) * 100) if utils else 0,
            "open_seconds": total_open,
            "closed_seconds": total_closed,
            "downtime_seconds": total_down,
            "num_ports": len(list(ports)) if isinstance(pm, dict) else len(pm),
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_port_uptime_per_port(installation_id, date_from_str, date_to_str):
    """Per (date, port) uptime metrics for a single installation.

    Unlike query_port_uptime (which averages every port into one site figure),
    this keeps each port separate so a single site can be broken down port by
    port. uptime_pct = (open + closed) / (open + closed + error downtime),
    matching the portal (manual stopped/disabled time does not count against it).
    """
    url = f"{BASE_URL}/installations/{installation_id}/port-uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        pm = day_result.get("result", {}).get("port_metrics", {})
        if not pm:
            continue
        items = pm.items() if isinstance(pm, dict) else enumerate(pm)
        for pid, p in items:
            _o = p.get("open_seconds", 0) or 0
            _c = p.get("closed_seconds", 0) or 0
            _d = p.get("downtime_seconds", 0) or 0
            _denom = _o + _c + _d
            rows.append({
                "date": d,
                "port": str(pid),
                "uptime_pct": ((_o + _c) / _denom * 100) if _denom else 0.0,
                "utilization_pct": (p.get("utilization") or 0) * 100,
                "open_seconds": p.get("open_seconds", 0) or 0,
                "closed_seconds": p.get("closed_seconds", 0) or 0,
                "downtime_seconds": p.get("downtime_seconds", 0) or 0,
                "stopped_seconds": p.get("stopped_seconds", 0) or 0,
                "disabled_seconds": p.get("disabled_seconds", 0) or 0,
                "down_periods": p.get("total_down_periods", 0) or 0,
            })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_incidents(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/incidents/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        incidents = day_result.get("result", {}).get("incidents", [])
        rows.append({
            "date": day_result.get("date"),
            "incident_count": len(incidents),
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_robot_errors(installation_id, date_from_str, date_to_str):
    url_re = f"{BASE_URL}/installations/{installation_id}/robot-errors/"
    url_inc = f"{BASE_URL}/installations/{installation_id}/incidents/"
    params = {"after": date_from_str, "before": date_to_str}
    re_results = _fetch_days(url_re, params)
    inc_results = _fetch_days(url_inc, params)

    all_errors = []
    for day_result in re_results:
        d = day_result.get("date")
        for e in day_result.get("result", {}).get("robot_errors", []):
            all_errors.append({
                "date": d,
                "ts": e.get("local_installation_timestamp", ""),
                "error_x": e.get("error_x"),
                "error_y": e.get("error_y"),
                "error_stopped_system": e.get("error_stopped_system"),
                "is_bin_quality": e.get("is_bin_quality"),
                "is_port": e.get("is_port"),
            })

    inc_keys = set()
    for day_result in inc_results:
        for inc in day_result.get("result", {}).get("incidents", []):
            ts = inc.get("start_local_timestamp", "")
            ts_sec = ts[:19] if len(ts) >= 19 else ts
            inc_keys.add((ts_sec, inc.get("x"), inc.get("y")))

    rows_by_date = {}
    for e in all_errors:
        ts_sec = e["ts"][:19] if len(e["ts"]) >= 19 else e["ts"]
        if (ts_sec, e["error_x"], e["error_y"]) not in inc_keys:
            continue
        d = e["date"]
        if d not in rows_by_date:
            rows_by_date[d] = {"date": d, "error_stopped_true": 0, "error_stopped_false": 0,
                               "total_errors": 0, "ops_errors": 0, "facility_errors": 0}
        r = rows_by_date[d]
        r["total_errors"] += 1
        if e["error_stopped_system"] is True:
            r["error_stopped_true"] += 1
        else:
            r["error_stopped_false"] += 1
        if e["is_bin_quality"] is True and e["is_port"] is False:
            r["ops_errors"] += 1
        if e["is_bin_quality"] is False:
            r["facility_errors"] += 1

    if not rows_by_date:
        return pd.DataFrame()
    df = pd.DataFrame(list(rows_by_date.values()))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# Stop codes grouped into recovery categories.
# Manual = human-initiated stops: STOPPED_FROM_CONSOLE (operator stopped from the
# console) and KEYLOCK_DISARMED (key switch left off after a restart auto-stops the
# system). Every other coded downtime segment is a fault / error stop — this matches
# the AutoStore portal's "Errors causing system stop" total, which counts all fault
# codes (XHANDLER_ROBOT_ERROR_FAILED, RETRANS_MISSING_AP, ROBOT_DOOR_STOP, …), not
# just the generic robot-error wrapper.
_MANUAL_STOP_CODES = {"STOPPED_FROM_CONSOLE", "KEYLOCK_DISARMED"}


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_recovery_times(installation_id, date_from_str, date_to_str):
    """'Time to recover' events read from the uptime endpoint's downtime periods.

    Returns one row per recovery event with columns:
      date, category ('error_stop' | 'manual'), recovery_seconds

    The uptime endpoint reports every downtime segment with its stop code and the
    total time the system was down (down_seconds = STOPPED -> RUNNING), so it is a
    first-party source with no event-log reconstruction needed.

    - error_stop: system force-stopped by any fault code (robot errors such as
      MISSING_GAP/BRAKE_FAILURE reported under XHANDLER_ROBOT_ERROR_FAILED, plus
      RETRANS_MISSING_AP, ROBOT_DOOR_STOP, …) — i.e. any non-manual coded stop.
    - manual: operator/console stop or a key-lock-disarmed stop (human-initiated).

    recovery_seconds = down_seconds (total time the system was stopped).
    """
    url = f"{BASE_URL}/installations/{installation_id}/uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = _recovery_rows_from_uptime(results)
    if not rows:
        return pd.DataFrame(columns=["date", "category", "recovery_seconds"])
    return pd.DataFrame(rows)


def _recovery_rows_from_uptime(results):
    """Turn raw uptime pages into recovery rows (date, category, recovery_seconds).

    Kept pure (no I/O) so the classification and stitching rules can be tested.
    """
    segs = []
    for day_result in results:
        for seg in day_result.get("result", {}).get("periods", []):
            if seg.get("mode") != "downtime":
                continue
            code = seg.get("stop_code_name")
            if not code:
                continue
            start = pd.to_datetime(seg.get("start_at"), errors="coerce")
            if pd.isna(start):
                continue
            segs.append({
                "start": start,
                "end": pd.to_datetime(seg.get("end_at"), errors="coerce"),
                "code": code,
                "down_seconds": seg.get("down_seconds", 0) or 0,
            })

    # The uptime endpoint truncates a stop that spans midnight into one segment per
    # day, which double-counts a single physical stop. Merge a segment into the
    # previous one when it has the same stop code and starts within a couple of
    # seconds of the previous segment's end (i.e. is contiguous across the day
    # boundary). This matches the AutoStore portal's per-stop counting.
    segs.sort(key=lambda s: s["start"])
    merged = []
    for s in segs:
        if merged:
            prev = merged[-1]
            if (s["code"] == prev["code"] and pd.notna(prev["end"])
                    and 0 <= (s["start"] - prev["end"]).total_seconds() <= 2):
                prev["end"] = s["end"]
                prev["down_seconds"] += s["down_seconds"]
                continue
        merged.append(s)

    return [{
        "date": s["start"].normalize(),
        "category": "manual" if s["code"] in _MANUAL_STOP_CODES else "error_stop",
        "recovery_seconds": s["down_seconds"],
    } for s in merged]


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_port_wait_time_daily(installation_id, date_from_str, date_to_str):
    """Port bin-wait-time collapsed to one row per (date, port, pick type, category).

    The API returns hourly records; over long ranges that is hundreds of thousands
    of rows per site, which exhausts memory when several sites load at once. We
    aggregate the hourly records to daily on the fly (count-weighted averages,
    identical to the downstream aggregation) so only the compact daily grain is
    retained. Full hourly detail remains available via query_port_wait_time().
    """
    url = f"{BASE_URL}/installations/{installation_id}/port-bin-wait-time/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    # key -> [count, sum(wait_bin*count), sum(wait_user*count), sum(waste*count)]
    agg = {}
    for day_result in results:
        date_str = day_result.get("date")
        port_data = day_result.get("result", {}).get("port_hour_wait_time", {})
        for port_id_str, records in port_data.items():
            port_id = int(port_id_str)
            for rec in records:
                if rec.get("subtype") != "BIN_PRESENTATIONS":
                    continue
                cat = rec.get("category")
                key = (
                    date_str,
                    port_id,
                    rec.get("pick_type", ""),
                    str(int(cat)) if cat is not None else "",
                )
                count = rec.get("count", 0) or 0
                slot = agg.get(key)
                if slot is None:
                    slot = [0, 0.0, 0.0, 0.0]
                    agg[key] = slot
                slot[0] += count
                slot[1] += rec.get("average_wait_bin", 0) * count
                slot[2] += rec.get("average_wait_user", 0) * count
                slot[3] += rec.get("average_waste_time", 0) * count

    if not agg:
        return pd.DataFrame()

    rows = []
    for (date_str, port_id, pick_type, category), (count, w_bin, w_user, w_waste) in agg.items():
        denom = count if count else 1
        rows.append({
            "date": date_str,
            "port_id": port_id,
            "pick_type": pick_type,
            "category": category,
            "count": count,
            "average_wait_bin": w_bin / denom,
            "average_wait_user": w_user / denom,
            "average_waste_time": w_waste / denom,
        })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_port_wait_time(installation_id, date_from_str, date_to_str):
    """Fetch port-bin-wait-time data and return a DataFrame matching the CSV format."""
    url = f"{BASE_URL}/installations/{installation_id}/port-bin-wait-time/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        port_data = day_result.get("result", {}).get("port_hour_wait_time", {})
        for port_id_str, records in port_data.items():
            for rec in records:
                if rec.get("subtype") != "BIN_PRESENTATIONS":
                    continue
                cat = rec.get("category")
                category = str(int(cat)) if cat is not None else ""

                rows.append({
                    "Timestamp": rec["hour"],
                    "Port ID": int(port_id_str),
                    "Pick type": rec.get("pick_type", ""),
                    "Count": rec.get("count", 0),
                    "Average bin wait time": rec.get("average_wait_bin", 0),
                    "Average operator handling time": rec.get("average_wait_user", 0),
                    "Category": category,
                })

    if not rows:
        return pd.DataFrame(columns=[
            "Timestamp", "Port ID", "Pick type", "Count",
            "Average bin wait time", "Average operator handling time", "Category",
        ])

    df = pd.DataFrame(rows)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    return df


_INSTALLATION_GROUPS = [
    "bin", "port", "robot", "charger", "interface", "xhandler",
    "zone_type", "radio_frequency", "environment_type",
]


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_installation_data(installation_id, date_from_str, date_to_str):
    """Daily asset census from the installation-data endpoint.

    Returns one row per (date, group, type) with columns:
      date, group, type, count

    group is one of bin/port/robot/charger/interface/xhandler/zone_type/
    radio_frequency/environment_type; type is the asset type (e.g. bins:
    'Standard 330', 'outside'); count is that day's snapshot count.
    """
    url = f"{BASE_URL}/installations/{installation_id}/installation-data/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        res = day_result.get("result", {})
        for group in _INSTALLATION_GROUPS:
            for pair in res.get(group) or []:
                rows.append({
                    "date": d,
                    "group": group,
                    "type": pair.get("type"),
                    "count": pair.get("count"),
                })

    if not rows:
        return pd.DataFrame(columns=["date", "group", "type", "count"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


_VERSION_RE = re.compile(r"^\d+(\.\d+)+")


def _representative_version(inst_map):
    """Pick a single version string for a module from its per-instance entries.

    Prefers entries with no sub_module (the module's own version). Otherwise
    falls back to the most common version-looking value across instances. A
    trailing '*' marks modules that report more than one distinct version.
    """
    null_versions = set()
    version_counts = Counter()
    for entries in inst_map.values():
        for e in entries:
            val = e.get("data")
            if val is None:
                continue
            if e.get("sub_module") is None:
                null_versions.add(val)
            if _VERSION_RE.match(str(val)):
                version_counts[val] += 1

    if null_versions:
        vals = sorted(null_versions)
        return vals[0] + (" *" if len(vals) > 1 else "")
    if version_counts:
        distinct = len(version_counts)
        top = version_counts.most_common(1)[0][0]
        return top + (" *" if distinct > 1 else "")
    return ""


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_module_versions(installation_id, date_from_str, date_to_str):
    """Module software/firmware versions from the module-versions endpoint.

    Returns one row per (date, module) with columns: date, module, version.
    version is a single representative string per module (see
    _representative_version); '*' flags modules with mixed versions.
    """
    url = f"{BASE_URL}/installations/{installation_id}/module-versions/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        data = day_result.get("result", {}).get("data", {})
        for module, inst_map in data.items():
            rows.append({
                "date": d,
                "module": module,
                "version": _representative_version(inst_map),
            })

    if not rows:
        return pd.DataFrame(columns=["date", "module", "version"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


@st.cache_data(ttl=86400, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_module_device_versions(installation_id, date_from_str, date_to_str):
    """Per-device firmware from the module-versions endpoint.

    Unlike query_module_versions (one representative version per module), this
    keeps the full breakdown: one row per (date, module, module_id, sub_module,
    value) so each robot/port/charger/access-point component can be inspected.
    """
    url = f"{BASE_URL}/installations/{installation_id}/module-versions/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_days(url, params)

    rows = []
    for day_result in results:
        d = day_result.get("date")
        data = day_result.get("result", {}).get("data", {})
        for module, inst_map in data.items():
            for module_id, entries in inst_map.items():
                for e in entries:
                    rows.append({
                        "date": d,
                        "module": module,
                        "module_id": str(module_id),
                        "sub_module": e.get("sub_module") or "(module)",
                        "value": e.get("data"),
                    })

    cols = ["date", "module", "module_id", "sub_module", "value"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# --- Live event stream (BIN_AND_TASK / ROBOT_STATE, 5-min) -----------------
# The REST live-events-stream is a short-retention repository of the WebSocket
# events: only the last 48h (lastHours) or a max 3-day window is available, and
# the response is a bare JSON list of events (no results/next pagination). To
# see more than that, events are served from the collector store when mounted
# (full retained history) and the REST ~48h window is merged on top for
# freshness; without the store we fall back to REST only.

# Robot state buckets we surface as "avg concurrent robots" per 5-min window.
_ROBOT_STATE_KEYS = (
    "working", "available", "recovery", "unavailable",
    "charging_available", "charging_unavailable",
    "service_on_grid", "service_off_grid",
)

# TTL kept short: live data changes every 5 min, so caching longer would show
# stale charts. Bound entries like the other queries.
_LIVE_TTL_SECONDS = int(os.environ.get("CUBE_LIVE_TTL_SECONDS", "300"))


def _parse_live_ts(raw):
    """Parse local_installation_timestamp keeping the installation's local
    wall-clock. All events from one installation share the same UTC offset, so
    parsing tz-aware then dropping the tz yields the local time directly. Falls
    back to UTC if offsets are mixed (e.g. a DST change within the window)."""
    ts = pd.to_datetime(raw, errors="coerce")
    if isinstance(ts.dtype, pd.DatetimeTZDtype):
        return ts.dt.tz_localize(None)
    # Mixed offsets -> object dtype; normalise via UTC so it stays sortable.
    return pd.to_datetime(raw, errors="coerce", utc=True).dt.tz_localize(None)


def _fetch_live_events(installation_id, last_hours, event_type):
    url = f"{BASE_URL}/installations/{installation_id}/live-events-stream/"
    params = {"lastHours": int(last_hours), "eventType": event_type}
    resp = _session().get(url, headers=_headers(), params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    # Bare list of events; the docs note the final item may hold missing
    # sequence numbers rather than an event, so keep only real events.
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("event_type") == event_type]


def _live_events(installation_id, last_hours, event_type):
    """Events for one installation/type, preferring the collector store.

    The REST ``live-events-stream`` only retains ~48h, so on its own the live
    views can never show more than two days. The night collector, however,
    writes every WebSocket event to an append-only store with no expiry. When
    that store is mounted we serve the **full retained history** from it and
    still merge the REST window on top for the freshest few minutes (the store
    can lag by one WebSocket batch). Events are de-duplicated by ``uuid`` so the
    overlap never double-counts. Without the store we fall back to REST only.
    """
    if not raw_events.is_available():
        return _fetch_live_events(installation_id, last_hours, event_type)

    merged = {}
    fallback_key = 0
    for e in raw_events.read_stream_events(installation_id, event_type):
        if not isinstance(e, dict) or e.get("event_type") != event_type:
            continue
        uid = e.get("uuid")
        if uid is None:
            uid = f"_noid_{fallback_key}"
            fallback_key += 1
        merged[uid] = e
    try:
        for e in _fetch_live_events(installation_id, last_hours, event_type):
            uid = e.get("uuid")
            if uid is None:
                uid = f"_noid_{fallback_key}"
                fallback_key += 1
            merged.setdefault(uid, e)
    except requests.RequestException:
        pass  # store history is enough; skip the freshness top-up on API error
    return list(merged.values())


@st.cache_data(ttl=_LIVE_TTL_SECONDS, max_entries=_CACHE_MAX_ENTRIES)
def query_live_jobs(installation_id, last_hours=48):
    """Bin-and-task job counts per 5-min live event for one installation.

    Columns: ts (tz-aware), created, updated, deleted, completed, active,
    total, unique, total_prepared, unique_prepared. Full collector history
    when the store is mounted, else the REST rolling ~48h.
    """
    fields = ("active", "total", "unique", "total_prepared", "unique_prepared",
              "created", "updated", "deleted", "completed")
    rows = []
    for e in _live_events(installation_id, last_hours, "BIN_AND_TASK"):
        d = e.get("data", {})
        row = {"ts": e.get("local_installation_timestamp")}
        for f in fields:
            row[f] = d.get(f, 0)
        rows.append(row)
    df = pd.DataFrame(rows, columns=["ts", *fields])
    if not df.empty:
        df["ts"] = _parse_live_ts(df["ts"])
        df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return df


@st.cache_data(ttl=_LIVE_TTL_SECONDS, max_entries=_CACHE_MAX_ENTRIES)
def query_live_robot_battery(installation_id, last_hours=48):
    """Per-robot battery % over time from the ROBOT_STATE live event.

    Returns a wide DataFrame: index-less with a 'ts' column and one column per
    robot_id holding its battery %. Full collector history when the store is
    mounted, else the REST rolling ~48h.
    """
    rows = []
    for e in _live_events(installation_id, last_hours, "ROBOT_STATE"):
        ts = e.get("local_installation_timestamp")
        for r in e.get("data", {}).get("robots", []):
            b = r.get("battery")
            rid = r.get("robot_id")
            if b is None or rid is None:
                continue
            rows.append({"ts": ts, "robot_id": int(rid), "battery": b})
    if not rows:
        return pd.DataFrame(columns=["ts"])
    long = pd.DataFrame(rows)
    long["ts"] = _parse_live_ts(long["ts"])
    long = long.dropna(subset=["ts"])
    wide = long.pivot_table(index="ts", columns="robot_id", values="battery",
                            aggfunc="last").sort_index()
    wide.columns = [f"R{c}" for c in wide.columns]
    return wide.reset_index()


@st.cache_data(ttl=_LIVE_TTL_SECONDS, max_entries=_CACHE_MAX_ENTRIES)
def query_live_robots(installation_id, last_hours=48):
    """Robot activity per 5-min live event for one installation.

    Each state column is the average number of robots concurrently in that
    state during the window (sum of per-robot seconds in state / window span).
    battery_avg is the mean battery % across robots. Full collector history
    when the store is mounted, else the REST rolling ~48h.
    """
    rows = []
    for e in _live_events(installation_id, last_hours, "ROBOT_STATE"):
        robots = e.get("data", {}).get("robots", [])
        if not robots:
            continue
        span = 0
        totals = {k: 0.0 for k in _ROBOT_STATE_KEYS}
        batteries = []
        for r in robots:
            sts = r.get("state_time_span_seconds", {})
            span = max(span, sum(v for v in sts.values() if isinstance(v, (int, float))))
            for k in _ROBOT_STATE_KEYS:
                totals[k] += sts.get(k, 0) or 0
            b = r.get("battery")
            if b is not None:
                batteries.append(b)
        span = span or 300
        row = {"ts": e.get("local_installation_timestamp"),
               "robots": len(robots),
               "battery_avg": sum(batteries) / len(batteries) if batteries else None}
        for k in _ROBOT_STATE_KEYS:
            row[k] = totals[k] / span
        rows.append(row)
    cols = ["ts", "robots", "battery_avg", *_ROBOT_STATE_KEYS]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["ts"] = _parse_live_ts(df["ts"])
        df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return df


# Live event types the REST live-events-stream accepts. CHARGER_STATE and
# STATUS are WebSocket-only and rejected here (HTTP 400), so they are excluded.
LIVE_EVENT_TYPES = (
    "BIN_AND_TASK", "ROBOT_STATE", "DOOR_STATE", "DELAYED_SYSTEM_STOP",
    "SYSTEM_MODE", "INCIDENT", "PORT_STATE", "PORT_ERROR", "ROBOT_ERROR",
)


@st.cache_data(ttl=_LIVE_TTL_SECONDS, max_entries=_CACHE_MAX_ENTRIES)
def query_live_event_table(installation_id, event_type, last_hours=48):
    """Flatten arbitrary live events into a per-record table for display.

    For array-valued events (DOOR_STATE door_states, ROBOT_STATE robots, …)
    each nested record becomes a row with a parsed 'ts'; scalar events (e.g.
    SYSTEM_MODE, INCIDENT, DELAYED_SYSTEM_STOP) give one row per event.
    Returns a DataFrame sorted by ts. Full collector history when the store
    is mounted, else the REST rolling ~48h.
    """
    rows = []
    for e in _live_events(installation_id, last_hours, event_type):
        ts = e.get("local_installation_timestamp")
        for rec in _flatten_event_data(e.get("data", {})):
            rows.append({"ts": ts, **rec})
    if not rows:
        return pd.DataFrame(columns=["ts"])
    df = pd.DataFrame(rows)
    df["ts"] = _parse_live_ts(df["ts"])
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    return df


def _flatten_one(d):
    """Flatten a dict one level deep, keeping scalars and the scalar sub-keys
    of any nested dict (e.g. DOOR_STATE's state -> grid/robot)."""
    row = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                if not isinstance(sv, (list, dict)):
                    row[sk] = sv
        elif not isinstance(v, list):
            row[k] = v
    return row


def _flatten_event_data(data):
    """Turn one event's data object into a list of flat dict rows.

    If the event has exactly one list-valued key (robots, door_states, ports,
    …) it explodes into one row per element; otherwise the scalar event yields
    a single row.
    """
    if not isinstance(data, dict):
        return [{"value": data}]
    list_keys = [k for k, v in data.items() if isinstance(v, list)]
    if len(list_keys) == 1:
        key = list_keys[0]
        scalars = {k: v for k, v in data.items() if k != key}
        out = []
        for item in data[key]:
            if isinstance(item, dict):
                out.append({**scalars, **_flatten_one(item)})
            else:
                out.append({**scalars, key: item})
        return out or [scalars]
    return [_flatten_one(data)]


@st.cache_data(ttl=3600, persist="disk", max_entries=_CACHE_MAX_ENTRIES)
def query_access_point_load(installation_id, date_from_str, date_to_str):
    """Hourly radio load per access point (REST /access-point-load).

    Returns a tidy DataFrame with one row per (access point, hour):
    ts, ap_id, x, y, channel, load (avg), peak_ap_load. Historical/daily data
    (not live), hourly resolution.
    """
    url = f"{BASE_URL}/installations/{installation_id}/access-point-load/"
    params = {"after": date_from_str, "before": date_to_str}
    rows = []
    for day_result in _fetch_days(url, params):
        apl = day_result.get("result", {}).get("access_point_load", {})
        for ap_id, ap in apl.items():
            for hl in ap.get("hourly_load", []):
                rows.append({
                    "ts": hl.get("hour"),
                    "ap_id": str(ap_id),
                    "x": ap.get("x"),
                    "y": ap.get("y"),
                    "channel": ap.get("channel"),
                    "load": hl.get("load"),
                    "peak_ap_load": hl.get("peak_ap_load"),
                })
    cols = ["ts", "ap_id", "x", "y", "channel", "load", "peak_ap_load"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.dropna(subset=["ts"]).sort_values(["ts", "ap_id"]).reset_index(drop=True)
    return df
