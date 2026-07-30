"""Append-only NDJSON store for raw CubeAnalytics events.

Layout on disk:  <root>/<installation_id>/<EVENT_TYPE>/<YYYY-MM-DD>.ndjson
One event = one JSON line, stored exactly as received (nothing dropped or
recomputed). Duplicates are rejected by the event ``uuid`` so the WebSocket
stream and the REST backfill can overlap freely.
"""
import json
import os
import threading


class RawStore:
    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()
        self._handles = {}
        self._seen = {}

    def _paths(self, installation_id, event_type, date_str):
        directory = os.path.join(self.root, installation_id, event_type)
        return directory, os.path.join(directory, f"{date_str}.ndjson")

    def _seen_uuids(self, path):
        """uuid set for a day file, seeded once from any events already on disk
        so dedup survives restarts."""
        if path in self._seen:
            return self._seen[path]
        seen = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(json.loads(line).get("uuid"))
                    except json.JSONDecodeError:
                        continue
        self._seen[path] = seen
        return seen

    def _handle(self, directory, path):
        fh = self._handles.get(path)
        if fh is None:
            os.makedirs(directory, exist_ok=True)
            fh = open(path, "a", encoding="utf-8")
            self._handles[path] = fh
        return fh

    def write(self, event):
        """Append one raw event. Returns True if written, False if a duplicate
        or missing the fields needed to place it."""
        if not isinstance(event, dict):
            return False
        installation_id = event.get("installation_id")
        event_type = event.get("event_type")
        date_str = event.get("date")
        uuid = event.get("uuid")
        if not (installation_id and event_type and date_str):
            return False
        with self._lock:
            directory, path = self._paths(
                str(installation_id), str(event_type), str(date_str)
            )
            seen = self._seen_uuids(path)
            if uuid is not None:
                if uuid in seen:
                    return False
                seen.add(uuid)
            fh = self._handle(directory, path)
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            fh.flush()
            return True

    def close(self):
        with self._lock:
            for fh in self._handles.values():
                try:
                    fh.close()
                except OSError:
                    pass
            self._handles.clear()
