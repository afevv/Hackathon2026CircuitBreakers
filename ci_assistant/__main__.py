"""`python -m ci_assistant` runs the CLI view."""
import argparse

from ci_assistant.config import Settings
from ci_assistant.container import build_container
from ci_assistant.views.cli_view import run_cli


def main() -> None:
    parser = argparse.ArgumentParser(description="Competitor Intelligence Assistant")
    parser.add_argument("--target", help="Danfoss product id to compare (default: first one)")
    args = parser.parse_args()
    run_cli(build_container(Settings.from_env()), target_id=args.target)


if __name__ == "__main__":
    main()
