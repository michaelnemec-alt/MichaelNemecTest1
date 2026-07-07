import streamlit as st
import pandas as pd
from datetime import date, timedelta


def is_snowflake_configured():
    try:
        return "snowflake" in st.secrets
    except Exception:
        return False


@st.cache_resource
def _get_connection():
    import snowflake.connector
    cfg = st.secrets["snowflake"]
    return snowflake.connector.connect(
        account=cfg["account"],
        user=cfg["user"],
        password=cfg["password"],
        warehouse=cfg.get("warehouse", ""),
        database=cfg.get("database", "DWH_PROD_TRANSFORM"),
        schema=cfg.get("schema", "TR_LEVEL_0_ARCOS"),
        role=cfg.get("role", ""),
    )


@st.cache_data(ttl=600)
def get_available_warehouses():
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT "auto_store_id"
        FROM "DWH_PROD_TRANSFORM"."TR_LEVEL_0_ARCOS"."pick_task"
        WHERE "state" = 'FINISHED'
          AND "finished_at" >= DATEADD(day, -30, CURRENT_DATE())
        ORDER BY "auto_store_id"
    """)
    rows = cur.fetchall()
    prefixes = sorted(set(
        ".".join(row[0].split(".")[:2]) for row in rows
    ))
    return prefixes


@st.cache_data(ttl=300)
def query_picking_data(warehouse_prefix, date_from_str, date_to_str):
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            "auto_store_id"        AS "AutoStore",
            "customer_order"       AS "Order ID",
            "id"                   AS "Pick Task ID",
            "type"                 AS "Type",
            "prioritization_time"  AS "Prioritization Time",
            "state"                AS "State",
            "started_at"           AS "Started Picking At",
            "finished_at"          AS "Finished Picking At",
            "operator"             AS "Operator"
        FROM "DWH_PROD_TRANSFORM"."TR_LEVEL_0_ARCOS"."pick_task"
        WHERE "auto_store_id" LIKE %s
          AND "state" = 'FINISHED'
          AND "type" IN ('STANDARD', 'EXPRESS')
          AND "finished_at" >= DATEADD(day, -1, %s::DATE)
          AND "finished_at" <  DATEADD(day,  1, %s::DATE)
        ORDER BY "finished_at"
        """,
        (f"{warehouse_prefix}.%", date_from_str, date_to_str),
    )
    df = cur.fetch_pandas_all()
    return df
