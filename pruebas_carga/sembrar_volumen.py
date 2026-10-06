"""
Siembra una base de datos APARTE (agrofalma_carga) con un volumen de datos
mucho mayor al actual, para medir cómo se comporta el sistema cuando la
empresa crezca: 340 productos (tamaño del catálogo real), 1 500 clientes,
25 000 ventas con ~60 000 líneas de detalle, créditos y cobros, y las tablas
de analítica completas.

Nunca toca agrofalma_bd (la base real): trabaja solo sobre agrofalma_carga.

Uso (desde agrofalma_web):  python pruebas_carga/sembrar_volumen.py
"""

import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Categoria, Cliente, Credito, DetalleVenta, PrediccionDemanda, Producto, RecomendacionAbastecimiento,
    Recuperacion, Rol, Sede, SegmentacionProducto, StockPorSede, UnidadMedida, Usuario, Venta,
)
from config import Config  # noqa: E402

NOMBRE_BD = "agrofalma_carga"
N_PRODUCTOS, N_CLIENTES, N_VENTAS, N_TRABAJADORES = 340, 1500, 25000, 8
CLAVE_ADMIN = "AdminCarga2026"
CLAVE_TRABAJADOR = "TrabajoCarga2026"


class ConfigCarga(Config):
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}@{Config.DB_HOST}:{Config.DB_PORT}/"
        f"{NOMBRE_BD}?charset=utf8mb4"
    )
    SECRET_KEY = "clave-solo-para-pruebas-de-carga-0123456789"


