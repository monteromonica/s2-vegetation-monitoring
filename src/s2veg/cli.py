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

    for name, help_text in (("search", "list scenes that cover the AOI"),
                            ("run", "full pipeline: composites, clustering, validation"),
                            ("access-demo", "compare HTTPS / S3 / requester-pays access")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", required=True)
        if name == "run":
            p.add_argument("--out", default="outputs")

    sub.add_parser("hub-token", help="test Sentinel Hub OAuth only (no data request, secrets not printed)")

    hub = sub.add_parser("hub-fetch", help="download an L2A cut-out with Sentinel Hub Process API")
    hub.add_argument("--config", required=True)
    hub.add_argument("--start", required=True, help="YYYY-MM-DD")
    hub.add_argument("--end", required=True, help="YYYY-MM-DD")
    hub.add_argument("--out", default="outputs/hub_cutout.tif")

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
    elif args.command == "run":
        from .pipeline import run
        run(cfg, args.out)
    elif args.command == "access-demo":
        from .access import run_demo
        run_demo(cfg)
    elif args.command == "hub-fetch":
        from .hub import fetch
        fetch(cfg.bbox, args.start, args.end, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
