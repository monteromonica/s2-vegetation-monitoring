"""Command line entry point: `s2veg <command>`."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="s2veg", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("search", "list scenes that cover the AOI (Earth Search COGs)"),
                            ("cdse-search", "list the same AOI in the Copernicus Data Space STAC catalogue"),
                            ("run", "full pipeline: composites, clustering, validation"),
                            ("access-demo", "compare HTTPS / S3 / requester-pays access"),
                            ("cdse-s3", "read one JP2 window from CDSE eodata (needs CDSE S3 keys)")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", required=True)
        if name == "run":
            p.add_argument("--out", default="outputs")

    sub.add_parser("hub-token", help="test Sentinel Hub OAuth only (no data request, secrets not printed)")

    def add_cutout_args(p, default_out: str) -> None:
        p.add_argument("--config", required=True)
        p.add_argument("--start", required=True, help="YYYY-MM-DD")
        p.add_argument("--end", required=True, help="YYYY-MM-DD")
        p.add_argument("--out", default=default_out)

    hub = sub.add_parser("hub-fetch", help="download an L2A cut-out with Sentinel Hub Process API")
    add_cutout_args(hub, "outputs/hub_cutout.tif")

    openeo = sub.add_parser("openeo-ndvi", help="median SCL-masked NDVI via CDSE openEO (same OAuth client)")
    add_cutout_args(openeo, "outputs/openeo_ndvi.tif")

    args = parser.parse_args(argv)
    if args.command == "hub-token":
        from .hub import check_auth
        info = check_auth()
        print("OAuth OK. Token details (the token itself is never printed):")
        for k, v in info.items():
            print(f"  {k}: {v}")
        return 0
    cfg = load_config(args.config)

    if args.command == "search":
        from .stac import covering_items, deduplicate_acquisitions, search_items
        found = search_items(cfg)
        covering = covering_items(found, cfg.bbox)
        items = deduplicate_acquisitions(covering)
        print(f"{len(found)} matched, {len(covering)} cover the AOI, {len(items)} unique acquisitions\n")
        print(f"{'id':<30s} {'tile cloud %':>12s} {'nodata %':>9s} {'baseline':>9s}")
        for it in items:
            p = it.properties
            print(f"{it.id:<30s} {p.get('eo:cloud_cover', float('nan')):12.1f} "
                  f"{p.get('s2:nodata_pixel_percentage', float('nan')):9.1f} {p.get('s2:processing_baseline', '?'):>9s}")
    elif args.command == "cdse-search":
        from .cdse import print_search
        print_search(cfg)
    elif args.command == "run":
        from .pipeline import run
        run(cfg, args.out)
    elif args.command == "access-demo":
        from .access import run_demo
        run_demo(cfg)
    elif args.command == "cdse-s3":
        from .cdse_s3 import run_demo as cdse_s3_demo
        cdse_s3_demo(cfg)
    elif args.command == "hub-fetch":
        from .hub import fetch
        fetch(cfg.bbox, args.start, args.end, args.out)
    elif args.command == "openeo-ndvi":
        from .openeo_ndvi import fetch as openeo_fetch
        openeo_fetch(cfg.bbox, args.start, args.end, args.out, scl_valid=cfg.scl_valid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
