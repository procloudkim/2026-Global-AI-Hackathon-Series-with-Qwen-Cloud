# ADR-14: OpenAPI MCP IaC 진단 plane과 release 비용 게이트

- 상태: Accepted — bounded diagnostic verified; release fix not yet deployed
- 작성일: 2026-07-14
- 결정 범위: 기존 Alibaba ECS의 진단, MCP/IaC 책임, release gate 실행 순서
- 결정권자: repository owner
- 선행 결정: ADR-11, ADR-12, ADR-13
- 대체 대상: 전체 release workflow를 원인 진단 수단으로 반복 실행하는 방식

## 1. 결정 요약

현재 Alibaba ECS, persistent disk, HTTPS, security group은 이미 존재한다.
실패 지점은 immutable release를 전환한 뒤 `librarian.service`가 `/health`를
통과하지 못한 것이다. 따라서 새 Compute를 만들거나 전체 Qwen release gate를
반복하는 것은 진단 작업이 아니다.

다음 세 plane을 분리한다.

1. **MCP diagnostic plane**: Custom OpenAPI MCP Server와 OAuth를 사용해 기존
   ECS 하나의 Cloud Assistant 상태를 확인하고, 고정된 read-only diagnostic
   command를 한 번 실행한다.
2. **IaC creation plane**: OpenAPI MCP Terraform Tool은 명시적으로 승인된
   신규·임시 자원 생성에만 사용한다. 현재 production ECS의 lifecycle owner로
   사용하지 않는다.
3. **release plane**: GitHub Actions는 candidate가 로컬·host 진단을 통과한 뒤
   exact SHA를 배포하는 최종 gate다. 원인 탐색을 위해 반복 실행하지 않는다.

현재 진단에는 Terraform Tool을 사용하되 생성 가능한 resource type을
`alicloud_ecs_command`와 `alicloud_ecs_invocation`으로 제한한다. 이 두 자원은
기존 ECS에서 고정 명령을 한 번 실행하기 위한 control-plane record이며 새
instance, disk, network 또는 public IP를 만들지 않는다. MCP Terraform Tool의
deletion policy는 `ALWAYS`다.

## 2. 실패에서 확인한 설계 결함

현재 `.github/workflows/deploy-alibaba.yml`은 host inspector보다 먼저 2-case
live Qwen gate를 실행한다. 배포 스크립트·host runtime 오류가 있어도 Qwen
호출과 전체 workflow compute가 먼저 소비된다. 실제로 동일 release chain을
여러 번 실행한 뒤에야 startup health failure에 도달했다.

원인은 다음과 같다.

- release workflow가 release gate와 diagnostic tool을 동시에 담당했다.
- host-side failure를 cheap probe로 먼저 확정하는 단계가 없었다.
- OpenAPI MCP/Cloud Assistant를 기존 ECS 운영 plane 후보로 평가하지 않았다.
- Terraform을 모든 인프라를 새로 만드는 도구로만 해석해, command/invocation
  같은 bounded control resource 사용을 놓쳤다.

## 3. 공식 계약에서 확인한 사실

2026-07-14에 다음 공식 문서를 확인했다.

- OpenAPI MCP Custom Edition은 선택한 API만 직접 Tool로 노출하고 OAuth
  short-lived authorization을 지원한다.
  https://www.alibabacloud.com/help/en/openapi/user-guide/openapi-mcp-server-guide
- Terraform Tool은 resource creation only이며 `ALWAYS`, `NEVER`,
  `ON_FAILURE` cleanup policy를 제공한다. `NEVER` 또는 성공한
  `ON_FAILURE` 실행은 같은 도구로 나중에 release할 수 없고, 재실행은 새 자원을
  만들려고 한다.
  https://www.alibabacloud.com/help/en/openapi/how-to-use-terraform-tools-in-openapi-mcp-server
- Cloud Assistant는 추가 서비스 요금이 없고 기존 ECS 사용량만 소비하며,
  agent overhead는 Linux에서 평균 CPU 1% 미만, 약 20 MB memory다.
  https://www.alibabacloud.com/help/en/ecs/user-guide/overview-10
- `alicloud_ecs_command`는 Base64 command와 timeout을 고정할 수 있고,
  `alicloud_ecs_invocation`은 기존 instance에 명령을 한 번 실행한다.
  https://help.aliyun.com/zh/terraform/alicloud-ecs-command
  https://help.aliyun.com/en/terraform/alicloud-ecs-invocation
- `alicloud_ecs_invocations` data source는 instance별 status, exit code,
  output을 반환한다.
  https://help.aliyun.com/zh/terraform/alicloud-ecs-invocations

OpenAPI Explorer와 product API 호출 자체는 무료지만, API가 billable resource를
만들면 해당 product 요금이 발생한다. 이 ADR은 resource allow-list로 그 경로를
차단한다.

