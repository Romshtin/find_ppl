# find_ppl — единая точка входа.
#
# Использование:
#   powershell -File C:\Users\kiril\.claude\skills\find_ppl\run.ps1
#   powershell -File C:\Users\kiril\.claude\skills\find_ppl\run.ps1 -Source telegram -Strategy individual_rel
#   powershell -File C:\Users\kiril\.claude\skills\find_ppl\run.ps1 -Source both -Strategy individual_rel
#   powershell -File C:\Users\kiril\.claude\skills\find_ppl\run.ps1 -Source telegram,vk -Strategy individual_rel
#   /find_ppl                                # в Claude Code (с дефолтами)
#   /find_ppl -strategy unusual_places       # другая стратегия
#
# Что делает:
#   1. Загружает .env проекта
#   2. Находит корень проекта findppl (FINDPPL_ROOT → подъём по дереву)
#   3. Для каждого источника в -Source делегирует работу sources\<s>.ps1
#   4. Источники выполняются параллельно через PowerShell Start-Job
#
# Поддерживаемые источники:
#   telegram — Telethon через Shadowsocks-мост (С ОТКЛЮЧЁННЫМ ТУННЕЛЕМ)
#   vk       — VK API напрямую (без моста), использует VK ID session + user_token
#   both     — алиас для "telegram,vk" (для удобства)
#   forums   — зарезервировано, ещё не реализовано (выдаст понятную ошибку)

[CmdletBinding()]
param(
    [string[]]$Source = @("telegram"),
    [string]$Strategy = "individual_rel",
    [switch]$ForceFull
)

$ErrorActionPreference = "Stop"

# --- Подключаем библиотеки ---
$SkillDir = $PSScriptRoot
. (Join-Path $SkillDir "lib\find-root.ps1")
. (Join-Path $SkillDir "lib\load-env.ps1")

# --- Находим корень проекта ---
$ProjectRoot = Find-FindpplRoot
Write-Host "[ROOT] $ProjectRoot" -ForegroundColor Cyan

# --- Загружаем .env ---
Import-FindpplEnv -ProjectRoot $ProjectRoot

# --- Разворачиваем алиасы и нормализуем список источников ---
$SourcesRaw = $Source -join ","                       # принимаем и массив, и "a,b"
$SourcesList = $SourcesRaw -split "," | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }

# Алиас "both" = "telegram,vk"
if ($SourcesList -contains "both") {
    $SourcesList = @("telegram", "vk") + ($SourcesList | Where-Object { $_ -ne "both" })
    $SourcesList = $SourcesList | Select-Object -Unique
}

# Алиас "all" = всё, что реализовано
if ($SourcesList -contains "all") {
    $Implemented = Get-ChildItem (Join-Path $SkillDir "sources") -Filter "*.ps1" |
        ForEach-Object { $_.BaseName.ToLower() } | Where-Object { $_ -ne "both" -and $_ -ne "all" }
    $SourcesList = $Implemented + ($SourcesList | Where-Object { $_ -ne "all" })
    $SourcesList = $SourcesList | Select-Object -Unique
}

if (-not $SourcesList) {
    Write-Host "[ERR] Не указан ни один источник в -Source" -ForegroundColor Red
    exit 1
}

Write-Host "[STRATEGY] $Strategy" -ForegroundColor Cyan
Write-Host "[SOURCES] $($SourcesList -join ', ')" -ForegroundColor Cyan
Write-Host ""

# --- Делегируем каждому источнику (параллельно через Start-Job) ---
Write-Host "[PARALLEL] запущено $($SourcesList.Count) источник(ов): $($SourcesList -join ', ')" -ForegroundColor Cyan

# 1. Пре-валидация путей ДО запуска job'ов (fail-fast)
foreach ($s in $SourcesList) {
    $SourceScript = Join-Path $SkillDir "sources\$s.ps1"
    if (-not (Test-Path $SourceScript)) {
        Write-Host "[ERR] Источник '$s' не реализован: $SourceScript" -ForegroundColor Red
        Write-Host "      Реализованные источники:" -ForegroundColor Red
        Get-ChildItem (Join-Path $SkillDir "sources") -Filter "*.ps1" | ForEach-Object {
            Write-Host "        - $($_.BaseName)" -ForegroundColor Red
        }
        exit 1
    }
}

