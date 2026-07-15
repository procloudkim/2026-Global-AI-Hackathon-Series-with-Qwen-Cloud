import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "infra" / "alibaba" / "mcp" / "service-contract.json"
HCL_PATH = ROOT / "infra" / "alibaba" / "mcp" / "diagnose-librarian-service.tf"


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_custom_oauth_service_exposes_only_bounded_tools() -> None:
    contract = load_contract()

    assert contract["edition"] == "custom"
    assert contract["authentication"] == {
        "mode": "oauth",
        "provider": "alibaba-cloud-official",
        "static_access_key_allowed": False,
    }
    assert set(contract["api_tools"]) == {
        "Sts-20150401-GetCallerIdentity",
        "Ecs-20140526-DescribeCloudAssistantStatus",
        "Ecs-20140526-DescribeInstances",
        "Ecs-20140526-DescribeInvocationResults",
    }
    assert all("Delete" not in tool for tool in contract["api_tools"])
    assert contract["network"]["private_link_allowed"] is False


def test_account_binding_fails_closed_before_terraform() -> None:
    contract = load_contract()
    hcl = HCL_PATH.read_text(encoding="utf-8")
    binding = contract["account_binding_gate"]

    assert contract["account_scope"] == "deployment_root_account_only"
    assert binding["required"] is True
    assert binding["discovery_tool"] == "Ecs-20140526-DescribeInstances"
    assert binding["max_receipt_age_seconds"] == 300
    assert binding["on_zero_multiple_or_mismatch"] == "STOP_BEFORE_TERRAFORM"
    assert binding["multi_account_enabled"] is False
    assert binding["terraform_inputs"] == [
        "region",
        "instance_id",
        "approved_instance_sha256",
    ]
    assert "exactly_one_instance" in binding["required_conditions"]
    assert "x_mcp_region_id_equals_region_id" in binding["required_conditions"]
    assert "caller_identity_type_is_known" in binding["required_conditions"]
    assert "caller_account_sha256_matches_endpoint_owner" in binding["required_conditions"]
    assert "instance_status_running" in binding["required_conditions"]
    assert "sha256_instance_id_matches_approved_target" in binding["required_conditions"]
    assert 'variable "approved_instance_sha256"' in hcl
    assert "sha256(var.instance_id) == var.approved_instance_sha256" in hcl


def test_mcp_region_routing_is_explicit_and_fail_closed() -> None:
    contract = load_contract()
    routing = contract["mcp_region_routing"]

    assert routing == {
        "required": True,
        "api_argument": "RegionId",
        "routing_argument": "x_mcp_region_id",
        "value": "ap-southeast-1",
        "must_match": True,
        "on_missing_or_mismatch": "STOP_BEFORE_TERRAFORM",
    }


def test_cost_gate_is_free_tier_only_and_blocks_overage_risk() -> None:
    contract = load_contract()
    cost = contract["cost_gate"]

    assert cost["required"] is True
    assert cost["mode"] == "FREE_TIER_ONLY"
    assert cost["max_unapproved_spend_usd"] == 0
    assert cost["paid_fallback_allowed"] is False
    assert "pay_by_traffic_overage_prevented_or_explicitly_approved" in cost[
        "required_conditions"
    ]
    assert cost["on_unverified_or_overage_risk"] == "STOP_BEFORE_TERRAFORM"


def test_terraform_tool_can_create_only_command_and_invocation() -> None:
    contract = load_contract()
    hcl = HCL_PATH.read_text(encoding="utf-8")
    resource_types = re.findall(r'^resource\s+"([^"]+)"', hcl, flags=re.MULTILINE)

    tool = contract["terraform_tools"][0]
    assert tool["destroy_policy"] == "ALWAYS"
    assert tool["async"] is True
    assert resource_types == ["alicloud_ecs_command", "alicloud_ecs_invocation"]
    assert set(resource_types) == set(tool["allowed_resource_types"])
    assert not set(resource_types).intersection(contract["forbidden_resource_types"])


def test_diagnostic_command_has_zero_release_or_service_mutations() -> None:
    contract = load_contract()
    hcl = HCL_PATH.read_text(encoding="utf-8")
    execution = contract["execution_contract"]

    assert execution["max_unapproved_spend_usd"] == 0
    assert execution["qwen_calls"] == 0
    assert execution["service_state_changes"] == 0
    assert execution["application_file_writes"] == 0
    assert 'default     = "ap-southeast-1"' in hcl
    assert "timeout          = 30" in hcl
    assert 'repeat_mode = "Once"' in hcl
    assert "SYSTEMD_PAGER=" not in hcl
    assert "section=" not in hcl

    forbidden_command_fragments = (
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "systemctl enable",
        "systemctl disable",
        "apt install",
        "apt-get install",
        "docker run",
        "docker compose",
        "/etc/librarian/librarian.env",
        "/etc/librarian/caddy.env",
    )
    assert not any(fragment in hcl for fragment in forbidden_command_fragments)


def test_contract_and_hcl_contain_no_bound_cloud_identifier_or_secret() -> None:
    text = CONTRACT_PATH.read_text(encoding="utf-8") + HCL_PATH.read_text(encoding="utf-8")

    assert not re.search(r"\bi-[a-z0-9]{8,}\b", text)
    assert "ALIBABA_CLOUD_ACCESS_KEY_SECRET=" not in text
    assert "DASHSCOPE_API_KEY=" not in text