## 4. Custom MCP service 계약

Canonical local projection은
[`infra/alibaba/mcp/service-contract.json`](../../infra/alibaba/mcp/service-contract.json)이다.
Console endpoint, account ID, instance ID, AccessKey는 repository에 기록하지 않는다.

| 항목 | 결정 |
|---|---|
| edition | Custom |
| authentication | Alibaba Cloud official OAuth |
| static AccessKey | 금지 |
| account scope | current account only |
| public endpoint | local interactive client only |
| PrivateLink | 금지 — 별도 PAYG endpoint 비용 |
| API Tools | DescribeInstances, DescribeCloudAssistantStatus, DescribeInvocationResults |
| delete API | 노출·권한 모두 금지 |
| Terraform Tool | `diagnose_librarian_service` 하나 |
| execution | asynchronous, 60-second outer ceiling |
| deletion policy | ALWAYS |

Core Edition은 전체 OpenAPI semantic search를 사용하므로 이 fixed operation에는
사용하지 않는다. Custom service 이름과 instruction에는 다음 제한을 명시한다.

- exact instance ID 하나만 대상으로 한다.
- `ap-southeast-1` 이외 region을 거부한다.
- Terraform diagnostic tool 외 resource mutation tool을 호출하지 않는다.
- start, stop, restart, enable, disable, package install, file write를 하지 않는다.
- output에서 key, authorization header, instance ID, public/private IP를 masking한다.

## 5. Terraform diagnostic tool 계약

Canonical HCL은
[`infra/alibaba/mcp/diagnose-librarian-service.tf`](../../infra/alibaba/mcp/diagnose-librarian-service.tf)다.

허용 resource block은 정확히 두 개다.

1. `alicloud_ecs_command.diagnose`
2. `alicloud_ecs_invocation.diagnose`

금지 resource에는 `alicloud_instance`, disk, VPC, vSwitch, security group, EIP,
NAT, load balancer, OSS, NAS, database가 포함된다. data source는 invocation output
조회에만 사용한다.

command는 다음만 읽는다.

- `systemctl is-active/is-failed/status librarian.service`
- `/etc/systemd/system/librarian.service`
- active release symlink의 basename
- release runtime executable의 존재·mode
- localhost `/health`
- 최근 `journalctl -u librarian.service` 120줄

command는 environment file을 읽지 않고, Qwen endpoint를 호출하지 않으며,
service를 시작·중지·재시작하지 않는다. application memory와 release directory를
변경하지 않는다. Cloud Assistant가 command staging과 execution record를 만드는
것은 이 tool의 유일한 host/control-plane write다.

## 6. 실행 순서

### Phase A — local freeze

1. clean Git tree와 exact HEAD를 확인한다.
2. service contract와 HCL의 static policy test를 통과시킨다.
3. Console HCL validator가 같은 HCL을 승인하는지 확인한다.

### Phase B — MCP bootstrap

1. Custom MCP service를 만든다.
2. official OAuth application을 설치·할당한다.
3. 위 세 API와 한 Terraform Tool만 노출한다.
4. endpoint는 local Codex OAuth client에만 연결한다.
5. endpoint·token·resource ID를 log나 repository에 기록하지 않는다.

### Phase C — one bounded diagnostic

1. `DescribeInstances`로 exact target이 Running인지 확인한다.
2. `DescribeCloudAssistantStatus`가 connected인지 확인한다.
3. `diagnose_librarian_service`를 정확히 한 번 호출한다.
4. `QueryTerraformTaskStatus` 또는 tool output에서 status와 masked output을 받는다.
5. command exit, systemd result, first actionable journal error만 receipt로 남긴다.
6. Terraform-created command/invocation이 `ALWAYS` policy로 제거됐는지 확인한다.

### Phase D — single-fix release

1. journal의 첫 load-bearing error 하나만 수정한다.
2. focused regression과 deterministic CI를 통과한다.
3. failed unverified release가 rollback candidate로 오인되지 않게 containment를
   확인한다.
4. host cheap gate를 live Qwen gate보다 앞에 배치한다.
5. 새 candidate에 대해 release workflow를 한 번만 실행한다.

## 7. 비용·보안 게이트

- `MAX_UNAPPROVED_SPEND_USD=0`
- 신규 ECS, disk, IP, bandwidth, PrivateLink, OSS/NAS 생성 금지
- static AccessKey 생성·저장 금지; interactive OAuth만 사용
- no delete permission
- exact instance 하나, exact region 하나
- command timeout 30초, execution once, outer polling 60초
- Qwen call 0, provider token 0
- service restart 0, application file write 0
- raw journal은 public artifact가 아니며 masking 후 필요한 error만 보존

다음 중 하나면 tool invocation 전에 중단한다.

