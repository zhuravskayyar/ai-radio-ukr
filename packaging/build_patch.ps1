$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$innoCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$innoCompiler = $innoCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $innoCompiler) {
    throw "Inno Setup 6 не встановлено. Виконайте: winget install --id JRSoftware.InnoSetup -e"
}

Push-Location $PSScriptRoot
try {
    & $innoCompiler (Join-Path $PSScriptRoot "patch.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Не вдалося зібрати патч Vector Radio."
    }
}
finally {
    Pop-Location
}

$output = Join-Path $projectRoot "dist\Vector_Radio_Patch_1.0.1.exe"
Write-Host "Patch created: $output"
Get-FileHash -LiteralPath $output -Algorithm SHA256
