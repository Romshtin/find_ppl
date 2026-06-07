# find-root.ps1 — поиск корня проекта findppl
# Стратегия (по приоритету):
#   1. Переменная окружения FINDPPL_ROOT (задать в .env или системно)
#   2. Подъём от текущей директории вверх, ищем scripts\telegram\run_harvest.py
#   3. Ошибка с понятным сообщением

function Find-FindpplRoot {
    [CmdletBinding()]
    param()

    # 1) FINDPPL_ROOT из env
    if ($env:FINDPPL_ROOT -and (Test-Path $env:FINDPPL_ROOT)) {
        if (Test-Path (Join-Path $env:FINDPPL_ROOT "scripts\telegram\run_harvest.py")) {
            return (Resolve-Path $env:FINDPPL_ROOT).Path
        }
    }

    # 2) Подъём от CWD вверх
    $current = (Get-Location).Path
    $cursor = $current
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path (Join-Path $cursor "scripts\telegram\run_harvest.py")) {
            return (Resolve-Path $cursor).Path
        }
        $parent = Split-Path -Path $cursor -Parent
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }

    # 3) Не нашли
    Write-Host "[ERR] Корень проекта findppl не найден." -ForegroundColor Red
    Write-Host "      Запустите Claude Code в директории проекта" -ForegroundColor Red
    Write-Host "      или задайте FINDPPL_ROOT в .env проекта (например, FINDPPL_ROOT=D:\СС\IdeaProjects\findppl)" -ForegroundColor Red
    exit 1
}
