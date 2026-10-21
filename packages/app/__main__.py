"""Run the app:  python -m packages.app  [--port 8765] [--no-browser] [--demo]

Binds to 127.0.0.1 only. See server.py for why that, and the two request
checks, are what keep a web page you visit from driving the agent.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="packages.app", description="Run the Tripwire booking assistant locally.")
    parser.add_argument("--port", type=int, default=None, help="port on 127.0.0.1 (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument("--demo", action="store_true", help="force demo mode for this run")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print('The app needs its extra dependencies:  pip install -e ".[app]"')
        return 1

    if args.port:
        os.environ["BSYNC_PORT"] = str(args.port)
    if args.demo:
        os.environ["BSYNC_MODE"] = "demo"

    from packages.app.config import load_settings
    from packages.app.server import create_app

    try:
        settings = load_settings()
    except ValueError as exc:
        print("Configuration error: %s" % exc)
        return 2

    app = create_app(settings)
    url = settings.base_url + "/"
    print("Tripwire running at %s  (mode: %s)  -- Ctrl+C to stop" % (url, app.state.runtime.settings.mode))
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
