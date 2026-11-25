"""Hash de contraseñas y PIN con bcrypt. Nunca se guarda ni se registra el valor en claro."""

from functools import lru_cache

import bcrypt

from app.errors import ApiError

MAX_BYTES = 72  # límite de bcrypt


def _bytes(secreto: str) -> bytes:
    datos = secreto.encode("utf-8")
    if len(datos) > MAX_BYTES:
        raise ApiError("SOLICITUD_INVALIDA", f"Máximo {MAX_BYTES} bytes")
    return datos


def hash_secreto(secreto: str) -> str:
    return bcrypt.hashpw(_bytes(secreto), bcrypt.gensalt()).decode("ascii")


def verificar_secreto(secreto: str, hash_guardado: str) -> bool:
    try:
        return bcrypt.checkpw(_bytes(secreto), hash_guardado.encode("ascii"))
    except (ValueError, ApiError):
        return False


@lru_cache
def hash_senuelo() -> str:
    """Hash de relleno: se verifica contra él cuando el usuario no existe, para que
    responder 'usuario inexistente' tarde lo mismo que 'contraseña incorrecta'."""
    return hash_secreto("senuelo-no-es-una-contrasena")