- HCL validator가 unknown resource 또는 provider drift를 보고함
- deletion policy가 `ALWAYS`가 아님
- target instance/region이 approved runtime과 다름
- Terraform plan에 두 allow-listed resource 이외 항목이 존재함
- OAuth가 아닌 static AK를 요구함
- Cloud Assistant가 connected가 아님
- invocation이 새 billable resource를 만들 가능성이 있음

## 8. 검증과 receipt

local static test는 다음을 증명한다.

- service contract에 세 API만 있음
- delete API와 static credential이 없음
- HCL resource type이 command/invocation 두 개뿐임
- command에 mutation verb와 secret-file read가 없음
- timeout, Once, ALWAYS policy가 고정됨

live diagnostic receipt는 다음만 가진다.

현재 masked local receipt는
[`proof/runs/mcp-diagnostic-20260714/receipt.json`](../../proof/runs/mcp-diagnostic-20260714/receipt.json)에
있으며 `proof/runs/` 정책에 따라 Git에는 포함하지 않는다.

- schema version
- executed_at
- candidate SHA
- masked target digest
- MCP service contract hash
- HCL hash
- task status와 command exit code
- systemd state/result/exit status
- masked first actionable error
- cleanup verified 여부

이 receipt는 deployed-persistence나 private-promotion PASS가 아니다.

## 9. 거부한 대안

| 대안 | 거부 이유 |
|---|---|
| 전체 release workflow 재실행 | root cause를 모른 채 Qwen와 CI compute를 먼저 소비한다. |
| Core MCP | 전체 API semantic search가 fixed operation에 과도하다. |
| static AccessKey | 장기 credential과 repository/host secret surface가 늘어난다. |
| Terraform으로 새 ECS 재생성 | 기존 free-tier instance, disk, proof chain을 버리고 비용 위험을 만든다. |
| OpenAPI Terraform Tool을 production IaC SoT로 사용 | 공식적으로 creation-only이고 성공 자원의 후속 destroy/lifecycle 관리가 제한된다. |
| PrivateLink | 현재 local one-shot diagnostic에 불필요한 PAYG endpoint다. |
| SSH 수동 진단을 계속 반복 | 가능하지만 MCP command contract, audit, cleanup 일관성이 없다. |

## 10. 결과

MCP는 release workflow를 대체하지 않는다. MCP는 기존 Alibaba runtime의 cheap,
bounded diagnostic control plane이다. Terraform은 새 Compute를 만드는 대신
고정 Cloud Assistant command의 생성·실행·cleanup을 결정적으로 묶는 데만 쓴다.
원인 수정 이후 최종 exact-SHA release와 Qwen behavioral gate는 한 번만 실행한다.

## 11. 2026-07-14 실행 결과와 증명 경계

- official OpenAPI MCP OAuth application을 설치하고 short-lived OAuth를 승인했다.
- Custom service에서 ECS 조회 API 3개, Terraform diagnostic 1개, task-status
  system tool 1개만 노출되는 것을 확인했다.
- `DescribeInstances`는 approved region의 Running instance를 정확히 하나 반환했고,
  `DescribeCloudAssistantStatus`는 Linux agent connected를 반환했다.
- 첫 Terraform 진단은 HCL transport가 heredoc 안의 literal `=`를 재정렬하는
  동작을 드러냈다. 명령을 colon marker와 short option으로 축소한 뒤 두 번째
  bounded 진단에서 journal을 회수했다.
- journal의 첫 load-bearing error는 release staging path에서 생성한
  `.venv/bin/uvicorn` shebang이 rename 뒤에도 삭제된 staging Python을 가리키는
  `bad interpreter`였다. service는 exit 126으로 반복 실패했고 localhost health는
  connection refused였다.
- deployed SHA, final release symlink, uvicorn file 존재, persistent memory 경로는
  확인됐다. 따라서 실패 원인은 Compute 부족이나 Qwen connectivity가 아니라
  non-relocatable virtualenv를 staging path에서 생성한 deploy ordering이다.
- `deploy/deploy.sh`는 final release path로 이동한 뒤 `uv sync`를 실행하도록
  수정했다. unhealthy current release를 rollback 후보로 인정하지 않고, 실패한
  신규 candidate symlink와 이 실행이 만든 release directory를 containment한다.
- live 진단 중 Qwen 호출은 0회였고 신규 ECS, disk, network, IP, storage는
  생성하지 않았다. Terraform task는 `Success`, command exit는 0이었다.
- cleanup policy는 `ALWAYS`였으나 command/invocation 삭제를 별도 list API로
  독립 조회하지 않았으므로 cleanup은 policy-asserted이며 independently verified가
  아니다.
- 이 결과는 cloud diagnosis PASS이지 deployment, restart persistence, private
  holdout promotion 또는 submission completeness PASS가 아니다.
