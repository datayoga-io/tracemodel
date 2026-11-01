"""`python -m studio` or `tracemodel-studio`: serve the grid on the sample model."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app, sample_studio


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracemodel-studio", description="A grid over the sample tracemodel model.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--author", default="you", help="who commits the changes made in the grid")
    args = parser.parse_args()
    uvicorn.run(create_app(sample_studio(args.author)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
