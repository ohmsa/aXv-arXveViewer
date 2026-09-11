$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path

function Open-OfficialPage([string]$Url) {
    Write-Host "Opening official page: $Url"
    Start-Process $Url
}

Write-Host "aXv optional tool setup"
Write-Host "External tools are downloaded from or obtained through their official distributors."
Write-Host "1: Download supported ncnn Vulkan AI tools automatically"
Write-Host "2: Open the official UnRAR download page"
Write-Host "3: Open the official 7-Zip download page"
Write-Host "4: Check files already installed"
Write-Host "Q: Quit"

while ($true) {
    $choice = (Read-Host "Select").Trim().ToUpperInvariant()
    switch ($choice) {
        "1" {
            & (Join-Path $root "download_ai_tools.ps1")
            & (Join-Path $root "prepare_ai_folder.bat")
        }
        "2" {
            Write-Host "UnRAR must be obtained by the user under the RARLAB license."
            Write-Host "After downloading/extracting it, place unrar.exe here:"
            Write-Host (Join-Path $root "unrar.exe")
            Open-OfficialPage "https://www.rarlab.com/download.htm"
        }
        "3" {
            Write-Host "Install 7-Zip normally, or place 7z.exe beside axv.exe."
            Open-OfficialPage "https://www.7-zip.org/download.html"
        }
        "4" {
            & (Join-Path $root "prepare_ai_folder.bat")
            $unrar = Join-Path $root "unrar.exe"
            if (Test-Path $unrar) { Write-Host "[OK] unrar.exe" } else { Write-Host "[--] unrar.exe (RAR viewing unavailable)" }
            Write-Host "[OK] TLG6 decoder is built into axv.exe"
        }
        "Q" { exit 0 }
        default { Write-Host "Enter 1, 2, 3, 4, or Q." }
    }
}
