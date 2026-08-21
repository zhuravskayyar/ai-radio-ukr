param(
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$assetsDirectory = Join-Path $PSScriptRoot "assets"
$iconPath = Join-Path $assetsDirectory "vector-radio.ico"
$launcherPath = Join-Path $assetsDirectory "VectorRadio.exe"
$getPipPath = Join-Path $PSScriptRoot "get-pip.py"
$getPipHash = "FB24E693BAB954209A063D90953621412CCAD4A500905A726286E038F508DDF6"
$ttsSourceDirectory = Join-Path $PSScriptRoot "tts-sources"
$ttsSources = @(
    @{
        Name = "styletts2-inference.zip"
        Url = "https://codeload.github.com/patriotyk/styletts2-inference/zip/105aed29fa1a7698d08d920986890e9bbd03447c"
        Sha256 = "6F42FAAA717F6F34982D46E6A492B14E693D3DBAF2777F2BBC75AA407DC118E6"
    },
    @{
        Name = "ukrainian-word-stress.zip"
        Url = "https://codeload.github.com/patriotyk/ukrainian-word-stress/zip/d5b37ea0abe9711930d9c3b0b13edec5a0675512"
        Sha256 = "DB938BF735F51EEC8EA47A9FEA813D0237808469236DAA52CAA2CC3C5EE4B353"
    },
    @{
        Name = "ipa-uk.zip"
        Url = "https://codeload.github.com/patriotyk/ipa-uk/zip/3faf05ec50fd4880965b6c76f8e479a4f82117f5"
        Sha256 = "247ED1A5AC07C986C9962C4A8B3DE4F079B72B231C8D4679927259824F0ED12D"
    },
    @{
        Name = "ukrainian-accentor.zip"
        Url = "https://codeload.github.com/egorsmkv/ukrainian-accentor/zip/3dff5ecd2ac91e879086c0e2a7f1ce079603c9f1"
        Sha256 = "E65040E31C1CD041ED1FA0EE250D2B6AA498EB3F2AD61EB34B29C82633FAE21D"
    }
)

if (-not $SkipIcon) {
    & (Join-Path $PSScriptRoot "build_icon.ps1")
}

if (-not (Test-Path -LiteralPath $getPipPath)) {
    Write-Host "Downloading official get-pip.py..."
    Invoke-WebRequest -UseBasicParsing "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath
}
$actualGetPipHash = (Get-FileHash -LiteralPath $getPipPath -Algorithm SHA256).Hash
if ($actualGetPipHash -ne $getPipHash) {
    throw "get-pip.py hash mismatch. Expected $getPipHash, got $actualGetPipHash."
}

New-Item -ItemType Directory -Force -Path $ttsSourceDirectory | Out-Null
foreach ($source in $ttsSources) {
    $sourcePath = Join-Path $ttsSourceDirectory $source.Name
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        Write-Host "Downloading pinned TTS source: $($source.Name)..."
        Invoke-WebRequest -UseBasicParsing $source.Url -OutFile $sourcePath
    }
    $actualSourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    if ($actualSourceHash -ne $source.Sha256) {
        throw "$($source.Name) hash mismatch. Expected $($source.Sha256), got $actualSourceHash."
    }
}

$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csharpCompiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $csharpCompiler) {
    throw "Не знайдено компілятор C# у .NET Framework."
}

& $csharpCompiler /nologo /target:winexe /platform:x64 /optimize+ `
    /reference:System.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$iconPath" "/out:$launcherPath" `
    (Join-Path $PSScriptRoot "VectorRadioLauncher.cs")
if ($LASTEXITCODE -ne 0) {
    throw "Не вдалося зібрати VectorRadio.exe."
}

$innoCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$innoCompiler = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $innoCompiler) {
    throw "Inno Setup 6 не встановлено. Виконайте: winget install --id JRSoftware.InnoSetup -e"
}

Push-Location $PSScriptRoot
try {
    & $innoCompiler (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Не вдалося зібрати інсталятор."
    }
}
finally {
    Pop-Location
}

$installerOutput = Join-Path $projectRoot 'dist\Vector_Radio_Setup_1.0.0.12.exe'
$installerHash = Get-FileHash -LiteralPath $installerOutput -Algorithm SHA256
$installerChecksum = "$installerOutput.sha256"
Set-Content -LiteralPath $installerChecksum -Value "$($installerHash.Hash.ToLower())  $([IO.Path]::GetFileName($installerOutput))" -Encoding ascii
Write-Host "Installer created: $installerOutput"
Write-Host "Checksum created: $installerChecksum"
