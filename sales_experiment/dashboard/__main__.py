from __future__ import annotations

import argparse
from pathlib import Path

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the local bargaining experiment dashboard")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--config", type=Path, help="Configuration used to prefill the experiment form")
    args = parser.parse_args()
    try:
        app = create_app(results_dir=args.results_dir, env_file=args.env_file, initial_config=args.config)
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Dashboard: http://127.0.0.1:{args.port}", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
