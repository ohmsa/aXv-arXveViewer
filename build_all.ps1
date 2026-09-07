param([ValidateSet('fast', 'debug', 'final')][string]$Mode = 'fast')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Invoke-Python([string[]]$Arguments) {
    & $script:Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed ($LASTEXITCODE): $Arguments" }
}

try {
    foreach ($file in @('viewer.py', 'ai_upscale.py', 'tlg_decoder.py', 'requirements-build.txt',
                        'requirements.txt', 'requirements-openvino.txt', 'requirements-directml.txt',
                        'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md')) {
        if (-not (Test-Path -LiteralPath $file)) { throw "Required file is missing: $file" }
    }
    $buildRoot = Join-Path $PSScriptRoot 'build\all'
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    $zipName = 'python-3.11.9-embed-amd64.zip'
    $zip = Join-Path $buildRoot $zipName
    if (-not (Test-Path -LiteralPath $zip)) {
        $existingZip = Join-Path $PSScriptRoot "python_embed\$zipName"
        if (Test-Path -LiteralPath $existingZip) {
            Copy-Item -LiteralPath $existingZip -Destination $zip
        } else {
            Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/$zipName" -OutFile $zip
        }
    }
    $pip = Join-Path $buildRoot 'get-pip.py'
    if (-not (Test-Path -LiteralPath $pip)) {
        $existingPip = Join-Path $PSScriptRoot 'python_embed\get-pip.py'
        if (Test-Path -LiteralPath $existingPip) {
            Copy-Item -LiteralPath $existingPip -Destination $pip
        } else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile $pip
        }
    }
    foreach ($backend in @('original', 'openVINO', 'directML')) {
        Write-Host "=== Building $backend ($Mode) ==="
        $work = Join-Path $buildRoot $backend
        $runtime = Join-Path $work 'python_embed'
        $script:Python = Join-Path $runtime 'python.exe'
        $ready = Join-Path $runtime '.pip-ready'
        if (-not (Test-Path -LiteralPath $ready)) {
            New-Item -ItemType Directory -Force -Path $runtime | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $runtime -Force
            # Embedded Python uses an explicit path instead of an ordinary venv.
            Set-Content -LiteralPath (Join-Path $runtime 'python311._pth') -Encoding ascii -Value @(
                'python311.zip', '.', 'Lib\site-packages', $PSScriptRoot, 'import site')
            Invoke-Python @($pip, '--no-warn-script-location')
            New-Item -ItemType File -Path $ready -Force | Out-Null
        }
        # Fail rather than package a runtime contaminated by another backend.
        $check = @'
import importlib.metadata as m, sys
backend = sys.argv[1].lower()
installed = {d.metadata['Name'].lower().replace('_', '-') for d in m.distributions()}
allowed = {'original': set(), 'openvino': {'openvino'}, 'directml': {'onnxruntime-directml'}}[backend]
conflicts = (installed & {'openvino', 'onnxruntime', 'onnxruntime-gpu', 'onnxruntime-directml', 'onnxruntime-openvino'}) - allowed
if conflicts: raise SystemExit('Conflicting backend packages in dedicated runtime: ' + ', '.join(sorted(conflicts)))
'@
        Invoke-Python @('-c', $check, $backend)
        # These are transitive py7zr extensions. Windows App Control rejects
        # their unsigned .pyd files; Windows builds use the signed OS bsdtar.
        Invoke-Python @('-m', 'pip', 'uninstall', '-y', 'py7zr', 'backports.zstd',
                        'inflate64', 'pybcj', 'pyppmd', 'multivolumefile', 'texttable')
        $install = @('-m', 'pip', 'install', '--no-warn-script-location', '-r', 'requirements-build.txt')
        if ($backend -ne 'original') { $install += @('-r', "requirements-$($backend.ToLowerInvariant()).txt") }
        Invoke-Python $install
        Invoke-Python @('-m', 'pip', 'check')
        Invoke-Python @('-c', $check, $backend)
        if ($backend -eq 'openVINO') { Invoke-Python @('-c', 'import openvino; print(openvino.__version__)') }
        if ($backend -eq 'directML') {
            Invoke-Python @('-c', "import onnxruntime as o; print(o.get_available_providers()); assert 'DmlExecutionProvider' in o.get_available_providers()")
        }
        $destination = Join-Path $PSScriptRoot "dist\aXv_$backend"
        $arguments = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'aXv',
                       '--workpath', (Join-Path $work 'pyinstaller'), '--specpath', $work)
        if ($Mode -eq 'final') {
            $arguments += @('--onefile', '--windowed', '--distpath', $destination)
        } else {
            $arguments += @('--onedir', '--distpath', (Join-Path $work 'dist'))
            if ($Mode -eq 'debug') { $arguments += '--console' } else { $arguments += '--windowed' }
        }
        if ($backend -ne 'openVINO') { $arguments += @('--exclude-module', 'openvino') }
        if ($backend -ne 'directML') { $arguments += @('--exclude-module', 'onnxruntime') }
        $arguments += @('--exclude-module', 'py7zr', '--exclude-module', 'backports.zstd')
        $arguments += 'viewer.py'
        Invoke-Python $arguments
        if ($Mode -ne 'final') {
            New-Item -ItemType Directory -Path $destination -Force | Out-Null
            # Replace only generated libraries; preserve settings and local assets.
            $internal = [IO.Path]::GetFullPath((Join-Path $destination '_internal'))
            $expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "dist\aXv_$backend\_internal"))
            if ($internal -ne $expected) { throw "Unsafe library destination: $internal" }
            if (Test-Path -LiteralPath $internal) { Remove-Item -LiteralPath $internal -Recurse -Force }
            & robocopy (Join-Path $work 'dist\aXv') $destination /E /NFL /NDL /NJH /NJS /NP
            if ($LASTEXITCODE -ge 8) { throw "Copying $backend build failed" }
        }
        foreach ($file in @('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'unrar.exe', 'tlg6_native.dll')) {
            if (Test-Path -LiteralPath $file) { Copy-Item -LiteralPath $file -Destination $destination -Force }
        }
        if (Test-Path -LiteralPath 'ai_upscale') {
            & robocopy (Join-Path $PSScriptRoot 'ai_upscale') (Join-Path $destination 'ai_upscale') /E /NFL /NDL /NJH /NJS /NP
            if ($LASTEXITCODE -ge 8) { throw 'Copying local AI assets failed' }
        }
        $exe = Join-Path $destination 'aXv.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw "Missing EXE: $exe" }
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $PSScriptRoot "aXv_$backend.lnk"))
        $shortcut.TargetPath = $exe
        $shortcut.WorkingDirectory = $destination
        $shortcut.IconLocation = "$exe,0"
        $shortcut.Save()
        Write-Host "Created $exe and aXv_$backend.lnk"
    }
    Write-Host 'All three builds completed.'
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
