# Operations plan — exact-candidate Alibaba release

## Proof and authority boundary

Official requirements and dates are canonical only in
[`submission/hackathon-contract.json`](../../submission/hackathon-contract.json).
Account-specific benefits and the zero-spend decision are in
[`submission/account-credit-audit.json`](../../submission/account-credit-audit.json).
Artifact status is in
[`submission/evidence-manifest.json`](../../submission/evidence-manifest.json).

The release chain is implemented locally but has not been run against Alibaba
Cloud. A local CI pass is not live-Qwen, deployed-persistence, private-holdout,
or submission proof.

## Runtime decision

| Candidate | Current decision | Evidence boundary |
|---|---|---|
| Alibaba ECS trial | Recommended, conditional | Best fit for the current file-backed persistence and Workbench proof; account eligibility and expiry behavior remain unknown |
| Simple Application Server trial | Single fallback | The same Ubuntu/systemd layout is compatible; trial eligibility and Workbench suitability must be verified in the logged-in console |
| Coupon-covered ECS/SAS | Not assumed | The observed cash voucher's Compute, disk, bandwidth, region, SKU, and billing-method scope is unknown |
| Function Compute + NAS/OSS | Rejected for this release | Atomic replace, process lock, crash recovery, and zero-cost persistent storage have not been proven |
| Minimal paid ECS/SAS | Emergency proposal only | Requires a separately calculated cost ceiling, shutdown time, and explicit user approval |

`MAX_UNAPPROVED_SPEND_USD=0`. Trial activation, coupon use, payment-method
changes, resource creation, paid fallback, and deployment each remain separate
approval gates.

## Host and storage contract

The selected host must be an approved Alibaba Cloud Ubuntu 22.04 or 24.04
instance with a non-ephemeral filesystem and at least 1 GiB free for the proof
lane. `deploy/setup.sh` creates:

```text
/opt/librarian/releases/<40-hex-sha>   immutable application release
/opt/librarian/current                 atomic symlink to the active release
/var/lib/librarian/memory              persistent canonical memory
/var/lib/librarian/deployments         append-only release/proof receipts
/etc/librarian/librarian.env           Qwen runtime secret and bounded settings
/etc/librarian/caddy.env               domain and bcrypt Basic Auth values
```

The `librarian` systemd service runs as a non-login user, binds only to
`127.0.0.1:8080`, and receives write access only to the memory directory.
Caddy terminates HTTPS, denies public `/health/qwen`, allows unauthenticated
process `/health`, protects the demo with Basic Auth, and limits request bodies
to 64 KiB. The application enforces a per-client request-rate ceiling.
Alibaba security-group evidence must show only approved SSH ingress and public
HTTPS; this is a console/approval receipt, not a host-script inference.

## One-time setup gate

After explicit resource approval only:

1. Create `submission/evidence/cloud-approval-receipt.json` against
   `deploy/cloud-approval.schema.json`. Bind it to the candidate tree, approval
   ticket, and masked deployment-target digest covering the SSH host, port,
   pinned host key, and public URL. Hash it and mark the matching
   evidence-manifest item `verified`; omit account identifiers and coupon codes.
2. Run `sudo bash deploy/setup.sh`; this installs host definitions but does not
   deploy source or start the application.
3. Populate `/etc/librarian/librarian.env` with the Qwen secret, exact model
   IDs, timeout, `LIBRARIAN_QWEN_MAX_RETRIES=0`, completion cap, rate limit,
   and health token.
4. Populate `/etc/librarian/caddy.env` with the approved host name, demo user,
   and bcrypt password hash; do not store plaintext credentials in Git or
   workflow inputs.
5. Start and verify Caddy, configure the Alibaba security group, and run the
   read-only host inspector. Any failed readiness check stops before source
   deployment.

## CI and release sequence

### Deterministic CI

`.github/workflows/ci.yml` runs for every push and pull request at the event
SHA. It performs locked dependency sync, tracked-file secret scanning,
application tests, evaluator-contract tests, the live-runner self-test,
contract preflight in `ci` mode, and an exact-SHA Docker build. It receives no
Qwen or Alibaba credential.

### Manual production workflow

`.github/workflows/deploy-alibaba.yml` requires the GitHub `production`
environment, a concurrency lock, a full reviewed candidate SHA, an approval
reference, a masked cloud-approval receipt digest, and the approved HTTPS URL.
It then:

1. checks out the exact candidate and requires it to be an ancestor of
   `origin/main`;
2. repeats deterministic/structural gates in a clean checkout;
3. validates the candidate-bound masked cloud approval receipt, cost ceiling,
   Compute eligibility, billing controls, persistent storage, network review,
   Workbench access, and judging-period retention before any live-provider call;
4. builds `proof/Dockerfile.live` and runs exactly two development cases in a
   no-gold container with fixed call, token, provider-error, completion, and
   wall-time limits;
