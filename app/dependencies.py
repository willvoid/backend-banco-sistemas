from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.firma import cadena_canonica, verificar
from app.models import Banco, SolicitudEntrante


@dataclass
class Llamador:
    """Banco que llamó, ya autenticado por firma."""

    banco_id: str
    idempotency_key: str
    cuerpo: bytes


def _registrar_rechazo(db: Session, request: Request, status: int, codigo: str) -> None:
    h = request.headers
    db.add(
        SolicitudEntrante(
            banco_declarado=(h.get("x-bank-id") or "")[:10] or None,
            metodo=request.method[:7],
            ruta=request.url.path[:255],
            idempotency_key=(h.get("x-idempotency-key") or "")[:64] or None,
            x_timestamp=(h.get("x-timestamp") or "")[:40] or None,
            firma_valida=False,
            http_status=status,
            codigo_error=codigo,
        )
    )
    db.commit()


def _verificar(db: Session, request: Request, cuerpo: bytes) -> Llamador:
    h = request.headers
    banco_id, clave, ts, firma = (
        h.get("x-bank-id"),
        h.get("x-idempotency-key"),
        h.get("x-timestamp"),
        h.get("x-signature"),
    )
    if not (banco_id and clave and ts and firma):
        raise ApiError(
            "SOLICITUD_INVALIDA",
            "Faltan headers: X-Bank-Id, X-Timestamp, X-Idempotency-Key, X-Signature",
        )

    try:
        banco = db.get(Banco, banco_id)
        if banco is None or not banco.activo:
            raise ApiError("BANCO_NO_RECONOCIDO", f"banco_id desconocido: {banco_id}")

        canonica = cadena_canonica(
            request.method, request.url.path, banco_id, clave, ts, cuerpo.decode("utf-8")
        )
        if not verificar(banco.clave_publica_pem, canonica, firma):
            raise ApiError("FIRMA_INVALIDA", "La firma no verifica contra la clave pública")

        try:
            instante = datetime.fromisoformat(ts)
        except ValueError:
            raise ApiError("SOLICITUD_INVALIDA", "X-Timestamp no es ISO 8601") from None
        if instante.tzinfo is None:
            instante = instante.replace(tzinfo=timezone.utc)
        desfase = abs((datetime.now(timezone.utc) - instante).total_seconds())
        if desfase > get_settings().ventana_firma_seg:
            raise ApiError("TIMESTAMP_FUERA_DE_RANGO", "X-Timestamp fuera de la ventana permitida")
    except ApiError as exc:
        _registrar_rechazo(db, request, exc.status, exc.error)
        raise

    return Llamador(banco_id=banco_id, idempotency_key=clave, cuerpo=cuerpo)


async def verificar_firma(request: Request, db: Session = Depends(get_db)) -> Llamador:
    """Autentica al banco que llama (doc 02, sección 1). Usar en los endpoints interbancarios."""
    cuerpo = await request.body()
    return await run_in_threadpool(_verificar, db, request, cuerpo)
