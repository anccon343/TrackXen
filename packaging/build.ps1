# Build the TrackXen installer:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#   1. the web UI (npm run build)   2. TrackXen.exe with PyInstaller (dist\TrackXen)
#   3. the setup program with Inno Setup 6, if it is installed (installer-output\)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "1/3 web UI"
Push-Location ui
npm run build
if ($LASTEXITCODE) { throw "UI build failed" }
Pop-Location

Write-Host "2/3 TrackXen.exe"
& $python tools\gpu_libs_manifest.py --check  # the NVIDIA download list matches the packages it's built with
if ($LASTEXITCODE) { throw "NVIDIA download list out of date: run tools\gpu_libs_manifest.py" }
& $python -m PyInstaller packaging\trackxen.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller failed" }
$nvidia = Get-ChildItem dist\TrackXen -Recurse -Include "cublas*.dll", "cudnn*.dll", "nvinfer*.dll", "cudart*.dll"
if ($nvidia) { throw "NVIDIA DLLs got into the program (they are downloaded on first start): $($nvidia.Name -join ', ')" }
$size = (Get-ChildItem dist\TrackXen -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("dist\TrackXen: {0:N0} MB" -f ($size / 1MB))

Write-Host "3/3 installer"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup 6 not found: install it (winget install JRSoftware.InnoSetup) and run this again."
    Write-Host "dist\TrackXen already works as a portable copy: start dist\TrackXen\TrackXen.exe."
    exit 0
}
& $iscc packaging\trackxen.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Done: installer-output\"
