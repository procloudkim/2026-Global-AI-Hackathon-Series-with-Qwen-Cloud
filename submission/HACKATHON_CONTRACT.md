# Hackathon Contract Projection

<!-- canonical-json-sha256: 66cd2090d6b0ace5633b15d44d94beaa28f08c159068c4a209fea1a5340f3353 -->

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

The 2026-07-18 refresh used the authenticated Devpost Hackathons connector to
re-read the overview, Rules, current submission requirements, key dates,
judging criteria, and announcements. The complete 18-field submission response
is frozen in `evidence/devpost-submission-requirements-20260715T134537Z.json`
and its file digest is bound into the canonical source entry. This observation
corrected one material drift: field `27898` (Testing Instructions) is optional
in the current form, while the repository continues to require it as an
internal judge-readiness gate. The normalized form response was unchanged from
the 2026-07-15 capture except for its fetch timestamp. Rules/date wording conflicts remain recorded
without silently resolving them.

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
