<#
.SYNOPSIS
  Independent Windows installed-wheel consumer proof for real Runplane.
.DESCRIPTION
  Builds TWO wheels from pinned repository commits using read-only detached
  temporary worktrees; installs them in a NEW isolated venv with --no-deps
  (deliberate override of Runplane's historical Invariant Git SHA), copies
  the Python probe OUTSIDE BOTH source trees, invokes python -I, and
  removes only temporary worktrees afterwards. Both checkouts untouched.
  Requires authenticated access to two PRIVATE local Git repositories and
  pip/build dependencies. Does not perform desktop/process/network effects.
#>
[CmdletBinding()]
param(
    [string]$InvariantRepo = "W:\dev\invariant-runtime",
    [string]$RunplaneRepo = "",
    [string]$InvariantRef = "0f81d09f517390fc2f0ae0a6ccce55eb56316763",
    [string]$RunplaneRef = "8385b348b54901103feab887f453a0ae36daa71f",
    [string]$ProofRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "NATIVE_COMMAND_FAILED exit=$LASTEXITCODE executable=$Executable args=$($Arguments -join ' ')"
    }
}

if (-not $RunplaneRepo) {
    foreach ($candidate in @("W:\dev\hde\runplane", "W:\dev\runplane")) {
        if (Test-Path (Join-Path $candidate ".git")) {
            $RunplaneRepo = $candidate
            break
        }
    }
}
if (-not $RunplaneRepo) {
    throw "Provide -RunplaneRepo pointing to your existing authenticated Runplane checkout."
}
if (-not (Test-Path (Join-Path $InvariantRepo ".git"))) {
    throw "Invariant checkout not found: $InvariantRepo"
}
if (-not (Test-Path (Join-Path $RunplaneRepo ".git"))) {
    throw "Runplane checkout not found: $RunplaneRepo"
}

$InvariantRepo = (Resolve-Path $InvariantRepo).Path
$RunplaneRepo = (Resolve-Path $RunplaneRepo).Path
if (-not $ProofRoot) {
    $ProofRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
        "invariant-runplane-wheel-" + [guid]::NewGuid().ToString("N")
    )
}
if (Test-Path $ProofRoot) {
    throw "ProofRoot must be new; refusing to overwrite: $ProofRoot"
}
New-Item -ItemType Directory -Path $ProofRoot | Out-Null
$ProofRoot = (Resolve-Path $ProofRoot).Path

$InvariantTree = Join-Path $ProofRoot "invariant-source"
$RunplaneTree = Join-Path $ProofRoot "runplane-source"
$Wheelhouse = Join-Path $ProofRoot "wheelhouse"
$Consumer = Join-Path $ProofRoot "consumer-venv"
$Probe = Join-Path $ProofRoot "external_probe.py"
New-Item -ItemType Directory -Path $Wheelhouse | Out-Null
$BootstrapPython = (Get-Command python -ErrorAction Stop).Source
$InvariantAdded = $false
$RunplaneAdded = $false

