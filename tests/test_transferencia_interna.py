"""Transferencia interna contra MySQL (ver tests/conftest.py para cómo correrlos)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import usuario_actual
from app.database import get_db
from app.errors import ApiError
from app.main import app
from app.models import ConsumoDiario, Cuenta, Movimiento, Transferencia, Usuario
from app.schemas import SolicitudTransferencia
from app.services.transferencias import transferir
from tests.conftest import en_paralelo

# Datos del esquema: titular 1 = Ana (1001, 1004 bloqueada), 2 = Bruno (1002, 1005 cerrada),
# 3 = Carla (1003). Saldos: 1001=5.000.000, 1002=1.000.000, 1003=50.000.000.
ANA, BRUNO, CARLA = 1, 2, 3


def _sol(origen, destino, importe, banco="BCO1"):
    return SolicitudTransferencia(
        cuenta_origen=origen, banco_destino=banco, cuenta_destino=destino, importe=importe
    )


def _hacer(S, titular, sol, clave=None):
    with S() as db:
        return transferir(db, titular, sol, clave or str(uuid.uuid4()))


def _error(S, titular, sol, clave=None) -> str:
    with pytest.raises(ApiError) as e:
        _hacer(S, titular, sol, clave)
    return e.value.error


def _saldo(S, numero):
    with S() as db:
        return db.scalar(select(Cuenta.saldo).where(Cuenta.numero_cuenta == numero))


def _total(S):
    with S() as db:
        return db.scalar(select(func.sum(Cuenta.saldo)))


def _contar(S, modelo):
    with S() as db:
        return db.scalar(select(func.count()).select_from(modelo))


# --- Plan de pruebas, sección A -------------------------------------------------------------
def test_caso1_exitosa_mueve_exactamente_el_importe(S):
    total = _total(S)
    t = _hacer(S, ANA, _sol("1001", "1002", 150_000))
    assert t.estado == "COMPLETADA" and t.tipo == "INTERNA"
    assert _saldo(S, "1001") == 4_850_000
    assert _saldo(S, "1002") == 1_150_000
    assert _total(S) == total  # no se crea ni se destruye dinero
    with S() as db:
        movs = db.scalars(select(Movimiento).where(Movimiento.referencia_id == t.transfer_id)).all()
        assert sorted(m.tipo for m in movs) == ["CREDITO", "DEBITO"]
        assert db.scalar(select(ConsumoDiario.total)) == 150_000


def test_caso2_fondos_insuficientes_no_cambia_saldos(S):
    assert _error(S, BRUNO, _sol("1002", "1001", 1_000_001)) == "FONDOS_INSUFICIENTES"
    assert _saldo(S, "1002") == 1_000_000 and _saldo(S, "1001") == 5_000_000
    with S() as db:  # el rechazo queda registrado para auditoría e idempotencia
        t = db.scalar(select(Transferencia))
        assert t.estado == "RECHAZADA" and t.motivo_rechazo == "FONDOS_INSUFICIENTES"
    assert _contar(S, Movimiento) == 0


def test_caso3_destino_inexistente(S):
    assert _error(S, ANA, _sol("1001", "9999", 1_000)) == "CUENTA_NO_EXISTE"
    assert _saldo(S, "1001") == 5_000_000


def test_caso4_origen_bloqueada_o_cerrada(S):
    assert _error(S, ANA, _sol("1004", "1001", 1_000)) == "CUENTA_INACTIVA"
    assert _error(S, BRUNO, _sol("1005", "1002", 1_000)) == "CUENTA_INACTIVA"


def test_destino_cerrada(S):
    assert _error(S, ANA, _sol("1001", "1005", 1_000)) == "CUENTA_INACTIVA"
    assert _saldo(S, "1001") == 5_000_000


def test_caso5_limite_por_operacion(S):
    assert _error(S, CARLA, _sol("1003", "1001", 10_000_001)) == "LIMITE_EXCEDIDO"
    assert _hacer(S, CARLA, _sol("1003", "1001", 10_000_000)).estado == "COMPLETADA"


def test_limite_diario_combinado(S):
    _hacer(S, CARLA, _sol("1003", "1001", 6_000_000))
    assert _error(S, CARLA, _sol("1003", "1001", 5_000_000)) == "LIMITE_EXCEDIDO"
    _hacer(S, CARLA, _sol("1003", "1001", 4_000_000))  # 10.000.000 justos
    assert _error(S, CARLA, _sol("1003", "1001", 1)) == "LIMITE_EXCEDIDO"


# --- Idempotencia (casos 10 y 13 aplicados a la interna) --------------------------------------
def test_reintento_con_la_misma_clave_no_debita_dos_veces(S):
    clave = str(uuid.uuid4())
    t1 = _hacer(S, ANA, _sol("1001", "1002", 200_000), clave)
    t2 = _hacer(S, ANA, _sol("1001", "1002", 200_000), clave)
    assert t1.transfer_id == t2.transfer_id
    assert _saldo(S, "1001") == 4_800_000
    assert _contar(S, Transferencia) == 1


def test_misma_clave_con_otros_datos_es_conflicto(S):
    clave = str(uuid.uuid4())
    _hacer(S, ANA, _sol("1001", "1002", 200_000), clave)
    assert _error(S, ANA, _sol("1001", "1002", 999), clave) == "CONFLICTO_IDEMPOTENCIA"
    assert _saldo(S, "1001") == 4_800_000


def test_misma_clave_de_otro_cliente_es_conflicto(S):
    clave = str(uuid.uuid4())
    _hacer(S, ANA, _sol("1001", "1002", 1_000), clave)
    assert _error(S, BRUNO, _sol("1001", "1002", 1_000), clave) == "CONFLICTO_IDEMPOTENCIA"


def test_reintento_de_un_rechazo_devuelve_el_mismo_error(S):
    clave = str(uuid.uuid4())
    assert _error(S, BRUNO, _sol("1002", "1001", 5_000_000), clave) == "FONDOS_INSUFICIENTES"
    assert _error(S, BRUNO, _sol("1002", "1001", 5_000_000), clave) == "FONDOS_INSUFICIENTES"
    assert _contar(S, Transferencia) == 1


# --- Reglas propias de la API -------------------------------------------------------------------
def test_cuenta_ajena_es_prohibido_y_no_se_registra(S):
    assert _error(S, ANA, _sol("1002", "1001", 1_000)) == "PROHIBIDO"
    assert _contar(S, Transferencia) == 0


def test_misma_cuenta_es_solicitud_invalida(S):
    assert _error(S, ANA, _sol("1001", "1001", 1_000)) == "SOLICITUD_INVALIDA"


def test_interbancaria_todavia_no_implementada(S):
    with pytest.raises(ApiError) as e:
        _hacer(S, ANA, _sol("1001", "2001", 1_000, banco="BCO2"))
    assert e.value.status == 501


# --- Concurrencia (caso 14) ------------------------------------------------------------------------
def test_caso14_dos_debitos_simultaneos_solo_uno_pasa(S):
    r = en_paralelo([lambda: _hacer(S, BRUNO, _sol("1002", "1001", 700_000)).estado] * 2)
    assert sorted(r) == ["COMPLETADA", "FONDOS_INSUFICIENTES"]
    assert _saldo(S, "1002") == 300_000


def test_transferencias_cruzadas_simultaneas_no_se_bloquean(S):
    total = _total(S)
    ida = lambda: _hacer(S, ANA, _sol("1001", "1002", 1_000)).estado  # noqa: E731
    vuelta = lambda: _hacer(S, BRUNO, _sol("1002", "1001", 1_000)).estado  # noqa: E731
    r = en_paralelo([ida, vuelta] * 8)
    assert r == ["COMPLETADA"] * 16
    assert _saldo(S, "1001") == 5_000_000 and _saldo(S, "1002") == 1_000_000
    assert _total(S) == total


def test_misma_clave_en_paralelo_debita_una_sola_vez(S):
    clave = str(uuid.uuid4())
    r = en_paralelo([lambda: _hacer(S, ANA, _sol("1001", "1002", 50_000), clave).transfer_id] * 4)
    assert len(set(r)) == 1, r
    assert _saldo(S, "1001") == 4_950_000
    assert _contar(S, Transferencia) == 1


# --- Endpoint HTTP ----------------------------------------------------------------------------------
def test_endpoint_devuelve_la_transferencia(S):
    def db():
        with S() as sesion:
            yield sesion

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[usuario_actual] = lambda: Usuario(id=1, titular_id=ANA)
    try:
        cliente = TestClient(app, raise_server_exceptions=False)
        cuerpo = {"cuenta_origen": "1001", "banco_destino": "BCO1", "cuenta_destino": "1002", "importe": 1234}
        r = cliente.post("/transferencias", json=cuerpo, headers={"Idempotency-Key": "k-1"})
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "COMPLETADA" and r.json()["importe"] == 1234
        r2 = cliente.post("/transferencias", json={**cuerpo, "importe": 10**9}, headers={"Idempotency-Key": "k-2"})
        assert r2.status_code == 409 and r2.json()["error"] == "LIMITE_EXCEDIDO"
        assert cliente.post("/transferencias", json=cuerpo).status_code == 400  # sin Idempotency-Key
    finally:
        app.dependency_overrides.clear()