# 2. Script block: try/catch + явный возврат {Source, Ok, Exit, Error}.
#    sources\<s>.ps1 делает exit N (нас интересует $LASTEXITCODE)
#    или бросает throw (попадёт в catch).
$JobBlock = {
    param($SourceName, $ProjectRoot, $Strategy, $SourceScriptPath, $ForceFull)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        if ($ForceFull) {
            & $SourceScriptPath -ProjectRoot $ProjectRoot -Strategy $Strategy -ForceFull
        } else {
            & $SourceScriptPath -ProjectRoot $ProjectRoot -Strategy $Strategy
        }
        [pscustomobject]@{
            Source  = $SourceName
            Ok      = ($LASTEXITCODE -eq 0)
            Exit    = $LASTEXITCODE
            Error   = $null
            Elapsed = $sw.Elapsed
        }
    } catch {
        [pscustomobject]@{
            Source  = $SourceName
            Ok      = $false
            Exit    = -1
            Error   = $_.Exception.Message
            Elapsed = $sw.Elapsed
        }
    }
}

# 3. Fan-out: запускаем все job'ы, логируем старт
$Wall = [System.Diagnostics.Stopwatch]::StartNew()
$Jobs = @()
foreach ($s in $SourcesList) {
    $SourceScript = Join-Path $SkillDir "sources\$s.ps1"
    $j = Start-Job -Name $s -ScriptBlock $JobBlock `
        -ArgumentList @($s, $ProjectRoot, $Strategy, $SourceScript, [bool]$ForceFull)
    Write-Host "-> '$s' стартовал (job id $($j.Id))" -ForegroundColor Yellow
    $Jobs += $j
}

# 4. Wait + Receive + Remove. Wait-Job без аргументов ждёт ВСЕ job'ы разом —
#    реальная параллельность сохраняется (а не последовательное ожидание).
try {
    $null = Wait-Job $Jobs

    $Failed = @()
    foreach ($j in $Jobs) {
        # Receive-Job возвращает массив (stdout job'а + явный emit).
        # Наш объект — последний, т.к. emit идёт после `& $SourceScript`.
        $result = Receive-Job $j -ErrorAction SilentlyContinue |
            Where-Object { $_ -is [pscustomobject] -and $_.Source } |
            Select-Object -Last 1

        if (-not $result) {
            # job завершился без явного emit (например, killed)
            $result = [pscustomobject]@{
                Source  = $j.Name
                Ok      = $false
                Exit    = -1
                Error   = "Job завершился без выходного объекта (state: $($j.State))"
                Elapsed = [TimeSpan]::Zero
            }
        }

        # Format Elapsed как "m:ss" для <1h, "h:mm:ss" для длинных.
        $e = $result.Elapsed
        if ($e.TotalHours -ge 1) {
            $eStr = ("{0}:{1:00}:{2:00}" -f [int]$e.TotalHours, $e.Minutes, $e.Seconds)
        } else {
            $eStr = ("{0}:{1:00}" -f [int]$e.TotalMinutes, $e.Seconds)
        }

        if ($result.Ok) {
            Write-Host "[$($result.Source)] завершён (exit $($result.Exit), $eStr)" -ForegroundColor Green
        } else {
            Write-Host "[$($result.Source)] FAILED (exit $($result.Exit), $eStr): $($result.Error)" -ForegroundColor Red
            $Failed += $result.Source
        }
    }
    $Wall.Stop()
}
finally {
    # Гарантированная очистка job'ов, даже если Receive-Job упал
    foreach ($j in $Jobs) {
        if ($j.State -ne 'Completed') {
            Stop-Job $j -ErrorAction SilentlyContinue
        }
        Remove-Job $j -Force -ErrorAction SilentlyContinue
    }
}

# 4b. Wall clock для всего прогона
$w = $Wall.Elapsed
if ($w.TotalHours -ge 1) {
    $wStr = ("{0}:{1:00}:{2:00}" -f [int]$w.TotalHours, $w.Minutes, $w.Seconds)
} else {
    $wStr = ("{0}:{1:00}" -f [int]$w.TotalMinutes, $w.Seconds)
}
Write-Host ""
Write-Host "[WALL] $wStr (parallel, $($SourcesList.Count) источник(ов))" -ForegroundColor Cyan

# 5. Exit code run.ps1
if ($Failed.Count -gt 0) {
    Write-Host ""
    Write-Host "[ERR] Провалились: $($Failed -join ', ')" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[DONE] Все источники отработали. Результаты в data\<source>_harvest\filtered\" -ForegroundColor Green