def crear_bd():
    conexion = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER, password=Config.DB_PASSWORD)
    with conexion.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS `{NOMBRE_BD}`")
        cursor.execute(f"CREATE DATABASE `{NOMBRE_BD}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conexion.commit()
    conexion.close()


def insertar(modelo, filas, lote=5000):
    for i in range(0, len(filas), lote):
        db.session.execute(modelo.__table__.insert(), filas[i:i + lote])
    db.session.commit()


def sembrar():
    azar = random.Random(2026)

    insertar(Rol, [{"id_rol": 1, "nombre_rol": "Administrador", "estado": True},
                   {"id_rol": 2, "nombre_rol": "Trabajador", "estado": True}])
    insertar(Sede, [{"id_sede": 1, "nombre_sede": "Chiclayo", "estado": True},
                    {"id_sede": 2, "nombre_sede": "Pimentel", "estado": True}])

    admin = Usuario(nombre="Admin", apellido="Carga", usuario_login="admin_c", id_rol=1, id_sede=1)
    admin.set_password(CLAVE_ADMIN)
    hash_trab = Usuario(nombre="x", apellido="x", usuario_login="x", id_rol=2, id_sede=1)
    hash_trab.set_password(CLAVE_TRABAJADOR)
    usuarios = [{"id_usuario": 1, "nombre": "Admin", "apellido": "Carga", "usuario_login": "admin_c",
                 "contrasena_hash": admin.contrasena_hash, "id_rol": 1, "id_sede": 1, "estado": True}]
    for i in range(1, N_TRABAJADORES + 1):
        usuarios.append({"id_usuario": 1 + i, "nombre": f"Trabajador{i}", "apellido": "Carga", "usuario_login": f"w{i}",
                         "contrasena_hash": hash_trab.contrasena_hash, "id_rol": 2, "id_sede": 1 + (i % 2), "estado": True})
    insertar(Usuario, usuarios)

    insertar(Categoria, [{"id_categoria": i, "nombre_categoria": n, "estado": True}
                         for i, n in enumerate(["Foliares", "Fertilizantes", "Quimicos", "Bioestimulantes", "Insecticidas", "Otros"], 1)])
    insertar(UnidadMedida, [{"id_unidad": 1, "nombre_unidad": "Litro", "abreviatura": "L"},
                            {"id_unidad": 2, "nombre_unidad": "Kilogramo", "abreviatura": "Kg"},
                            {"id_unidad": 3, "nombre_unidad": "Unidad", "abreviatura": "Und"}])

    productos = [{
        "id_producto": i, "codigo_producto": f"P{i:04d}", "nombre_producto": f"Producto de carga {i}",
        "id_categoria": 1 + i % 6, "id_unidad": 1 + i % 3,
        "precio_unitario": Decimal(azar.randint(8, 400)), "stock_minimo": azar.randint(2, 30), "estado": True,
    } for i in range(1, N_PRODUCTOS + 1)]
    insertar(Producto, productos)
    precios = {p["id_producto"]: p["precio_unitario"] for p in productos}

    insertar(StockPorSede, [{"id_producto": i, "id_sede": s, "cantidad_actual": 1_000_000}
                            for i in range(1, N_PRODUCTOS + 1) for s in (1, 2)])
    insertar(Cliente, [{"id_cliente": i, "nombre_cliente": f"Cliente de carga {i}", "estado": True}
                       for i in range(1, N_CLIENTES + 1)])

    # --- ventas repartidas en ~3 años ---
    ahora = datetime.now()
    ventas, detalles, creditos, recuperaciones = [], [], [], []
    id_detalle = id_credito = 1
    for id_venta in range(1, N_VENTAS + 1):
        fecha = ahora - timedelta(days=azar.randint(0, 1095), minutes=azar.randint(0, 600))
        id_usuario = 2 + azar.randrange(N_TRABAJADORES)
        es_credito = azar.random() < 0.30
        total = Decimal(0)
        for _ in range(azar.randint(1, 4)):
            id_producto = azar.randint(1, N_PRODUCTOS)
            cantidad = azar.randint(1, 12)
            subtotal = precios[id_producto] * cantidad
            total += subtotal
            detalles.append({"id_detalle": id_detalle, "id_venta": id_venta, "id_producto": id_producto,
                             "cantidad": cantidad, "precio_unitario": precios[id_producto], "subtotal": subtotal})
            id_detalle += 1
        pagado = (not es_credito) or azar.random() < 0.6
        ventas.append({
            "id_venta": id_venta, "numero_operacion": f"OP-{id_venta:05d}", "fecha_hora": fecha,
            "id_cliente": azar.randint(1, N_CLIENTES), "id_usuario": id_usuario, "id_sede": 1 + (id_usuario % 2),
            "tipo_venta": "credito" if es_credito else "contado",
            "estado_pago": "pagado" if pagado else "pendiente", "total": total,
        })
        if es_credito:
            saldo = Decimal(0) if pagado else total
            creditos.append({"id_credito": id_credito, "id_venta": id_venta, "monto_credito": total,
                             "fecha_credito": fecha.date(), "saldo_pendiente": saldo,
                             "estado": "pagado" if pagado else "pendiente"})
            if pagado:
                recuperaciones.append({"id_credito": id_credito, "monto_recuperado": total,
                                       "fecha_recuperacion": fecha.date() + timedelta(days=azar.randint(1, 45)),
                                       "id_usuario": id_usuario})
            id_credito += 1

    insertar(Venta, ventas)
    insertar(DetalleVenta, detalles)
    insertar(Credito, creditos)
    insertar(Recuperacion, recuperaciones)

    # --- tablas de analítica completas ---
    insertar(PrediccionDemanda, [{
        "id_producto": i, "horizonte_semanas": 4, "demanda_estimada": Decimal(azar.randint(0, 300)),
        "mape": Decimal(azar.randint(5, 60)), "tendencia_pct": Decimal(azar.randint(-30, 40)),
    } for i in range(1, N_PRODUCTOS + 1)])
    niveles = ["urgente", "media", "sin_accion"]
    insertar(RecomendacionAbastecimiento, [{
        "id_producto": i, "id_sede": s, "stock_actual": azar.randint(0, 80), "demanda_prevista": Decimal(azar.randint(0, 200)),
        "cantidad_sugerida": azar.randint(0, 100), "nivel_urgencia": azar.choice(niveles), "motivo": "Dato de prueba de carga",
    } for i in range(1, N_PRODUCTOS + 1) for s in (1, 2)])
    insertar(SegmentacionProducto, [{
        "id_producto": i, "periodo_inicio": date.today() - timedelta(days=365), "periodo_fin": date.today(),
        "ventas_periodo": Decimal(azar.randint(100, 90000)), "pct_acumulado": Decimal(min(i / N_PRODUCTOS * 100, 99.99)).quantize(Decimal("0.01")),
        "clase_abc": "A" if i < 70 else ("B" if i < 150 else "C"),
    } for i in range(1, N_PRODUCTOS + 1)])

    return len(ventas), len(detalles), len(creditos)


if __name__ == "__main__":
    crear_bd()
    app = create_app(ConfigCarga)
    with app.app_context():
        db.create_all()
        v, d, c = sembrar()
    print(f"Base '{NOMBRE_BD}' lista: {N_PRODUCTOS} productos, {N_CLIENTES} clientes, {v} ventas, {d} líneas de detalle, {c} créditos.")
    print(f"Usuarios: admin_c / {CLAVE_ADMIN}  ·  w1..w{N_TRABAJADORES} / {CLAVE_TRABAJADOR}")
