# Windows Build Script for Hobo RPG Save Editor
# Uses Nuitka to compile Python to a C++ binary for improved performance and AV compatibility.

$ProjectRoot = Get-Location
$DistDir = Join-Path $ProjectRoot "dist"
$EntryPath = "src/hobo_rpg_save_editor/__main__.py"

Write-Host "Checking for build dependencies..." -ForegroundColor Cyan
python -m pip install -e ".[build]"

if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}

Write-Host "Starting Nuitka build process (Single-File EXE)..." -ForegroundColor Cyan
Write-Host "Note: This may take several minutes as it compiles Python to C++." -ForegroundColor Gray

python -m nuitka `
    --standalone `
    --onefile `
    --plugin-enable=tk-inter `
    --include-package-data=textual `
    --include-package-data=UnityPy `
    --output-dir="dist" `
    --output-filename="hobo-rpg-save-editor.exe" `
    --windows-icon-from-ico=research/app.ico ` # Placeholder if you add an icon later
    --assume-yes-for-downloads `
    $EntryPath

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nBuild successful! Executable is located at: dist\hobo-rpg-save-editor.exe" -ForegroundColor Green
} else {
    Write-Error "Nuitka build failed with exit code $LASTEXITCODE."
}
