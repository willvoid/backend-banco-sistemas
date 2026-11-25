from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import Llamador, verificar_firma
from app.errors import ApiError
from app.models import Cuenta
from app.schemas import CuentaOut

router = APIRouter(tags=["Cuentas"])


@router.get("/cuentas/{numero_cuenta}", response_model=CuentaOut)
def consultar_cuenta(
    numero_cuenta: str,
    _: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Datos básicos de una cuenta propia (existe, estado, titular)."""
    cuenta = db.scalar(select(Cuenta).where(Cuenta.numero_cuenta == numero_cuenta))
    if cuenta is None:
        raise ApiError("CUENTA_NO_EXISTE", "La cuenta no existe en este banco")
    return CuentaOut(
        banco_id=get_settings().banco_id,
        numero_cuenta=cuenta.numero_cuenta,
        estado=cuenta.estado,
        titular=cuenta.titular.nombre,
    )
