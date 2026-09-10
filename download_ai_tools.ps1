$ErrorActionPreference = "Stop"
Write-Host "This downloads AI tools from official GitHub Releases."
Write-Host "Review each project's license before continuing."
$answer = Read-Host "Type yes to continue"
if ($answer -ne "yes") { exit 2 }

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$destination = Join-Path $projectRoot "ai_upscale"
$temporary = Join-Path $env:TEMP ("axv-ai-" + [guid]::NewGuid())
$headers = @{ "User-Agent" = "aXv-arXveViewer-setup" }
$projects = @(
    @{ repo = "xinntao/Real-ESRGAN"; exe = "realesrgan-ncnn-vulkan.exe"; model = "models" },
    @{ repo = "nihui/realcugan-ncnn-vulkan"; exe = "realcugan-ncnn-vulkan.exe"; model = "models-se" },
    @{ repo = "nihui/waifu2x-ncnn-vulkan"; exe = "waifu2x-ncnn-vulkan.exe"; model = "models-cunet" },
    @{ repo = "nihui/realsr-ncnn-vulkan"; exe = "realsr-ncnn-vulkan.exe"; model = "models-DF2K" },
    @{ repo = "nihui/srmd-ncnn-vulkan"; exe = "srmd-ncnn-vulkan.exe"; model = "models-srmd" }
)

New-Item -ItemType Directory -Force $destination, $temporary | Out-Null
try {
    foreach ($project in $projects) {
        $releases = Invoke-RestMethod -Headers $headers ("https://api.github.com/repos/" + $project.repo + "/releases")
        $asset = $releases.assets | Where-Object { $_.name -match "windows.*\.zip$|win.*\.zip$" } | Select-Object -First 1
        if (-not $asset) { throw "Windows ZIP not found: $($project.repo)" }
        $zipPath = Join-Path $temporary $asset.name
        $expanded = Join-Path $temporary ([IO.Path]::GetFileNameWithoutExtension($asset.name))
        Write-Host "Downloading $($project.repo) ..."
        Invoke-WebRequest -Headers $headers $asset.browser_download_url -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $expanded -Force
        $executable = Get-ChildItem $expanded -Recurse -File -Filter $project.exe | Select-Object -First 1
        if (-not $executable) { throw "Executable not found: $($project.exe)" }
        Copy-Item $executable.FullName (Join-Path $destination $project.exe) -Force
        $models = Get-ChildItem $expanded -Recurse -Directory -Filter $project.model | Select-Object -First 1
        if ($models) { Copy-Item $models.FullName (Join-Path $destination $project.model) -Recurse -Force }
        $runtime = Get-ChildItem $executable.DirectoryName -File -Filter "vcomp140.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($runtime) { Copy-Item $runtime.FullName (Join-Path $destination "vcomp140.dll") -Force }
    }
}
finally {
    if ($temporary.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "AI tool setup completed."
