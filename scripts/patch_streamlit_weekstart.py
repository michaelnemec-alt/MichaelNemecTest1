"""Force Streamlit's date_input calendar to start the week on Monday.

Streamlit derives the datepicker's first day of week from the browser locale
(window.navigator.language) with no server-side option, so a US-English browser
shows a Sunday-first calendar. This rewrites the compiled frontend so the week
always starts on Monday regardless of browser. Runs at Docker build time; safe
to re-run (idempotent) and a no-op if a future Streamlit bundle changes.
"""

import os
import sys

import streamlit

# The minified expression that turns the locale's weekInfo.firstDay into
# date-fns weekStartsOn; forcing it to 1 = Monday.
_TARGET = "r.firstDay===7?0:r.firstDay"
_REPLACEMENT = "1"


def main():
    static_js = os.path.join(os.path.dirname(streamlit.__file__), "static", "static", "js")
    if not os.path.isdir(static_js):
        print(f"[weekstart] static js dir not found: {static_js}", file=sys.stderr)
        return 0
    patched = 0
    for name in os.listdir(static_js):
        if not name.endswith(".js"):
            continue
        path = os.path.join(static_js, name)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if _TARGET in content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content.replace(_TARGET, _REPLACEMENT))
            patched += 1
    if patched:
        print(f"[weekstart] patched {patched} file(s) to Monday-first calendar")
    else:
        print("[weekstart] target not found — calendar left at browser default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
