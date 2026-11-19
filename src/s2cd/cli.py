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
    figures: Annotated[bool, typer.Option(help="Render static figures and the interactive map.")] = True,
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
        paths.append(viz.folium_map(products, web, Path("docs/change_map.html")))
        logging.getLogger("s2cd").info("Wrote %d figures/maps", len(paths))
    typer.echo(json.dumps(products.summary["changed_area_ha"], indent=2))


@app.command()
def enrich(
    config: Annotated[Path, typer.Option(help="YAML config (paths); the same file as for `run`.")] = Path("configs/default.yaml"),
    timeline: Annotated[bool, typer.Option(help="Also build the June-October AOI timeline (many small reads).")] = True,
    lookback_days: Annotated[
        int, typer.Option(help="Search STAC items this many days before each nominal date (inputs are mosaics).")
    ] = 75,
    refresh: Annotated[bool, typer.Option(help="Ignore cached windows under data/cache/stac.")] = False,
) -> None:
    """Optional: validate/interpret the changes with extra bands from Planetary Computer (needs `--extra stac` and network)."""
    from . import pipeline, stac_context

    cfg = pipeline.Config.from_yaml(config) if config.exists() else pipeline.Config()
    try:
        summary = stac_context.enrich(
            cfg.before, cfg.after, cfg.aoi, cfg.out_dir, cfg.sqlite_path, cfg.figures_dir,
            lookback_days=lookback_days, with_timeline=timeline, refresh=refresh,
        )  # fmt: skip
    except (ImportError, FileNotFoundError) as e:
        typer.echo(f"{e}\n(`s2cd run` must have been run first; enrichment needs `uv run --extra stac`.)", err=True)
        raise typer.Exit(2) from e
    typer.echo(
        json.dumps(
            {"area_by_likely_cause": summary["area_by_likely_cause"], "aot": summary["aot_by_zone"]["overall_mean"]}, indent=2
        )
    )
