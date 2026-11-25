"""Sesiones con timeout por inactividad (expiración deslizante) sobre JWT.

El JWT solo identifica la sesión (claim jti). El estado vive en la tabla `sesiones`:
  - inactividad: si pasan INACTIVIDAD_SEG sin requests, la sesión se cierra;
  - tope absoluto: a los JWT_MINUTOS del login se cierra aunque haya actividad;
  - logout / cambio de contraseña: la sesión queda cerrada y el token deja de servir.
Cada request autenticado renueva `ultima_actividad`.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.models import Sesion, Usuario

ALGORITMO = "HS256"
bearer = HTTPBearer(auto_error=False)  # auto_error=False: el error sale en nuestro formato


def ahora_utc() -> datetime:
    # La sesión de MySQL trabaja en UTC y devuelve DATETIME sin zona.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _secreto() -> str:
    secreto = get_settings().jwt_secret
    if len(secreto) < 32:
        raise RuntimeError("JWT_SECRET debe tener al menos 32 caracteres (ver .env.example)")
    return secreto


def abrir_sesion(db: Session, usuario: Usuario) -> tuple[str, Sesion]:
    """Crea la sesión y el token que la identifica. Devuelve (token, sesion)."""
    s = get_settings()
    ahora = ahora_utc()
    sesion = Sesion(
        id=str(uuid.uuid4()),
        usuario_id=usuario.id,
        creada_en=ahora,
        ultima_actividad=ahora,
        expira_en=ahora + timedelta(minutes=s.jwt_minutos),
    )
    db.add(sesion)
    db.commit()
    claims = {
        "sub": str(usuario.id),
        "jti": sesion.id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=s.jwt_minutos),
    }
    return jwt.encode(claims, _secreto(), algorithm=ALGORITMO), sesion


def decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, _secreto(), algorithms=[ALGORITMO], options={"require": ["exp", "sub", "jti"]}
        )
    except jwt.PyJWTError:
        raise ApiError("NO_AUTENTICADO", "Token inválido o vencido") from None


def cerrar_sesion(db: Session, sesion: Sesion, motivo: str) -> None:
    if sesion.cerrada_en is None:
        sesion.cerrada_en = ahora_utc()
        sesion.motivo_cierre = motivo
        db.commit()


def validar_sesion(db: Session, sesion_id: str) -> Sesion:
    """Comprueba la sesión y, si sigue viva, renueva su última actividad."""
    sesion = db.get(Sesion, sesion_id)
    if sesion is None or sesion.cerrada_en is not None:
        raise ApiError("NO_AUTENTICADO", "La sesión no existe o ya fue cerrada")

    ahora = ahora_utc()
    if ahora > sesion.expira_en:
        cerrar_sesion(db, sesion, "EXPIRADA")
        raise ApiError("SESION_EXPIRADA", "La sesión venció. Iniciá sesión de nuevo.")
    inactivo = (ahora - sesion.ultima_actividad).total_seconds()
    if inactivo > get_settings().inactividad_seg:
        cerrar_sesion(db, sesion, "INACTIVIDAD")
        raise ApiError("SESION_EXPIRADA", "Sesión cerrada por inactividad")

    sesion.ultima_actividad = ahora
    db.commit()
    return sesion


def sesion_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Sesion:
    if credenciales is None:
        raise ApiError("NO_AUTENTICADO", "Falta el header Authorization: Bearer <token>")
    claims = decodificar_token(credenciales.credentials)
    return validar_sesion(db, claims["jti"])


def usuario_actual(sesion: Sesion = Depends(sesion_actual)) -> Usuario:
    if not sesion.usuario.activo:
        raise ApiError("NO_AUTENTICADO", "Usuario inactivo")
    return sesion.usuario
