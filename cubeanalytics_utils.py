"""CubeAnalytics API helpers for the UNIFY Pivot Ready page."""

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
def query_port_wait_time(installation_id, date_from_str, date_to_str):
    """Fetch port-bin-wait-time data and return a DataFrame matching the CSV format.

    Columns: Timestamp, Port ID, Pick type, Count,
             Average bin wait time, Average operator handling time, Category
    """
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
