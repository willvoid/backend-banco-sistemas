"""Esquemas según 05_openapi_v2.yaml.

Los topes de importe (transferencia, retiro) NO se validan aquí a propósito: superarlos
debe responder LIMITE_EXCEDIDO (409), no SOLICITUD_INVALIDA (400). Los valida la lógica
de negocio contra config_banco.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field

# MySQL devuelve DATETIME sin zona; la base trabaja en UTC. Se marca como UTC al responder.
FechaUTC = Annotated[
    datetime, AfterValidator(lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d)
]

# Longitudes de las columnas de la base: sin esto, un valor largo terminaría en un 500.
NumeroCuenta = Annotated[str, Field(min_length=1, max_length=30)]
BancoId = Annotated[str, Field(min_length=1, max_length=10)]
Concepto = Annotated[str, Field(max_length=255)]


class ErrorOut(BaseModel):
    error: str
    mensaje: str


# --- Créditos interbancarios (este banco como destino) ---------------------------------
class SolicitudCredito(BaseModel):
    transfer_id: UUID
    cuenta_destino: NumeroCuenta
    banco_origen: BancoId
    cuenta_origen: NumeroCuenta
    importe: int = Field(ge=1, description="Guaraníes, entero")
    moneda: Literal["PYG"]
    concepto: Concepto | None = None


class CreditoOut(BaseModel):
    transfer_id: UUID
    estado: Literal["ACREDITADO", "RECHAZADO", "PENDIENTE"]
    motivo_rechazo: str | None = None
    fecha_hora: FechaUTC | None = None


# --- Cuentas ---------------------------------------------------------------------------
class CuentaOut(BaseModel):
    banco_id: str
    numero_cuenta: str
    estado: Literal["ACTIVA", "BLOQUEADA", "CERRADA"]
    titular: str | None = None


# --- Cajero (este banco como emisor) ---------------------------------------------------
class SolicitudAutorizacionCajero(BaseModel):
    operacion_id: UUID
    banco_cuenta: BancoId
    numero_cuenta: NumeroCuenta
    pin: str = Field(max_length=72, repr=False, description="Nunca registrar en logs ni en auditoría")
    importe: int = Field(ge=1)
    moneda: Literal["PYG"]


class AutorizacionCajeroOut(BaseModel):
    operacion_id: UUID
    estado: Literal["AUTORIZADO", "RECHAZADO", "PENDIENTE"]
    motivo_rechazo: str | None = None
    fecha_hora: FechaUTC | None = None


# --- API propia para el frontend de este banco (no es parte del contrato) ----------------
class SolicitudTransferencia(BaseModel):
    cuenta_origen: NumeroCuenta
    banco_destino: BancoId
    cuenta_destino: NumeroCuenta
    importe: int = Field(ge=1)
    concepto: Concepto | None = None


class TransferenciaOut(BaseModel):
    transfer_id: UUID
    estado: Literal["RECIBIDA", "RESERVADA", "COMPLETADA", "FALLIDA", "PENDIENTE", "RECHAZADA"]
    motivo_rechazo: str | None = None
    tipo: Literal["INTERNA", "INTERBANCARIA"]
    cuenta_origen: str
    banco_destino: str
    cuenta_destino: str
    importe: int
    fecha_hora: FechaUTC | None = None


# --- Login (API propia del frontend) -----------------------------------------------------
class LoginIn(BaseModel):
    documento: str = Field(min_length=1, max_length=30, description="C.I. del titular")
    password: str = Field(min_length=1, max_length=72, repr=False)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Segundos hasta el tope absoluto de la sesión")
    inactividad_seg: int = Field(description="Segundos sin actividad tras los cuales se cierra la sesión")


class CambioPasswordIn(BaseModel):
    password_actual: str = Field(min_length=1, max_length=72, repr=False)
    password_nueva: str = Field(min_length=8, max_length=72, repr=False)


class CuentaPropiaOut(BaseModel):
    numero_cuenta: str
    estado: Literal["ACTIVA", "BLOQUEADA", "CERRADA"]
    saldo: int
    saldo_disponible: int


class PerfilOut(BaseModel):
    nombre: str
    documento: str
    cuentas: list[CuentaPropiaOut]
