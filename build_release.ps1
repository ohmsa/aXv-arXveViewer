$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
$env:RUSTUP_HOME = Join-Path $root ".rustup"
$env:CARGO_HOME = Join-Path $root ".cargo"
$cargo = Join-Path $env:CARGO_HOME "bin\cargo.exe"
if (-not (Test-Path $cargo)) { $cargo = "cargo" }

& $cargo build --release --offline
if ($LASTEXITCODE -ne 0) { throw "Release build failed." }

$metadataJson = & $cargo metadata --offline --format-version 1 --filter-platform x86_64-pc-windows-msvc
if ($LASTEXITCODE -ne 0) { throw "Cargo metadata collection failed." }
$metadata = $metadataJson | ConvertFrom-Json
$package = $metadata.packages | Where-Object name -eq "axv" | Select-Object -First 1
if (-not $package) { throw "aXv package metadata was not found." }
$version = $package.version
$stage = Join-Path $root "release\aXv-$version-windows-x64"
$licenseDir = Join-Path $stage "licenses"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force $licenseDir | Out-Null

Copy-Item (Join-Path $root "target\release\axv.exe") $stage
foreach ($file in @("README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "AI_SETUP.md", "setup_optional_tools.bat", "setup_optional_tools.ps1", "download_ai_tools.ps1", "prepare_ai_folder.bat")) {
    Copy-Item (Join-Path $root $file) $stage
}
Copy-Item (Join-Path $root "LICENSES\*") $licenseDir

$manifest = @("Package`tVersion`tLicense`tRepository")
foreach ($dependency in $metadata.packages | Sort-Object name, version) {
    $manifest += "$($dependency.name)`t$($dependency.version)`t$($dependency.license)`t$($dependency.repository)"
    $manifestPath = Split-Path $dependency.manifest_path
    $safeName = ($dependency.name + "-" + $dependency.version) -replace '[^A-Za-z0-9._-]', '_'
    Get-ChildItem $manifestPath -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|UNLICENSE)' } |
        ForEach-Object { Copy-Item $_.FullName (Join-Path $licenseDir ($safeName + "-" + $_.Name)) -Force }
}
$manifest | Set-Content -Encoding utf8 (Join-Path $stage "THIRD_PARTY_PACKAGES.tsv")

$hash = (Get-FileHash (Join-Path $stage "axv.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  axv.exe" | Set-Content -Encoding ascii (Join-Path $stage "SHA256SUMS.txt")
$archive = "$stage.zip"
if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -LiteralPath $stage -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Created: $archive"
