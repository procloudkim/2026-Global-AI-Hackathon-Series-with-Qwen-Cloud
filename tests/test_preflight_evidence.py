from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
ANSI_CONTROL_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _fixture_repo(tmp_path: Path, first_artifact_id: str | None = None) -> Path:
    repo = tmp_path / "repo"
    submission = repo / "submission"
    submission.mkdir(parents=True)
    for name in (
        "hackathon-contract.json",
        "HACKATHON_CONTRACT.md",
        "evidence-manifest.json",
        "DEVPOST_TEMPLATE.md",
    ):
        shutil.copy2(ROOT / "submission" / name, submission / name)

    contract_path = submission / "hackathon-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    fixture_now = datetime.now(timezone.utc).isoformat()
    contract["snapshot"]["audit_window"]["start"] = fixture_now
    contract["snapshot"]["audit_window"]["end"] = fixture_now
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    contract_sha256 = hashlib.sha256(contract_path.read_bytes()).hexdigest()

    projection_path = submission / "HACKATHON_CONTRACT.md"
    projection = projection_path.read_text(encoding="utf-8")
    projection = re.sub(
        r"(?<=canonical-json-sha256: )[0-9a-f]{64}",
        contract_sha256,
        projection,
        count=1,
    )
    projection_path.write_text(projection, encoding="utf-8")

    manifest_path = submission / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract"]["sha256"] = contract_sha256
    manifest["local_release_chain_files"] = []
    for artifact in manifest["artifacts"]:
        relative = artifact.get("path")
        if not relative:
            continue
        source = ROOT / relative
        destination = repo / relative
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    if first_artifact_id is not None:
        artifacts = manifest["artifacts"]
        target = next(item for item in artifacts if item["id"] == first_artifact_id)
        manifest["artifacts"] = [target, *(item for item in artifacts if item is not target)]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return repo


def _artifact(repo: Path, artifact_id: str) -> tuple[dict[str, object], dict[str, object]]:
    path = repo / "submission" / "evidence-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    artifact = next(item for item in manifest["artifacts"] if item["id"] == artifact_id)
    return manifest, artifact


def _write_manifest(repo: Path, manifest: dict[str, object]) -> None:
    (repo / "submission" / "evidence-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _run(repo: Path, mode: str) -> subprocess.CompletedProcess[str]:
    if PWSH is None:
        pytest.skip("pwsh is required for PowerShell preflight contract tests")
    return subprocess.run(
        [
            PWSH,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(ROOT / "scripts" / "preflight.ps1"),
            "-Mode",
            mode,
            "-RepoRoot",
            str(repo),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    rendered = ANSI_CONTROL_SEQUENCE.sub("", result.stdout + result.stderr)
    rendered = re.sub(r"\s+\|\s+", " ", rendered)
    return " ".join(rendered.split())


def test_output_normalizes_powershell_terminal_formatting() -> None:
    result = subprocess.CompletedProcess(
        args=["pwsh"],
        returncode=1,
        stdout="",
        stderr=(
            "\x1b[31;1mrequires a nonblank\x1b[0m\n"
            "     | repository-contained path"
        ),
    )

    assert "requires a nonblank repository-contained path" in _output(result)


@pytest.mark.parametrize(
    "artifact_id",
    (
        "workbench_deployment_screenshot",
        "deployed_release_manifest",
        "restart_persistence_receipt",
    ),
)
def test_submit_rejects_verified_file_evidence_without_path(
    tmp_path: Path,
    artifact_id: str,
) -> None:
    repo = _fixture_repo(tmp_path, artifact_id)
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(status="verified", path=None, sha256="0" * 64)
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "requires a nonblank repository-contained path" in _output(result)


def test_submit_rejects_verified_file_outside_repository(tmp_path: Path) -> None:
    artifact_id = "workbench_deployment_screenshot"
    repo = _fixture_repo(tmp_path, artifact_id)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not a repository artifact")
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(
        status="verified",
        path=str(outside),
        sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
    )
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "path escapes the repository root" in _output(result)


def test_submit_rejects_verified_file_without_digest(tmp_path: Path) -> None:
    artifact_id = "workbench_deployment_screenshot"
    repo = _fixture_repo(tmp_path, artifact_id)
    evidence = repo / "submission" / "evidence" / "workbench.jpg"
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_bytes(b"candidate-bound workbench proof")
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(
        status="verified",
        path="submission/evidence/workbench.jpg",
        sha256=None,
    )
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "lacks a candidate-bound SHA-256" in _output(result)


def test_submit_rejects_verified_url_without_https(tmp_path: Path) -> None:
    artifact_id = "public_demo_url"
    repo = _fixture_repo(tmp_path, artifact_id)
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(status="verified", value="http://demo.invalid")
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "is missing an HTTPS URL" in _output(result)


@pytest.mark.parametrize(
    ("artifact_id", "status", "value"),
    (
        ("existing_project_update", "not_applicable", "TBD"),
        ("eligibility_confirmations", "verified", "<EVIDENCE_CONFIRMATION>"),
    ),
)
def test_submit_rejects_placeholder_conditional_or_human_value(
    tmp_path: Path,
    artifact_id: str,
    status: str,
    value: str,
) -> None:
    repo = _fixture_repo(tmp_path, artifact_id)
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(status=status, value=value)
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "requires a non-placeholder value" in _output(result)


def test_submit_rejects_not_applicable_for_mandatory_file_evidence(tmp_path: Path) -> None:
    artifact_id = "workbench_deployment_screenshot"
    repo = _fixture_repo(tmp_path, artifact_id)
    manifest, artifact = _artifact(repo, artifact_id)
    artifact.update(status="not_applicable", path=None, sha256=None)
    _write_manifest(repo, manifest)

    result = _run(repo, "submit")

    assert result.returncode != 0
    assert "Only conditional artifacts may be marked not_applicable" in _output(result)


def test_ci_still_allows_explicitly_pending_external_evidence(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Preflight Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "preflight@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)

    result = _run(repo, "ci")

    assert result.returncode == 0, _output(result)
    assert "PREFLIGHT_STATUS: PASS (ci)" in _output(result)
    assert "External evidence is explicitly pending" in _output(result)
