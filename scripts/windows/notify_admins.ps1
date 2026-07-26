param([string]$Message = 'BOTWANFA notification')
$ErrorActionPreference = 'SilentlyContinue'
Set-Location (Join-Path $PSScriptRoot '../..')

if (-not (Test-Path -LiteralPath '.env')) { exit 0 }

$EnvMap = @{}
Get-Content -LiteralPath '.env' -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        $EnvMap[$Matches[1]] = $Matches[2]
    }
}

if (-not $EnvMap.ContainsKey('BOT_TOKEN')) { exit 0 }
if (-not $EnvMap.ContainsKey('SUPER_ADMIN_IDS')) { exit 0 }

$Token = $EnvMap['BOT_TOKEN']
$AdminIds = $EnvMap['SUPER_ADMIN_IDS'].Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }

foreach ($AdminId in $AdminIds) {
    try {
        Invoke-RestMethod `
            -Uri ("https://api.telegram.org/bot{0}/sendMessage" -f $Token) `
            -Method Post `
            -Body @{ chat_id = $AdminId; text = $Message } | Out-Null
    } catch {
    }
}
