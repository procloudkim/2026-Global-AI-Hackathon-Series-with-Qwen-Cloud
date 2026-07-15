from __future__ import annotations

import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_release_json_and_workflow_files_parse() -> None:
    for relative in (
        "deploy/cloud-approval.schema.json",
        "deploy/infrastructure-readiness.schema.json",
        "deploy/release-finalization.schema.json",
        "deploy/release-gate.schema.json",
        "deploy/release-manifest.schema.json",
    ):
        value = json.loads(read(relative))
        assert value["type"] == "object"
        assert value["additionalProperties"] is False

    for relative in (
        ".github/workflows/ci.yml",
        ".github/workflows/deploy-alibaba.yml",
        "docker-compose.yml",
    ):
        assert isinstance(yaml.safe_load(read(relative)), dict)


def test_workflows_pin_actions_and_keep_live_secrets_out_of_ci() -> None:
    ci = read(".github/workflows/ci.yml")
    deploy = read(".github/workflows/deploy-alibaba.yml")
    uses = re.findall(r"uses:\s*([^\s]+)", ci + "\n" + deploy)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", item) for item in uses)
    assert "secrets.DASHSCOPE_API_KEY" not in ci
    assert "deploy/scan-secrets.py" in ci
    assert re.search(r"(?m)^  push:\s*$", ci)


def test_release_workflow_is_fail_closed_and_ordered() -> None:
    workflow = read(".github/workflows/deploy-alibaba.yml")
    structural = workflow.index("-Mode ci -CandidateSha")
    cloud_approval = workflow.index("deploy/verify-cloud-approval.py")
    live = workflow.index("proof/live_qwen_contract.py attest-docker")
    host = workflow.index("deploy/inspect-infrastructure.py")
    gate = workflow.index("deploy/verify-release-gate.py")
    deploy_preflight = workflow.index("-Mode deploy")
    deployment = workflow.index("sudo bash /tmp/librarian-deploy-")
    assert structural < cloud_approval < host < live < gate < deploy_preflight < deployment
    assert "Inspect approved host before live Qwen spend" in workflow
    assert "proof/Dockerfile.live" in workflow
    assert "evaluator-only/gold.jsonl,dst=" not in workflow
    assert "--max-calls 18" in workflow
    assert "--max-total-tokens 25000" in workflow
    assert "environment: production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "ALIBABA_SSH_PRIVATE_KEY" in workflow
    assert "AccessKey" not in workflow
    assert "always() && steps.deploy.outcome == 'success' && steps.finalization.outcome != 'success'" in workflow
    assert "rollback-manifest.json" in workflow
    assert "librarian-rollback-" in workflow
    assert "LIBRARIAN_TARGET_SSH_HOST_KEY" in workflow
    assert "--print-target-sha256-from-env" in workflow
    assert workflow.count("--expected-target-sha256") == 3
    assert "steps.deployment_target.outputs.sha256" in workflow
    assert "Release-gate receipt lacks its approved deployment-target digest" in read("scripts/preflight.ps1")


def test_live_image_excludes_private_promotion_evaluator() -> None:
    dockerignore = {line.strip() for line in read(".dockerignore").splitlines()}
    assert "eval/private" in dockerignore
    assert "eval/private_promotion.py" in dockerignore
    assert "eval/private-paired-results.schema.json" in dockerignore


def test_live_evaluator_uses_current_diagnostic_gate_schema() -> None:
    runner = read("proof/live_qwen_contract.py")
    assert 'policy["repository_diagnostic_gates"]' in runner
    assert 'policy["promotion_gates"]' not in runner


def test_cloud_target_digest_is_bound_through_host_and_release_receipts() -> None:
    cloud_schema = json.loads(read("deploy/cloud-approval.schema.json"))
    infrastructure_schema = json.loads(read("deploy/infrastructure-readiness.schema.json"))
    release_schema = json.loads(read("deploy/release-gate.schema.json"))
    assert "deployment_target_sha256" in cloud_schema["required"]
    assert "deployment_target_sha256" in infrastructure_schema["required"]
    assert "deployment_target_sha256" in release_schema["required"]
    inspector = read("deploy/inspect-infrastructure.py")
    release_gate = read("deploy/verify-release-gate.py")
    assert '"deployment_target_sha256": args.expected_target_sha256' in inspector
    assert "host readiness belongs to another deployment target" in release_gate
    assert '"deployment_target_sha256": args.expected_target_sha256' in release_gate


def test_deploy_and_rollback_preserve_memory_and_require_gate_receipt() -> None:
    deploy = read("deploy/deploy.sh")
    rollback = read("deploy/rollback.sh")
    combined = deploy + rollback
    assert "origin/main" not in combined
    assert "reset --hard" not in combined
    assert 'RELEASE_PATH="${RELEASE_ROOT}/${CANDIDATE_SHA}"' in deploy
    assert "/var/lib/librarian/memory" in combined
    assert "--release-gate-receipt" in deploy
    assert "git get-tar-commit-id" in deploy
    assert 'gzip -dc -- "${ARCHIVE}" >"${ARCHIVE_TAR}"' in deploy
    assert 'git get-tar-commit-id <"${ARCHIVE_TAR}"' in deploy
    assert 'rm -f -- "${ARCHIVE_TAR}"' in deploy
    assert "gzip -dc -- \"${ARCHIVE}\" | git get-tar-commit-id" not in deploy
    assert "atomic_link" in deploy and "atomic_link" in rollback
    assert "release_is_finalized" in deploy and "release_is_finalized" in rollback
    assert "librarian-release-finalization/v1" in deploy
    assert "librarian-release-finalization/v1" in rollback
    assert "librarian-restart-persistence-proof/v1" in deploy
    assert "librarian-restart-persistence-proof/v1" in rollback
    assert 'receipt.get("status") == "RELEASE_VERIFIED"' in deploy
    assert 'receipt.get("status") == "RELEASE_VERIFIED"' in rollback
    assert 'rm -rf -- "${MEMORY_ROOT}"' not in combined
    assert "MEMORY_BEFORE" in combined and "MEMORY_AFTER" in combined


