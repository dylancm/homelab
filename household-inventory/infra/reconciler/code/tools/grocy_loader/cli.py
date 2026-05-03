"""Typer CLI entry point for the loader."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from .loader import run

app = typer.Typer(
    add_completion=False,
    help="Bulk-load Grocy products from a YAML file. Idempotent by name.",
)


@app.command()
def main(
    input_path: Path = typer.Option(
        Path("products.yaml"),
        "--input",
        "-i",
        exists=False,
        dir_okay=False,
        readable=True,
        help="Path to the YAML file describing products.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve names and print what would be created. Make zero writes.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable INFO-level logging."
    ),
) -> None:
    """Load products from `input_path` into Grocy.

    Reads `GROCY_BASE_URL` and `GROCY_API_KEY` from the environment, with a
    `.env` file in the script directory loaded automatically if present.
    """
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load .env that lives next to this package, then fall back to CWD.
    pkg_env = Path(__file__).parent / ".env"
    if pkg_env.exists():
        load_dotenv(pkg_env)
    load_dotenv()  # also picks up CWD/.env if present, doesn't override

    base_url = os.environ.get("GROCY_BASE_URL", "").strip()
    api_key = os.environ.get("GROCY_API_KEY", "").strip()
    missing = [
        name
        for name, value in [
            ("GROCY_BASE_URL", base_url),
            ("GROCY_API_KEY", api_key),
        ]
        if not value
    ]
    if missing:
        print(
            f"ERROR: missing required env var(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        raise typer.Exit(code=2)

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        raise typer.Exit(code=2)

    code = run(
        yaml_path=input_path,
        base_url=base_url,
        api_key=api_key,
        dry_run=dry_run,
    )
    raise typer.Exit(code=code)
