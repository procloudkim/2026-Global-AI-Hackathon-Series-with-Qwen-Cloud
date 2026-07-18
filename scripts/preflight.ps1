[CmdletBinding()]
param(
  [ValidateSet("ci", "deploy", "submit")]
  [string]$Mode = "ci",
  [string]$RepoRoot = ".",
  [string]$CandidateSha,
  [string]$ReleaseGateReceipt,
  [switch]$RunTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ModeKey = $Mode.ToLowerInvariant()
$OriginalLocation = Get-Location

function Stop-Preflight {
  param([Parameter(Mandatory = $true)][string]$Message)
  throw "[preflight:$ModeKey] $Message"
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Test-Sha256 {
  param([AllowNull()][object]$Value)
  if ($null -eq $Value) { return $false }
  return ([string]$Value) -match '^[0-9a-fA-F]{64}$'
}

function Get-ArtifactRequirement {
  param(
    [Parameter(Mandatory = $true)][object[]]$Requirements,
    [Parameter(Mandatory = $true)][string]$RequirementId
  )
  return @($Requirements | Where-Object { $_.id -eq $RequirementId })[0]
}

function Test-PlaceholderValue {
  param([AllowNull()][object]$Value)

  if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
    return $true
  }

  $Text = [string]$Value
  foreach ($Pattern in @(
    '\{\{[^{}\r\n]+\}\}',
    '<EVIDENCE_[A-Z0-9_]+>',
    '\[(?:ADD|INSERT|REPLACE)[^\]\r\n]*\]',
    '\b(?:TODO|TBD|REPLACE_ME|CHANGEME|PLACEHOLDER)\b',
    'https://example\.com'
  )) {
    if ($Text -match $Pattern) {
      return $true
    }
  }
  return $false
}

function Resolve-RepositoryArtifactFile {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$ArtifactId,
    [AllowEmptyString()][string]$Path
  )

  if ([string]::IsNullOrWhiteSpace($Path)) {
    Stop-Preflight "Verified file-backed artifact '$ArtifactId' requires a nonblank repository-contained path."
  }

  $RootFull = [IO.Path]::GetFullPath($Root).TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
  )
  $CandidateFull = if ([IO.Path]::IsPathRooted($Path)) {
    [IO.Path]::GetFullPath($Path)
  }
  else {
    [IO.Path]::GetFullPath((Join-Path -Path $RootFull -ChildPath $Path))
  }
  $RootPrefix = $RootFull + [IO.Path]::DirectorySeparatorChar
  $Comparison = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    [StringComparison]::OrdinalIgnoreCase
  }
  else {
    [StringComparison]::Ordinal
  }
  if (-not $CandidateFull.StartsWith($RootPrefix, $Comparison)) {
    Stop-Preflight "Verified file-backed artifact '$ArtifactId' path escapes the repository root."
  }
  if (-not (Test-Path -LiteralPath $CandidateFull -PathType Leaf)) {
    Stop-Preflight "Verified file-backed artifact '$ArtifactId' points to a missing regular file."
  }

  $Item = Get-Item -Force -LiteralPath $CandidateFull
  if ($Item.PSIsContainer -or
      (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    Stop-Preflight "Verified file-backed artifact '$ArtifactId' must point to a regular file, not a directory or link."
  }
  return $CandidateFull
}

