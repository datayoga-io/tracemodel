"""`python -m studio [repo]` or `tracemodel-studio [repo]`: serve the grid on a repo, or on the sample model."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app, open_studio, sample_studio


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracemodel-studio", description="A grid over a tracemodel repo, or the sample model.")
    parser.add_argument("repo", nargs="?", help="a repo directory (made if it is missing); the sample model when left out")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--author", default="you", help="who commits the changes made in the grid")
    args = parser.parse_args()
    uvicorn.run(create_app(open_studio(args.repo, args.author) if args.repo else sample_studio(args.author)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
