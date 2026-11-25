import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("banco")

# Catálogo mínimo del doc 02, sección 6.
HTTP_POR_CODIGO = {
    "CUENTA_NO_EXISTE": 404,
    "CUENTA_INACTIVA": 409,
    "FONDOS_INSUFICIENTES": 409,
    "LIMITE_EXCEDIDO": 409,
    "BANCO_NO_RECONOCIDO": 400,
    "SOLICITUD_INVALIDA": 400,
    "FIRMA_INVALIDA": 401,
    "TIMESTAMP_FUERA_DE_RANGO": 401,
    "CONFLICTO_IDEMPOTENCIA": 409,
    "NO_ENCONTRADO": 404,
    "ERROR_INTERNO": 500,
    # API propia del frontend (no son parte del contrato entre bancos).
    "NO_AUTENTICADO": 401,
    "SESION_EXPIRADA": 401,
    "CREDENCIALES_INVALIDAS": 401,
    "DEMASIADOS_INTENTOS": 429,
    "PROHIBIDO": 403,
    # No está en el catálogo oficial: a acordar con el BCP simulado (ver schema SQL).
    "PIN_INCORRECTO": 409,
}


class ApiError(Exception):
    """Error de negocio o de contrato. Se responde con {"error": ..., "mensaje": ...}."""

    def __init__(self, error: str, mensaje: str, status: int | None = None):
        super().__init__(mensaje)
        self.error = error
        self.mensaje = mensaje
        self.status = status or HTTP_POR_CODIGO.get(error, 500)


def _respuesta(status: int, error: str, mensaje: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "mensaje": mensaje})


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _respuesta(exc.status, exc.error, exc.mensaje)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError):
        # No se incluye "input": el cuerpo de una autorización de cajero trae el PIN.
        primero = exc.errors()[0]
        donde = ".".join(str(p) for p in primero["loc"] if p != "body")
        return _respuesta(400, "SOLICITUD_INVALIDA", f"{donde}: {primero['msg']}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        codigo = "NO_ENCONTRADO" if exc.status_code == 404 else "SOLICITUD_INVALIDA"
        return _respuesta(exc.status_code, codigo, str(exc.detail))

    @app.exception_handler(Exception)
    async def _inesperado(_: Request, exc: Exception):
        log.exception("Error no controlado")
        return _respuesta(500, "ERROR_INTERNO", "Error interno del banco")
