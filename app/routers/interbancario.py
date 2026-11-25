from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Llamador, verificar_firma
from app.errors import ApiError
from app.models import CreditoInterbancario
from app.schemas import CreditoOut, SolicitudCredito

router = APIRouter(prefix="/interbancario", tags=["Interbancario"])


@router.post("/creditos", response_model=CreditoOut)
def crear_credito(
    datos: SolicitudCredito,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Acredita una transferencia entrante (doc 02, sección 4, paso 2).

    TODO:
      1. Buscar (banco_origen, transfer_id) en creditos_interbancarios: si existe con el
         mismo request_hash devolver lo guardado; con otro hash -> CONFLICTO_IDEMPOTENCIA.
      2. Validar que datos.banco_origen == llamador.banco_id y que esté en bancos.
      3. Transacción: bloquear la cuenta destino (FOR UPDATE), validar existencia y estado,
         acreditar, registrar movimiento y el crédito (ACREDITADO / RECHAZADO + motivo).
    """
    raise ApiError("ERROR_INTERNO", "Pendiente de implementar", 501)


@router.get("/creditos/{transfer_id}", response_model=CreditoOut)
def consultar_credito(
    transfer_id: UUID,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Estado de un crédito ya solicitado. Cada banco solo ve los suyos."""
    credito = db.scalar(
        select(CreditoInterbancario).where(
            CreditoInterbancario.banco_origen == llamador.banco_id,
            CreditoInterbancario.transfer_id == str(transfer_id),
        )
    )
    if credito is None:
        raise ApiError("NO_ENCONTRADO", "No hay registro de ese transfer_id")
    return CreditoOut(
        transfer_id=transfer_id,
        estado=credito.estado,
        motivo_rechazo=credito.motivo_rechazo,
        fecha_hora=credito.fecha_hora,
    )
