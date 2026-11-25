from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Llamador, verificar_firma
from app.errors import ApiError
from app.models import AutorizacionCajero
from app.schemas import AutorizacionCajeroOut, SolicitudAutorizacionCajero

router = APIRouter(prefix="/cajero", tags=["Cajero"])


@router.post("/autorizaciones", response_model=AutorizacionCajeroOut)
def crear_autorizacion(
    datos: SolicitudAutorizacionCajero,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Autoriza y debita un retiro de cajero (doc 03, sección 2).

    TODO:
      1. Idempotencia por (llamador.banco_id, operacion_id). El request_hash se calcula
         con hash_solicitud() SIN el campo pin.
      2. Validar en orden: cuenta existe en este banco, PIN (contra pin_hash), cuenta
         ACTIVA, tope de retiro, límite diario combinado, saldo suficiente.
      3. Si todo es correcto, débito atómico (FOR UPDATE) + consumo_diario + movimiento
         y responder AUTORIZADO; si no, RECHAZADO con el código del catálogo.
      4. Nunca registrar el PIN en logs, eventos ni base de datos.
    """
    raise ApiError("ERROR_INTERNO", "Pendiente de implementar", 501)


@router.get("/autorizaciones/{operacion_id}", response_model=AutorizacionCajeroOut)
def consultar_autorizacion(
    operacion_id: UUID,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Estado de una autorización ya solicitada. Cada banco-cajero solo ve las suyas."""
    aut = db.scalar(
        select(AutorizacionCajero).where(
            AutorizacionCajero.banco_cajero == llamador.banco_id,
            AutorizacionCajero.operacion_id == str(operacion_id),
        )
    )
    if aut is None:
        raise ApiError("NO_ENCONTRADO", "No hay registro de esa operacion_id")
    return AutorizacionCajeroOut(
        operacion_id=operacion_id,
        estado=aut.estado,
        motivo_rechazo=aut.motivo_rechazo,
        fecha_hora=aut.fecha_hora,
    )
