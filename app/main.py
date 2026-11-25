import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import registrar_manejadores
from app.routers import auth, cajero, cuentas, interbancario, transferencias

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("banco")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if not s.private_key_path.exists():
        log.warning(
            "No existe %s: generar con 'python -m scripts.generar_claves'. "
            "Sin clave privada no se pueden firmar llamadas a otros bancos.",
            s.private_key_path,
        )
    yield


app = FastAPI(
    title="Banco - API de interoperabilidad (BCP simulado)",
    version="2.1.0",
    description="Implementa el contrato de 05_openapi_v2.yaml para un banco del sistema simulado.",
    lifespan=lifespan,
)
registrar_manejadores(app)

# CORS: sin esto el navegador del frontend (otra máquina/puerto) bloquea las respuestas.
# Se autentica con Bearer (sin cookies), por eso "*" es aceptable en desarrollo.
_origenes = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
if _origenes:
    app.add_middleware(
        CORSMiddleware, allow_origins=_origenes, allow_methods=["*"], allow_headers=["*"]
    )

app.include_router(auth.router)
app.include_router(interbancario.router)
app.include_router(cuentas.router)
app.include_router(cajero.router)
app.include_router(transferencias.router)


@app.get("/health", tags=["Sistema"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        log.exception("Health: base de datos no disponible")
        return JSONResponse(status_code=503, content={"estado": "sin_base_de_datos"})
    return {"estado": "ok", "banco_id": get_settings().banco_id}
