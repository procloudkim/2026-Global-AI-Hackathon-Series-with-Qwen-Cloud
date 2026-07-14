#!/usr/bin/env python3
"""Create a masked, read-only host-readiness receipt before deployment."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


RELEASE_ROOT = Path("/opt/librarian/releases")
MEMORY_ROOT = Path("/var/lib/librarian/memory")
DEPLOYMENT_ROOT = Path("/var/lib/librarian/deployments/infrastructure")
APP_ENV = Path("/etc/librarian/librarian.env")
CADDY_ENV = Path("/etc/librarian/caddy.env")
CADDYFILE = Path("/etc/caddy/Caddyfile")
SERVICE_UNIT = Path("/etc/systemd/system/librarian.service")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--cloud-approval-receipt-sha256", required=True)
    parser.add_argument("--approval-ticket-sha256", required=True)
    parser.add_argument("--expected-target-sha256", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def env_keys(path: Path) -> tuple[set[str], dict[str, str]]:
    keys: set[str] = set()
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment line in {path.name}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid environment key in {path.name}")
        if key in keys:
            raise ValueError(f"duplicate environment key in {path.name}: {key}")
        keys.add(key)
        values[key] = value
    return keys, values


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def add_check(checks: dict[str, dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks[name] = {"passed": bool(passed), "detail": detail}


def safe_mode(path: Path, *, allow_group_read: bool) -> bool:
    mode = stat.S_IMODE(path.stat().st_mode)
    forbidden = stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO
    if not allow_group_read:
        forbidden |= stat.S_IRGRP
    return path.stat().st_uid == 0 and mode & forbidden == 0


def mount_details(path: Path) -> tuple[str, str]:
    result = command("findmnt", "--json", "--target", str(path), "--output", "TARGET,FSTYPE")
    if result.returncode != 0:
        raise RuntimeError("findmnt could not resolve the persistent memory filesystem")
    payload = json.loads(result.stdout)
    filesystems = payload.get("filesystems") or []
    if len(filesystems) != 1:
        raise RuntimeError("findmnt returned an ambiguous filesystem")
    return str(filesystems[0].get("target", "")), str(filesystems[0].get("fstype", ""))


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise SystemExit("inspect-infrastructure.py must run as root")
    if not SHA_RE.fullmatch(args.candidate_sha):
        raise SystemExit("candidate SHA must be full lowercase 40-hex")
    if not DIGEST_RE.fullmatch(args.cloud_approval_receipt_sha256):
        raise SystemExit("cloud approval receipt digest must be lowercase SHA-256")
    if not DIGEST_RE.fullmatch(args.approval_ticket_sha256):
        raise SystemExit("approval ticket digest must be lowercase SHA-256")
    if not DIGEST_RE.fullmatch(args.expected_target_sha256):
        raise SystemExit("deployment target digest must be lowercase SHA-256")

    output = Path(args.output).resolve(strict=False)
    try:
        output.relative_to(DEPLOYMENT_ROOT)
    except ValueError as exc:
        raise SystemExit(f"output must be below {DEPLOYMENT_ROOT}") from exc
    if output.exists() or output.is_symlink():
        raise SystemExit("infrastructure receipt output already exists")

    checks: dict[str, dict[str, Any]] = {}
    storage = {"mount_target": "unknown", "filesystem_type": "unknown", "available_bytes": 0}
    runtime_limits = {
        "qwen_max_retries": 0,
        "qwen_timeout_seconds": 30.0,
        "qwen_max_completion_tokens": 1600,
        "rate_limit_per_minute": 60,
    }
    runtime_contract = {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "light_model": "qwen-flash",
        "heavy_model": "qwen-plus-2025-07-28",
    }

    os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    supported_os = 'ID=ubuntu' in os_release and any(
        marker in os_release for marker in ('VERSION_ID="22.04"', 'VERSION_ID="24.04"')
    )
    add_check(checks, "supported_os", supported_os, "Ubuntu 22.04/24.04 required")

    dmi_values = []
    for dmi_path in (
        Path("/sys/class/dmi/id/sys_vendor"),
        Path("/sys/class/dmi/id/product_name"),
    ):
        try:
            dmi_values.append(dmi_path.read_text(encoding="utf-8").strip().casefold())
        except OSError:
            dmi_values.append("")
    alibaba_host = any("alibaba" in value for value in dmi_values)
    add_check(
        checks,
        "alibaba_host_identity",
        alibaba_host,
        "local DMI identifies Alibaba Cloud; raw DMI values are not recorded",
    )
    add_check(
        checks,
        "external_cloud_console_attestation",
        True,
        "approved masked receipt digest is supplied externally; this host check does not prove security-group, public-IP, coupon, or trial eligibility",
    )
    add_check(
        checks,
        "deployment_target_binding",
        True,
        "approved target digest is bound to this pinned SSH session; raw target identifiers are not recorded",
    )

    try:
        identity = pwd.getpwnam("librarian")
        runtime_identity_ok = identity.pw_shell.endswith("nologin")
    except KeyError:
        runtime_identity_ok = False
    add_check(checks, "runtime_identity", runtime_identity_ok, "non-login librarian user exists")

    required_paths = (RELEASE_ROOT, MEMORY_ROOT, DEPLOYMENT_ROOT.parent, APP_ENV, CADDY_ENV, CADDYFILE, SERVICE_UNIT)
    paths_ok = all(path.exists() and not path.is_symlink() for path in required_paths)
    add_check(checks, "required_paths", paths_ok, "release, state, config, and unit paths exist without symlinks")

    app_env_ok = False
    caddy_env_ok = False
    if APP_ENV.is_file() and CADDY_ENV.is_file():
        try:
            app_keys, app_values = env_keys(APP_ENV)
            caddy_keys, caddy_values = env_keys(CADDY_ENV)
            app_env_ok = (
                "DASHSCOPE_API_KEY" in app_keys
                and bool(app_values["DASHSCOPE_API_KEY"])
                and not app_values["DASHSCOPE_API_KEY"].startswith("sk-your")
                and safe_mode(APP_ENV, allow_group_read=True)
            )
            runtime_limits = {
                "qwen_max_retries": int(app_values.get("LIBRARIAN_QWEN_MAX_RETRIES", "0")),
                "qwen_timeout_seconds": float(app_values.get("LIBRARIAN_QWEN_TIMEOUT_SECONDS", "30")),
                "qwen_max_completion_tokens": int(
                    app_values.get("LIBRARIAN_QWEN_MAX_COMPLETION_TOKENS", "1600")
                ),
                "rate_limit_per_minute": int(
                    app_values.get("LIBRARIAN_RATE_LIMIT_PER_MINUTE", "60")
                ),
            }
            runtime_contract = {
                "base_url": app_values.get(
                    "DASHSCOPE_BASE_URL",
                    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                ),
                "light_model": app_values.get("LIBRARIAN_LIGHT_MODEL", "qwen-flash"),
                "heavy_model": app_values.get(
                    "LIBRARIAN_HEAVY_MODEL", "qwen-plus-2025-07-28"
                ),
            }
            caddy_env_ok = (
                {"LIBRARIAN_DOMAIN", "LIBRARIAN_BASIC_AUTH_USER", "LIBRARIAN_BASIC_AUTH_HASH"}
                <= caddy_keys
                and re.fullmatch(r"[A-Za-z0-9.-]+", caddy_values["LIBRARIAN_DOMAIN"]) is not None
                and re.fullmatch(r"[A-Za-z0-9._~-]{1,64}", caddy_values["LIBRARIAN_BASIC_AUTH_USER"]) is not None
                and caddy_values["LIBRARIAN_BASIC_AUTH_HASH"].startswith("$2")
                and safe_mode(CADDY_ENV, allow_group_read=True)
            )
        except (OSError, ValueError):
            app_env_ok = False
            caddy_env_ok = False
    add_check(checks, "application_secret_config", app_env_ok, "Qwen key is present in a masked root-owned env file")
    add_check(checks, "caddy_secret_config", caddy_env_ok, "domain and bcrypt Basic Auth values are present in a masked env file")
    runtime_limits_ok = (
        runtime_limits["qwen_max_retries"] == 0
        and 1 <= runtime_limits["qwen_timeout_seconds"] <= 45
        and 8 <= runtime_limits["qwen_max_completion_tokens"] <= 1600
        and 1 <= runtime_limits["rate_limit_per_minute"] <= 60
    )
    add_check(checks, "bounded_runtime", runtime_limits_ok, "retry, timeout, completion, and rate limits are bounded")
    runtime_contract_ok = runtime_contract == {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "light_model": "qwen-flash",
        "heavy_model": "qwen-plus-2025-07-28",
    }
    add_check(
        checks,
        "qwen_runtime_parity",
        runtime_contract_ok,
        "host uses the allowlisted international DashScope endpoint and candidate Qwen model IDs",
    )

    service_text = SERVICE_UNIT.read_text(encoding="utf-8") if SERVICE_UNIT.is_file() else ""
    service_contract_ok = all(
        value in service_text
        for value in (
            "User=librarian",
            "WorkingDirectory=/opt/librarian/current",
            "Environment=LIBRARIAN_MEMORY_ROOT=/var/lib/librarian/memory",
            "--host 127.0.0.1",
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ReadWritePaths=/var/lib/librarian/memory",
        )
    )
    add_check(checks, "systemd_contract", service_contract_ok, "service binds loopback and only memory is writable")

    caddy_text = CADDYFILE.read_text(encoding="utf-8") if CADDYFILE.is_file() else ""
    caddy_contract_ok = all(
        value in caddy_text
        for value in ("/health/qwen", "respond @qwen_health 404", "basic_auth", "max_size 64KB", "127.0.0.1:8080")
    )
    add_check(checks, "caddy_contract", caddy_contract_ok, "public Qwen health is denied and demo traffic is bounded/authenticated")

    librarian_enabled = command("systemctl", "is-enabled", "librarian.service").returncode == 0
    caddy_enabled = command("systemctl", "is-enabled", "caddy.service").returncode == 0
    caddy_active = command("systemctl", "is-active", "caddy.service").returncode == 0
    add_check(checks, "services_enabled", librarian_enabled and caddy_enabled, "librarian and caddy are enabled")
    add_check(checks, "https_proxy_active", caddy_active, "Caddy is active before application deployment")

    sockets = command("ss", "-H", "-ltn").stdout
    local_addresses = [
        fields[3]
        for line in sockets.splitlines()
        if len(fields := line.split()) >= 4
    ]
    port_443 = any(address.rsplit(":", 1)[-1] == "443" for address in local_addresses)
    public_8080 = any(
        address in {"0.0.0.0:8080", "[::]:8080", "*:8080"}
        for address in local_addresses
    )
    add_check(checks, "https_listener", port_443, "host has a TCP 443 listener")
    add_check(checks, "application_not_public", not public_8080, "port 8080 is not bound on a wildcard address")

    try:
        mount_target, filesystem_type = mount_details(MEMORY_ROOT)
        available_bytes = shutil.disk_usage(MEMORY_ROOT).free
        storage = {
            "mount_target": mount_target,
            "filesystem_type": filesystem_type,
            "available_bytes": available_bytes,
        }
        storage_ok = filesystem_type not in {"tmpfs", "ramfs", "overlay"} and available_bytes >= 1024**3
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        storage_ok = False
    add_check(checks, "persistent_storage", storage_ok, "non-ephemeral filesystem has at least 1 GiB free")

    uv_ok = command("uv", "--version").stdout.strip() == "uv 0.11.28 (ebf0f43d7 2026-07-07 x86_64-unknown-linux-gnu)"
    if not uv_ok:
        uv_ok = command("uv", "--version").stdout.startswith("uv 0.11.28")
    add_check(checks, "pinned_uv", uv_ok, "uv 0.11.28 is installed")

    status = "PASS" if all(item["passed"] for item in checks.values()) else "FAIL"
    payload = {
        "schema_version": "librarian-infrastructure-readiness/v1",
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "candidate_sha": args.candidate_sha,
        "provider_signal": "alibaba_cloud_dmi" if alibaba_host else "unverified",
        "cloud_approval_receipt_sha256": args.cloud_approval_receipt_sha256,
        "approval_ticket_sha256": args.approval_ticket_sha256,
        "deployment_target_sha256": args.expected_target_sha256,
        "checks": checks,
        "runtime_contract": runtime_contract,
        "runtime_limits": runtime_limits,
        "storage": storage,
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(output)
    output.chmod(0o640)
    print(f"infrastructure_status={status}")
    print(f"infrastructure_receipt={output}")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
