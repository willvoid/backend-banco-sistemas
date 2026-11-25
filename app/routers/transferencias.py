from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.auth import usuario_actual
from app.database import get_db
from app.errors import ApiError
from app.models import Usuario
from app.schemas import SolicitudTransferencia, TransferenciaOut

# API propia para el frontend de ESTE banco: no es parte del contrato entre bancos.
router = APIRouter(prefix="/transferencias", tags=["Transferencias (frontend propio)"])


@router.post("", response_model=TransferenciaOut)
def crear_transferencia(
    datos: SolicitudTransferencia,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    """Inicia una transferencia desde una cuenta propia.

    Requiere login (Bearer). TODO:
      - Verificar que cuenta_origen pertenece a usuario.titular_id; si no, ApiError("PROHIBIDO", ...).
      - banco_destino == banco propio -> interna (doc 02, sección 2): una sola transacción,
        bloqueo de ambas cuentas en orden ascendente de número de cuenta.
      - banco_destino distinto -> interbancaria (doc 02, sección 4): reservar, llamar con
        app.banco_client.llamar_banco(POST /interbancario/creditos), confirmar o liberar.
        Ante timeout consultar GET /interbancario/creditos/{transfer_id}; si el destino
        tampoco responde, dejar PENDIENTE.
    """
    raise ApiError("ERROR_INTERNO", "Pendiente de implementar", 501)
