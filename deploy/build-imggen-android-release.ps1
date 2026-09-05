[CmdletBinding()]
param(
    [Parameter(Mandatory)] [int] $VersionCode,
    [Parameter(Mandatory)] [string] $VersionName,
    [string] $ReleaseNotes = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$mobileRoot = Join-Path $projectRoot 'mobile'
$releaseRoot = Join-Path $mobileRoot 'release\android'
$gradle = 'D:\ANDROID\.gradle\wrapper\dists\gradle-8.14.3-all\cbf6zifq8xavouihta8md72jo\gradle-8.14.3\bin\gradle.bat'

if (-not (Test-Path $gradle)) { throw "Cached Gradle not found: $gradle" }
if (-not $env:EASYAPITOOL_RELEASE_PROPERTIES) { throw 'Set EASYAPITOOL_RELEASE_PROPERTIES to the external release signing file.' }

$buildTimestampMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$env:EASYAPITOOL_ANDROID_VERSION_CODE = [string]$VersionCode
$env:EASYAPITOOL_ANDROID_VERSION_NAME = $VersionName
$env:EASYAPITOOL_ANDROID_BUILD_TIMESTAMP_MS = [string]$buildTimestampMs
$env:GRADLE_USER_HOME = 'D:\ANDROID\.gradle'

Push-Location $mobileRoot
try {
    & node scripts\sync-web.mjs
    & .\node_modules\.bin\cap.cmd sync android
    & $gradle -p android app:assembleRelease --offline --no-daemon
} finally {
    Pop-Location
}

$apk = Join-Path $mobileRoot 'android\app\build\outputs\apk\release\app-release.apk'
if (-not (Test-Path $apk)) { throw "Release APK not found: $apk" }
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$publishedApk = Join-Path $releaseRoot 'easyapitool-mobile.apk'
Copy-Item -Force $apk $publishedApk
$item = Get-Item $publishedApk
$manifest = [ordered]@{
    platform = 'android'
    packageName = 'cn.easyapitool.mobile'
    versionName = $VersionName
    versionCode = $VersionCode
    buildTimestampMs = $buildTimestampMs
    fileName = $item.Name
    mimeType = 'application/vnd.android.package-archive'
    sizeBytes = $item.Length
    sha256 = (Get-FileHash -Algorithm SHA256 $publishedApk).Hash.ToLowerInvariant()
    releaseNotes = $ReleaseNotes
}
$manifestPath = Join-Path $releaseRoot 'manifest.json'
$manifestJson = $manifest | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)
Write-Output "release_directory=$releaseRoot"
Write-Output "version_code=$VersionCode"
Write-Output "build_timestamp_ms=$buildTimestampMs"
Write-Output "apk_sha256=$($manifest.sha256)"