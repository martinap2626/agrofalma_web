"""
Cuentas de MySQL por uso: la aplicación usa DB_USER; el volcado usa DB_RESPALDO_* (solo lectura) y restaurar o verificar
usa DB_ADMIN_*. Si una cuenta aparte no está definida se usa la de la aplicación (así funciona igual en una instalación simple).
"""

from app.comandos_respaldo import _cuenta, archivo_credenciales


class ConCuentasAparte:
    DB_HOST, DB_PORT = "localhost", "3306"
    DB_USER, DB_PASSWORD = "app", "clave-app"
    DB_RESPALDO_USER, DB_RESPALDO_PASSWORD = "respaldo", 'clave"de\\respaldo'
    DB_ADMIN_USER, DB_ADMIN_PASSWORD = "admin", "clave-admin"


class SoloAplicacion:
    DB_HOST, DB_PORT = "localhost", "3306"
    DB_USER, DB_PASSWORD = "app", "clave-app"


def test_cada_uso_toma_su_propia_cuenta():
    assert _cuenta(ConCuentasAparte, "RESPALDO") == ("respaldo", 'clave"de\\respaldo')
    assert _cuenta(ConCuentasAparte, "ADMIN") == ("admin", "clave-admin")


def test_sin_cuentas_aparte_se_usa_la_de_la_aplicacion():
    assert _cuenta(SoloAplicacion, "RESPALDO") == ("app", "clave-app")
    assert _cuenta(SoloAplicacion, "ADMIN") == ("app", "clave-app")


def test_el_archivo_de_credenciales_lleva_la_cuenta_pedida_y_escapa_la_clave():
    with archivo_credenciales(ConCuentasAparte, "RESPALDO") as ruta:
        texto = open(ruta, encoding="utf-8").read()
    assert "user=respaldo" in texto and "user=app" not in texto
    assert 'password="clave\\"de\\\\respaldo"' in texto
    with archivo_credenciales(ConCuentasAparte) as ruta:  # sin indicar, es la cuenta de administración
        assert "user=admin" in open(ruta, encoding="utf-8").read()
