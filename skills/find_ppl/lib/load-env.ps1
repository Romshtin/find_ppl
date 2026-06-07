# load-env.ps1 — загрузить переменные из .env проекта в окружение текущего процесса.
# Формат .env: KEY=VALUE, по одному на строку. Комментарии (#) и пустые строки игнорируются.
# Кавычки вокруг значения снимаются.

function Import-FindpplEnv {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $EnvFile = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $EnvFile)) {
        Write-Host "[WARN] .env не найден: $EnvFile" -ForegroundColor Yellow
        return
    }

    Get-Content $EnvFile -Encoding utf8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }

        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2]
            # Снять окружающие кавычки, если есть
            if ($value.StartsWith('"') -and $value.EndsWith('"')) {
                $value = $value.Substring(1, $value.Length - 2)
            } elseif ($value.StartsWith("'") -and $value.EndsWith("'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            Set-Item -Path "Env:$key" -Value $value
        }
    }
}
