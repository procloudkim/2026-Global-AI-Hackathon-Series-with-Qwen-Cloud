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
        "Ecs-20140526-DescribeCloudAssistantStatus",
        "Ecs-20140526-DescribeInstances",
        "Ecs-20140526-DescribeInvocationResults",
    }
    assert all("Delete" not in tool for tool in contract["api_tools"])
    assert contract["network"]["private_link_allowed"] is False


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
