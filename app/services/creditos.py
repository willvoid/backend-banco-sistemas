"""Créditos interbancarios recibidos: este banco como DESTINO (doc 02, sección 4, paso 2).

El banco origen ya reservó el importe y pide acreditarlo. Este banco valida que la cuenta
exista y esté activa, y acredita de forma atómica. La respuesta siempre es 200 con estado
ACREDITADO o RECHAZADO (+ motivo), como pide el OpenAPI.

Idempotencia: la clave es (banco_origen, transfer_id). Si el origen reintenta tras un
timeout recibe el mismo resultado y no se acredita dos veces (caso 10 del plan).
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.firma import hash_solicitud
from app.models import CreditoInterbancario, Cuenta, EventoOperacion, Movimiento
from app.schemas import SolicitudCredito


def acreditar(db: Session, banco_llamador: str, datos: SolicitudCredito) -> CreditoInterbancario:
    """Devuelve el crédito registrado (ACREDITADO o RECHAZADO)."""
    if datos.banco_origen != banco_llamador:
        raise ApiError("SOLICITUD_INVALIDA", "banco_origen no coincide con X-Bank-Id")
    if datos.banco_origen == get_settings().banco_id:
        raise ApiError("SOLICITUD_INVALIDA", "Un banco no puede enviarse un crédito a sí mismo")

    transfer_id = str(datos.transfer_id)
    request_hash = hash_solicitud(datos.model_dump(mode="json"))
    previo = _buscar_previo(db, datos.banco_origen, transfer_id, request_hash)
    if previo is not None:
        return previo

    try:
        credito = _registrar(db, datos, transfer_id, request_hash)
        db.commit()
    except IntegrityError:
        # El mismo crédito llegó dos veces a la vez y el otro ganó: nada de este se aplicó.
        db.rollback()
        previo = _buscar_previo(db, datos.banco_origen, transfer_id, request_hash)
        if previo is None:
            raise
        return previo
    except BaseException:
        db.rollback()
        raise
    return credito


def consultar(db: Session, banco_llamador: str, transfer_id: str) -> CreditoInterbancario:
    """Estado de un crédito ya solicitado. Cada banco solo ve los suyos."""
    credito = db.scalar(
        select(CreditoInterbancario).where(
            CreditoInterbancario.banco_origen == banco_llamador,
            CreditoInterbancario.transfer_id == transfer_id,
        )
    )
    if credito is None:
        raise ApiError("NO_ENCONTRADO", "No hay registro de ese transfer_id")
    return credito


def _buscar_previo(
    db: Session, banco_origen: str, transfer_id: str, request_hash: str
) -> CreditoInterbancario | None:
    previo = db.scalar(
        select(CreditoInterbancario).where(
            CreditoInterbancario.banco_origen == banco_origen,
            CreditoInterbancario.transfer_id == transfer_id,
        )
    )
    if previo is not None and previo.request_hash != request_hash:
        raise ApiError("CONFLICTO_IDEMPOTENCIA", "Ese transfer_id ya se usó con otros datos")
    return previo


def _registrar(
    db: Session, datos: SolicitudCredito, transfer_id: str, request_hash: str
) -> CreditoInterbancario:
    cuenta = db.scalar(
        select(Cuenta)
        .where(Cuenta.numero_cuenta == datos.cuenta_destino)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    credito = CreditoInterbancario(
        transfer_id=transfer_id,
        banco_origen=datos.banco_origen,
        cuenta_origen=datos.cuenta_origen,
        cuenta_destino=datos.cuenta_destino,
        cuenta_destino_id=cuenta.id if cuenta else None,
        importe=datos.importe,
        moneda=datos.moneda,
        concepto=datos.concepto,
        request_hash=request_hash,
    )

    if cuenta is None:
        credito.estado, credito.motivo_rechazo = "RECHAZADO", "CUENTA_NO_EXISTE"
    elif cuenta.estado != "ACTIVA":
        credito.estado, credito.motivo_rechazo = "RECHAZADO", "CUENTA_INACTIVA"
    else:
        cuenta.saldo += datos.importe
        db.add(
            Movimiento(
                cuenta_id=cuenta.id,
                tipo="CREDITO",
                importe=datos.importe,
                saldo_post=cuenta.saldo,
                reservado_post=cuenta.saldo_reservado,
                origen="CREDITO_INTERBANCARIO",
                referencia_id=transfer_id,
            )
        )
        credito.estado = "ACREDITADO"

    db.add(credito)
    db.add(
        EventoOperacion(
            entidad="CREDITO_INTERBANCARIO",
            referencia_id=transfer_id,
            estado_nuevo=credito.estado,
            codigo_error=credito.motivo_rechazo,
            detalle=f"desde {datos.banco_origen}/{datos.cuenta_origen}",
        )
    )
    db.flush()  # la UNIQUE (banco_origen, transfer_id) salta acá si hubo una carrera
    return credito
