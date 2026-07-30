"""Raw live-event collector for CubeAnalytics.

Streams every WebSocket event (and backfills the REST live-events window on
start-up) and appends it verbatim to an append-only NDJSON store, so any chart
or metric can be recomputed later from the untouched source. See collect.py.
"""