try {
    Invoke-Checked $BootstrapPython @("-c",
        "import sys; assert sys.version_info >= (3, 11), sys.version")
    # The PROBE is authored on the research branch, not on the older
    # Invariant runtime commit being packaged. Pin its Git object before
    # subsequent fetches overwrite FETCH_HEAD; do not use a mutable
    # checkout or expect it to appear in the candidate wheel worktree.
    Invoke-Checked "git" @("-C", $InvariantRepo, "fetch", "origin",
        "research/runplane-installed-wheel-consumer-2026-10-15")
    $ProbeCommit = (& git -C $InvariantRepo rev-parse FETCH_HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $ProbeCommit -notmatch '^[0-9a-f]{40}    Invoke-Checked "git" @("-C", $RunplaneRepo, "fetch", "origin", "main")
    Invoke-Checked "git" @("-C", $InvariantRepo, "cat-file", "-e",
        "$InvariantRef^{commit}")
    Invoke-Checked "git" @("-C", $RunplaneRepo, "cat-file", "-e",
        "$RunplaneRef^{commit}")

    Invoke-Checked "git" @("-C", $InvariantRepo, "worktree", "add",
        "--detach", $InvariantTree, $InvariantRef)
    $InvariantAdded = $true
    Invoke-Checked "git" @("-C", $RunplaneRepo, "worktree", "add",
        "--detach", $RunplaneTree, $RunplaneRef)
    $RunplaneAdded = $true

    Invoke-Checked $BootstrapPython @("-m", "venv", $Consumer)
    $ProofPython = Join-Path $Consumer "Scripts\python.exe"
    if (-not (Test-Path $ProofPython)) {
        throw "Expected isolated Windows venv python not found: $ProofPython"
    }
    # Fresh wheel builds; --no-deps avoids silently fetching the OLD
    # pinned Invariant during Runplane wheel construction.
    Invoke-Checked $ProofPython @("-m", "pip", "wheel", "--no-deps",
        "--wheel-dir", $Wheelhouse, $InvariantTree)
    Invoke-Checked $ProofPython @("-m", "pip", "wheel", "--no-deps",
        "--wheel-dir", $Wheelhouse, $RunplaneTree)

    $InvariantWheels = @(Get-ChildItem $Wheelhouse -Filter "invariant_runtime-*.whl")
    $RunplaneWheels = @(Get-ChildItem $Wheelhouse -Filter "runplane-*.whl")
    if ($InvariantWheels.Count -ne 1 -or $RunplaneWheels.Count -ne 1) {
        throw "Exactly one wheel for each distribution is required."
    }
    # Intentional, transparent candidate override. Runplane's declared
    # Git revision remains unchanged and pip check is NOT used as a pass gate.
    Invoke-Checked $ProofPython @("-m", "pip", "install", "--no-deps",
        $InvariantWheels[0].FullName, $RunplaneWheels[0].FullName)

    # Extract the verified research probe from its pinned Git object,
    # rather than from the packaged Invariant commit (where it does not exist).
    # PowerShell's Set-Content emits UTF-8 source readable by python -I.
    $ProbeSource = & git -C $InvariantRepo show $ProbeObject
    if ($LASTEXITCODE -ne 0 -or -not $ProbeSource) {
        throw "PROBE_EXTRACTION_FAILED: $ProbeObject"
    }
    $ProbeSource | Set-Content -LiteralPath $Probe -Encoding UTF8
    if (-not (Test-Path -LiteralPath $Probe)) {
        throw "PROBE_FILE_NOT_CREATED: $Probe"
    }

    Push-Location $ProofRoot
    try {
        Invoke-Checked $ProofPython @("-I", $Probe)
    }
    finally {
        Pop-Location
    }
    Write-Host "INVARIANT_RUNPLANE_INSTALLED_WHEEL_GATE_OK"
    Write-Host "INVARIANT_RUNPLANE_PROOF_ROOT=$ProofRoot"
}
finally {
    # Only remove worktrees created here; retain wheelhouse and consumer
    # environment as inspectable evidence in the unique proof root.
    if ($RunplaneAdded) {
        & git -C $RunplaneRepo worktree remove --force $RunplaneTree
        if ($LASTEXITCODE -ne 0) { Write-Warning "Runplane worktree cleanup failed: $RunplaneTree" }
    }
    if ($InvariantAdded) {
        & git -C $InvariantRepo worktree remove --force $InvariantTree
        if ($LASTEXITCODE -ne 0) { Write-Warning "Invariant worktree cleanup failed: $InvariantTree" }
    }
}
) {
        throw "PROBE_COMMIT_NOT_RESOLVED"
    }
    $ProbeObject = "${ProbeCommit}:tools/runplane_installed_compat_probe.py"
    Invoke-Checked "git" @("-C", $InvariantRepo, "cat-file", "-e", $ProbeObject)
    Write-Host "INVARIANT_RUNPLANE_PROBE_COMMIT=$ProbeCommit"

    # Fetch pinned refs; never check out or update the original working tree.
    Invoke-Checked "git" @("-C", $InvariantRepo, "fetch", "origin",
        "fix/runtime-host-close-lifecycle-2026-10-14")
    Invoke-Checked "git" @("-C", $RunplaneRepo, "fetch", "origin", "main")
    Invoke-Checked "git" @("-C", $InvariantRepo, "cat-file", "-e",
        "$InvariantRef^{commit}")
    Invoke-Checked "git" @("-C", $RunplaneRepo, "cat-file", "-e",
        "$RunplaneRef^{commit}")

    Invoke-Checked "git" @("-C", $InvariantRepo, "worktree", "add",
        "--detach", $InvariantTree, $InvariantRef)
    $InvariantAdded = $true
    Invoke-Checked "git" @("-C", $RunplaneRepo, "worktree", "add",
        "--detach", $RunplaneTree, $RunplaneRef)
    $RunplaneAdded = $true

    Invoke-Checked $BootstrapPython @("-m", "venv", $Consumer)
    $ProofPython = Join-Path $Consumer "Scripts\python.exe"
    if (-not (Test-Path $ProofPython)) {
        throw "Expected isolated Windows venv python not found: $ProofPython"
    }
    # Fresh wheel builds; --no-deps avoids silently fetching the OLD
    # pinned Invariant during Runplane wheel construction.
    Invoke-Checked $ProofPython @("-m", "pip", "wheel", "--no-deps",
        "--wheel-dir", $Wheelhouse, $InvariantTree)
    Invoke-Checked $ProofPython @("-m", "pip", "wheel", "--no-deps",
        "--wheel-dir", $Wheelhouse, $RunplaneTree)

    $InvariantWheels = @(Get-ChildItem $Wheelhouse -Filter "invariant_runtime-*.whl")
    $RunplaneWheels = @(Get-ChildItem $Wheelhouse -Filter "runplane-*.whl")
    if ($InvariantWheels.Count -ne 1 -or $RunplaneWheels.Count -ne 1) {
        throw "Exactly one wheel for each distribution is required."
    }
    # Intentional, transparent candidate override. Runplane's declared
    # Git revision remains unchanged and pip check is NOT used as a pass gate.
    Invoke-Checked $ProofPython @("-m", "pip", "install", "--no-deps",
        $InvariantWheels[0].FullName, $RunplaneWheels[0].FullName)

    $SourceProbe = Join-Path $InvariantTree "tools\runplane_installed_compat_probe.py"
    Copy-Item -LiteralPath $SourceProbe -Destination $Probe

    Push-Location $ProofRoot
    try {
        Invoke-Checked $ProofPython @("-I", $Probe)
    }
    finally {
        Pop-Location
    }
    Write-Host "INVARIANT_RUNPLANE_INSTALLED_WHEEL_GATE_OK"
    Write-Host "INVARIANT_RUNPLANE_PROOF_ROOT=$ProofRoot"
}
finally {
    # Only remove worktrees created here; retain wheelhouse and consumer
    # environment as inspectable evidence in the unique proof root.
    if ($RunplaneAdded) {
        & git -C $RunplaneRepo worktree remove --force $RunplaneTree
        if ($LASTEXITCODE -ne 0) { Write-Warning "Runplane worktree cleanup failed: $RunplaneTree" }
    }
    if ($InvariantAdded) {
        & git -C $InvariantRepo worktree remove --force $InvariantTree
        if ($LASTEXITCODE -ne 0) { Write-Warning "Invariant worktree cleanup failed: $InvariantTree" }
    }
}
