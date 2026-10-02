# BorderCraft release packer (stub) - run from the repo root:  pwsh tools/package.ps1
# Builds:
#   dist/BorderCraft-BL2-<version>.zip     -> sdk_mods/BorderCraft/ + protocol copy
#   fabric/build/libs/bordercraft-<version>.jar  (built by gradle)
$ErrorActionPreference = "Stop"
$version = "0.1.0"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Write-Host ">> building Fabric jar"
Push-Location (Join-Path $root "fabric")
./gradlew build
Pop-Location

Write-Host ">> packing BL2 sdk_mod"
$stage = Join-Path $dist "BorderCraft-BL2-$version"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -Recurse (Join-Path $root "bl2sdk\BorderCraft") (Join-Path $stage "BorderCraft")
New-Item -ItemType Directory -Force -Path (Join-Path $stage "BorderCraft\protocol") | Out-Null
Copy-Item (Join-Path $root "protocol\python\bordercraft_protocol.py") (Join-Path $stage "BorderCraft\protocol")

Compress-Archive -Path (Join-Path $stage "*") -DestinationPath (Join-Path $dist "BorderCraft-BL2-$version.zip") -Force
Write-Host ">> done: dist\BorderCraft-BL2-$version.zip"
