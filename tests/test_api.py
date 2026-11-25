from fastapi.testclient import TestClient
from sqlalchemy.orm import configure_mappers

from app.database import get_db
from app.main import app


def _cliente():
    # Estos casos se rechazan antes de tocar la base: no hace falta MySQL.
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def test_modelos_mapean_bien():
    configure_mappers()


def test_sin_headers_de_firma_es_solicitud_invalida():
    r = _cliente().get("/cuentas/1001")
    assert r.status_code == 400
    assert r.json()["error"] == "SOLICITUD_INVALIDA"


def test_ruta_inexistente_usa_el_formato_de_error():
    r = _cliente().get("/no-existe")
    assert r.status_code == 404
    assert set(r.json()) == {"error", "mensaje"}


def test_cors_permite_preflight_del_login(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cors_origins", "http://192.168.1.50:5173")
    import importlib

    import app.main as main

    importlib.reload(main)
    r = TestClient(main.app).options(
        "/auth/login",
        headers={
            "Origin": "http://192.168.1.50:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://192.168.1.50:5173"
