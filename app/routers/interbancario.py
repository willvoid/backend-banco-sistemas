from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Llamador, verificar_firma
from app.models import CreditoInterbancario
from app.schemas import CreditoOut, SolicitudCredito
from app.services.creditos import acreditar, consultar

router = APIRouter(prefix="/interbancario", tags=["Interbancario"])


def _salida(c: CreditoInterbancario) -> CreditoOut:
    return CreditoOut(
        transfer_id=c.transfer_id,
        estado=c.estado,
        motivo_rechazo=c.motivo_rechazo,
        fecha_hora=c.fecha_hora,
    )


@router.post("/creditos", response_model=CreditoOut)
def crear_credito(
    datos: SolicitudCredito,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Acredita una transferencia entrante (doc 02, sección 4, paso 2).

    Responde 200 con estado ACREDITADO o RECHAZADO (+ motivo_rechazo). Repetir el mismo
    transfer_id devuelve el mismo resultado sin acreditar dos veces.
    """
    return _salida(acreditar(db, llamador.banco_id, datos))


@router.get("/creditos/{transfer_id}", response_model=CreditoOut)
def consultar_credito(
    transfer_id: UUID,
    llamador: Llamador = Depends(verificar_firma),
    db: Session = Depends(get_db),
):
    """Estado de un crédito ya solicitado (doc 02, sección 4, paso 4). Cada banco solo ve los suyos."""
    return _salida(consultar(db, llamador.banco_id, str(transfer_id)))