5. inspects container mounts/image contents and evaluates outside the runner;
6. creates a Git archive whose embedded commit equals the candidate;
7. connects with a dedicated SSH key and pinned SSH host key, never a long-lived
   Alibaba AccessKey;
8. verifies the approved Alibaba host, persistent disk, loopback binding,
   service definitions, and masked secret-file shape;
9. binds live-Qwen and infrastructure receipts to the candidate tree and
   canonical-contract digest;
10. uploads the archive and audited scripts, then runs `deploy/deploy.sh` with
   all expected digests;
11. verifies `/health` returns the same deployed SHA and runs the bounded
    restart-persistence vertical slice;
12. appends a finalization receipt on PASS; on proof/finalization failure it
    restores the previous immutable release and rechecks memory integrity, or
    leaves the service stopped if no safe rollback exists;
13. uploads receipts even on failure and removes transient credentials/uploads.

Any live-Qwen failure blocks host deployment. Any post-deploy proof failure
retains the unique proof namespace for audit, does not delete or rewind shared
memory, and triggers the previous-release rollback contract; rollback failure
leaves the service stopped.

## Track 1 restart-persistence proof

`deploy/verify-restart-persistence.sh` uses a unique namespace and enforces the
following observable checks:

- source A sets quota 100 and an unrelated marker;
- source B explicitly replaces quota with 1000;
- the old claim is `superseded`, the new claim is `active`, and the marker
  remains `active`;
- before and after service restart, quota answers select 1000, cite source B,
  exclude standalone 100 from answer/facts/selected context, and identify the
  filtered old claim;
- top-K/context trace, exact deployed SHA, decision transition evidence, Qwen
  usage, and persistent-memory digests are recorded;
- the proof permits five API operations, no HTTP retries, at most ten routed
  provider calls, at most 25,000 observed tokens, and 120 seconds per HTTP
  operation.

The vertical slice must pass before any larger live suite or submission-ready
claim.

## Rollback and failure containment

`deploy/deploy.sh` switches an atomic symlink only after archive, release-gate,
lockfile sync, import, and persistent-store checks. If startup or SHA health
fails after the switch, it restores the previous immutable release. It leaves
memory in place and stops the service if the memory digest changes.

`deploy/rollback.sh --sha <previous-sha>` accepts only an existing immutable
release with matching embedded metadata and release-gate receipt. It snapshots
the memory digest, switches the symlink, checks SHA health, and restores the
original release or stops the service on failure. Rollback does not imply
cross-version memory-schema compatibility; that compatibility must be verified
before choosing the target.

## Secrets and quota controls

- GitHub repository: no Qwen key, Alibaba AccessKey, SSH key, coupon code, or
  account identifier.
- GitHub `production` secrets: dedicated SSH private key, pinned SSH host key,
  Qwen key, and demo credentials. Host/user/port may be non-secret variables.
- Host: root-owned, group-readable environment files with no secret values in
  receipts or command output.
- Qwen console: reverify `Free quota only` immediately before every approved
  live gate. Load balancers use `/health`, never `/health/qwen`.
- Alibaba billing: configure budget/spending alerts when the account console is
  operable, but treat them as advisory notifications rather than spend stops.
  Record a blocked alert surface as `console_blocked`; never claim it is
  configured. A zero-cost approval then requires a separately verified hard
  containment control, currently the account-console scheduled release of the
  instance and system disk after judging and before the trial window ends.
- Public endpoint: HTTPS, Basic Auth, request-size cap, application rate limit,
  and only required security-group ports.

## Required receipts and retention

The evidence manifest remains `pending_external` until candidate-bound copies
exist for the live behavioral gate, infrastructure readiness, deployment
manifest, restart persistence, Workbench screenshot, public demo URL, and
judge-access test. Receipts must record the same candidate SHA and relevant
digests; raw provider output must not contain secrets.

Keep the approved runtime through the judging period defined by the canonical
contract. A longer winners-announcement retention window is allowed only if its
maximum cost is explicitly approved. Do not tear down before judging ends;
after the approved retention boundary, archive non-secret receipts, revoke
keys, remove public ingress, and delete resources under a separate teardown
approval.

## Stop conditions

Stop without deployment or paid fallback when any of these is true:

- Compute/trial/coupon eligibility or overage behavior is unknown at activation;
- the official contract is stale for the release mode;
- the candidate is dirty, unfrozen, or differs from the evaluated SHA;
- deterministic, live-Qwen, host-readiness, restart-persistence, or private
  holdout gates fail;
- persistent storage is ephemeral or memory integrity changes during release;
- the public endpoint, Workbench proof, or receipt chain cannot be bound to the
  deployed SHA;
- the operation would exceed USD 0 without explicit approval.
