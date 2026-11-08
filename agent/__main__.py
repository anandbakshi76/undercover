"""Run the UnderCover Agent from the command line against a running backend."""

import argparse

from agent import claim, main


def cli(argv: list[str] | None = None) -> str:
    """Work one claim, and return its line."""
    parser = argparse.ArgumentParser(description="Run the UnderCover Agent.")
    parser.add_argument("--claim", required=True, help="the claim to work")
    args = parser.parse_args(argv)
    run = main.call("POST", "/api/runs", {"limit": 1})
    line = claim.work_claim(run["run_id"], args.claim)
    main.call(
        "POST",
        f"/api/runs/{run['run_id']}/finish",
        {"status": "finished", "summary": line},
    )
    return line


if __name__ == "__main__":
    print(cli())