try {
  $ResolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
  Set-Location -LiteralPath $ResolvedRoot

  Write-Host "== Librarian contract preflight ($ModeKey) =="

  $ContractPath = "submission/hackathon-contract.json"
  $ProjectionPath = "submission/HACKATHON_CONTRACT.md"
  $ManifestPath = "submission/evidence-manifest.json"
  $TemplatePath = "submission/DEVPOST_TEMPLATE.md"

  foreach ($Path in @($ContractPath, $ProjectionPath, $ManifestPath, $TemplatePath)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
      Stop-Preflight "Missing contract-chain file: $Path"
    }
  }

  try {
    $Contract = Get-Content -Raw -LiteralPath $ContractPath | ConvertFrom-Json
    $Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
  }
  catch {
    Stop-Preflight "Invalid JSON in the contract chain: $($_.Exception.Message)"
  }

  if ($Contract.schema_version -ne "1.0.0" -or $Contract.canonical -ne $true) {
    Stop-Preflight "Unsupported or non-canonical hackathon contract."
  }
  if ($Manifest.schema_version -ne "1.0.0") {
    Stop-Preflight "Unsupported evidence manifest schema."
  }

  $ContractHash = Get-Sha256 -Path $ContractPath
  if ([string]$Manifest.contract.path -ne $ContractPath) {
    Stop-Preflight "Evidence manifest points to the wrong canonical contract path."
  }
  if ([string]$Manifest.contract.sha256 -ne $ContractHash) {
    Stop-Preflight "Evidence manifest contract digest is stale. Expected $ContractHash."
  }

  $Projection = Get-Content -Raw -LiteralPath $ProjectionPath
  $ProjectionMatch = [regex]::Match(
    $Projection,
    '<!--\s*canonical-json-sha256:\s*([0-9a-fA-F]{64})\s*-->'
  )
  if (-not $ProjectionMatch.Success) {
    Stop-Preflight "Human contract projection is missing its canonical JSON digest marker."
  }
  if ($ProjectionMatch.Groups[1].Value.ToLowerInvariant() -ne $ContractHash) {
    Stop-Preflight "Human contract projection is stale for the canonical JSON."
  }

  $Sources = @($Contract.sources)
  if ($Sources.Count -eq 0) {
    Stop-Preflight "Canonical contract has no official sources."
  }
  $SourceIds = @($Sources | ForEach-Object { [string]$_.id })
  if (@($SourceIds | Select-Object -Unique).Count -ne $SourceIds.Count) {
    Stop-Preflight "Canonical contract contains duplicate source IDs."
  }
  foreach ($Source in $Sources) {
    if ([string]::IsNullOrWhiteSpace([string]$Source.id)) {
      Stop-Preflight "Canonical contract contains a source without an ID."
    }
    if ([string]$Source.url -notmatch '^https://') {
      Stop-Preflight "Source '$($Source.id)' is not an HTTPS official URL."
    }
    if ($null -eq $Source.fetched_at.window_start -or $null -eq $Source.fetched_at.window_end) {
      Stop-Preflight "Source '$($Source.id)' lacks its explicit audit window."
    }
    try {
      [void][DateTimeOffset]::Parse([string]$Source.fetched_at.window_start)
      [void][DateTimeOffset]::Parse([string]$Source.fetched_at.window_end)
    }
    catch {
      Stop-Preflight "Source '$($Source.id)' has an invalid audit-window timestamp."
    }
    if ($null -ne $Source.content_sha256 -and -not (Test-Sha256 $Source.content_sha256)) {
      Stop-Preflight "Source '$($Source.id)' has a malformed content SHA-256."
    }
  }

  $MandatoryRequirements = @($Contract.mandatory_requirements)
  $OptionalRequirements = @($Contract.optional_items)
  $Requirements = @($MandatoryRequirements) + @($OptionalRequirements)
  $RequirementIds = @($Requirements | ForEach-Object { [string]$_.id })
  if ($MandatoryRequirements.Count -eq 0 -or
      @($RequirementIds | Select-Object -Unique).Count -ne $RequirementIds.Count) {
    Stop-Preflight "Requirement IDs are empty or duplicated."
  }
  foreach ($Requirement in $MandatoryRequirements) {
    if ([string]$Requirement.classification -notin @(
      "mandatory",
      "mandatory_if_existing_project",
      "mandatory_form_field"
    )) {
      Stop-Preflight "Requirement '$($Requirement.id)' has an unsupported classification."
    }
    foreach ($SourceId in @($Requirement.source_ids)) {
      if ($SourceIds -notcontains [string]$SourceId) {
        Stop-Preflight "Requirement '$($Requirement.id)' references unknown source '$SourceId'."
      }
    }
  }
  foreach ($Requirement in $OptionalRequirements) {
    if ([string]$Requirement.classification -notin @(
      "optional",
      "optional_recommended_form_field",
      "optional_technical_signal_not_contract"
    )) {
      Stop-Preflight "Optional requirement '$($Requirement.id)' has an unsupported classification."
    }
    foreach ($SourceId in @($Requirement.source_ids)) {
      if ($SourceIds -notcontains [string]$SourceId) {
        Stop-Preflight "Optional requirement '$($Requirement.id)' references unknown source '$SourceId'."
      }
    }
  }

  $FormFields = @($Contract.submission_fields)
  $CapturedFieldIds = @($FormFields | Where-Object { $null -ne $_.id } | ForEach-Object { [string]$_.id })
  if (@($CapturedFieldIds | Select-Object -Unique).Count -ne $CapturedFieldIds.Count) {
    Stop-Preflight "Submission form contains duplicate captured field IDs."
  }
  foreach ($ExpectedFieldKey in @(
    "track_selection",
    "public_repository_url",
    "alibaba_cloud_proof_code_url",
    "architecture_diagram_upload",
    "workbench_deployment_screenshot_upload",
    "ai_tools_used",
    "testing_instructions",
    "public_demo_video"
  )) {
    if (@($FormFields | Where-Object { $_.key -eq $ExpectedFieldKey }).Count -ne 1) {
      Stop-Preflight "Expected current form field '$ExpectedFieldKey' is absent or duplicated."
    }
  }

  try {
    $SubmissionDeadline = [DateTimeOffset]::Parse([string]$Contract.deadlines.submission.utc)
    $JudgingStart = [DateTimeOffset]::Parse([string]$Contract.deadlines.judging.start_utc)
    $JudgingEnd = [DateTimeOffset]::Parse([string]$Contract.deadlines.judging.end_utc)
    $AuditEnd = [DateTimeOffset]::Parse([string]$Contract.snapshot.audit_window.end)
  }
  catch {
    Stop-Preflight "Canonical deadline or audit timestamp is invalid."
  }
  if ($JudgingStart -ge $JudgingEnd -or $SubmissionDeadline -ge $JudgingEnd) {
    Stop-Preflight "Canonical deadline ordering is invalid."
  }
  if ([DateTimeOffset]::Now -gt $SubmissionDeadline) {
    Stop-Preflight "The canonical submission deadline has passed."
  }

  $FreshnessProperty = $Contract.snapshot.freshness.max_age_hours.PSObject.Properties[$ModeKey]
  if ($null -eq $FreshnessProperty) {
    Stop-Preflight "No freshness threshold is configured for mode '$ModeKey'."
  }
  $AgeHours = ([DateTimeOffset]::Now - $AuditEnd).TotalHours
  if ($AgeHours -lt -1) {
    Stop-Preflight "Canonical audit window is unexpectedly in the future."
  }
  if ($AgeHours -gt [double]$FreshnessProperty.Value) {
    Stop-Preflight "Canonical contract is stale for '$ModeKey' mode ($([math]::Round($AgeHours, 1)) hours old)."
  }

  $AllowedStatuses = @(
    "verified",
    "verified_local",
    "needs_refresh",
    "pending_local",
    "pending_external",
    "blocked_external",
    "not_applicable"
  )
  $DeployRuntimeArtifactIds = @(
    "live_qwen_behavioral_receipt",
    "infrastructure_readiness_receipt"
  )
  $DeployGateValidated = $false
  if ($ModeKey -eq "deploy") {
    if ([string]::IsNullOrWhiteSpace($CandidateSha) -or $CandidateSha -notmatch '^[0-9a-fA-F]{40}$') {
      Stop-Preflight "Deploy mode requires a full 40-character CandidateSha."
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseGateReceipt) -or
        -not (Test-Path -LiteralPath $ReleaseGateReceipt -PathType Leaf)) {
      Stop-Preflight "Deploy mode requires an existing -ReleaseGateReceipt."
    }
    try {
      $DeployGate = Get-Content -Raw -LiteralPath $ReleaseGateReceipt | ConvertFrom-Json
      $DeployGateCreatedAt = [DateTimeOffset]::Parse([string]$DeployGate.created_at)
    }
    catch {
      Stop-Preflight "Release-gate receipt is invalid JSON or has an invalid timestamp: $($_.Exception.Message)"
    }
    if ([string]$DeployGate.schema_version -ne "librarian-release-gate/v1" -or
        [string]$DeployGate.status -ne "PASS") {
      Stop-Preflight "Release-gate receipt is not a supported PASS receipt."
    }
    if ([string]$DeployGate.candidate_sha -ne $CandidateSha.ToLowerInvariant()) {
      Stop-Preflight "Release-gate receipt belongs to another candidate SHA."
    }
    if (-not (Test-Sha256 $DeployGate.deployment_target_sha256)) {
      Stop-Preflight "Release-gate receipt lacks its approved deployment-target digest."
    }
    if ([string]$Manifest.candidate.status -ne "frozen" -or
        -not (Test-Sha256 $Manifest.candidate.tree_sha256)) {
      Stop-Preflight "Deploy mode requires a frozen candidate tree in the evidence manifest."
    }
    if ([string]$DeployGate.candidate_tree_sha256 -ne
        ([string]$Manifest.candidate.tree_sha256).ToLowerInvariant()) {
      Stop-Preflight "Release-gate candidate tree does not match the frozen evidence manifest."
    }
    if ([string]$DeployGate.evidence_sha256.contract -ne $ContractHash) {
      Stop-Preflight "Release-gate receipt is bound to another canonical contract digest."
    }
    foreach ($EvidenceKey in @(
      "live_metrics",
      "isolation_attestation",
      "infrastructure_readiness",
      "contract"
    )) {
      $EvidenceProperty = $DeployGate.evidence_sha256.PSObject.Properties[$EvidenceKey]
      if ($null -eq $EvidenceProperty -or -not (Test-Sha256 $EvidenceProperty.Value)) {
        Stop-Preflight "Release-gate receipt lacks a valid '$EvidenceKey' evidence digest."
      }
    }
    if ([int]$DeployGate.limits.maximum_calls -ne 18 -or
        [int]$DeployGate.limits.maximum_total_tokens -ne 25000 -or
        [int]$DeployGate.limits.maximum_provider_errors -ne 0) {
      Stop-Preflight "Release-gate budget differs from the approved bounded contract."
    }
    $DeployGateAgeHours = ([DateTimeOffset]::Now - $DeployGateCreatedAt).TotalHours
    $DeployFreshnessHours = [double]$Contract.snapshot.freshness.max_age_hours.deploy
    if ($DeployGateAgeHours -lt -1 -or $DeployGateAgeHours -gt $DeployFreshnessHours) {
      Stop-Preflight "Release-gate receipt is outside the deployment freshness window."
    }
    $DeployGateValidated = $true
  }

  $Artifacts = @($Manifest.artifacts)
  $ArtifactIds = @($Artifacts | ForEach-Object { [string]$_.id })
  if ($Artifacts.Count -eq 0 -or @($ArtifactIds | Select-Object -Unique).Count -ne $ArtifactIds.Count) {
    Stop-Preflight "Evidence artifact IDs are empty or duplicated."
  }

  foreach ($RequiredPath in @($Manifest.local_release_chain_files)) {
    if (-not (Test-Path -LiteralPath ([string]$RequiredPath) -PathType Leaf)) {
      Stop-Preflight "Missing local release-chain file: $RequiredPath"
    }
  }

  foreach ($Artifact in $Artifacts) {
    $Status = [string]$Artifact.status
    $Kind = [string]$Artifact.kind
    $IsFileBacked = $Kind -match '(?:^|_)(?:file|upload|receipt)(?:_|$)'
    $IsUrlBacked = $Kind -match '(?:^|_)url(?:_|$)'
    $IsConditional = $Kind -match '(?:^|_)conditional(?:_|$)'
    $RequiresHumanValue = $Kind -match '(?:^|_)human(?:_|$)'
    if ($AllowedStatuses -notcontains $Status) {
      Stop-Preflight "Artifact '$($Artifact.id)' has unsupported status '$Status'."
    }
    if ($RequirementIds -notcontains [string]$Artifact.requirement_id) {
      Stop-Preflight "Artifact '$($Artifact.id)' references unknown requirement '$($Artifact.requirement_id)'."
    }
    foreach ($RequiredMode in @($Artifact.required_by)) {
      if ([string]$RequiredMode -notin @("deploy", "submit")) {
        Stop-Preflight "Artifact '$($Artifact.id)' has invalid required mode '$RequiredMode'."
      }
    }

    $PathValue = $Artifact.path
    if ($null -ne $PathValue -and -not [string]::IsNullOrWhiteSpace([string]$PathValue)) {
      $ArtifactPath = [string]$PathValue
      if (-not (Test-Path -LiteralPath $ArtifactPath -PathType Leaf)) {
        Stop-Preflight "Artifact '$($Artifact.id)' points to missing file '$ArtifactPath'."
      }
      if ($null -ne $Artifact.sha256) {
        if (-not (Test-Sha256 $Artifact.sha256)) {
          Stop-Preflight "Artifact '$($Artifact.id)' has a malformed SHA-256."
        }
        if ((Get-Sha256 -Path $ArtifactPath) -ne ([string]$Artifact.sha256).ToLowerInvariant()) {
          Stop-Preflight "Artifact '$($Artifact.id)' digest does not match '$ArtifactPath'."
        }
      }

      $Requirement = Get-ArtifactRequirement -Requirements $Requirements -RequirementId ([string]$Artifact.requirement_id)
      $ConstraintsProperty = $Requirement.PSObject.Properties["constraints"]
      if ($null -ne $ConstraintsProperty -and $null -ne $Requirement.constraints.accepted_extensions) {
        $Extension = [IO.Path]::GetExtension($ArtifactPath).TrimStart('.').ToLowerInvariant()
        if (@($Requirement.constraints.accepted_extensions) -notcontains $Extension) {
          Stop-Preflight "Artifact '$($Artifact.id)' has disallowed extension '.$Extension'."
        }
        if ($null -ne $Requirement.constraints.max_megabytes) {
          $SizeMegabytes = (Get-Item -LiteralPath $ArtifactPath).Length / 1MB
          if ($SizeMegabytes -gt [double]$Requirement.constraints.max_megabytes) {
            Stop-Preflight "Artifact '$($Artifact.id)' exceeds its upload-size limit."
          }
        }
      }
    }

    if (@($Artifact.required_by) -contains $ModeKey) {
      if ($Status -eq "verified" -and $IsFileBacked) {
        $VerifiedArtifactPath = Resolve-RepositoryArtifactFile `
          -Root $ResolvedRoot `
          -ArtifactId ([string]$Artifact.id) `
          -Path ([string]$Artifact.path)
        if (-not (Test-Sha256 $Artifact.sha256)) {
          Stop-Preflight "Verified file-backed artifact '$($Artifact.id)' lacks a candidate-bound SHA-256."
        }
        if ((Get-Sha256 -Path $VerifiedArtifactPath) -ne ([string]$Artifact.sha256).ToLowerInvariant()) {
          Stop-Preflight "Verified file-backed artifact '$($Artifact.id)' digest does not match its repository file."
        }
      }
      if ($Status -eq "verified" -and $IsUrlBacked -and [string]$Artifact.value -notmatch '^https://') {
        Stop-Preflight "Verified URL artifact '$($Artifact.id)' is missing an HTTPS URL."
      }
      if (($IsConditional -or $RequiresHumanValue) -and
          $Status -in @("verified", "not_applicable") -and
          (Test-PlaceholderValue $Artifact.value)) {
        Stop-Preflight "Conditional or human artifact '$($Artifact.id)' requires a non-placeholder value."
      }

      $SatisfiedByRuntimeGate = (
        $ModeKey -eq "deploy" -and
        $DeployGateValidated -and
        $DeployRuntimeArtifactIds -contains [string]$Artifact.id
      )
      if (-not $SatisfiedByRuntimeGate) {
        if ($Status -notin @("verified", "not_applicable")) {
          Stop-Preflight "Artifact '$($Artifact.id)' is required for '$ModeKey' but is '$Status'."
        }
        if ($Status -eq "not_applicable" -and -not $IsConditional) {
          Stop-Preflight "Only conditional artifacts may be marked not_applicable; '$($Artifact.id)' is mandatory."
        }
      }
    }
  }

  $PlaceholderPatterns = @(
    '\{\{[^{}\r\n]+\}\}',
    '\[(?:ADD|INSERT|REPLACE)[^\]\r\n]*\]',
    '\b(?:TODO|TBD|REPLACE_ME|CHANGEME)\b',
    'https://example\.com'
  )
  foreach ($ScanPath in @($ContractPath, $ProjectionPath, $ManifestPath, $TemplatePath)) {
    $Text = Get-Content -Raw -LiteralPath $ScanPath
    foreach ($Pattern in $PlaceholderPatterns) {
      if ($Text -match $Pattern) {
        Stop-Preflight "Placeholder pattern '$Pattern' remains in '$ScanPath'."
      }
    }
  }

  if ($ModeKey -eq "submit") {
    $SubmissionTextFiles = @(
      Get-ChildItem -LiteralPath "submission" -File |
        Where-Object { $_.Extension.ToLowerInvariant() -in @(".md", ".mmd", ".json") }
    )
    $SubmitOnlyPatterns = @(
      '<EVIDENCE_[A-Z0-9_]+>',
      '\[(?:ADD|INSERT|REPLACE)[^\]\r\n]*\]',
      '\b(?:TODO|TBD|REPLACE_ME|CHANGEME)\b',
      'https://example\.com'
    )
    foreach ($SubmissionFile in $SubmissionTextFiles) {
      $Text = Get-Content -Raw -LiteralPath $SubmissionFile.FullName
      foreach ($Pattern in $SubmitOnlyPatterns) {
        if ($Text -match $Pattern) {
          Stop-Preflight "Submit placeholder pattern '$Pattern' remains in '$($SubmissionFile.FullName)'."
        }
      }
    }
  }

  $CurrentHead = (& git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0 -or $CurrentHead -notmatch '^[0-9a-fA-F]{40}$') {
    Stop-Preflight "Unable to resolve the current Git HEAD."
  }

  if (-not [string]::IsNullOrWhiteSpace($CandidateSha)) {
    if ($CandidateSha -notmatch '^[0-9a-fA-F]{40}$') {
      Stop-Preflight "CandidateSha must be a full 40-character commit SHA."
    }
    if ($ModeKey -eq "deploy" -and $CurrentHead -ne $CandidateSha.ToLowerInvariant()) {
      Stop-Preflight "Deploy checkout HEAD does not equal CandidateSha."
    }
  }

  if ($ModeKey -eq "deploy") {
    $Dirty = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $Dirty.Count -ne 0) {
      Stop-Preflight "Deploy mode requires a clean exact-SHA checkout."
    }
  }

  if ($ModeKey -eq "submit") {
    if ([string]$Contract.snapshot.status -ne "verified") {
      Stop-Preflight "Submit mode requires snapshot.status='verified'."
    }
    if ($Contract.snapshot.source_content_hashes_complete -ne $true) {
      Stop-Preflight "Submit mode requires a complete official-source hash capture."
    }
    foreach ($Source in $Sources | Where-Object { $_.hash_required_for_submission -eq $true }) {
      if (-not (Test-Sha256 $Source.content_sha256) -or [string]$Source.hash_status -ne "captured") {
        Stop-Preflight "Submit source '$($Source.id)' lacks a captured content digest."
      }
      if ($null -ne $Source.PSObject.Properties["content_path"] -and
          -not [string]::IsNullOrWhiteSpace([string]$Source.content_path)) {
        $SourceFile = Resolve-RepositoryArtifactFile `
          -Root $ResolvedRoot `
          -ArtifactId "submit source '$($Source.id)'" `
          -Path ([string]$Source.content_path)
        if ((Get-Sha256 -Path $SourceFile) -ne ([string]$Source.content_sha256).ToLowerInvariant()) {
          Stop-Preflight "Submit source '$($Source.id)' content file does not match its captured digest."
        }
      }
    }
    if ([string]$Contract.judging_criteria.status -eq "not_captured") {
      Stop-Preflight "Submit mode requires a refreshed judging-criteria observation."
    }
    if ([string]$Manifest.candidate.status -ne "frozen" -or -not (Test-Sha256 $Manifest.candidate.tree_sha256)) {
      Stop-Preflight "Submit mode requires a frozen candidate and source-tree SHA-256."
    }
    if ([string]$Manifest.candidate.commit_sha -notmatch '^[0-9a-fA-F]{40}$') {
      Stop-Preflight "Submit mode requires the deployed candidate commit SHA."
    }
    if (-not [string]::IsNullOrWhiteSpace($CandidateSha) -and [string]$Manifest.candidate.commit_sha -ne $CandidateSha.ToLowerInvariant()) {
      Stop-Preflight "Submit CandidateSha does not match the evidence manifest candidate."
    }
    $Dirty = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $Dirty.Count -ne 0) {
      Stop-Preflight "Submit mode requires a clean repository checkout."
    }

    $Video = @($Artifacts | Where-Object { $_.id -eq "public_demo_video" })[0]
    $VideoRequirement = Get-ArtifactRequirement -Requirements $Requirements -RequirementId "public_demo_video"
    if ($null -eq $Video.PSObject.Properties["duration_seconds"] -or $null -eq $Video.duration_seconds) {
      Stop-Preflight "Verified demo video needs a measured duration_seconds value."
    }
    if ([double]$Video.duration_seconds -ge [double]$VideoRequirement.constraints.maximum_seconds_exclusive) {
      Stop-Preflight "Demo video must be shorter than 180 seconds."
    }
    $VideoHost = ([Uri][string]$Video.value).Host.ToLowerInvariant()
    $AllowedVideoHosts = @($VideoRequirement.constraints.rules_named_hosts)
    if (@($AllowedVideoHosts | Where-Object { $VideoHost -eq $_ -or $VideoHost.EndsWith(".$_") }).Count -eq 0) {
      Stop-Preflight "Demo video host '$VideoHost' is not one of the Rules-named hosts."
    }
  }

  if ($RunTests) {
    Write-Host "Running deterministic tests by explicit request..."
    & uv run --frozen pytest tests eval/tests -q
    if ($LASTEXITCODE -ne 0) {
      Stop-Preflight "Deterministic tests failed."
    }
  }

  $PendingCount = @($Artifacts | Where-Object { $_.status -notin @("verified", "verified_local", "not_applicable") }).Count
  Write-Host "Canonical contract SHA-256: $ContractHash"
  Write-Host "Contract age: $([math]::Round($AgeHours, 1)) hours"
  Write-Host "Evidence items not yet verified: $PendingCount"
  if ($ModeKey -eq "ci" -and $PendingCount -gt 0) {
    Write-Host "External evidence is explicitly pending; CI mode does not promote deployment or submission."
  }
  Write-Host "PREFLIGHT_STATUS: PASS ($ModeKey)"
}
finally {
  Set-Location -LiteralPath $OriginalLocation
}
