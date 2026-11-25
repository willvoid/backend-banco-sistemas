"""Envía una solicitud firmada a un banco, para probar a mano.

Ejemplos (desde banco_api/):
  python -m scripts.cliente_prueba GET /cuentas/1001
  python -m scripts.cliente_prueba POST /interbancario/creditos --cuerpo '{"transfer_id": "..."}'

Para probar contra tu propia API, cargá tu clave pública en la tabla bancos
(banco_id = el de --banco) y firmá con la privada correspondiente.
"""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.firma import cadena_canonica, cargar_clave_privada, firmar


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("metodo")
    ap.add_argument("ruta")
    ap.add_argument("--cuerpo", default="", help="JSON tal cual se enviará")
    ap.add_argument("--banco", default="BCO1", help="X-Bank-Id con el que se firma")
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--clave", default="keys/privada.pem")
    a = ap.parse_args()

    if a.cuerpo:
        json.loads(a.cuerpo)  # falla temprano si no es JSON válido
    clave_idem = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canonica = cadena_canonica(a.metodo, a.ruta, a.banco, clave_idem, ts, a.cuerpo)
    headers = {
        "X-Bank-Id": a.banco,
        "X-Timestamp": ts,
        "X-Idempotency-Key": clave_idem,
        "X-Signature": firmar(cargar_clave_privada(Path(a.clave)), canonica),
        "Content-Type": "application/json",
    }
    r = httpx.request(
        a.metodo.upper(),
        a.url + a.ruta,
        content=a.cuerpo.encode("utf-8") if a.cuerpo else None,
        headers=headers,
    )
    print(r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
