"""CubeAnalytics API helpers."""

import re
import streamlit as st
import pandas as pd
import requests

BASE_URL = "https://api.cubeanalytics.autostoresystem.com/v1"


def is_api_configured():
    """Return True when a CubeAnalytics API token is present in secrets."""
    try:
        return bool(st.secrets["cubeanalytics"]["token"])
    except (KeyError, FileNotFoundError):
        return False


def _headers():
    token = st.secrets["cubeanalytics"]["token"]
    return {"API-Authorization": f"Token {token}"}


@st.cache_data(ttl=3600)
def get_installations():
    """Fetch the list of installations the token has access to.

    Returns a list of dicts with keys: id, name, city, country.
    """
    resp = requests.get(f"{BASE_URL}/installations/", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    installations = []
    for r in data.get("results", []):
        installations.append({
            "id": r["id"],
            "name": r["name"],
            "city": r.get("city", ""),
            "country": r.get("country", ""),
        })
    return installations


def _fetch_all_pages(url, params):
    """Follow pagination and collect all results."""
    all_results = []
    while url:
        resp = requests.get(url, headers=_headers(), params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        all_results.extend(data.get("results", []))
        url = data.get("next")
        params = None  # next URL already contains params
    return all_results


@st.cache_data(ttl=300)
def query_system_health(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/system-health/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_uptime(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_robot_state(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/robot-state/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_bin_presentations(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/bin-presentations/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_port_uptime(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/port-uptime/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

    rows = []
    for day_result in results:
        pm = day_result.get("result", {}).get("port_metrics", {})
        if not pm:
            continue
        ports = pm.values() if isinstance(pm, dict) else pm
        total_open = sum(p.get("open_seconds", 0) for p in ports)
        total_closed = sum(p.get("closed_seconds", 0) for p in ports)
        total_down = sum(p.get("downtime_seconds", 0) for p in ports)
        total_stopped = sum(p.get("stopped_seconds", 0) for p in ports)
        total_disabled = sum(p.get("disabled_seconds", 0) for p in ports)
        total_all = total_open + total_closed + total_down + total_stopped + total_disabled
        utils = [p.get("utilization", 0) for p in ports if p.get("utilization") is not None]
        uptimes = [p.get("uptime_percentage", 0) for p in ports if p.get("uptime_percentage") is not None]

        rows.append({
            "date": day_result.get("date"),
            "uptime_pct": (sum(uptimes) / len(uptimes) * 100) if uptimes else 0,
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


@st.cache_data(ttl=300)
def query_incidents(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/incidents/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_robot_errors(installation_id, date_from_str, date_to_str):
    url_re = f"{BASE_URL}/installations/{installation_id}/robot-errors/"
    url_inc = f"{BASE_URL}/installations/{installation_id}/incidents/"
    params = {"after": date_from_str, "before": date_to_str}
    re_results = _fetch_all_pages(url_re, params)
    inc_results = _fetch_all_pages(url_inc, params)

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


# A manual (console) stop is treated as recovery from a tolerated error only when it
# follows a delayed-stop robot error within this window.
_DELAYED_STOP_WINDOW_S = 35 * 60
# Stop code that marks a system that was force-stopped by a robot error.
_ERROR_STOP_CODE = "XHANDLER_ROBOT_ERROR_FAILED"
_CONSOLE_STOP_CODE = "STOPPED_FROM_CONSOLE"


def _iter_event_log_system_mode(installation_id, params):
    """Stream the event-log endpoint, yielding only 'System mode' events.

    The event-log is very heavy (~90k events/site/week), most of it robot notify /
    port / recovery noise. We discard everything except System mode transitions per
    page so memory stays tiny (~a few hundred rows per month).
    """
    url = f"{BASE_URL}/installations/{installation_id}/event-log/"
    while url:
        resp = requests.get(url, headers=_headers(), params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        for day_result in data.get("results", []):
            for e in day_result.get("result", {}).get("event_log", []):
                if e.get("event", "").startswith("System mode"):
                    yield e
        url = data.get("next")
        params = None


def _parse_stop_code(event):
    parts = event.split("\t")
    if len(parts) >= 3:
        return re.sub(r"^\[\d+\]", "", parts[2])
    return _CONSOLE_STOP_CODE


@st.cache_data(ttl=300)
def query_recovery_times(installation_id, date_from_str, date_to_str):
    """Reconstruct 'time to recover' events from the event-log.

    Returns one row per recovery event with columns:
      date, category ('error_stop' | 'manual_delayed'), recovery_seconds

    - error_stop: system force-stopped by a robot error (stop code
      XHANDLER_ROBOT_ERROR_FAILED). Recovery = STOPPED -> next RUNNING.
    - manual_delayed: system tolerated an error (delayed_stop) and kept running,
      then an operator manually stopped it (STOPPED_FROM_CONSOLE) within the
      delayed-stop window to fix it. Recovery = STOPPED -> next RUNNING.
    """
    params = {"after": date_from_str, "before": date_to_str}

    # Sorted timeline of System mode events.
    evs = []
    for e in _iter_event_log_system_mode(installation_id, params):
        ts = e.get("local_timestamp")
        if ts:
            evs.append((pd.to_datetime(ts, errors="coerce"), e.get("event", "")))
    evs = [x for x in evs if pd.notna(x[0])]
    evs.sort(key=lambda x: x[0])

    # Delayed-stop robot error timestamps (for linking manual stops).
    re_results = _fetch_all_pages(
        f"{BASE_URL}/installations/{installation_id}/robot-errors/", dict(params))
    delayed_ts = []
    for day_result in re_results:
        for er in day_result.get("result", {}).get("robot_errors", []):
            if er.get("delayed_stop"):
                t = pd.to_datetime(er.get("local_installation_timestamp"), errors="coerce")
                if pd.notna(t):
                    delayed_ts.append(t)
    delayed_ts.sort()

    rows = []
    i, n = 0, len(evs)
    while i < n:
        ts, ev = evs[i]
        state = ev.split("\t")[1] if "\t" in ev else ev
        if "[40]STOPPING" in state or "[70]STOPPED" in state:
            j, end = i + 1, None
            while j < n:
                if "[20]RUNNING" in evs[j][1]:
                    end = evs[j][0]
                    break
                j += 1
            if end is None:
                break
            code = _parse_stop_code(ev)
            dur = int((end - ts).total_seconds())
            if code == _ERROR_STOP_CODE:
                rows.append({"date": ts.normalize(), "category": "error_stop",
                             "recovery_seconds": dur})
            elif code == _CONSOLE_STOP_CODE:
                linked = any(0 <= (ts - d).total_seconds() <= _DELAYED_STOP_WINDOW_S
                             for d in delayed_ts)
                if linked:
                    rows.append({"date": ts.normalize(), "category": "manual_delayed",
                                 "recovery_seconds": dur})
            i = j + 1
        else:
            i += 1

    if not rows:
        return pd.DataFrame(columns=["date", "category", "recovery_seconds"])
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
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
    results = _fetch_all_pages(url, params)

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


@st.cache_data(ttl=300)
def query_port_wait_time(installation_id, date_from_str, date_to_str):
    """Fetch port-bin-wait-time data and return a DataFrame matching the CSV format."""
    url = f"{BASE_URL}/installations/{installation_id}/port-bin-wait-time/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

    rows = []
    for day_result in results:
        port_data = day_result.get("result", {}).get("port_hour_wait_time", {})
        for port_id_str, records in port_data.items():
            for rec in records:
                if rec.get("subtype") != "BIN_PRESENTATIONS":
                    continue
                pick_type_raw = rec.get("pick_type", "")
                if pick_type_raw == "picks":
                    pick_type = "Pick"
                elif pick_type_raw == "goods_in":
                    pick_type = "Goods In"
                else:
                    pick_type = str(pick_type_raw)

                cat = rec.get("category")
                category = str(int(cat)) if cat is not None else ""

                rows.append({
                    "Timestamp": rec["hour"],
                    "Port ID": int(port_id_str),
                    "Pick type": pick_type,
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
