"""Pruebas de la pantalla «Parámetros»: quién puede usarla, validación, auditoría y efecto sobre el cálculo y las ventas."""

import uuid

import pytest

from app.extensions import db
from app.models import Bitacora, ParametroSistema
from app.parametros import parametros_vigentes, valores
from conftest import crear_producto
from test_inventario_y_anulacion import vender


def texto(respuesta):
    return respuesta.get_data(as_text=True)


FORMULARIO = {
    "tiempo_reposicion_dias": "15", "proteccion_A": "95", "proteccion_B": "90", "proteccion_C": "80",
    "descuento_max_trabajador_pct": "10",
}


def enviar(c, **cambios):
    return c.post("/parametros", data={**FORMULARIO, **cambios})


@pytest.fixture(autouse=True)
def parametros_de_fabrica(app, monkeypatch):
    """Cada prueba empieza y termina sin valores guardados, para no afectar a las demás."""
    def limpiar():
        with app.app_context():
            ParametroSistema.query.delete()
            db.session.commit()

    monkeypatch.setitem(app.config, "DESCUENTO_MAX_TRABAJADOR_PCT", 10.0)  # las pruebas usan 100 para no estorbar a las demás
    limpiar()
    yield
    limpiar()


def test_solo_el_administrador_entra_a_parametros(admin, trabajador_a):
    assert admin.get("/parametros").status_code == 200
    assert trabajador_a.get("/parametros").status_code == 403
    assert trabajador_a.post("/parametros", data=FORMULARIO).status_code == 403


def test_sin_cambios_guardados_se_usan_los_valores_de_fabrica(app, admin):
    html = texto(admin.get("/parametros"))
    assert 'value="15"' in html and 'value="95"' in html and "Guardar parámetros" in html
    with app.app_context():
        v = valores()
        assert v["tiempo_reposicion_dias"] == 15 and v["proteccion_C"] == 80
        p = parametros_vigentes()
        assert p.tiempo_reposicion_dias == 15 and p.z_por_clase == {"A": 1.65, "B": 1.28, "C": 0.84}
        assert p.nivel_proteccion_pct("A") == 95


def test_guardar_cambia_los_valores_y_deja_valor_anterior_y_nuevo_en_la_bitacora(app, admin):
    r = enviar(admin, tiempo_reposicion_dias="30", proteccion_A="99")
    assert r.status_code == 302
    with app.app_context():
        assert valores()["tiempo_reposicion_dias"] == 30 and valores()["proteccion_A"] == 99
        p = parametros_vigentes()
        assert p.tiempo_reposicion_dias == 30 and p.z_por_clase["A"] > 2.3 and p.nivel_proteccion_pct("A") == 99
        assert p.z_por_clase["B"] == 1.28  # lo que no se tocó conserva su valor de siempre
        fila = Bitacora.query.filter_by(accion="parametros_editados").order_by(Bitacora.id_bitacora.desc()).first()
    assert "de 15 a 30" in fila.detalle and "de 95 a 99" in fila.detalle and "clase B" not in fila.detalle
    assert "99" in texto(admin.get("/parametros"))


def test_guardar_sin_cambios_no_anota_nada(app, admin):
    with app.app_context():
        antes = Bitacora.query.filter_by(accion="parametros_editados").count()
    assert enviar(admin).status_code == 302
    with app.app_context():
        assert Bitacora.query.filter_by(accion="parametros_editados").count() == antes
        assert ParametroSistema.query.count() == 0


@pytest.mark.parametrize("campo, valor", [
    ("tiempo_reposicion_dias", "0"), ("tiempo_reposicion_dias", "91"), ("tiempo_reposicion_dias", "7.5"),
    ("tiempo_reposicion_dias", "abc"), ("tiempo_reposicion_dias", ""), ("proteccion_A", "100"), ("proteccion_B", "49"),
    ("descuento_max_trabajador_pct", "-1"), ("descuento_max_trabajador_pct", "101"), ("descuento_max_trabajador_pct", "nan"),
])
def test_un_valor_invalido_se_rechaza_sin_guardar_nada(app, admin, campo, valor):
    r = enviar(admin, **{campo: valor, "proteccion_C": "70"})  # un cambio válido junto al inválido tampoco se guarda
    html = texto(r)
    assert r.status_code == 400 and ("debe estar entre" in html or "escribe un número" in html or "entero" in html)
    with app.app_context():
        assert ParametroSistema.query.count() == 0


def test_el_descuento_maximo_guardado_manda_sobre_el_de_la_configuracion(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "PAR-" + uuid.uuid4().hex[:6], precio=100, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=1, precio="85")  # 15 % menos, con el tope de 10 % de fábrica
    assert r.status_code == 200 and "Pide autorización" in texto(r)
    assert enviar(admin, descuento_max_trabajador_pct="20").status_code == 302
    assert vender(trabajador_a, datos, id_producto, cantidad=1, precio="85").status_code == 302
    assert enviar(admin, descuento_max_trabajador_pct="5").status_code == 302
    assert "Pide autorización" in texto(vender(trabajador_a, datos, id_producto, cantidad=1, precio="93"))


def test_el_simulador_usa_los_dias_de_reposicion_guardados(app, admin):
    from app.simulador import calcular
    from test_simulador_y_explicabilidad import entrada, recomendacion, HOY

    assert enviar(admin, tiempo_reposicion_dias="40").status_code == 302
    with app.app_context():
        r = calcular(recomendacion(), entrada("normal", stock=40), HOY)
    assert r["base"]["dias_reposicion"] == 40


def test_la_ayuda_y_el_menu_mencionan_los_parametros(admin, trabajador_a):
    assert "/parametros" in texto(admin.get("/dashboard"))
    assert "/parametros" not in texto(trabajador_a.get("/dashboard"))
    assert 'id="parametros"' in texto(admin.get("/ayuda"))
