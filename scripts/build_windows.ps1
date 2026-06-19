# Windows Build Script for Hobo RPG Save Editor
# Uses Nuitka to compile Python to a C++ binary for improved performance and AV compatibility.

$ErrorActionPreference = "Stop"

$ProjectRoot = Get-Location
$DistDir = Join-Path $ProjectRoot "dist"
$EntryPath = "src/hobo_rpg_save_editor/__main__.py"

Write-Host "Checking for build dependencies..." -ForegroundColor Cyan
python -m pip install -e ".[build]"
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}

Write-Host "Starting Nuitka build process (Single-File EXE)..." -ForegroundColor Cyan
Write-Host "Note: This may take several minutes as it compiles Python to C++." -ForegroundColor Gray

$NuitkaArgs = @(
    "-m",
    "nuitka",
    "--standalone",
    "--onefile",
    "--plugin-enable=tk-inter",
    "--include-package-data=textual",
    "--include-package-data=UnityPy",
    "--output-dir=$DistDir",
    "--output-filename=hobo-rpg-save-editor.exe",
    "--assume-yes-for-downloads"
)

$IconPath = Join-Path $ProjectRoot "research/app.ico"
if (Test-Path $IconPath) {
    $NuitkaArgs += "--windows-icon-from-ico=$IconPath"
}

$NuitkaArgs += $EntryPath

python @NuitkaArgs
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE."
}

Write-Host "`nBuild successful! Executable is located at: dist\hobo-rpg-save-editor.exe" -ForegroundColor Green
