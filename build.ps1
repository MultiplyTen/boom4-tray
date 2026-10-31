param(
    [string]$Python = '',
    [switch]$InstallDependencies,
    [string]$OutputDirectory = 'dist',
    [string]$WorkDirectory = 'build'
)

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $Python) {
        $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Python environment missing. Run: python -m venv .venv'
    }
    # PyInstaller may replace its own bundle directory. Resolve and check both
    # generated paths before invoking it, keeping them inside this checkout.
    $workspacePrefix = $PSScriptRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $outputPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $OutputDirectory))
    $workPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $WorkDirectory))
    foreach ($generatedPath in @($outputPath, $workPath)) {
        if (-not $generatedPath.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Build output and work directories must stay inside the source checkout.'
        }
    }
    if ($InstallDependencies) {
        & $Python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }

    & $Python -m py_compile boom_tray.py boom_backend.py app_version.py
    if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed.' }
    & $Python boom_tray.py --smoke
    if ($LASTEXITCODE -ne 0) { throw 'UI smoke check failed.' }
    & $Python -m PyInstaller --noconfirm --distpath $outputPath --workpath $workPath (Join-Path $PSScriptRoot 'BOOM4-Tray.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $bundlePath = Join-Path $outputPath 'BOOM4-Tray'
    $executablePath = Join-Path $bundlePath 'BOOM4-Tray.exe'
    if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
        throw 'The packaged executable was not produced.'
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination $bundlePath -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination $bundlePath -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination $bundlePath -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs') -Destination $bundlePath -Recurse -Force
    $checkProcess = Start-Process -FilePath $executablePath -ArgumentList '--smoke' -WindowStyle Hidden -Wait -PassThru
    if ($checkProcess.ExitCode -ne 0) { throw 'Packaged UI smoke check failed.' }
    $archivePath = Join-Path $outputPath 'BOOM4-Tray-Windows-x64.zip'
    Compress-Archive -LiteralPath $bundlePath -DestinationPath $archivePath -Force
    $checksum = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $outputPath 'SHA256SUMS.txt'), "$checksum  BOOM4-Tray-Windows-x64.zip`n", [Text.UTF8Encoding]::new($false))
    Write-Output ('Ready: ' + $executablePath)
}
finally {
    Pop-Location
}

