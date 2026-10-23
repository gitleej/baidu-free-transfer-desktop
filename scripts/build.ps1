param(
    [string]$PythonExe = "python",
    [string]$OutputDirectory = "release",
    [switch]$SkipEnvironmentSetup
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildEnvironment = Join-Path $ProjectRoot ".build-venv"
$BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"

Push-Location $ProjectRoot
try {
    if (-not $SkipEnvironmentSetup) {
        if (-not (Test-Path -LiteralPath $BuildPython)) {
            & $PythonExe -m venv $BuildEnvironment
        }
        & $BuildPython -m pip install --upgrade pip
        & $BuildPython -m pip install -r requirements.txt -r requirements-build.txt
    }
    if (-not (Test-Path -LiteralPath $BuildPython)) {
        throw "Build environment is missing. Run without -SkipEnvironmentSetup first."
    }

    & $BuildPython scripts\make_icon.py assets\app.ico
    & $BuildPython -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name BaiduFreeTransfer `
        --icon assets\app.ico `
        --add-data "assets\app.png;assets" `
        app_main.py

    $ResolvedOutput = Join-Path $ProjectRoot $OutputDirectory
    New-Item -ItemType Directory -Path $ResolvedOutput -Force | Out-Null
    $Artifact = Join-Path $ResolvedOutput "BaiduFreeTransfer.exe"
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "dist\BaiduFreeTransfer.exe") -Destination $Artifact -Force
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact
    "$($Hash.Hash.ToLower())  BaiduFreeTransfer.exe" | Set-Content -LiteralPath (Join-Path $ResolvedOutput "SHA256SUMS.txt") -Encoding ascii
    Write-Host "Built $Artifact"
    Write-Host "SHA256 $($Hash.Hash.ToLower())"
}
finally {
    Pop-Location
}
