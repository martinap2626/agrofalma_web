<#
.SYNOPSIS
    Programa el respaldo diario de la base de datos en el Programador de tareas de Windows.

.DESCRIPTION
    Crea (o reemplaza) una tarea llamada "Agrofalma - Respaldo diario" que ejecuta
    `flask respaldar-bd` todos los días a la hora indicada. El comando conserva los
    últimos 7 respaldos y borra los más antiguos (ver app/comandos_respaldo.py).

    NO se ejecuta solo: hay que correrlo a mano, una vez, desde una consola de
    PowerShell abierta en la carpeta agrofalma_web:

        powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo_windows.ps1
        powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo_windows.ps1 -Hora 03:30

    Para quitar la tarea:
        Unregister-ScheduledTask -TaskName "Agrofalma - Respaldo diario" -Confirm:$false

    En el servidor de producción (Linux) se usa cron en vez de este script (ver el
    encabezado de app/comandos_respaldo.py).
#>
param(
    [string]$Hora = "02:00"
)

$ErrorActionPreference = "Stop"

$carpetaProyecto = Split-Path -Parent $PSScriptRoot
$flask = Join-Path $carpetaProyecto ".venv\Scripts\flask.exe"
if (-not (Test-Path $flask)) {
    throw "No se encontró $flask. Ejecuta este script desde el proyecto agrofalma_web con su entorno virtual creado."
}

$log = Join-Path $carpetaProyecto "logs\respaldo_programado.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# cmd /c permite redirigir la salida a un archivo de registro
$comando = "/c `"cd /d `"$carpetaProyecto`" && `"$flask`" --app run respaldar-bd >> `"$log`" 2>&1`""
$accion = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $comando
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
# si el equipo estaba apagado a la hora programada, lo ejecuta apenas se encienda
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "Agrofalma - Respaldo diario" -Action $accion -Trigger $disparador `
    -Settings $ajustes -Description "Respaldo diario de la base de datos agrofalma_bd (flask respaldar-bd)" -Force | Out-Null

Write-Host "Tarea programada: respaldo diario a las $Hora. Registro de resultados en $log"
