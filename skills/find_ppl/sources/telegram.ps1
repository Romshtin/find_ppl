# sources/telegram.ps1 — разведка по Telegram-каналам.
#
# Что делает:
#   1. Поднимает Shadowsocks-мост (ss-local.exe) на 127.0.0.1:1080
#   2. Ждёт готовности порта
#   3. Запускает harvest + filter с подставленной стратегией
#   4. Гасит мост по PID в finally-блоке
#
# Параметры:
#   -ProjectRoot    — корень проекта findppl (обязателен)
#   -Strategy       — имя стратегии (без .json), дефолт individual_rel
#   -ForceFull      — игнорировать seen, пересобрать raw целиком (по умолч. выкл.)

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [string]$Strategy = "individual_rel",

    [switch]$ForceFull
)

$ErrorActionPreference = "Stop"

# --- Стратегия ---
$StrategyFile = Join-Path $ProjectRoot "data\strategies\$Strategy.json"
if (-not (Test-Path $StrategyFile)) {
    Write-Host "[ERR] Стратегия '$Strategy' не найдена: $StrategyFile" -ForegroundColor Red
    Write-Host "      Доступные стратегии:" -ForegroundColor Red
    $dir = Join-Path $ProjectRoot "data\strategies"
    if (Test-Path $dir) {
        Get-ChildItem $dir -Filter "*.json" | ForEach-Object { Write-Host "        - $($_.BaseName)" -ForegroundColor Red }
    } else {
        Write-Host "        (папка data\strategies не существует)" -ForegroundColor Red
    }
    exit 1
}
Write-Host "[STRATEGY] $Strategy -> $StrategyFile" -ForegroundColor Cyan

# --- Пути к ss-local ---
$SsLocalExe = if ($env:SSLOCAL_EXE) { $env:SSLOCAL_EXE } else { "C:\Tools\ss-local\sslocal.exe" }
$SsConfig   = if ($env:SSLOCAL_CONFIG) { $env:SSLOCAL_CONFIG } else { "C:\Tools\ss-local\ss-config.json" }

if (-not (Test-Path $SsLocalExe)) {
    Write-Host "[ERR] ss-local не найден: $SsLocalExe" -ForegroundColor Red
    Write-Host "      Укажите SSLOCAL_EXE в .env или установите по этому пути." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $SsConfig)) {
    Write-Host "[ERR] Конфиг ss-local не найден: $SsConfig" -ForegroundColor Red
    exit 1
}

# --- Поднимаем мост ---
Write-Host "[BRIDGE] Запускаю ss-local..." -ForegroundColor Cyan
$SsProcess = Start-Process -FilePath $SsLocalExe `
    -ArgumentList "-c", "`"$SsConfig`"" `
    -PassThru -WindowStyle Hidden
Write-Host "[BRIDGE] ss-local PID = $($SsProcess.Id)"

function Stop-Bridge {
    if ($SsProcess -and -not $SsProcess.HasExited) {
        Write-Host "[BRIDGE] Гашу ss-local (PID $($SsProcess.Id))..." -ForegroundColor Yellow
        try {
            Stop-Process -Id $SsProcess.Id -Force -ErrorAction Stop
        } catch {
            Write-Host "[WARN] Не удалось убить PID $($SsProcess.Id): $_" -ForegroundColor Yellow
        }
    }
}

# --- Ждём готовности SOCKS5 (макс 10 сек) ---
Write-Host "[BRIDGE] Жду готовности 127.0.0.1:1080..."
$BridgeReady = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port 1080 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($tcp) {
        $BridgeReady = $true
        break
    }
}
if (-not $BridgeReady) {
    Write-Host "[ERR] Мост не поднялся за 10 секунд. Проверьте конфиг ss-local." -ForegroundColor Red
    Stop-Bridge
    exit 1
}
Write-Host "[BRIDGE] OK, мост работает." -ForegroundColor Green

# --- Основной цикл: harvest + filter ---
try {
    Push-Location $ProjectRoot
    Write-Host ""
    Write-Host "[HARVEST] === Сбор каналов (стратегия: $Strategy) ===" -ForegroundColor Cyan
    if ($ForceFull) {
        & py -m scripts.telegram.run_harvest harvest --strategy $Strategy --force-full
    } else {
        & py -m scripts.telegram.run_harvest harvest --strategy $Strategy
    }
    if ($LASTEXITCODE -ne 0) {
        throw "harvest завершился с кодом $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "[FILTER] === Фильтрация по маркерам ===" -ForegroundColor Cyan
    & py -m scripts.telegram.run_harvest filter --strategy $Strategy
    if ($LASTEXITCODE -ne 0) {
        throw "filter завершился с кодом $LASTEXITCODE"
    }
}
catch {
    Write-Host "[ERR] Ошибка в основном цикле: $_" -ForegroundColor Red
    Stop-Bridge
    Pop-Location
    exit 1
}
finally {
    Pop-Location
    Stop-Bridge
}

Write-Host ""
Write-Host "[DONE] Готово. Результаты: data\telegram_harvest\filtered\" -ForegroundColor Green
