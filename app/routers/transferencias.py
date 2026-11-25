from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.database import get_db
from app.models import Usuario
from app.schemas import SolicitudTransferencia, TransferenciaOut
from app.services.transferencias import transferir

# API propia para el frontend de ESTE banco: no es parte del contrato entre bancos.
router = APIRouter(prefix="/transferencias", tags=["Transferencias (frontend propio)"])


@router.post("", response_model=TransferenciaOut)
def crear_transferencia(
    datos: SolicitudTransferencia,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=64),
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    """Transfiere desde una cuenta propia. Requiere login (Bearer) e Idempotency-Key (UUID sugerido).

    Repetir la solicitud con la misma Idempotency-Key devuelve el mismo resultado sin debitar
    dos veces. Si banco_destino es este banco es una transferencia interna; la interbancaria
    todavía responde 501.
    """
    t = transferir(db, usuario.titular_id, datos, idempotency_key)
    return TransferenciaOut(
        transfer_id=t.transfer_id,
        estado=t.estado,
        motivo_rechazo=t.motivo_rechazo,
        tipo=t.tipo,
        cuenta_origen=datos.cuenta_origen,
        banco_destino=t.banco_destino,
        cuenta_destino=t.cuenta_destino,
        importe=t.importe,
        fecha_hora=t.creado_en,
    )
