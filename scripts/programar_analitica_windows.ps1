<#
.SYNOPSIS
    Programa la actualizacion mensual de la analitica (prediccion, segmentacion ABC, recomendaciones y plan de campana).

.DESCRIPTION
    Crea una tarea llamada "Agrofalma - Actualizar analitica" que ejecuta `flask actualizar-analitica` el dia 1 de cada mes
    a la hora indicada (por defecto 03:00). Se elige el dia 1 porque el mes anterior ya esta completo y sus ventas ya estan registradas.

    NO se ejecuta sola: hay que correrla a mano, una vez, desde PowerShell en la carpeta agrofalma_web:

        powershell -ExecutionPolicy Bypass -File scripts\programar_analitica_windows.ps1
        powershell -ExecutionPolicy Bypass -File scripts\programar_analitica_windows.ps1 -Hora 04:00

    Para quitarla:
        schtasks /delete /tn "Agrofalma - Actualizar analitica" /f

    En Linux se usa cron (ver deploy/cron_respaldo.txt).
#>
param(
    [string]$Hora = "03:00",
    [switch]$ComoSistema   # ejecutar aunque nadie haya iniciado sesion (requiere abrir PowerShell como administrador)
)

$ErrorActionPreference = "Stop"
$carpetaProyecto = Split-Path -Parent $PSScriptRoot
$flask = Join-Path $carpetaProyecto ".venv\Scripts\flask.exe"
if (-not (Test-Path $flask)) { throw "No se encontro $flask. Ejecuta este script desde agrofalma_web con su entorno virtual creado." }

$log = Join-Path $carpetaProyecto "logs\analitica_programada.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

$comando = "cmd.exe /c `"cd /d `"$carpetaProyecto`" && `"$flask`" --app run actualizar-analitica >> `"$log`" 2>&1`""
$nombre = "Agrofalma - Actualizar analitica"
if ($ComoSistema) {
    schtasks /create /tn $nombre /tr $comando /sc monthly /d 1 /st $Hora /ru SYSTEM /f | Out-Null
} else {
    schtasks /create /tn $nombre /tr $comando /sc monthly /d 1 /st $Hora /f | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear la tarea programada." }

# si el equipo estaba apagado el dia 1, que la ejecute apenas se encienda
try {
    $tarea = Get-ScheduledTask -TaskName $nombre
    $tarea.Settings.StartWhenAvailable = $true
    $tarea.Settings.ExecutionTimeLimit = "PT30M"
    Set-ScheduledTask -InputObject $tarea | Out-Null
} catch { Write-Host "Aviso: no se pudo activar 'ejecutar si se perdio la hora'. Activalo a mano en el Programador de tareas." }
Write-Host "Tarea programada: actualizacion de la analitica el dia 1 de cada mes a las $Hora. Resultados en $log"
