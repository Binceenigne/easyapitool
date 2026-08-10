$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$MainHtml = Join-Path $ProjectRoot 'frontend\index.html'
$InitializeHtml = Join-Path $ProjectRoot 'frontend\initialize.html'
$Changelog = Join-Path $ProjectRoot 'CHANGELOG.md'
$AppScss = Join-Path $ProjectRoot 'frontend\styles\app.scss'
$AppCss = Join-Path $ProjectRoot 'frontend\styles\app.css'
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue

if (-not (Test-Path $Python)) {
    throw 'Missing .venv. Create a Python 3.12 virtual environment and install requirements.txt.'
}

if (-not (Test-Path $MainHtml) -or -not (Test-Path $InitializeHtml) -or -not (Test-Path $Changelog) -or -not (Test-Path $AppScss)) {
    throw 'Frontend entry, initialization page, CHANGELOG.md, or frontend\styles\app.scss not found.'
}

if (-not $Npm) {
    throw 'npm.cmd is required to compile frontend styles with Dart Sass.'
}

Push-Location $ProjectRoot
try {
    $SassCommand = Join-Path $ProjectRoot 'node_modules\.bin\sass.cmd'
    if (-not (Test-Path $SassCommand)) {
        & $Npm.Source ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            throw 'Dart Sass dependency installation failed.'
        }
    }
    & $Npm.Source run build:css
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $AppCss)) {
        throw 'SCSS compilation failed.'
    }
}
finally {
    Pop-Location
}

$LegacyDist = Join-Path $ProjectRoot 'dist\API_TOOLS'
if (Test-Path $LegacyDist) {
    Remove-Item -LiteralPath $LegacyDist -Recurse -Force
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    (Join-Path $ProjectRoot 'API_TOOLS.spec')

if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$Executable = Join-Path $ProjectRoot 'dist\API_TOOLS.exe'
$ChecksumFile = "$Executable.sha256"
$Checksum = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $ChecksumFile -Value "$Checksum  API_TOOLS.exe" -Encoding ascii

Write-Host "Portable build complete: $Executable"
Write-Host "SHA-256: $ChecksumFile"
