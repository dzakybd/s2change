"""Command line entry point: ``s2cd run`` (and ``s2cd --help``)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Sentinel-2 change detection for open-pit mine monitoring.")


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )


@app.command()
def run(
    config: Annotated[Path, typer.Option(help="YAML config (paths and thresholds).")] = Path("configs/default.yaml"),
    postgis: Annotated[bool, typer.Option(help="Also write to PostGIS using $DATABASE_URL.")] = False,
    figures: Annotated[bool, typer.Option(help="Render the static figures and the web overlays.")] = True,
) -> None:
    """Run the full pipeline: stack -> detect -> vectorize -> store -> visualize."""
    from . import pipeline, viz

    cfg = pipeline.Config.from_yaml(config) if config.exists() else pipeline.Config()
    if postgis:
        cfg.database_url = os.environ.get("DATABASE_URL")
        if not cfg.database_url:
            typer.echo("--postgis needs DATABASE_URL, e.g. postgresql+psycopg://s2cd:s2cd@localhost:5432/s2cd", err=True)
            raise typer.Exit(2)
    products = pipeline.run(cfg)
    if figures:
        paths = viz.figures(products, cfg.figures_dir)
        web = Path("docs/web")
        viz.export_web(products, web)
        logging.getLogger("s2cd").info("Wrote %d figures", len(paths))
    typer.echo(json.dumps(products.summary["changed_area_ha"], indent=2))


@app.command()
def provenance(
    config: Annotated[Path, typer.Option(help="YAML config (paths); the same file as for `run`.")] = Path("configs/default.yaml"),
    lookback_days: Annotated[
        int, typer.Option(help="Search STAC items this many days before each nominal date (inputs are mosaics).")
    ] = 75,
    refresh: Annotated[bool, typer.Option(help="Ignore cached windows under data/cache/stac.")] = False,
) -> None:
    """Optional: find the source Sentinel-2 items behind each provided date and check the B02/B03/B04 match pixel by pixel.

    Needs `--extra stac` and network; reads only B02/B03/B04 from Planetary Computer.
    """
    from . import pipeline
    from .provenance import ProvenanceRules
    from .provenance import provenance as run_provenance

    cfg = pipeline.Config.from_yaml(config) if config.exists() else pipeline.Config()
    try:
        summary = run_provenance(
            cfg.before, cfg.after, cfg.aoi, cfg.out_dir, refresh=refresh,
            rules=ProvenanceRules(lookback_days=lookback_days),
        )  # fmt: skip
    except (ImportError, FileNotFoundError) as e:
        typer.echo(f"{e}\n(`s2cd run` must have been run first; provenance needs `uv run --extra stac`.)", err=True)
        raise typer.Exit(2) from e
    typer.echo(
        json.dumps(
            {
                "sources": {d: [s["label"] for s in v["sources"]] for d, v in summary["stac"].items()},
                "exact_match_fraction": {
                    d: {b: c["exact_match_fraction"] for b, c in v.items()}
                    for d, v in summary["provided_vs_planetary_computer_mosaic"].items()
                },
                "zone_source_agreement": summary["zone_source_agreement"],
            },
            indent=2,
        )
    )
