#!/usr/bin/env python3
"""Append a candidate-bound final receipt after restart persistence passes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ROOT = Path("/var/lib/librarian/deployments")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--deployment-manifest", required=True)
    parser.add_argument("--restart-proof", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise SystemExit("finalize-release.py must run as root")
    if not SHA_RE.fullmatch(args.candidate_sha):
        raise SystemExit("candidate SHA must be full lowercase 40-hex")

    deployment_path = Path(args.deployment_manifest).resolve(strict=True)
    restart_path = Path(args.restart_proof).resolve(strict=True)
    output = Path(args.output).resolve(strict=False)
    for path in (deployment_path, restart_path, output):
        try:
            path.relative_to(ROOT)
        except ValueError as exc:
            raise SystemExit(f"receipt path is outside {ROOT}") from exc
    if output.exists() or output.is_symlink():
        raise SystemExit("finalization output already exists")

    deployment = load(deployment_path)
    restart = load(restart_path)
    if deployment.get("schema_version") != "librarian-release-event/v1":
        raise SystemExit("unsupported deployment manifest schema")
    if deployment.get("status") != "DEPLOYED":
        raise SystemExit("deployment manifest is not DEPLOYED")
    if restart.get("schema_version") != "librarian-restart-persistence-proof/v1":
        raise SystemExit("unsupported restart proof schema")
    if restart.get("status") != "PASS":
        raise SystemExit("restart persistence proof did not pass")
    if deployment.get("candidate_sha") != args.candidate_sha or restart.get("candidate_sha") != args.candidate_sha:
        raise SystemExit("release receipts are not bound to the candidate SHA")

    payload = {
        "schema_version": "librarian-release-finalization/v1",
        "status": "RELEASE_VERIFIED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha": args.candidate_sha,
        "deployment_manifest": {
            "path": str(deployment_path),
            "sha256": digest(deployment_path),
            "status": deployment["status"],
        },
        "restart_persistence_proof": {
            "path": str(restart_path),
            "sha256": digest(restart_path),
            "status": restart["status"],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(output)
    output.chmod(0o640)
    print("release_finalization_status=RELEASE_VERIFIED")
    print(f"release_finalization_receipt={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