def test_deploy_builds_non_relocatable_venv_at_final_release_path() -> None:
    deploy = read("deploy/deploy.sh")

    move_to_release = deploy.index('mv "${STAGING_PATH}" "${RELEASE_PATH}"')
    sync_at_release = deploy.index(
        '/usr/local/bin/uv --directory "${RELEASE_PATH}" sync --frozen --no-dev'
    )
    assert move_to_release < sync_at_release
    assert '/usr/local/bin/uv --directory "${STAGING_PATH}"' not in deploy
    assert '"${RELEASE_PATH}/.venv/bin/python" -c' in deploy


def test_systemd_runtime_can_import_the_src_layout_on_new_and_existing_hosts() -> None:
    setup = read("deploy/setup.sh")
    deploy = read("deploy/deploy.sh")
    environment = "Environment=PYTHONPATH=${CURRENT_LINK}/src"

    assert environment in setup
    assert environment in deploy
    drop_in = deploy.index("librarian-runtime.conf")
    daemon_reload = deploy.index("systemctl daemon-reload", drop_in)
    service_start = deploy.index(
        'systemctl start "${SERVICE_NAME}.service"', daemon_reload
    )
    assert drop_in < daemon_reload < service_start


def test_failed_candidate_is_not_left_as_current_or_used_as_rollback() -> None:
    deploy = read("deploy/deploy.sh")

    assert "PREVIOUS_HEALTHY=0" in deploy
    assert "PREVIOUS_FINALIZED=0" in deploy
    assert 'if health_matches_sha "${PREVIOUS_SHA}"; then' in deploy
    assert '&& "${PREVIOUS_HEALTHY}" -eq 1' in deploy
    assert '&& "${PREVIOUS_FINALIZED}" -eq 1' in deploy
    assert 'unlink_current_if_target "${RELEASE_PATH}"' in deploy
    assert 'RELEASE_CREATED=1' in deploy
    assert 'rm -rf -- "${RELEASE_PATH}"' in deploy


def test_setup_runs_service_user_uv_outside_root_home() -> None:
    setup = read("deploy/setup.sh")
    assert "runuser -u \"${SERVICE_USER}\"" in setup
    assert "sh \"${STATE_ROOT}\"" in setup
    assert "cd \"$1\"" in setup
    assert 'UV_PYTHON_INSTALL_DIR="$1/python"' in setup


def test_restart_proof_has_budget_trace_and_failure_quarantine() -> None:
    proof = read("deploy/verify-restart-persistence.sh")
    assert "maximum_provider_attempts_with_retry_zero" in proof
    assert '"maximum_total_tokens": 25000' in proof
    assert '"api_operations": request_delta' in proof
    assert "FAIL_QUARANTINED" in proof
    assert 'systemctl stop "${SERVICE_NAME}.service"' in proof
    assert "trap 'on_error ${LINENO} 130' INT" in proof
    assert "trap 'on_error ${LINENO} 143' TERM" in proof
    assert "trap 'on_error ${LINENO} 129' HUP" in proof
    assert proof.index("PROOF_STARTED=1") < proof.index("get_json /health")
    assert '"memory_deleted_or_rewound": False' in proof
    assert "superseded_claim_ids_filtered" in proof
    assert "active_claim_ids_loaded" in proof


def test_restart_proof_uses_a_stable_cross_page_claim_key() -> None:
    proof = read("deploy/verify-restart-persistence.sh")

    assert "In release-proof, {namespace}'s production-quota is" in proof
    assert "This record explicitly replaces" in proof
    assert "{os.environ['SOURCE_A_VALUE']}" in proof
    assert 'quota_key = f"release-proof::{namespace}::production-quota"' in proof
    assert 'marker_key = f"release-proof::{namespace}::retention-marker"' in proof
    assert 'quantity(c["value"]) == "100"' in proof
    assert 'quantity(c["value"]) == "1000"' in proof
    assert "It does not change" not in proof


def test_runtime_is_non_root_and_proxy_hides_paid_health() -> None:
    dockerfile = read("Dockerfile")
    compose = yaml.safe_load(read("docker-compose.yml"))
    caddy = read("deploy/Caddyfile")
    assert "USER 10001:10001" in dockerfile
    service = compose["services"]["librarian"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["environment"]["LIBRARIAN_MEMORY_ROOT"] == "/app/memory"
    assert "@qwen_health path /health/qwen" in caddy
    assert "respond @qwen_health 404" in caddy
    assert "basic_auth" in caddy
    assert "max_size 64KB" in caddy


def test_deploy_python_helpers_compile_without_execution() -> None:
    for relative in (
        "deploy/verify-cloud-approval.py",
        "deploy/inspect-infrastructure.py",
        "deploy/finalize-release.py",
        "deploy/scan-secrets.py",
        "deploy/verify-release-gate.py",
    ):
        compile(read(relative), relative, "exec")


def test_host_python_helpers_support_ubuntu_2204_system_python() -> None:
    for relative in (
        "deploy/inspect-infrastructure.py",
        "deploy/finalize-release.py",
    ):
        source = read(relative)
        assert "from datetime import UTC" not in source
        assert "timezone.utc" in source
