"""Work one claim from the command line against a running backend."""

import argparse

from dotenv import load_dotenv

from agent import claim, main


def cli(argv: list[str] | None = None) -> str:
    """Work one named claim: start it if it is waiting, or carry it on."""
    parser = argparse.ArgumentParser(description="Work one claim.")
    parser.add_argument("claim_id", help="the claim to work, such as CLM-1001")
    claim_id = parser.parse_args(argv).claim_id
    found = main.call("GET", f"/api/claims/{claim_id}")
    if found["status"] == "in_progress":
        return claim.carry_on(claim_id)
    if found["status"] != "waiting":
        return claim.report(found, "")
    run = main.call("POST", f"/api/claims/{claim_id}/start-run")
    line = claim.work_claim(claim_id, run["run_id"])
    main.call(
        "POST",
        f"/api/runs/{run['run_id']}/finish",
        {"status": "finished"},
    )
    return line


if __name__ == "__main__":
    load_dotenv(override=True)
    print(cli())
