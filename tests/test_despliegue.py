"""Pruebas de la verificación de despliegue y de la preparación de la base."""

from app.comandos_despliegue import AVISO, FALLO, OK, diagnosticar


def por_tema(resultados):
    return {tema: (nivel, msg) for nivel, tema, msg in resultados}


def test_una_configuracion_sana_no_tiene_fallos(app):
    with app.app_context():
        r = por_tema(diagnosticar(app))
    assert r["SECRET_KEY"][0] == OK
    assert r["Conexión a la base"][0] == OK
    assert r["Tablas"][0] == OK and r["Administrador"][0] == OK
    assert not [t for t, (n, _) in r.items() if n == FALLO and t != "Contraseña de la base"]


def test_una_clave_corta_es_un_fallo(app, monkeypatch):
    monkeypatch.setitem(app.config, "SECRET_KEY", "corta")
    with app.app_context():
        assert por_tema(diagnosticar(app))["SECRET_KEY"][0] == FALLO


def test_el_modo_depuracion_activo_es_un_fallo(app, monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with app.app_context():
        assert por_tema(diagnosticar(app))["Modo depuración"][0] == FALLO


def test_cookie_no_segura_y_proxy_generan_avisos(app, monkeypatch):
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", False)
    with app.app_context():
        assert por_tema(diagnosticar(app))["Cookie de sesión segura"][0] == AVISO
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setitem(app.config, "CONFIAR_EN_PROXY", False)
    with app.app_context():
        r = por_tema(diagnosticar(app))
    assert r["Cookie de sesión segura"][0] == OK and r["Proxy"][0] == AVISO


def test_preparar_bd_es_repetible(app):
    ejecutor = app.test_cli_runner()
    for _ in range(2):
        r = ejecutor.invoke(args=["preparar-bd"])
        assert r.exit_code == 0 and "Listo:" in r.output


def test_verificar_despliegue_sale_con_error_si_hay_fallos(app, monkeypatch):
    monkeypatch.setitem(app.config, "SECRET_KEY", "corta")
    r = app.test_cli_runner().invoke(args=["verificar-despliegue"])
    assert r.exit_code == 1 and "[FALLO] SECRET_KEY" in r.output
