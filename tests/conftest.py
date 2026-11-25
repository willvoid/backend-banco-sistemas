"""Fixtures compartidas.

Los tests que usan `S` corren contra un MySQL real (FOR UPDATE, UNIQUE y CHECK no se pueden
simular) y se saltean si no se define TEST_MYSQL_PORT. Usan la base `banco_test`, que se
borra y se recrea en cada test: NO apuntar a una base con datos reales.

  $env:TEST_MYSQL_PORT=3306; $env:TEST_MYSQL_PASSWORD="..."; python -m pytest
"""

import os
import threading
from pathlib import Path

import pymysql
import pytest
from pymysql.constants import CLIENT
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.errors import ApiError

DB = "banco_test"
ESQUEMA = Path(__file__).resolve().parent.parent / "db" / "schema_banco_familiar.sql"


def _credenciales():
    return dict(
        host=os.getenv("TEST_MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ["TEST_MYSQL_PORT"]),
        user=os.getenv("TEST_MYSQL_USER", "root"),
        password=os.getenv("TEST_MYSQL_PASSWORD", ""),
    )


@pytest.fixture
def S():
    """sessionmaker sobre una base banco_test recién creada con los datos de ejemplo."""
    if not os.getenv("TEST_MYSQL_PORT"):
        pytest.skip("Definir TEST_MYSQL_PORT para correr los tests contra MySQL")
    c = _credenciales()
    sql = ESQUEMA.read_text(encoding="utf-8").replace("banco_familiar", DB)
    con = pymysql.connect(
        **c, charset="utf8mb4", client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True
    )
    try:
        with con.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {DB}")
            cur.execute(sql)
            while cur.nextset():
                pass
    finally:
        con.close()
    engine = create_engine(
        URL.create(
            "mysql+pymysql",
            username=c["user"],
            password=c["password"] or None,
            host=c["host"],
            port=c["port"],
            database=DB,
            query={"charset": "utf8mb4"},
        ),
        connect_args={"init_command": "SET time_zone = '+00:00'"},
        pool_size=20,
    )
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


def en_paralelo(funciones):
    """Arranca todas las funciones a la vez y devuelve sus resultados (o el código de error)."""
    barrera = threading.Barrier(len(funciones))
    resultados = [None] * len(funciones)

    def correr(i, f):
        barrera.wait()
        try:
            resultados[i] = f()
        except ApiError as e:
            resultados[i] = e.error
        except Exception as e:  # deadlocks u otros errores de base harían fallar el test
            resultados[i] = repr(e)

    hilos = [threading.Thread(target=correr, args=(i, f)) for i, f in enumerate(funciones)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    return resultados
