"""CubeAnalytics API helpers."""

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
def query_port_wait_time_daily(installation_id, date_from_str, date_to_str):
    url = f"{BASE_URL}/installations/{installation_id}/port-bin-wait-time/"
    params = {"after": date_from_str, "before": date_to_str}
    results = _fetch_all_pages(url, params)

    rows = []
    for day_result in results:
        date_str = day_result.get("date")
        port_data = day_result.get("result", {}).get("port_hour_wait_time", {})
        for port_id_str, records in port_data.items():
            for rec in records:
                if rec.get("subtype") != "BIN_PRESENTATIONS":
                    continue
                cat = rec.get("category")
                rows.append({
                    "date": date_str,
                    "port_id": int(port_id_str),
                    "pick_type": rec.get("pick_type", ""),
                    "category": str(int(cat)) if cat is not None else "",
                    "count": rec.get("count", 0),
                    "average_wait_bin": rec.get("average_wait_bin", 0),
                    "average_wait_user": rec.get("average_wait_user", 0),
                    "average_waste_time": rec.get("average_waste_time", 0),
                })

    if not rows:
        return pd.DataFrame()
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
