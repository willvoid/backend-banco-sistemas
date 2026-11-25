"""Cliente para llamar a OTROS bancos con la firma de este banco."""

import json
import uuid
from datetime import datetime, timezone

import httpx

from app.config import get_settings
from app.firma import cadena_canonica, cargar_clave_privada, firmar
from app.models import Banco


def llamar_banco(
    banco: Banco,
    metodo: str,
    ruta: str,
    cuerpo: dict | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    """Envía una solicitud firmada. Puede lanzar httpx.TimeoutException / httpx.TransportError.

    Un timeout NO es un resultado (doc 01, sección 6): quien llama debe consultar el
    estado con GET antes de reintentar o revertir. El cuerpo se firma tal cual se envía.
    """
    s = get_settings()
    texto = json.dumps(cuerpo, separators=(",", ":"), ensure_ascii=False) if cuerpo else ""
    clave_idem = idempotency_key or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonica = cadena_canonica(metodo, ruta, s.banco_id, clave_idem, ts, texto)
    headers = {
        "X-Bank-Id": s.banco_id,
        "X-Timestamp": ts,
        "X-Idempotency-Key": clave_idem,
        "X-Signature": firmar(cargar_clave_privada(s.private_key_path), canonica),
        "Content-Type": "application/json",
    }
    timeout = banco.timeout_ms / 1000 if banco.timeout_ms else s.timeout_otros_bancos_seg
    return httpx.request(
        metodo.upper(),
        banco.url_base.rstrip("/") + ruta,
        content=texto.encode("utf-8") if texto else None,
        headers=headers,
        timeout=timeout,
    )
