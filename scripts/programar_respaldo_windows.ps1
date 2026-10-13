<#
.SYNOPSIS
    Programa el respaldo diario y el simulacro semanal de la base de datos en el Programador de tareas de Windows.

.DESCRIPTION
    Crea (o reemplaza) dos tareas:

      "Agrofalma - Respaldo diario"                 `flask respaldar-bd` todos los días a la hora indicada. Comprime y cifra el
                                                    respaldo (si hay RESPALDO_CLAVE en el .env), conserva los últimos 7 y deja una
                                                    copia verificada fuera del equipo (si hay RESPALDO_COPIA_DIR en el .env).
      "Agrofalma - Simulacro semanal de respaldo"   los domingos, `flask verificar-respaldo` y `flask verificar-bitacora`: comprueba que
                                                    el respaldo SÍ se puede restaurar y que la bitácora no fue alterada.

    NO se ejecuta solo: hay que correrlo a mano, una vez, desde una consola de
    PowerShell abierta en la carpeta agrofalma_web:

        powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo_windows.ps1
        powershell -ExecutionPolicy Bypass -File scripts\programar_respaldo_windows.ps1 -Hora 03:30

    Para quitar las tareas:
        Unregister-ScheduledTask -TaskName "Agrofalma - Respaldo diario" -Confirm:$false
        Unregister-ScheduledTask -TaskName "Agrofalma - Simulacro semanal de respaldo" -Confirm:$false

    En el servidor de producción (Linux) se usa cron en vez de este script (ver deploy/cron_respaldo.txt).
    Objetivos de recuperación (RPO y RTO) y modelo de acta: deploy/POLITICA_RESPALDO.md
#>
param(
    [string]$Hora = "02:00",
    [string]$HoraSimulacro = "04:00"
)

$ErrorActionPreference = "Stop"

$carpetaProyecto = Split-Path -Parent $PSScriptRoot
$flask = Join-Path $carpetaProyecto ".venv\Scripts\flask.exe"
if (-not (Test-Path $flask)) {
    throw "No se encontró $flask. Ejecuta este script desde el proyecto agrofalma_web con su entorno virtual creado."
}

$carpetaLogs = Join-Path $carpetaProyecto "logs"
New-Item -ItemType Directory -Force -Path $carpetaLogs | Out-Null
$log = Join-Path $carpetaLogs "respaldo_programado.log"
$logSimulacro = Join-Path $carpetaLogs "verificacion.log"

# si el equipo estaba apagado a la hora programada, lo ejecuta apenas se encienda
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# --- respaldo diario (cmd /c permite redirigir la salida a un archivo de registro) ---
$comando = "/c `"cd /d `"$carpetaProyecto`" && `"$flask`" --app run respaldar-bd >> `"$log`" 2>&1`""
$accion = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $comando
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
Register-ScheduledTask -TaskName "Agrofalma - Respaldo diario" -Action $accion -Trigger $disparador `
    -Settings $ajustes -Description "Respaldo diario de la base de datos agrofalma_bd (flask respaldar-bd)" -Force | Out-Null

# --- simulacro semanal: restaura el respaldo en una base temporal y verifica la bitácora ---
$comandoSimulacro = "/c `"cd /d `"$carpetaProyecto`" && (`"$flask`" --app run verificar-respaldo && `"$flask`" --app run verificar-bitacora) >> `"$logSimulacro`" 2>&1`""
$accionSimulacro = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $comandoSimulacro
$disparadorSimulacro = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $HoraSimulacro
Register-ScheduledTask -TaskName "Agrofalma - Simulacro semanal de respaldo" -Action $accionSimulacro -Trigger $disparadorSimulacro `
    -Settings $ajustes -Description "Comprueba cada semana que el respaldo se puede restaurar y que la bitácora no fue alterada" -Force | Out-Null

Write-Host "Tarea programada: respaldo diario a las $Hora. Registro de resultados en $log"
Write-Host "Tarea programada: simulacro semanal los domingos a las $HoraSimulacro. Registro de resultados en $logSimulacro"
Write-Host "Recuerda definir RESPALDO_CLAVE y RESPALDO_COPIA_DIR en el archivo .env para cifrar y copiar fuera del equipo."
