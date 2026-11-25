"""Transferencias iniciadas por clientes de este banco.

Interna (doc 02, sección 2): una sola transacción local.
  1. Idempotencia: si la Idempotency-Key ya se usó, se devuelve el resultado guardado.
  2. Bloqueo de ambas cuentas con SELECT ... FOR UPDATE en orden de número de cuenta,
     para que dos transferencias cruzadas (A->B y B->A) no se bloqueen entre sí.
  3. Validaciones con los datos ya bloqueados: estados, límites y saldo.
  4. Débito, crédito, movimientos, consumo diario y registro, todo en el mismo COMMIT.
Los rechazos también se registran (RECHAZADA) para que un reintento devuelva el mismo error.
"""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.firma import hash_solicitud
from app.models import ConfigBanco, ConsumoDiario, Cuenta, EventoOperacion, Movimiento, Transferencia
from app.schemas import SolicitudTransferencia

MENSAJES = {
    "CUENTA_NO_EXISTE": "La cuenta destino no existe en este banco",
    "CUENTA_INACTIVA": "La cuenta de origen o de destino está bloqueada o cerrada",
    "FONDOS_INSUFICIENTES": "El saldo disponible no alcanza",
    "LIMITE_EXCEDIDO": "Se supera el límite por operación o el límite diario",
}


def transferir(
    db: Session, titular_id: int, datos: SolicitudTransferencia, idempotency_key: str
) -> Transferencia:
    """Ejecuta la transferencia y devuelve el registro COMPLETADA.

    Si se rechaza, lanza ApiError con el código del catálogo (también en los reintentos).
    """
    # El titular entra en el hash: la misma clave usada por otro cliente es un conflicto.
    request_hash = hash_solicitud({**datos.model_dump(mode="json"), "titular_id": titular_id})
    previa = _buscar_previa(db, idempotency_key, request_hash)
    if previa is not None:
        return _resultado(previa)

    if datos.banco_destino != get_settings().banco_id:
        raise ApiError("ERROR_INTERNO", "Transferencia interbancaria pendiente de implementar", 501)
    if datos.cuenta_origen == datos.cuenta_destino:
        raise ApiError("SOLICITUD_INVALIDA", "La cuenta de origen y de destino son la misma")

    try:
        transferencia = _interna(db, titular_id, datos, idempotency_key, request_hash)
        db.commit()
    except IntegrityError:
        # Otra solicitud con la misma clave llegó primero: nada de esta se aplicó.
        db.rollback()
        previa = _buscar_previa(db, idempotency_key, request_hash)
        if previa is None:
            raise
        return _resultado(previa)
    except BaseException:
        db.rollback()
        raise
    return _resultado(transferencia)


def _buscar_previa(db: Session, clave: str, request_hash: str) -> Transferencia | None:
    previa = db.scalar(select(Transferencia).where(Transferencia.idempotency_key == clave))
    if previa is not None and previa.request_hash != request_hash:
        raise ApiError("CONFLICTO_IDEMPOTENCIA", "La Idempotency-Key ya se usó con otros datos")
    return previa


def _resultado(t: Transferencia) -> Transferencia:
    if t.estado == "RECHAZADA":
        raise ApiError(t.motivo_rechazo, MENSAJES.get(t.motivo_rechazo, "Transferencia rechazada"))
    return t


def _bloquear(db: Session, numero: str) -> Cuenta | None:
    return db.scalar(
        select(Cuenta)
        .where(Cuenta.numero_cuenta == numero)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _interna(
    db: Session,
    titular_id: int,
    datos: SolicitudTransferencia,
    clave: str,
    request_hash: str,
) -> Transferencia:
    cuentas = {n: _bloquear(db, n) for n in sorted([datos.cuenta_origen, datos.cuenta_destino])}
    origen, destino = cuentas[datos.cuenta_origen], cuentas[datos.cuenta_destino]
    if origen is None or origen.titular_id != titular_id:
        raise ApiError("PROHIBIDO", "La cuenta de origen no pertenece al usuario")

    cfg = db.get(ConfigBanco, 1)
    fecha_local = datetime.now(ZoneInfo(cfg.zona_horaria)).date()
    # Lectura con bloqueo: una lectura común vería la foto del inicio de la transacción.
    consumo = db.scalar(
        select(ConsumoDiario)
        .where(ConsumoDiario.cuenta_id == origen.id, ConsumoDiario.fecha_local == fecha_local)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    usado_hoy = consumo.total if consumo else 0

    t = Transferencia(
        transfer_id=str(uuid.uuid4()),
        idempotency_key=clave,
        request_hash=request_hash,
        tipo="INTERNA",
        cuenta_origen_id=origen.id,
        banco_destino=get_settings().banco_id,
        cuenta_destino=datos.cuenta_destino,
        cuenta_destino_id=destino.id if destino else None,
        importe=datos.importe,
        concepto=datos.concepto,
        fecha_local=fecha_local,
    )

    motivo = _validar(cfg, origen, destino, datos.importe, usado_hoy)
    if motivo:
        t.estado, t.motivo_rechazo = "RECHAZADA", motivo
    else:
        origen.saldo -= datos.importe
        destino.saldo += datos.importe
        for cuenta, tipo in ((origen, "DEBITO"), (destino, "CREDITO")):
            db.add(
                Movimiento(
                    cuenta_id=cuenta.id,
                    tipo=tipo,
                    importe=datos.importe,
                    saldo_post=cuenta.saldo,
                    reservado_post=cuenta.saldo_reservado,
                    origen="TRANSFERENCIA",
                    referencia_id=t.transfer_id,
                )
            )
        if consumo:
            consumo.total += datos.importe
        else:
            db.add(ConsumoDiario(cuenta_id=origen.id, fecha_local=fecha_local, total=datos.importe))
        t.estado = "COMPLETADA"

    db.add(t)
    db.add(
        EventoOperacion(
            entidad="TRANSFERENCIA",
            referencia_id=t.transfer_id,
            estado_nuevo=t.estado,
            codigo_error=t.motivo_rechazo,
        )
    )
    db.flush()  # la UNIQUE de idempotency_key salta acá si hubo una carrera
    return t


def _validar(
    cfg: ConfigBanco, origen: Cuenta, destino: Cuenta | None, importe: int, usado_hoy: int
) -> str | None:
    """Devuelve el código de rechazo o None. Orden: estados, límites, saldo."""
    if origen.estado != "ACTIVA":
        return "CUENTA_INACTIVA"
    if destino is None:
        return "CUENTA_NO_EXISTE"
    if destino.estado != "ACTIVA":
        return "CUENTA_INACTIVA"
    if importe > cfg.max_transferencia:
        return "LIMITE_EXCEDIDO"
    if usado_hoy + importe > cfg.max_diario_por_cuenta:
        return "LIMITE_EXCEDIDO"
    if origen.saldo_disponible < importe:
        return "FONDOS_INSUFICIENTES"
    return None
