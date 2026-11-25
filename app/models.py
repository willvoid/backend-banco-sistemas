"""Modelos ORM. La fuente de verdad del esquema es db/schema_banco_familiar.sql
(no se usa create_all): estos modelos solo reflejan esas tablas."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, SmallInteger, String, Text, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

U64 = BIGINT(unsigned=True)
DT3 = DATETIME(fsp=3)
AHORA = text("CURRENT_TIMESTAMP(3)")


def _ahora() -> Mapped[datetime]:
    return mapped_column(DT3, server_default=AHORA)


def _ahora_upd() -> Mapped[datetime]:
    return mapped_column(DT3, server_default=AHORA, server_onupdate=AHORA)


class CodigoError(Base):
    __tablename__ = "codigos_error"
    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    http_status: Mapped[int] = mapped_column(SmallInteger)
    descripcion: Mapped[str] = mapped_column(String(200))
    es_oficial: Mapped[bool] = mapped_column(Boolean, default=True)


class Banco(Base):
    __tablename__ = "bancos"
    banco_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    url_base: Mapped[str] = mapped_column(String(255))
    clave_publica_pem: Mapped[str] = mapped_column(Text)
    timeout_ms: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=5000)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class ConfigBanco(Base):
    __tablename__ = "config_banco"
    id: Mapped[int] = mapped_column(TINYINT(unsigned=True), primary_key=True, default=1)
    banco_id: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    zona_horaria: Mapped[str] = mapped_column(String(40), default="America/Asuncion")
    max_transferencia: Mapped[int] = mapped_column(U64, default=10_000_000)
    max_retiro_cajero: Mapped[int] = mapped_column(U64, default=1_500_000)
    max_diario_por_cuenta: Mapped[int] = mapped_column(U64, default=10_000_000)
    max_intentos_pin: Mapped[int] = mapped_column(TINYINT(unsigned=True), default=3)
    ventana_firma_seg: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=300)
    retencion_idem_horas: Mapped[int] = mapped_column(INTEGER(unsigned=True), default=24)


class Titular(Base):
    __tablename__ = "titulares"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(150))
    documento: Mapped[str] = mapped_column(String(30), unique=True)
    creado_en: Mapped[datetime] = _ahora()


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    titular_id: Mapped[int] = mapped_column(ForeignKey("titulares.id"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(TINYINT(unsigned=True), default=0)
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DT3)
    ultimo_login: Mapped[datetime | None] = mapped_column(DT3)
    creado_en: Mapped[datetime] = _ahora()

    titular: Mapped[Titular] = relationship()


class Sesion(Base):
    __tablename__ = "sesiones"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # jti del token
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    creada_en: Mapped[datetime] = _ahora()
    ultima_actividad: Mapped[datetime] = _ahora()
    expira_en: Mapped[datetime] = mapped_column(DT3)
    cerrada_en: Mapped[datetime | None] = mapped_column(DT3)
    # LOGOUT | INACTIVIDAD | EXPIRADA | CAMBIO_PASSWORD
    motivo_cierre: Mapped[str | None] = mapped_column(String(20))

    usuario: Mapped[Usuario] = relationship()


class Cuenta(Base):
    __tablename__ = "cuentas"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    numero_cuenta: Mapped[str] = mapped_column(String(30), unique=True)
    titular_id: Mapped[int] = mapped_column(ForeignKey("titulares.id"))
    saldo: Mapped[int] = mapped_column(U64, default=0)
    saldo_reservado: Mapped[int] = mapped_column(U64, default=0)
    estado: Mapped[str] = mapped_column(String(10), default="ACTIVA")  # ACTIVA | BLOQUEADA | CERRADA
    pin_hash: Mapped[str | None] = mapped_column(String(255))
    creado_en: Mapped[datetime] = _ahora()
    actualizado_en: Mapped[datetime] = _ahora_upd()

    titular: Mapped[Titular] = relationship()

    @property
    def saldo_disponible(self) -> int:
        return self.saldo - self.saldo_reservado


class ConsumoDiario(Base):
    __tablename__ = "consumo_diario"
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), primary_key=True)
    fecha_local: Mapped[date] = mapped_column(Date, primary_key=True)
    total: Mapped[int] = mapped_column(U64, default=0)


class Movimiento(Base):
    __tablename__ = "movimientos"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"))
    # DEBITO | CREDITO | RESERVA | LIBERACION_RESERVA | DEBITO_RESERVA
    tipo: Mapped[str] = mapped_column(String(20))
    importe: Mapped[int] = mapped_column(U64)
    saldo_post: Mapped[int] = mapped_column(U64)
    reservado_post: Mapped[int] = mapped_column(U64)
    # TRANSFERENCIA | CREDITO_INTERBANCARIO | AUTORIZACION_CAJERO | AJUSTE
    origen: Mapped[str] = mapped_column(String(25))
    referencia_id: Mapped[str] = mapped_column(String(36))
    creado_en: Mapped[datetime] = _ahora()


class Transferencia(Base):
    __tablename__ = "transferencias"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    transfer_id: Mapped[str] = mapped_column(String(36), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    tipo: Mapped[str] = mapped_column(String(14))  # INTERNA | INTERBANCARIA
    cuenta_origen_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"))
    banco_destino: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    cuenta_destino: Mapped[str] = mapped_column(String(30))
    cuenta_destino_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas.id"))
    importe: Mapped[int] = mapped_column(U64)
    moneda: Mapped[str] = mapped_column(String(3), default="PYG")
    concepto: Mapped[str | None] = mapped_column(String(255))
    # RECIBIDA | RESERVADA | COMPLETADA | FALLIDA | PENDIENTE | RECHAZADA
    estado: Mapped[str] = mapped_column(String(10), default="RECIBIDA")
    motivo_rechazo: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    intentos_envio: Mapped[int] = mapped_column(TINYINT(unsigned=True), default=0)
    ultimo_intento_en: Mapped[datetime | None] = mapped_column(DT3)
    proximo_reintento_en: Mapped[datetime | None] = mapped_column(DT3)
    fecha_local: Mapped[date] = mapped_column(Date)
    creado_en: Mapped[datetime] = _ahora()
    actualizado_en: Mapped[datetime] = _ahora_upd()


class CreditoInterbancario(Base):
    __tablename__ = "creditos_interbancarios"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    transfer_id: Mapped[str] = mapped_column(String(36))
    banco_origen: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    cuenta_origen: Mapped[str] = mapped_column(String(30))
    cuenta_destino: Mapped[str] = mapped_column(String(30))
    cuenta_destino_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas.id"))
    importe: Mapped[int] = mapped_column(U64)
    moneda: Mapped[str] = mapped_column(String(3), default="PYG")
    concepto: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(10))  # ACREDITADO | RECHAZADO | PENDIENTE
    motivo_rechazo: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    fecha_hora: Mapped[datetime] = _ahora()


class AutorizacionCajero(Base):
    __tablename__ = "autorizaciones_cajero"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    operacion_id: Mapped[str] = mapped_column(String(36))
    banco_cajero: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    numero_cuenta: Mapped[str] = mapped_column(String(30))
    cuenta_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas.id"))
    importe: Mapped[int] = mapped_column(U64)
    moneda: Mapped[str] = mapped_column(String(3), default="PYG")
    request_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 SIN el campo pin
    estado: Mapped[str] = mapped_column(String(10))  # AUTORIZADO | RECHAZADO | PENDIENTE
    motivo_rechazo: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    fecha_local: Mapped[date] = mapped_column(Date)
    fecha_hora: Mapped[datetime] = _ahora()


class Cajero(Base):
    __tablename__ = "cajeros"
    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    ubicacion: Mapped[str | None] = mapped_column(String(150))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class SesionCajero(Base):
    __tablename__ = "sesiones_cajero"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    cajero_id: Mapped[int] = mapped_column(ForeignKey("cajeros.id"))
    banco_cuenta: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    numero_cuenta: Mapped[str] = mapped_column(String(30))
    intentos_pin_fallidos: Mapped[int] = mapped_column(TINYINT(unsigned=True), default=0)
    estado: Mapped[str] = mapped_column(String(13), default="ABIERTA")  # ABIERTA | FINALIZADA | CANCELADA_PIN
    iniciada_en: Mapped[datetime] = mapped_column(DT3, server_default=AHORA)
    cerrada_en: Mapped[datetime | None] = mapped_column(DT3)


class RetiroCajero(Base):
    __tablename__ = "retiros_cajero"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    operacion_id: Mapped[str] = mapped_column(String(36), unique=True)
    sesion_id: Mapped[int] = mapped_column(ForeignKey("sesiones_cajero.id"))
    banco_cuenta: Mapped[str] = mapped_column(ForeignKey("bancos.banco_id"))
    numero_cuenta: Mapped[str] = mapped_column(String(30))
    importe: Mapped[int] = mapped_column(U64)
    estado: Mapped[str] = mapped_column(String(10), default="ENVIADO")  # ENVIADO | AUTORIZADO | RECHAZADO | PENDIENTE
    motivo_rechazo: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    efectivo_entregado: Mapped[bool] = mapped_column(Boolean, default=False)
    intentos_envio: Mapped[int] = mapped_column(TINYINT(unsigned=True), default=1)
    creado_en: Mapped[datetime] = _ahora()
    actualizado_en: Mapped[datetime] = _ahora_upd()


class EventoOperacion(Base):
    __tablename__ = "eventos_operacion"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    # TRANSFERENCIA | CREDITO_INTERBANCARIO | AUTORIZACION_CAJERO | RETIRO_CAJERO
    entidad: Mapped[str] = mapped_column(String(25))
    referencia_id: Mapped[str] = mapped_column(String(36))
    estado_anterior: Mapped[str | None] = mapped_column(String(20))
    estado_nuevo: Mapped[str] = mapped_column(String(20))
    codigo_error: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    detalle: Mapped[str | None] = mapped_column(String(255))  # nunca PIN ni claves
    creado_en: Mapped[datetime] = _ahora()


class SolicitudEntrante(Base):
    __tablename__ = "solicitudes_entrantes"
    id: Mapped[int] = mapped_column(U64, primary_key=True, autoincrement=True)
    banco_declarado: Mapped[str | None] = mapped_column(String(10))
    metodo: Mapped[str] = mapped_column(String(7))
    ruta: Mapped[str] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    x_timestamp: Mapped[str | None] = mapped_column(String(40))
    firma_valida: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[int] = mapped_column(SmallInteger)
    codigo_error: Mapped[str | None] = mapped_column(ForeignKey("codigos_error.codigo"))
    recibida_en: Mapped[datetime] = _ahora()
