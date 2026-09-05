[CmdletBinding()]
param(
    [string] $Server = 'root@39.105.74.222',
    [string] $ReleaseDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'mobile\release\android')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stage = '/tmp/imggen-deploy'
$requiredReleaseFiles = @('easyapitool-mobile.apk', 'manifest.json')
foreach ($name in $requiredReleaseFiles) {
    if (-not (Test-Path (Join-Path $ReleaseDirectory $name))) {
        throw "Release file is missing: $(Join-Path $ReleaseDirectory $name)"
    }
}

& ssh -o BatchMode=yes $Server "rm -rf $stage && mkdir -p $stage/release"
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the remote deployment stage.' }
& scp -r (Join-Path $projectRoot 'backend') "${Server}:$stage/"
if ($LASTEXITCODE -ne 0) { throw 'Unable to upload backend source.' }
$files = @(
    'assetlinks.json', 'imggen-api.service', 'imggen-api-pair', 'imggen-http.conf', 'imggen-https.conf',
    'imggen-cert-renew-hook.sh', 'imggen-landing.html', 'install-imggen-server.sh'
)
foreach ($name in $files) {
    & scp (Join-Path $PSScriptRoot $name) "${Server}:$stage/$name"
    if ($LASTEXITCODE -ne 0) { throw "Unable to upload $name." }
}
foreach ($name in $requiredReleaseFiles) {
    & scp (Join-Path $ReleaseDirectory $name) "${Server}:$stage/release/$name"
    if ($LASTEXITCODE -ne 0) { throw "Unable to upload release file $name." }
}
& ssh -o BatchMode=yes $Server "find $stage -maxdepth 1 -type f \( -name '*.sh' -o -name 'imggen-api-pair' \) -exec sed -i 's/\r$//' {} +"
if ($LASTEXITCODE -ne 0) { throw 'Unable to normalize remote shell script line endings.' }
& ssh -o BatchMode=yes $Server "bash $stage/install-imggen-server.sh"
if ($LASTEXITCODE -ne 0) { throw 'Remote IMGGEN deployment failed.' }