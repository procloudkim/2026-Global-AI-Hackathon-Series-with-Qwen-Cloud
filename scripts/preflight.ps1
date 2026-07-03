param(
  [string]$RepoRoot = "."
)

$ErrorActionPreference = "Stop"
Set-Location $RepoRoot

Write-Host "== Librarian preflight check =="

$required = @(
  "README.md",
  "LICENSE",
  "aidlc-docs\\construction\\architecture.md",
  "deploy\\setup.sh",
  "deploy\\deploy.sh",
  "src\\librarian\\llm.py"
)

foreach ($f in $required) {
  if (-not (Test-Path $f)) {
    throw "Missing required file: $f"
  }
}
Write-Host "Required files: OK"

Write-Host "Running tests..."
uv run pytest -q

Write-Host "Git status (must be clean before final submission):"
git --no-pager status --short

Write-Host ""
Write-Host "Reminder:"
Write-Host "1) Upload 3-min demo video URL to Devpost"
Write-Host "2) Add architecture diagram image export"
Write-Host "3) Verify MIT shows in GitHub About section"
Write-Host "4) Include Alibaba deployment proof link (deploy/* + src/librarian/llm.py)"
Write-Host "== preflight done =="

