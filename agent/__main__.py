"""Run the UnderCover Agent from the command line against a running backend."""

import argparse

from dotenv import load_dotenv

from agent import claim, main, queue_agent


def cli(argv: list[str] | None = None) -> str:
    """Run the queue capped at a number of claims, or work one named claim."""
    parser = argparse.ArgumentParser(description="Run the UnderCover Agent.")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--limit", type=int, help="run the queue on this many claims")
    choice.add_argument("--claim", help="work this one claim")
    args = parser.parse_args(argv)
    run = main.call("POST", "/api/runs", {"limit": args.limit or 1})
    if args.limit:
        return queue_agent.run_queue(run["run_id"])
    line = claim.work_claim(run["run_id"], args.claim)
    main.call(
        "POST",
        f"/api/runs/{run['run_id']}/finish",
        {"status": "finished", "summary": line},
    )
    return line


if __name__ == "__main__":
    load_dotenv(override=True)
    print(cli())
