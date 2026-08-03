# ShortsAI installer — Windows (PowerShell).
#
#   irm https://raw.githubusercontent.com/ashishprajapat0604/short_aiv2/version2/install.ps1 | iex
#
# Clones (or updates) the repo, then hands over to run.py which installs the
# Python deps, ffmpeg and the caption fonts, and starts the web UI.

$ErrorActionPreference = 'Stop'

$Repo   = if ($env:SHORTSAI_REPO)   { $env:SHORTSAI_REPO }   else { 'https://github.com/ashishprajapat0604/short_aiv2.git' }
$Branch = if ($env:SHORTSAI_BRANCH) { $env:SHORTSAI_BRANCH } else { 'version2' }
$Dest   = if ($env:SHORTSAI_DIR)    { $env:SHORTSAI_DIR }    else { Join-Path $HOME 'shortsai' }

function Info($m) { Write-Host "* $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "+ $m" -ForegroundColor Green }
function Die($m)  { Write-Host "x $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  ShortsAI installer" -ForegroundColor White
Write-Host ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is required. Install it with:  winget install --id Git.Git -e"
}

# Find a Python 3.10+ interpreter.
$Py = $null
foreach ($cand in @('python', 'python3', 'py')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        & $cand -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $Py = $cand; break }
    }
}
if (-not $Py) {
    Die "Python 3.10+ is required. Install it with:  winget install --id Python.Python.3.12 -e
    (tick 'Add python.exe to PATH', then open a NEW terminal and re-run)"
}
Ok "Using $(& $Py --version)"

if (Test-Path (Join-Path $Dest '.git')) {
    Info "Updating existing install at $Dest ..."
    git -C $Dest fetch --depth 1 origin $Branch
    git -C $Dest diff --quiet
    $dirty = ($LASTEXITCODE -ne 0)
    git -C $Dest diff --cached --quiet
    if ($dirty -or ($LASTEXITCODE -ne 0)) {
        Die "You have uncommitted changes in $Dest. Commit or stash them first, then re-run."
    }
    git -C $Dest checkout -q $Branch
    git -C $Dest reset --hard -q "origin/$Branch"
    Ok "Updated to the latest $Branch"
} elseif (Test-Path $Dest) {
    Die "$Dest already exists but is not a git checkout. Move it aside, or set `$env:SHORTSAI_DIR to another path."
} else {
    Info "Cloning into $Dest ..."
    git clone --depth 1 -b $Branch $Repo $Dest
    Ok "Cloned"
}

Write-Host ""
Info "Handing over to run.py (installs dependencies, ffmpeg and fonts, then starts) ..."
Write-Host ""
Set-Location $Dest
& $Py run.py --install-ffmpeg
