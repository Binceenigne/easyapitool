$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$apkPath = Join-Path $projectRoot 'mobile\android\app\build\outputs\apk\release\app-release.apk'
$androidSdkRoot = $env:ANDROID_SDK_ROOT
if ([string]::IsNullOrWhiteSpace($androidSdkRoot)) {
    $androidSdkRoot = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
}
$adbPath = Join-Path $androidSdkRoot 'platform-tools\adb.exe'

if (-not (Test-Path $apkPath)) {
    throw "Release APK not found: $apkPath"
}
if (-not (Test-Path $adbPath)) {
    throw "adb not found: $adbPath"
}

$devices = & $adbPath devices | Select-String '^\S+\s+device$'
if (-not $devices) {
    throw 'No authorized Android device is connected. Enable USB debugging and authorize this computer.'
}

& $adbPath install -r $apkPath
& $adbPath shell am start -W -n cn.easyapitool.mobile/.MainActivity
& $adbPath shell pm get-app-links cn.easyapitool.mobile
& $adbPath shell am start -W -a android.intent.action.VIEW -d 'https://imggen.djyx.me/v1/app'