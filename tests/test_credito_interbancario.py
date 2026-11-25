"""Crédito interbancario (este banco como destino) contra MySQL; ver tests/conftest.py.

Este banco es BCO1. Los créditos llegan "desde" BCO2/BCO3.
Saldos de ejemplo: 1002 = 1.000.000; 1004 bloqueada; 1005 cerrada.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.database import get_db
from app.errors import ApiError
from app.firma import cadena_canonica, firmar
from app.main import app
from app.models import Banco, CreditoInterbancario, Cuenta, EventoOperacion, Movimiento
from app.schemas import SolicitudCredito
from app.services.creditos import acreditar
from tests.conftest import en_paralelo


def _sol(cuenta_destino="1002", importe=150_000, banco_origen="BCO2", transfer_id=None, **kw):
    return SolicitudCredito(
        transfer_id=transfer_id or uuid.uuid4(),
        cuenta_destino=cuenta_destino,
        banco_origen=banco_origen,
        cuenta_origen="2001",
        importe=importe,
        moneda="PYG",
        **kw,
    )


def _acreditar(S, sol, llamador=None):
    with S() as db:
        return acreditar(db, llamador or sol.banco_origen, sol)


def _saldo(S, numero):
    with S() as db:
        return db.scalar(select(Cuenta.saldo).where(Cuenta.numero_cuenta == numero))


def _contar(S, modelo):
    with S() as db:
        return db.scalar(select(func.count()).select_from(modelo))


# --- Resultado del crédito ----------------------------------------------------------------------
def test_acredita_en_la_cuenta_destino(S):
    c = _acreditar(S, _sol())
    assert c.estado == "ACREDITADO" and c.motivo_rechazo is None
    assert _saldo(S, "1002") == 1_150_000
    with S() as db:
        mov = db.scalar(select(Movimiento))
        assert (mov.tipo, mov.importe, mov.origen) == ("CREDITO", 150_000, "CREDITO_INTERBANCARIO")
        assert mov.referencia_id == c.transfer_id
        assert db.scalar(select(EventoOperacion.estado_nuevo)) == "ACREDITADO"


def test_caso7_cuenta_inexistente_se_rechaza_y_queda_registrado(S):
    c = _acreditar(S, _sol(cuenta_destino="9999"))
    assert (c.estado, c.motivo_rechazo) == ("RECHAZADO", "CUENTA_NO_EXISTE")
    assert _contar(S, CreditoInterbancario) == 1
    assert _contar(S, Movimiento) == 0


@pytest.mark.parametrize("cuenta", ["1004", "1005"])  # bloqueada, cerrada
def test_cuenta_inactiva_se_rechaza_sin_acreditar(S, cuenta):
    antes = _saldo(S, cuenta)
    c = _acreditar(S, _sol(cuenta_destino=cuenta))
    assert (c.estado, c.motivo_rechazo) == ("RECHAZADO", "CUENTA_INACTIVA")
    assert _saldo(S, cuenta) == antes


# --- Idempotencia (caso 10) -----------------------------------------------------------------------
def test_caso10_reintento_devuelve_lo_mismo_sin_acreditar_dos_veces(S):
    sol = _sol()
    c1, c2 = _acreditar(S, sol), _acreditar(S, sol)
    assert c1.id == c2.id and c2.estado == "ACREDITADO"
    assert _saldo(S, "1002") == 1_150_000
    assert _contar(S, Movimiento) == 1


def test_reintento_de_un_rechazo_devuelve_el_mismo_rechazo(S):
    sol = _sol(cuenta_destino="9999")
    assert _acreditar(S, sol).estado == _acreditar(S, sol).estado == "RECHAZADO"
    assert _contar(S, CreditoInterbancario) == 1


def test_caso13_mismo_transfer_id_con_otro_importe_es_conflicto(S):
    tid = uuid.uuid4()
    _acreditar(S, _sol(transfer_id=tid, importe=100))
    with pytest.raises(ApiError) as e:
        _acreditar(S, _sol(transfer_id=tid, importe=999))
    assert e.value.error == "CONFLICTO_IDEMPOTENCIA" and e.value.status == 409
    assert _saldo(S, "1002") == 1_000_100


def test_mismo_transfer_id_desde_otro_banco_es_otro_credito(S):
    tid = uuid.uuid4()
    _acreditar(S, _sol(transfer_id=tid, banco_origen="BCO2"))
    _acreditar(S, _sol(transfer_id=tid, banco_origen="BCO3"))
    assert _saldo(S, "1002") == 1_300_000


def test_mismo_credito_en_paralelo_acredita_una_sola_vez(S):
    sol = _sol(importe=10_000)
    r = en_paralelo([lambda: _acreditar(S, sol).id] * 4)
    assert len(set(r)) == 1, r
    assert _saldo(S, "1002") == 1_010_000


def test_creditos_distintos_en_paralelo_a_la_misma_cuenta(S):
    r = en_paralelo([lambda: _acreditar(S, _sol(importe=1_000)).estado] * 10)
    assert r == ["ACREDITADO"] * 10
    assert _saldo(S, "1002") == 1_010_000


# --- Validaciones del llamador ----------------------------------------------------------------------
def test_banco_origen_distinto_al_que_firma_se_rechaza(S):
    with pytest.raises(ApiError) as e:
        _acreditar(S, _sol(banco_origen="BCO3"), llamador="BCO2")
    assert e.value.error == "SOLICITUD_INVALIDA"
    assert _contar(S, CreditoInterbancario) == 0


def test_credito_desde_el_propio_banco_se_rechaza(S):
    with pytest.raises(ApiError) as e:
        _acreditar(S, _sol(banco_origen="BCO1"))
    assert e.value.error == "SOLICITUD_INVALIDA"


# --- Por HTTP, con firma Ed25519 real ------------------------------------------------------------------
@pytest.fixture
def http(S):
    """Cliente HTTP contra banco_test y claves registradas para BCO2 y BCO3."""
    claves = {b: Ed25519PrivateKey.generate() for b in ("BCO2", "BCO3")}
    with S() as db:
        for banco, clave in claves.items():
            pem = clave.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            db.execute(update(Banco).where(Banco.banco_id == banco).values(clave_publica_pem=pem.decode()))
        db.commit()

    def db():
        with S() as sesion:
            yield sesion

    app.dependency_overrides[get_db] = db
    cliente = TestClient(app, raise_server_exceptions=False)

    def llamar(metodo, ruta, cuerpo=None, banco="BCO2", alterar=None):
        texto = json.dumps(cuerpo, separators=(",", ":")) if cuerpo is not None else ""
        clave_idem = cuerpo["transfer_id"] if cuerpo else str(uuid.uuid4())
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        firma = firmar(claves[banco], cadena_canonica(metodo, ruta, banco, clave_idem, ts, texto))
        if alterar:
            texto = alterar(texto)
        headers = {
            "X-Bank-Id": banco,
            "X-Timestamp": ts,
            "X-Idempotency-Key": clave_idem,
            "X-Signature": firma,
            "Content-Type": "application/json",
        }
        return cliente.request(metodo, ruta, content=texto.encode() or None, headers=headers)

    yield llamar
    app.dependency_overrides.clear()


def _cuerpo(**kw):
    datos = {
        "transfer_id": str(uuid.uuid4()),
        "cuenta_destino": "1002",
        "banco_origen": "BCO2",
        "cuenta_origen": "2001",
        "importe": 150000,
        "moneda": "PYG",
        "concepto": "prueba",
    }
    datos.update(kw)
    return datos


def test_http_credito_y_consulta(S, http):
    cuerpo = _cuerpo()
    r = http("POST", "/interbancario/creditos", cuerpo)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "ACREDITADO" and r.json()["fecha_hora"].endswith("Z")

    r = http("POST", "/interbancario/creditos", cuerpo)  # reintento tras "timeout"
    assert r.status_code == 200 and r.json()["estado"] == "ACREDITADO"
    assert _saldo(S, "1002") == 1_150_000

    ruta = f"/interbancario/creditos/{cuerpo['transfer_id']}"
    r = http("GET", ruta)
    assert r.status_code == 200 and r.json()["estado"] == "ACREDITADO"
    assert http("GET", ruta, banco="BCO3").status_code == 404  # otro banco no lo ve


def test_http_rechazo_responde_200_con_motivo(http):
    r = http("POST", "/interbancario/creditos", _cuerpo(cuenta_destino="9999"))
    assert r.status_code == 200
    assert r.json()["estado"] == "RECHAZADO" and r.json()["motivo_rechazo"] == "CUENTA_NO_EXISTE"


def test_http_cuerpo_alterado_despues_de_firmar_es_401(S, http):
    r = http(
        "POST",
        "/interbancario/creditos",
        _cuerpo(),
        alterar=lambda t: t.replace("150000", "9150000"),
    )
    assert r.status_code == 401 and r.json()["error"] == "FIRMA_INVALIDA"
    assert _saldo(S, "1002") == 1_000_000


def test_http_conflicto_de_idempotencia_es_409(http):
    cuerpo = _cuerpo()
    http("POST", "/interbancario/creditos", cuerpo)
    r = http("POST", "/interbancario/creditos", {**cuerpo, "importe": 1})
    assert r.status_code == 409 and r.json()["error"] == "CONFLICTO_IDEMPOTENCIA"


def test_http_campos_demasiado_largos_son_400(http):
    r = http("POST", "/interbancario/creditos", _cuerpo(concepto="x" * 256))
    assert r.status_code == 400 and r.json()["error"] == "SOLICITUD_INVALIDA"
