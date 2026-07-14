# Hackathon Contract Projection

<!-- canonical-json-sha256: e3b835936832c1cdc2e55458c17d1d17ec4debfc0df1b788c692726f3370df9a -->

This file is a human navigation layer, not an independent source of truth.
The canonical contract is [`hackathon-contract.json`](hackathon-contract.json),
and submission readiness is tracked in
[`evidence-manifest.json`](evidence-manifest.json).

The canonical JSON controls source priority, observation times, deadlines,
mandatory and optional classifications, current form field identifiers, Track
1 gates, and known unknowns. Its SHA-256 at the time this projection was
updated is recorded in the comment above. `scripts/preflight.ps1` rejects a
projection whose recorded digest no longer matches the JSON.

## Read the current projection

From the repository root:

```powershell
$contract = Get-Content -Raw submission/hackathon-contract.json | ConvertFrom-Json
$contract.snapshot
$contract.deadlines
$contract.mandatory_requirements | Select-Object id, classification, rule
$contract.submission_fields | Select-Object id, key, required
```

The 2026-07-14 public refresh captured exact Rules judging labels and weights,
per-source fetch timestamps, and HTTP response-body digests. It also records
the Rules/dates-page and Rules-internal wording conflicts without silently
resolving them. The authenticated submission-form response digest is still
pending, so `source_content_hashes_complete` remains false and submit mode must
fail closed until a final authenticated refresh. A missing value must never be
replaced by an inferred label, timestamp, field identifier, or digest.

## Validation modes

- `ci`: validates the canonical shape, projection digest, local artifact paths,
  placeholder absence, and the configured seven-day snapshot freshness window.
  Explicitly pending external evidence is allowed.
- `deploy`: additionally requires a frozen candidate commit, a clean checkout,
  fresh contract evidence, and every deploy-required artifact.
- `submit`: additionally requires verified submission evidence, captured hashes
  for official submission sources, a current contract snapshot, and a clean
  exact-candidate checkout.

Run a mode explicitly:

```powershell
pwsh -File scripts/preflight.ps1 -Mode ci
```

Passing one mode does not imply that a later mode has passed.
