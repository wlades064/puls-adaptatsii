param(
    [Parameter(Mandatory = $true)]
    [string]$WifiIp,

    [switch]$ImportSample
)

$ErrorActionPreference = 'Stop'
if ($WifiIp -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' -or
    $WifiIp -notmatch '^(\d{1,3}\.){3}\d{1,3}$' -or
    ($WifiIp.Split('.') | Where-Object { [int]$_ -gt 255 })) {
    throw 'Укажите локальный IPv4-адрес ноутбука из вывода ipconfig.'
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $demoPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $demoPython -PathType Leaf)) {
        throw 'Сначала создайте окружение и установите Django по инструкции DEMO-PHONE.md.'
    }

    $env:PULSE_DB = Join-Path $PSScriptRoot 'phone-demo.sqlite3'
    $env:PULSE_DEBUG = '1'
    $env:PULSE_ALLOWED_HOSTS = $WifiIp
    $secretBytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($secretBytes) } finally { $random.Dispose() }
    $env:PULSE_SECRET_KEY = [Convert]::ToBase64String($secretBytes)

    $demoPassword = Read-Host 'Введите новый пароль для демо (от 12 символов)' -AsSecureString
    $env:PULSE_DEMO_PASSWORD = [Net.NetworkCredential]::new('', $demoPassword).Password
    $demoPassword.Dispose()
    if ($env:PULSE_DEMO_PASSWORD.Length -lt 12) { throw 'Введите пароль длиной от 12 символов.' }

    & $demoPython manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Миграции не выполнены.' }
    & $demoPython manage.py seed_demo
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать демоданные.' }
    Remove-Item -LiteralPath 'Env:PULSE_DEMO_PASSWORD'
    if ($ImportSample) {
        & $demoPython manage.py import_bitrix_hire examples/bitrix-hire.json
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось импортировать пример задачи.' }
    }

    Write-Host "На телефоне откройте http://$($WifiIp):8000/ . Ctrl+C останавливает сервер."
    & $demoPython manage.py runserver "$($WifiIp):8000" --noreload
} finally {
    Remove-Item -LiteralPath 'Env:PULSE_DEMO_PASSWORD','Env:PULSE_SECRET_KEY','Env:PULSE_ALLOWED_HOSTS','Env:PULSE_DB','Env:PULSE_DEBUG' -ErrorAction SilentlyContinue
    Pop-Location
}
