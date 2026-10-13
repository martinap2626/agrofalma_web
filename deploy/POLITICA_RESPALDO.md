# Política de respaldo y recuperación

Documento de operación del sistema Agrofalma. Define qué se respalda, cada cuánto, dónde, cómo se comprueba y cuánto
puede tardar y cuántos datos se pueden perder en una emergencia (RPO y RTO).

## Objetivos

| Objetivo | Valor | Qué significa |
|---|---|---|
| **RPO** (punto de recuperación) | **24 horas** | En el peor caso se pierden las operaciones del día en que ocurre el desastre: hay un respaldo cada noche (2 AM). Si el negocio necesita perder menos, se programa el respaldo cada 6 o 12 horas cambiando una línea del programador de tareas. |
| **RTO** (tiempo de recuperación) | **menos de 1 hora** | Restaurar la base toma segundos (se mide en cada simulacro: hoy 1,4 s con 6 200 filas). El resto de la hora es reinstalar el sistema en un equipo nuevo siguiendo `docs/Guia_Restauracion_Proyecto`. |

## Qué se respalda y cómo

* **Qué:** toda la base de datos (usuarios, productos, stock, ventas, créditos, bitácora, kárdex y resultados de la analítica) con `mysqldump` en una sola transacción: el respaldo es consistente aunque el sistema esté en uso.
* **Formato:** comprimido (ocupa una décima parte) y **cifrado** si está definida `RESPALDO_CLAVE` (AES con firma de integridad). Un archivo cifrado no sirve de nada a quien se lo lleve sin la clave y cualquier alteración se detecta al restaurar.
* **Retención:** los 7 últimos respaldos en el equipo y los 30 últimos en la copia externa.
* **Copia fuera del equipo:** cada respaldo se copia a `RESPALDO_COPIA_DIR` (disco externo, unidad de red o carpeta sincronizada con la nube) y se comprueba que la copia quedó idéntica (huella SHA-256). Si el equipo se pierde, se quema o lo encripta un virus, esa copia sigue viva. Si la copia falla, el comando termina con código 2 y queda en el registro.

## Qué conservar aparte (muy importante)

1. **La clave `RESPALDO_CLAVE`**, en un lugar distinto del equipo (por ejemplo, impresa en un sobre dentro de la caja fuerte del gerente, o en un gestor de contraseñas). **Sin la clave, los respaldos cifrados no se pueden recuperar.** Nadie, ni el desarrollador, puede descifrarlos sin ella.
2. El archivo `.env` (o una copia segura de sus valores), porque tiene la clave de sesiones y las credenciales de la base.

## Comprobación periódica (simulacro)

* **Cada domingo a las 4 AM** (programado): `flask verificar-respaldo` genera un respaldo, lo restaura en una base temporal y compara tabla por tabla; y `flask verificar-bitacora` comprueba que la bitácora no fue alterada. El resultado queda en `logs/verificacion.log` y debe decir `RESULTADO: CORRECTO`.
* **Cada mes**, quien administra el sistema revisa ese registro y anota la fecha y el resultado en el acta de simulacro (modelo abajo).
* **Cada 6 meses**, un simulacro completo: restaurar el último respaldo de la **copia externa** en otro equipo y abrir el sistema. Así se comprueba también la clave guardada aparte.

`flask verificar-despliegue` avisa si falta el respaldo reciente, el cifrado o la copia externa.

## Cómo restaurar (resumen)

```
flask restaurar-bd ARCHIVO --destino agrofalma_bd --sobrescribir     # .sql, .sql.gz o .sql.gz.enc (necesita RESPALDO_CLAVE)
flask verificar-despliegue                                          # debe terminar sin FALLOS
flask verificar-bitacora                                            # debe decir CORRECTO
```

Restaurar encima de la base en uso exige `--sobrescribir`; sin esa opción el comando se niega, para que nadie borre datos por error.

## Modelo de acta de simulacro

| Dato | Registro |
|---|---|
| Fecha del simulacro | |
| Respaldo usado (nombre del archivo y origen: equipo o copia externa) | |
| ¿Se pudo descifrar con la clave guardada aparte? | Sí / No |
| Tiempo de restauración medido | |
| Tablas y filas coinciden con la base original | Sí / No |
| Resultado de `flask verificar-bitacora` | |
| Responsable y firma | |
