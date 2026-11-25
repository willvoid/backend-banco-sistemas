from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import abrir_sesion, ahora_utc, cerrar_sesion, sesion_actual, usuario_actual
from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.models import Cuenta, Sesion, Titular, Usuario
from app.passwords import hash_secreto, hash_senuelo, verificar_secreto
from app.schemas import CambioPasswordIn, CuentaPropiaOut, LoginIn, PerfilOut, TokenOut

router = APIRouter(prefix="/auth", tags=["Autenticación (frontend propio)"])


@router.post("/login", response_model=TokenOut)
def login(datos: LoginIn, db: Session = Depends(get_db)):
    """Documento + contraseña -> JWT. Mismo error si el usuario no existe o la clave es mala."""
    s = get_settings()
    ahora = ahora_utc()
    usuario = db.scalar(
        select(Usuario).join(Titular).where(Titular.documento == datos.documento)
    )

    if usuario and usuario.bloqueado_hasta and usuario.bloqueado_hasta > ahora:
        raise ApiError("DEMASIADOS_INTENTOS", "Cuenta bloqueada temporalmente. Reintentá más tarde.")

    # Se verifica siempre (con un hash señuelo si no existe) para igualar los tiempos.
    correcto = verificar_secreto(
        datos.password, usuario.password_hash if usuario else hash_senuelo()
    )
    if usuario is None or not usuario.activo or not correcto:
        if usuario is not None:
            usuario.intentos_fallidos += 1
            if usuario.intentos_fallidos >= s.max_intentos_login:
                usuario.bloqueado_hasta = ahora + timedelta(minutes=s.bloqueo_login_min)
                usuario.intentos_fallidos = 0
            db.commit()
        raise ApiError("CREDENCIALES_INVALIDAS", "Documento o contraseña incorrectos")

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_login = ahora
    db.commit()
    token, _ = abrir_sesion(db, usuario)
    return TokenOut(
        access_token=token,
        expires_in=s.jwt_minutos * 60,
        inactividad_seg=s.inactividad_seg,
    )


@router.post("/logout", status_code=204)
def logout(sesion: Sesion = Depends(sesion_actual), db: Session = Depends(get_db)):
    """Cierra la sesión actual: el token deja de ser válido aunque no haya vencido."""
    cerrar_sesion(db, sesion, "LOGOUT")


@router.get("/me", response_model=PerfilOut)
def perfil(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    """Datos del titular autenticado y sus cuentas."""
    cuentas = db.scalars(
        select(Cuenta).where(Cuenta.titular_id == usuario.titular_id).order_by(Cuenta.numero_cuenta)
    ).all()
    return PerfilOut(
        nombre=usuario.titular.nombre,
        documento=usuario.titular.documento,
        cuentas=[
            CuentaPropiaOut(
                numero_cuenta=c.numero_cuenta,
                estado=c.estado,
                saldo=c.saldo,
                saldo_disponible=c.saldo_disponible,
            )
            for c in cuentas
        ],
    )


@router.post("/cambiar-password", status_code=204)
def cambiar_password(
    datos: CambioPasswordIn,
    usuario: Usuario = Depends(usuario_actual),
    sesion: Sesion = Depends(sesion_actual),
    db: Session = Depends(get_db),
):
    if not verificar_secreto(datos.password_actual, usuario.password_hash):
        raise ApiError("CREDENCIALES_INVALIDAS", "La contraseña actual no es correcta")
    usuario.password_hash = hash_secreto(datos.password_nueva)
    # Se cierran las demás sesiones del usuario; la actual sigue abierta.
    db.execute(
        update(Sesion)
        .where(Sesion.usuario_id == usuario.id, Sesion.cerrada_en.is_(None), Sesion.id != sesion.id)
        .values(cerrada_en=ahora_utc(), motivo_cierre="CAMBIO_PASSWORD")
    )
    db.commit()
