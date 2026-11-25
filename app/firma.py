"""Firma Ed25519 entre bancos (doc 02, sección 1). Sin dependencias de base de datos."""

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key


def cadena_canonica(
    metodo: str, ruta: str, banco_id: str, idempotency_key: str, timestamp: str, cuerpo: str
) -> str:
    """METODO / RUTA / X-Bank-Id / X-Idempotency-Key / X-Timestamp / CUERPO, separados por \\n.

    Con GET el cuerpo es "" y la cadena termina en "\\n". Confirmar con los otros 3
    grupos que todos arman la cadena igual (el contrato no lo detalla para GET).
    """
    return "\n".join([metodo.upper(), ruta, banco_id, idempotency_key, timestamp, cuerpo])


def firmar(clave: Ed25519PrivateKey, canonica: str) -> str:
    return base64.b64encode(clave.sign(canonica.encode("utf-8"))).decode("ascii")


def verificar(clave_publica_pem: str, canonica: str, firma_b64: str) -> bool:
    try:
        clave = load_pem_public_key(clave_publica_pem.encode("utf-8"))
        if not isinstance(clave, Ed25519PublicKey):
            return False
        clave.verify(base64.b64decode(firma_b64, validate=True), canonica.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError):
        return False


@lru_cache
def cargar_clave_privada(ruta: Path) -> Ed25519PrivateKey:
    clave = load_pem_private_key(Path(ruta).read_bytes(), password=None)
    if not isinstance(clave, Ed25519PrivateKey):
        raise ValueError("La clave privada no es Ed25519")
    return clave


def hash_solicitud(datos: dict) -> str:
    """SHA-256 de los datos de una solicitud, para detectar CONFLICTO_IDEMPOTENCIA.

    En autorizaciones de cajero hay que excluir "pin" antes de llamar a esta función.
    """
    texto = json.dumps(datos, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()
