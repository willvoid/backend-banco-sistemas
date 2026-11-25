from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.main import app
from app.models import Sesion, Usuario
from app.passwords import hash_secreto, verificar_secreto


@pytest.fixture
def secreto(monkeypatch):
    monkeypatch.setattr(get_settings(), "jwt_secret", "x" * 40)


class FakeDB:
    """Sesión de base de datos mínima: guarda objetos por id y cuenta los commits."""

    def __init__(self, *objetos):
        self.objetos = {o.id: o for o in objetos}
        self.commits = 0

    def get(self, _modelo, clave):
        return self.objetos.get(clave)

    def add(self, objeto):
        self.objetos[objeto.id] = objeto

    def commit(self):
        self.commits += 1


def _sesion(**kw):
    ahora = auth.ahora_utc()
    datos = dict(
        id="s1",
        usuario_id=1,
        ultima_actividad=ahora,
        expira_en=ahora + timedelta(minutes=30),
        cerrada_en=None,
        motivo_cierre=None,
    )
    datos.update(kw)
    return Sesion(**datos)


def _cliente():
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


# --- contraseñas ---------------------------------------------------------------------------
def test_hash_y_verificacion():
    h = hash_secreto("clave-secreta")
    assert h != "clave-secreta"
    assert verificar_secreto("clave-secreta", h)
    assert not verificar_secreto("otra", h)


def test_hash_distinto_cada_vez():
    assert hash_secreto("igual") != hash_secreto("igual")


def test_verificar_con_hash_roto_no_lanza():
    assert not verificar_secreto("x", "no-es-un-hash")


def test_secreto_demasiado_largo_se_rechaza():
    with pytest.raises(ApiError):
        hash_secreto("a" * 100)


# --- token ---------------------------------------------------------------------------------
def test_token_lleva_el_id_de_sesion(secreto):
    db = FakeDB()
    token, sesion = auth.abrir_sesion(db, Usuario(id=7, titular_id=3))
    claims = auth.decodificar_token(token)
    assert claims["sub"] == "7" and claims["jti"] == sesion.id
    assert db.objetos[sesion.id] is sesion


def test_token_alterado_o_con_otra_clave_se_rechaza(secreto, monkeypatch):
    token, _ = auth.abrir_sesion(FakeDB(), Usuario(id=7, titular_id=3))
    with pytest.raises(ApiError) as e:
        auth.decodificar_token(token[:-2] + "xx")
    assert e.value.error == "NO_AUTENTICADO"
    monkeypatch.setattr(get_settings(), "jwt_secret", "y" * 40)
    with pytest.raises(ApiError):
        auth.decodificar_token(token)


def test_sin_secreto_configurado_no_emite_tokens(monkeypatch):
    monkeypatch.setattr(get_settings(), "jwt_secret", "corto")
    with pytest.raises(RuntimeError):
        auth.abrir_sesion(FakeDB(), Usuario(id=1, titular_id=1))


# --- inactividad y vida de la sesión ----------------------------------------------------------
def test_sesion_activa_renueva_la_ultima_actividad():
    antes = auth.ahora_utc() - timedelta(seconds=100)
    s = _sesion(ultima_actividad=antes)
    db = FakeDB(s)
    assert auth.validar_sesion(db, "s1") is s
    assert s.ultima_actividad > antes
    assert s.cerrada_en is None


def test_inactividad_cierra_la_sesion(monkeypatch):
    monkeypatch.setattr(get_settings(), "inactividad_seg", 300)
    s = _sesion(ultima_actividad=auth.ahora_utc() - timedelta(seconds=301))
    db = FakeDB(s)
    with pytest.raises(ApiError) as e:
        auth.validar_sesion(db, "s1")
    assert e.value.error == "SESION_EXPIRADA" and e.value.status == 401
    assert s.cerrada_en is not None and s.motivo_cierre == "INACTIVIDAD"
    # Una vez cerrada, ya no sirve ni siquiera con actividad nueva.
    with pytest.raises(ApiError) as e2:
        auth.validar_sesion(db, "s1")
    assert e2.value.error == "NO_AUTENTICADO"


def test_justo_dentro_del_limite_de_inactividad_sigue_viva(monkeypatch):
    monkeypatch.setattr(get_settings(), "inactividad_seg", 300)
    s = _sesion(ultima_actividad=auth.ahora_utc() - timedelta(seconds=290))
    auth.validar_sesion(FakeDB(s), "s1")
    assert s.cerrada_en is None


def test_tope_absoluto_cierra_aunque_haya_actividad():
    s = _sesion(expira_en=auth.ahora_utc() - timedelta(seconds=1))
    with pytest.raises(ApiError) as e:
        auth.validar_sesion(FakeDB(s), "s1")
    assert e.value.error == "SESION_EXPIRADA"
    assert s.motivo_cierre == "EXPIRADA"


def test_sesion_cerrada_o_inexistente_es_no_autenticado():
    with pytest.raises(ApiError) as e:
        auth.validar_sesion(FakeDB(_sesion(cerrada_en=auth.ahora_utc(), motivo_cierre="LOGOUT")), "s1")
    assert e.value.error == "NO_AUTENTICADO"
    with pytest.raises(ApiError):
        auth.validar_sesion(FakeDB(), "no-existe")


def test_logout_cierra_con_motivo():
    s = _sesion()
    auth.cerrar_sesion(FakeDB(s), s, "LOGOUT")
    assert s.motivo_cierre == "LOGOUT" and s.cerrada_en is not None


def test_cerrar_dos_veces_no_pisa_el_motivo():
    s = _sesion()
    db = FakeDB(s)
    auth.cerrar_sesion(db, s, "INACTIVIDAD")
    auth.cerrar_sesion(db, s, "LOGOUT")
    assert s.motivo_cierre == "INACTIVIDAD"


# --- API ---------------------------------------------------------------------------------------
def test_me_sin_token_es_401():
    r = _cliente().get("/auth/me")
    assert r.status_code == 401
    assert r.json()["error"] == "NO_AUTENTICADO"


def test_me_con_token_basura_es_401(secreto):
    r = _cliente().get("/auth/me", headers={"Authorization": "Bearer basura"})
    assert r.status_code == 401


def test_logout_sin_token_es_401():
    assert _cliente().post("/auth/logout").status_code == 401


def test_login_con_cuerpo_invalido_es_400():
    r = _cliente().post("/auth/login", json={"documento": "1"})
    assert r.status_code == 400
    assert r.json()["error"] == "SOLICITUD_INVALIDA"


def test_transferencias_exige_login():
    r = _cliente().post(
        "/transferencias",
        json={"cuenta_origen": "1", "banco_destino": "BCO2", "cuenta_destino": "2", "importe": 1},
        headers={"Idempotency-Key": "k"},
    )
    assert r.status_code == 401
