variable "region" {
  description = "Approved Alibaba region for the existing Librarian ECS."
  type        = string
  default     = "ap-southeast-1"

  validation {
    condition     = var.region == "ap-southeast-1"
    error_message = "Only the approved ap-southeast-1 runtime may be diagnosed."
  }
}

variable "instance_id" {
  description = "Exact existing ECS instance ID. Never persist this value in the repository or output."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^i-[a-z0-9]+$", var.instance_id))
    error_message = "instance_id must be one Alibaba ECS instance ID."
  }
}

variable "approved_instance_sha256" {
  description = "SHA-256 of the approved instance_id from the fresh DescribeInstances binding receipt. Never persist the source ID in the repository."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.approved_instance_sha256))
    error_message = "approved_instance_sha256 must be one lowercase SHA-256 digest."
  }
}

provider "alicloud" {
  region = var.region
}

locals {
  diagnostic_script = <<-SCRIPT
    #!/usr/bin/env bash
    set -u

    sanitize() {
      sed -E \
        -e 's/(DASHSCOPE_API_KEY|ALIBABA_CLOUD_ACCESS_KEY_ID|ALIBABA_CLOUD_ACCESS_KEY_SECRET)[[:space:][:punct:]]+[^[:space:]]+/\1 <MASKED>/g' \
        -e 's/(Authorization:)[[:space:]]+[^[:space:]]+/\1 <MASKED>/g' \
        -e 's/sk-[A-Za-z0-9_-]+/<MASKED_KEY>/g' \
        -e 's/(^|[^[:alnum:]])i-[a-z0-9]{8,}/\1<MASKED_INSTANCE>/g' \
        -e 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<MASKED_IP>/g'
    }

    {
      echo 'section:systemd_state'
      systemctl is-active librarian.service 2>&1 || true
      systemctl is-failed librarian.service 2>&1 || true
      systemctl status librarian.service 2>&1 || true

      echo 'section:systemd_unit'
      cat /etc/systemd/system/librarian.service 2>&1 || true

      echo 'section:current_release'
      if [ -L /opt/librarian/current ]; then
        printf 'release:%s\n' "$(basename "$(readlink -f /opt/librarian/current)")"
      else
        echo 'release:NONE'
      fi

      echo 'section:runtime_access'
      if [ -x /opt/librarian/current/.venv/bin/uvicorn ]; then
        echo 'uvicorn_executable:YES'
      else
        echo 'uvicorn_executable:NO'
      fi
      ls -ld /opt/librarian/current 2>&1 || true
      ls -ld /opt/librarian/current/.venv 2>&1 || true

      echo 'section:local_health'
      curl -fsS -m 2 http://127.0.0.1:8080/health 2>&1 || true
      echo

      echo 'section:journal'
      journalctl -u librarian.service -n 120 2>&1 || true
    } | sanitize
  SCRIPT
}

resource "alicloud_ecs_command" "diagnose" {
  name             = "librarian-health-diagnostic"
  description      = "Read-only Librarian systemd and health diagnostic; no restart or file mutation."
  command_content  = base64encode(local.diagnostic_script)
  type             = "RunShellScript"
  working_dir      = "/tmp"
  timeout          = 30
  enable_parameter = false

  lifecycle {
    precondition {
      condition     = sha256(var.instance_id) == var.approved_instance_sha256
      error_message = "The requested instance does not match the fresh approved target binding."
    }
  }
}

resource "alicloud_ecs_invocation" "diagnose" {
  command_id  = alicloud_ecs_command.diagnose.id
  instance_id = [var.instance_id]
  repeat_mode = "Once"
  timed       = false
  username    = "root"

  timeouts {
    create = "1m"
    delete = "1m"
  }
}

data "alicloud_ecs_invocations" "diagnose" {
  ids              = [alicloud_ecs_invocation.diagnose.id]
  content_encoding = "PlainText"
  depends_on       = [alicloud_ecs_invocation.diagnose]
}

output "diagnostic_status" {
  value = try(data.alicloud_ecs_invocations.diagnose.invocations[0].invoke_instances[0].invocation_status, "UNKNOWN")
}

output "diagnostic_exit_code" {
  value = try(data.alicloud_ecs_invocations.diagnose.invocations[0].invoke_instances[0].exit_code, -1)
}

output "diagnostic_output" {
  value = try(data.alicloud_ecs_invocations.diagnose.invocations[0].invoke_instances[0].output, "NO_OUTPUT")
}
