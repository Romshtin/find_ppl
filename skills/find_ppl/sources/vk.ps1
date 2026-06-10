# sources/vk.ps1 — разведка по VK-группам через VK API.
#
# Что делает:
#   1. Запускает harvest + filter через py -m scripts.vk.run_harvest
#   2. НЕ поднимает Shadowsocks-мост (VK работает напрямую, без прокси)
#   3. Использует токены из data/vk_session.json (VK ID SDK, refresh_token)
#      + data/vk_user_token.txt (для wall.getComments — обходить error 1051)
#      Если data/vk_session.json нет — fallback на user_token
#
# Параметры:
#   -ProjectRoot    — корень проекта findppl (обязателен)
#   -Strategy       — имя стратегии, дефолт individual_rel
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
    exit 1
}
Write-Host "[STRATEGY] $Strategy -> $StrategyFile" -ForegroundColor Cyan

# --- Проверка токена VK ---
$TokenFile = Join-Path $ProjectRoot "data\vk_user_token.txt"
if (-not (Test-Path $TokenFile)) {
    Write-Host "[ERR] Файл с токеном не найден: $TokenFile" -ForegroundColor Red
    Write-Host "      Положите пользовательский VK-токен в data\vk_user_token.txt" -ForegroundColor Red
    exit 1
}
Write-Host "[VK] Token file: $TokenFile" -ForegroundColor Cyan

# --- Основной цикл: harvest + filter ---
try {
    Push-Location $ProjectRoot

    Write-Host ""
    Write-Host "[HARVEST] === Сбор VK-групп (стратегия: $Strategy) ===" -ForegroundColor Cyan
    if ($ForceFull) {
        & py -m scripts.vk.run_harvest harvest --strategy $Strategy --force-full
    } else {
        & py -m scripts.vk.run_harvest harvest --strategy $Strategy
    }
    if ($LASTEXITCODE -ne 0) {
        throw "harvest завершился с кодом $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "[FILTER] === Фильтрация по маркерам ===" -ForegroundColor Cyan
    & py -m scripts.vk.run_harvest filter --strategy $Strategy
    if ($LASTEXITCODE -ne 0) {
        throw "filter завершился с кодом $LASTEXITCODE"
    }
}
catch {
    Write-Host "[ERR] Ошибка в основном цикле: $_" -ForegroundColor Red
    Pop-Location
    exit 1
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "[DONE] Готово. Результаты: data\vk_harvest\filtered\" -ForegroundColor Green
