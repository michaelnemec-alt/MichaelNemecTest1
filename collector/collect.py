"""Collect raw CubeAnalytics live events into an append-only NDJSON store.

On start-up it backfills the REST live-events window (default 48h) for every
installation and event type, then keeps a WebSocket connection open and writes
every pushed event verbatim. The WebSocket access URL is valid for only ten
minutes, so the connection is refreshed proactively and re-established with
back-off on any drop. Run with:  python -m collector.collect
"""
import asyncio
import json
import logging
import os
import signal
import time

import requests
import websockets

from collector.raw_store import RawStore

# Liveness marker touched while the collector is connected and receiving. The
# container healthcheck (see docker-compose.yml) checks its freshness, because
# the collector runs the shared app image but has no HTTP server for the
# image's default Streamlit healthcheck to probe.
HEARTBEAT_FILENAME = ".heartbeat"

REST_BASE_URL = "https://api.cubeanalytics.autostoresystem.com/v1"
CONNECT_URL = "https://live.cubeanalytics.autostoresystem.com/connect"
WS_SUBPROTOCOL = "json.webpubsub.azure.v1"

# Refresh the connection before the 10-minute access token expires.
WS_REFRESH_SECONDS = 9 * 60
RECV_TIMEOUT_SECONDS = 120
BACKOFF_START_SECONDS = 2
BACKOFF_MAX_SECONDS = 60

# Event types the REST backfill iterates. CHARGER_STATE and STATUS are
# WebSocket-only — the REST live-events-stream rejects them with HTTP 400 — so
# they are intentionally excluded here (they still arrive live over the
# WebSocket). The WebSocket delivers whatever the installation emits regardless
# of this list.
BACKFILL_EVENT_TYPES = (
    "SYSTEM_MODE", "ROBOT_STATE", "ROBOT_ERROR", "BIN_AND_TASK",
    "PORT_ERROR", "PORT_RETRY", "DOOR_STATE", "PORT_STATE",
    "INCIDENT", "DELAYED_SYSTEM_STOP",
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("collector")


def _touch_heartbeat(root):
    """Record that the collector is alive by updating the heartbeat file mtime."""
    try:
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, HEARTBEAT_FILENAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    except OSError as exc:
        logger.warning("could not write heartbeat: %s", exc)


def _token():
    token = os.environ.get("CUBEANALYTICS_TOKEN")
    if not token:
        raise SystemExit("CUBEANALYTICS_TOKEN environment variable is required")
    return token


def _auth_header(token):
    return {"Authorization": f"Token {token}"}


def _installations(token):
    """Installation ids to collect: COLLECTOR_INSTALLATIONS if set, else all the
    token can see."""
    configured = os.environ.get("COLLECTOR_INSTALLATIONS", "").strip()
    if configured:
        return [i.strip() for i in configured.split(",") if i.strip()]
    resp = requests.get(
        f"{REST_BASE_URL}/installations/",
        headers={"API-Authorization": f"Token {token}"}, timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    return [i["id"] for i in results if isinstance(i, dict) and i.get("id")]


def _backfill(store, token, installations, hours):
    header = {"API-Authorization": f"Token {token}"}
    total = 0
    for inst in installations:
        for event_type in BACKFILL_EVENT_TYPES:
            url = f"{REST_BASE_URL}/installations/{inst}/live-events-stream/"
            params = {"lastHours": int(hours), "eventType": event_type}
            try:
                resp = requests.get(url, headers=header, params=params, timeout=90)
                resp.raise_for_status()
                events = resp.json()
            except (requests.RequestException, json.JSONDecodeError) as exc:
                logger.warning("backfill %s/%s failed: %s", inst, event_type, exc)
                continue
            if not isinstance(events, list):
                continue
            written = sum(
                1 for e in events
                if isinstance(e, dict) and e.get("event_type") == event_type
                and store.write(e)
            )
            total += written
            if written:
                logger.info("backfill %s %s: %d events", inst, event_type, written)
    logger.info("backfill complete: %d new events", total)


def _request_wss_url(token, installations):
    params = {"installations": ",".join(installations)} if installations else None
    resp = requests.get(
        CONNECT_URL, headers=_auth_header(token), params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.text.strip().strip('"')


async def _stream_once(store, token, installations, stop_event):
    wss_url = _request_wss_url(token, installations)
    async with websockets.connect(
        wss_url, subprotocols=[WS_SUBPROTOCOL], max_size=None,
        ping_interval=30, ping_timeout=30,
    ) as ws:
        logger.info("WebSocket connected")
        _touch_heartbeat(store.root)
        deadline = asyncio.get_event_loop().time() + WS_REFRESH_SECONDS
        while not stop_event.is_set():
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.info("refreshing WebSocket access token")
                return
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=min(RECV_TIMEOUT_SECONDS, remaining)
                )
            except asyncio.TimeoutError:
                _touch_heartbeat(store.root)  # idle but alive
                continue
            _touch_heartbeat(store.root)
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = obj.get("data", obj) if isinstance(obj, dict) else None
            if isinstance(event, dict) and event.get("event_type"):
                store.write(event)


async def _run(store, token, installations, stop_event):
    backoff = BACKOFF_START_SECONDS
    while not stop_event.is_set():
        try:
            await _stream_once(store, token, installations, stop_event)
            backoff = BACKOFF_START_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep the collector alive across any drop
            logger.warning("WebSocket error, reconnecting in %ds: %s", backoff, exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)


def main():
    token = _token()
    root = os.environ.get("COLLECTOR_DATA_ROOT", "/data")
    hours = int(os.environ.get("COLLECTOR_BACKFILL_HOURS", "48"))
    installations = _installations(token)
    logger.info("collecting %d installation(s) into %s", len(installations), root)

    store = RawStore(root)
    _backfill(store, token, installations, hours)

    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    try:
        loop.run_until_complete(_run(store, token, installations, stop_event))
    finally:
        store.close()
        loop.close()


if __name__ == "__main__":
    main()
