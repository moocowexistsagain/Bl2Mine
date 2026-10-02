# Full release packer. The Borderlands package can be built without a JDK; the Fabric jar needs JDK 21.
# Run from anywhere with: pwsh tools/package.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Write-Host ">> packing Borderlands 2 SDK mod"
$python = Get-Command python3 -ErrorAction SilentlyContinue
if ($null -eq $python) { $python = Get-Command python -ErrorAction Stop }
& $python.Source (Join-Path $root "tools\package_bl2.py") --output-dir $dist
if ($LASTEXITCODE -ne 0) { throw "BL2 SDK package step failed" }

Write-Host ">> building Fabric jar"
Push-Location (Join-Path $root "fabric")
try {
    if (Test-Path ".\gradlew.bat") {
        & .\gradlew.bat build
    } else {
        & ./gradlew build
    }
    if ($LASTEXITCODE -ne 0) { throw "Fabric build failed" }
} finally {
    Pop-Location
}

$jar = Join-Path $root "fabric\build\libs\bordercraft-0.1.0.jar"
if (Test-Path $jar) {
    Copy-Item -Force $jar (Join-Path $dist "bordercraft-0.1.0.jar")
}
Write-Host ">> release files written to dist"
