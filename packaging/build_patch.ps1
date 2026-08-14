$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$iconPath = Join-Path $PSScriptRoot "assets\vector-radio.ico"
$launcherPath = Join-Path $PSScriptRoot "assets\VectorRadio.exe"

& (Join-Path $PSScriptRoot "build_icon.ps1")

$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csharpCompiler = $compilerCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $csharpCompiler) {
    throw "Не знайдено компілятор C# у .NET Framework."
}

& $csharpCompiler /nologo /target:winexe /platform:x64 /optimize+ `
    /reference:System.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$iconPath" "/out:$launcherPath" `
    (Join-Path $PSScriptRoot "VectorRadioLauncher.cs")
if ($LASTEXITCODE -ne 0) {
    throw "Не вдалося зібрати VectorRadio.exe для патча."
}

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

$output = Join-Path $projectRoot "dist\Vector_Radio_Patch_1.0.0.6.exe"
$checksumOutput = "$output.sha256"
Write-Host "Patch created: $output"
$hash = Get-FileHash -LiteralPath $output -Algorithm SHA256
Set-Content -LiteralPath $checksumOutput -Value "$($hash.Hash.ToLower())  $([IO.Path]::GetFileName($output))" -Encoding ascii
Write-Host "Checksum created: $checksumOutput"
$hash
