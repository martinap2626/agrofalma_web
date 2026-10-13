"""
Validaciones de datos de negocio reutilizables (documentos de identidad de clientes).

Documentos aceptados:
  DNI                          8 dígitos
  RUC                          11 dígitos, empieza con 10, 15, 16, 17 o 20 y cumple el dígito verificador de la SUNAT
  Carnet de extranjería / pasaporte / otro   de 9 a 12 caracteres alfanuméricos (los de 11 dígitos solo valen como RUC)

Se prefiere rechazar un documento mal digitado en el momento de registrar al cliente, y no descubrirlo meses después
cuando haya que emitir un comprobante o cobrar un crédito.
"""

from __future__ import annotations

import re

_PESOS_RUC = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_PREFIJOS_RUC = ("10", "15", "16", "17", "20")


# dígito verificador del RUC (módulo 11 sobre los 10 primeros dígitos)
def ruc_valido(ruc: str) -> bool:
    if not re.fullmatch(r"\d{11}", ruc) or ruc[:2] not in _PREFIJOS_RUC:
        return False
    suma = sum(int(d) * p for d, p in zip(ruc[:10], _PESOS_RUC))
    verificador = (11 - suma % 11) % 10
    return verificador == int(ruc[10])


def validar_documento(documento: str) -> str | None:
    """Devuelve un mensaje de error, o None si el documento está vacío (opcional) o es válido."""
    if not documento:
        return None
    if not documento.isascii() or not documento.isalnum():
        return "El documento solo puede tener letras y números, sin espacios ni guiones."
    if documento.isdigit():
        if len(documento) == 8:
            return None
        if len(documento) == 11:
            return None if ruc_valido(documento) else "El RUC no es válido: revisa los 11 dígitos."
        if 9 <= len(documento) <= 12:
            return None  # carnet de extranjería numérico u otro documento
        return "El documento numérico debe ser un DNI (8 dígitos), un RUC (11 dígitos) u otro documento de 9 a 12 caracteres."
    if 9 <= len(documento) <= 12:
        return None  # carnet de extranjería o pasaporte
    return "Un documento con letras (carnet de extranjería o pasaporte) debe tener entre 9 y 12 caracteres."
