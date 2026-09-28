# Установка навыка Son of Orchestrator для Windows PowerShell.
#
#   irm https://raw.githubusercontent.com/kriscokitchen/son-of-orchestrator/main/install.ps1 | iex
#
# По умолчанию ставит для всех проектов, в ~\.claude\skills\.
# Через переменные окружения, до запуска:
#   $env:SOO_SCOPE = 'project'      только в текущий проект
#   $env:SOO_DIR   = 'C:\путь'      произвольный каталог навыков
#   $env:REF       = 'ветка'        поставить из другой ветки

$ErrorActionPreference = 'Stop'

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host 'Нужен PowerShell 5 или новее — в более старом нет Expand-Archive.'
    Write-Host 'На Windows 10 и 11 подходящая версия есть из коробки.'
    exit 1
}

$Repo = 'kriscokitchen/son-of-orchestrator'
$Name = 'son-of-orchestrator'
$Ref  = if ($env:REF) { $env:REF } else { 'main' }

if ($env:SOO_DIR) {
    $Dest = Join-Path $env:SOO_DIR $Name
} elseif ($env:SOO_SCOPE -eq 'project') {
    $Dest = Join-Path (Join-Path (Get-Location).Path '.claude') 'skills'
    $Dest = Join-Path $Dest $Name
} else {
    $Dest = Join-Path (Join-Path $HOME '.claude') 'skills'
    $Dest = Join-Path $Dest $Name
}

$Tmp = Join-Path ([IO.Path]::GetTempPath()) ('soo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null

try {
    Write-Host "Качаю $Repo ($Ref)..."
    $Zip = Join-Path $Tmp 'src.zip'
    try {
        Invoke-WebRequest -Uri "https://codeload.github.com/$Repo/zip/refs/heads/$Ref" -OutFile $Zip -UseBasicParsing
    } catch {
        # ref может быть тегом, а не веткой
        Invoke-WebRequest -Uri "https://codeload.github.com/$Repo/zip/refs/tags/$Ref" -OutFile $Zip -UseBasicParsing
    }

    Expand-Archive -Path $Zip -DestinationPath $Tmp -Force

    $Src = Get-ChildItem -Path $Tmp -Recurse -Directory |
        Where-Object { $_.Name -eq $Name -and (Test-Path (Join-Path $_.FullName 'SKILL.md')) } |
        Select-Object -First 1

    if (-not $Src) {
        throw "В скачанном архиве нет skills\$Name\SKILL.md — установка отменена."
    }

    # Прежнюю копию не затираем молча, но и в папке навыков не оставляем: агент
    # читает каждую папку с SKILL.md как навык, и отодвинутая копия со старым
    # описанием срабатывала бы вместо новой. Копии уходят в skill-backups\
    # рядом с папкой навыков.
    $Skills  = Split-Path $Dest -Parent
    $Backups = Join-Path (Split-Path $Skills -Parent) 'skill-backups'

    # Свободное имя: две установки в одну секунду дали бы одно и то же, и
    # Move-Item вложил бы копию внутрь прежней, вместо того чтобы положить рядом.
    function Get-FreePath([string]$Path) {
        $p = $Path; $n = 2
        while (Test-Path $p) { $p = "$Path-$n"; $n++ }
        return $p
    }

    # Копии, которые прошлые версии установщика оставили прямо в папке навыков.
    if (Test-Path $Skills) {
        Get-ChildItem -Path $Skills -Directory -Filter "$Name.backup-*" | ForEach-Object {
            if (-not (Test-Path $Backups)) { New-Item -ItemType Directory -Path $Backups -Force | Out-Null }
            $To = Get-FreePath (Join-Path $Backups $_.Name)
            Move-Item -Path $_.FullName -Destination $To
            Write-Host "Старая копия убрана из папки навыков: $To"
        }
    }

    if (Test-Path $Dest) {
        if (-not (Test-Path $Backups)) { New-Item -ItemType Directory -Path $Backups -Force | Out-Null }
        $Backup = Get-FreePath (Join-Path $Backups ("$Name.backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss')))
        Move-Item -Path $Dest -Destination $Backup
        Write-Host "Прежняя версия отодвинута: $Backup"
    }

    $Parent = Split-Path $Dest -Parent
    if (-not (Test-Path $Parent)) { New-Item -ItemType Directory -Path $Parent -Force | Out-Null }
    Copy-Item -Path $Src.FullName -Destination $Dest -Recurse

    $PhasesDir = Join-Path $Dest 'phases'
    $Phases = 0
    if (Test-Path $PhasesDir) {
        $Phases = @(Get-ChildItem -Path $PhasesDir -Filter '*.md' -File).Count
    }

    Write-Host ''
    Write-Host "Готово: $Dest"
    Write-Host "SKILL.md и файлов фаз: $Phases"
    Write-Host ''
    Write-Host 'Осталось два шага:'
    Write-Host '  1. Поставить единственную зависимость, из корня проекта:'
    Write-Host '       npx impeccable install'
    Write-Host '  2. Перезапустить агента, чтобы он увидел навык.'
    Write-Host ''
    Write-Host "Дальше: /$Name и словами опиши, что нужно построить."
}
finally {
    if (Test-Path $Tmp) { Remove-Item -Path $Tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
