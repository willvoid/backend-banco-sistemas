-- =====================================================================
-- Esquema MySQL 8.0.16+ para UN banco del Sistema Bancario Distribuido
-- Simulado (BCP simulado). Cada grupo crea su propia base con este script.
--
-- Basado en:
--   01_reglamento_general_v2.md        reglas de negocio y límites
--   02_contrato_interoperabilidad_v2.md transferencias, idempotencia, errores
--   03_especificacion_atm_v2.md         cajero
--   04_plan_pruebas_v2.md               casos 1-20
--   05_openapi_v2.yaml                  nombres de campos y estados
--
-- Un banco cumple cuatro roles, y cada uno tiene su tabla:
--   ORIGEN   de transferencias  -> transferencias
--   DESTINO  de créditos        -> creditos_interbancarios      (POST/GET /interbancario/creditos)
--   EMISOR   ante cajeros       -> autorizaciones_cajero        (POST/GET /cajero/autorizaciones)
--   DUEÑO    de un cajero       -> cajeros, sesiones_cajero, retiros_cajero
--
-- Convenciones:
--   * Importes: BIGINT UNSIGNED en guaraníes, sin decimales (doc 01 §2).
--   * Fechas/hora: DATETIME(3) en UTC, escritas por la aplicación.
--   * fecha_local: fecha en America/Asuncion, la usa el límite diario (doc 01 §4).
--   * Nunca se guarda el PIN ni la clave privada (doc 01 §6). request_hash
--     de autorizaciones de cajero se calcula SIN el campo pin.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS banco_familiar
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE banco_familiar;

-- ---------------------------------------------------------------------
-- Catálogos y configuración
-- ---------------------------------------------------------------------

-- Catálogo de errores (doc 02 §6). es_oficial = FALSE marca códigos que
-- no están en el contrato y hay que acordar con el BCP simulado.
CREATE TABLE codigos_error (
  codigo      VARCHAR(40)  NOT NULL PRIMARY KEY,
  http_status SMALLINT     NOT NULL,
  descripcion VARCHAR(200) NOT NULL,
  es_oficial  BOOLEAN      NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

-- Registro de los 4 bancos (doc 01 §9, doc 02 §1). Solo claves PÚBLICAS.
CREATE TABLE bancos (
  banco_id          VARCHAR(10)  NOT NULL PRIMARY KEY,   -- BCO1..BCO4
  nombre            VARCHAR(100) NOT NULL,
  url_base          VARCHAR(255) NOT NULL,
  clave_publica_pem TEXT         NOT NULL,               -- Ed25519, PEM
  timeout_ms        INT UNSIGNED NOT NULL DEFAULT 5000,  -- doc 02 §4 paso 4
  activo            BOOLEAN      NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

-- Parámetros de este banco (una sola fila). Los límites del doc 01 §4
-- quedan aquí para no repetirlos en el código.
CREATE TABLE config_banco (
  id                     TINYINT UNSIGNED NOT NULL DEFAULT 1 PRIMARY KEY,
  banco_id               VARCHAR(10)     NOT NULL,
  zona_horaria           VARCHAR(40)     NOT NULL DEFAULT 'America/Asuncion',
  max_transferencia      BIGINT UNSIGNED NOT NULL DEFAULT 10000000,
  max_retiro_cajero      BIGINT UNSIGNED NOT NULL DEFAULT 1500000,
  max_diario_por_cuenta  BIGINT UNSIGNED NOT NULL DEFAULT 10000000,
  max_intentos_pin       TINYINT UNSIGNED NOT NULL DEFAULT 3,
  ventana_firma_seg      INT UNSIGNED    NOT NULL DEFAULT 300,     -- X-Timestamp ±5 min
  retencion_idem_horas   INT UNSIGNED    NOT NULL DEFAULT 24,      -- doc 02 §3
  CONSTRAINT chk_config_unica CHECK (id = 1),
  CONSTRAINT fk_config_banco FOREIGN KEY (banco_id) REFERENCES bancos(banco_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Clientes y cuentas
-- ---------------------------------------------------------------------

CREATE TABLE titulares (
  id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  nombre    VARCHAR(150) NOT NULL,
  documento VARCHAR(30)  NOT NULL UNIQUE,            -- C.I.
  creado_en DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;

-- Credenciales de acceso al frontend/API propia del banco (no es parte del contrato
-- entre bancos). Un usuario por titular. Login con documento + contraseña -> JWT.
-- password_hash es bcrypt; tras varios intentos fallidos se bloquea temporalmente.
CREATE TABLE usuarios (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  titular_id        BIGINT UNSIGNED NOT NULL UNIQUE,
  password_hash     VARCHAR(255)    NOT NULL,
  activo            BOOLEAN         NOT NULL DEFAULT TRUE,
  intentos_fallidos TINYINT UNSIGNED NOT NULL DEFAULT 0,
  bloqueado_hasta   DATETIME(3)     NULL,
  ultimo_login      DATETIME(3)     NULL,
  creado_en         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_usuario_titular FOREIGN KEY (titular_id) REFERENCES titulares(id)
) ENGINE=InnoDB;

-- Sesiones de login (timeout por inactividad y logout). El JWT lleva el id de la sesión
-- (claim jti); en cada request se valida acá. Se cierra por LOGOUT, INACTIVIDAD, EXPIRADA
-- (tope absoluto desde el login) o CAMBIO_PASSWORD.
CREATE TABLE sesiones (
  id               CHAR(36)        NOT NULL PRIMARY KEY,   -- UUID = jti del token
  usuario_id       BIGINT UNSIGNED NOT NULL,
  creada_en        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ultima_actividad DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expira_en        DATETIME(3)     NOT NULL,                -- vencimiento absoluto
  cerrada_en       DATETIME(3)     NULL,
  motivo_cierre    VARCHAR(20)     NULL,
  KEY idx_sesion_usuario (usuario_id, cerrada_en),
  CONSTRAINT fk_sesion_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
) ENGINE=InnoDB;

-- banco_id + numero_cuenta identifica la cuenta en todo el sistema (doc 01 §3).
-- Saldo disponible = saldo - saldo_reservado. La reserva la usa el paso 1
-- de la transferencia interbancaria (doc 02 §4).
CREATE TABLE cuentas (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  numero_cuenta   VARCHAR(30)     NOT NULL UNIQUE,
  titular_id      BIGINT UNSIGNED NOT NULL,
  saldo           BIGINT UNSIGNED NOT NULL DEFAULT 0,
  saldo_reservado BIGINT UNSIGNED NOT NULL DEFAULT 0,
  estado          ENUM('ACTIVA','BLOQUEADA','CERRADA') NOT NULL DEFAULT 'ACTIVA',
  pin_hash        VARCHAR(255)    NULL,                -- bcrypt/argon2, lo genera la app
  creado_en       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  actualizado_en  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_cuenta_titular FOREIGN KEY (titular_id) REFERENCES titulares(id),
  -- Respaldo del caso 14 (concurrencia): el disponible nunca queda negativo.
  CONSTRAINT chk_reserva CHECK (saldo_reservado <= saldo)
) ENGINE=InnoDB;

-- Consumo del límite diario combinado (transferencias salientes + retiros).
-- Se actualiza en la misma transacción que bloquea la cuenta con FOR UPDATE,
-- así dos operaciones simultáneas no pueden pasarse del límite (casos 9 y 18).
-- Si una transferencia termina FALLIDA, se descuenta lo reservado.
CREATE TABLE consumo_diario (
  cuenta_id   BIGINT UNSIGNED NOT NULL,
  fecha_local DATE            NOT NULL,
  total       BIGINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (cuenta_id, fecha_local),
  CONSTRAINT fk_consumo_cuenta FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
  CONSTRAINT chk_consumo_max CHECK (total <= 10000000)
) ENGINE=InnoDB;

-- Libro mayor: cada cambio de saldo o reserva de una cuenta. Permite
-- comprobar que no se crea ni destruye dinero (doc 01 §6).
CREATE TABLE movimientos (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  cuenta_id        BIGINT UNSIGNED NOT NULL,
  tipo             ENUM('DEBITO','CREDITO','RESERVA','LIBERACION_RESERVA','DEBITO_RESERVA') NOT NULL,
  importe          BIGINT UNSIGNED NOT NULL,
  saldo_post       BIGINT UNSIGNED NOT NULL,
  reservado_post   BIGINT UNSIGNED NOT NULL,
  origen           ENUM('TRANSFERENCIA','CREDITO_INTERBANCARIO','AUTORIZACION_CAJERO','AJUSTE') NOT NULL,
  referencia_id    CHAR(36)        NOT NULL,   -- transfer_id u operacion_id
  creado_en        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_mov_cuenta (cuenta_id, creado_en),
  KEY idx_mov_ref (referencia_id),
  CONSTRAINT fk_mov_cuenta FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
  CONSTRAINT chk_mov_importe CHECK (importe > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Rol ORIGEN: transferencias iniciadas en este banco
-- ---------------------------------------------------------------------

-- INTERNA: una transacción local (doc 02 §2), pasa de RECIBIDA a COMPLETADA o RECHAZADA.
-- INTERBANCARIA: RECIBIDA -> RESERVADA -> COMPLETADA | FALLIDA | PENDIENTE (doc 02 §4).
-- RECHAZADA: falló la validación local, nunca se contactó al destino (caso 8).
-- Sin CHECK de importe máximo: los rechazos por límite también se registran (caso 5).
CREATE TABLE transferencias (
  id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  transfer_id          CHAR(36)        NOT NULL UNIQUE,   -- se envía como X-Idempotency-Key al destino
  idempotency_key      VARCHAR(64)     NOT NULL UNIQUE,   -- la que envía el frontend propio
  request_hash         CHAR(64)        NOT NULL,          -- SHA-256 de la solicitud -> CONFLICTO_IDEMPOTENCIA
  tipo                 ENUM('INTERNA','INTERBANCARIA') NOT NULL,
  cuenta_origen_id     BIGINT UNSIGNED NOT NULL,
  banco_destino        VARCHAR(10)     NOT NULL,
  cuenta_destino       VARCHAR(30)     NOT NULL,
  cuenta_destino_id    BIGINT UNSIGNED NULL,             -- solo INTERNA y si existe
  importe              BIGINT UNSIGNED NOT NULL,
  moneda               CHAR(3)         NOT NULL DEFAULT 'PYG',
  concepto             VARCHAR(255)    NULL,
  estado               ENUM('RECIBIDA','RESERVADA','COMPLETADA','FALLIDA','PENDIENTE','RECHAZADA') NOT NULL DEFAULT 'RECIBIDA',
  motivo_rechazo       VARCHAR(40)     NULL,
  intentos_envio       TINYINT UNSIGNED NOT NULL DEFAULT 0,
  ultimo_intento_en    DATETIME(3)     NULL,
  proximo_reintento_en DATETIME(3)     NULL,             -- para resolver PENDIENTE
  fecha_local          DATE            NOT NULL,
  creado_en            DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  actualizado_en       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_trf_origen (cuenta_origen_id, creado_en),
  KEY idx_trf_pendientes (estado, proximo_reintento_en),
  CONSTRAINT fk_trf_origen  FOREIGN KEY (cuenta_origen_id)  REFERENCES cuentas(id),
  CONSTRAINT fk_trf_destino FOREIGN KEY (cuenta_destino_id) REFERENCES cuentas(id),
  CONSTRAINT fk_trf_banco   FOREIGN KEY (banco_destino)     REFERENCES bancos(banco_id),
  CONSTRAINT fk_trf_motivo  FOREIGN KEY (motivo_rechazo)    REFERENCES codigos_error(codigo),
  CONSTRAINT chk_trf_importe CHECK (importe > 0),
  CONSTRAINT chk_trf_moneda  CHECK (moneda = 'PYG'),
  CONSTRAINT chk_trf_interna CHECK (tipo = 'INTERBANCARIA' OR estado = 'RECHAZADA' OR cuenta_destino_id IS NOT NULL)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Rol DESTINO: créditos recibidos de otros bancos
-- ---------------------------------------------------------------------

-- POST /interbancario/creditos  y  GET /interbancario/creditos/{transfer_id}.
-- La unicidad (banco_origen, transfer_id) da la idempotencia del caso 10:
-- una repetición devuelve esta fila sin volver a acreditar.
-- El GET filtra por banco_origen = X-Bank-Id: un banco solo consulta lo suyo.
CREATE TABLE creditos_interbancarios (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  transfer_id       CHAR(36)        NOT NULL,
  banco_origen      VARCHAR(10)     NOT NULL,
  cuenta_origen     VARCHAR(30)     NOT NULL,
  cuenta_destino    VARCHAR(30)     NOT NULL,   -- tal como llegó
  cuenta_destino_id BIGINT UNSIGNED NULL,       -- NULL si no existe (caso 7)
  importe           BIGINT UNSIGNED NOT NULL,
  moneda            CHAR(3)         NOT NULL DEFAULT 'PYG',
  concepto          VARCHAR(255)    NULL,
  request_hash      CHAR(64)        NOT NULL,
  estado            ENUM('ACREDITADO','RECHAZADO','PENDIENTE') NOT NULL,
  motivo_rechazo    VARCHAR(40)     NULL,
  fecha_hora        DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uq_credito (banco_origen, transfer_id),
  KEY idx_cred_cuenta (cuenta_destino_id, fecha_hora),
  CONSTRAINT fk_cred_banco   FOREIGN KEY (banco_origen)      REFERENCES bancos(banco_id),
  CONSTRAINT fk_cred_cuenta  FOREIGN KEY (cuenta_destino_id) REFERENCES cuentas(id),
  CONSTRAINT fk_cred_motivo  FOREIGN KEY (motivo_rechazo)    REFERENCES codigos_error(codigo),
  CONSTRAINT chk_cred_importe CHECK (importe > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Rol EMISOR: autorizaciones de retiro pedidas por cualquier cajero
-- ---------------------------------------------------------------------

-- POST /cajero/autorizaciones  y  GET /cajero/autorizaciones/{operacion_id}.
-- banco_cajero = X-Bank-Id del banco dueño del cajero (puede ser este mismo).
-- La unicidad evita el doble débito del caso 19. El PIN no se guarda.
CREATE TABLE autorizaciones_cajero (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  operacion_id    CHAR(36)        NOT NULL,
  banco_cajero    VARCHAR(10)     NOT NULL,
  numero_cuenta   VARCHAR(30)     NOT NULL,
  cuenta_id       BIGINT UNSIGNED NULL,
  importe         BIGINT UNSIGNED NOT NULL,
  moneda          CHAR(3)         NOT NULL DEFAULT 'PYG',
  request_hash    CHAR(64)        NOT NULL,     -- SHA-256 del cuerpo SIN el pin
  estado          ENUM('AUTORIZADO','RECHAZADO','PENDIENTE') NOT NULL,
  motivo_rechazo  VARCHAR(40)     NULL,
  fecha_local     DATE            NOT NULL,
  fecha_hora      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uq_autorizacion (banco_cajero, operacion_id),
  KEY idx_aut_cuenta (cuenta_id, fecha_hora),
  CONSTRAINT fk_aut_banco  FOREIGN KEY (banco_cajero)   REFERENCES bancos(banco_id),
  CONSTRAINT fk_aut_cuenta FOREIGN KEY (cuenta_id)      REFERENCES cuentas(id),
  CONSTRAINT fk_aut_motivo FOREIGN KEY (motivo_rechazo) REFERENCES codigos_error(codigo),
  CONSTRAINT chk_aut_importe CHECK (importe > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Rol DUEÑO DE CAJERO (opcional: solo si el grupo construye un cajero)
-- ---------------------------------------------------------------------

CREATE TABLE cajeros (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  codigo    VARCHAR(20)  NOT NULL UNIQUE,
  ubicacion VARCHAR(150) NULL,
  activo    BOOLEAN      NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

-- Una sesión = un usuario frente al cajero. Cuenta los PIN incorrectos:
-- al tercero se cancela (doc 03 §4). Cada intento usa un operacion_id
-- nuevo, porque repetir el mismo devolvería el mismo rechazo.
CREATE TABLE sesiones_cajero (
  id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  cajero_id             INT UNSIGNED    NOT NULL,
  banco_cuenta          VARCHAR(10)     NOT NULL,
  numero_cuenta         VARCHAR(30)     NOT NULL,
  intentos_pin_fallidos TINYINT UNSIGNED NOT NULL DEFAULT 0,
  estado                ENUM('ABIERTA','FINALIZADA','CANCELADA_PIN') NOT NULL DEFAULT 'ABIERTA',
  iniciada_en           DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  cerrada_en            DATETIME(3)     NULL,
  CONSTRAINT fk_ses_cajero FOREIGN KEY (cajero_id)    REFERENCES cajeros(id),
  CONSTRAINT fk_ses_banco  FOREIGN KEY (banco_cuenta) REFERENCES bancos(banco_id),
  CONSTRAINT chk_ses_pin CHECK (intentos_pin_fallidos <= 3)
) ENGINE=InnoDB;

-- Lo que el cajero propio envió al banco emisor y qué respondió.
-- ENVIADO sin respuesta = timeout: consultar con GET antes de reintentar (doc 03 §3).
CREATE TABLE retiros_cajero (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  operacion_id      CHAR(36)        NOT NULL UNIQUE,
  sesion_id         BIGINT UNSIGNED NOT NULL,
  banco_cuenta      VARCHAR(10)     NOT NULL,
  numero_cuenta     VARCHAR(30)     NOT NULL,
  importe           BIGINT UNSIGNED NOT NULL,
  estado            ENUM('ENVIADO','AUTORIZADO','RECHAZADO','PENDIENTE') NOT NULL DEFAULT 'ENVIADO',
  motivo_rechazo    VARCHAR(40)     NULL,
  efectivo_entregado BOOLEAN        NOT NULL DEFAULT FALSE,
  intentos_envio    TINYINT UNSIGNED NOT NULL DEFAULT 1,
  creado_en         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  actualizado_en    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_ret_sesion FOREIGN KEY (sesion_id)      REFERENCES sesiones_cajero(id),
  CONSTRAINT fk_ret_banco  FOREIGN KEY (banco_cuenta)   REFERENCES bancos(banco_id),
  CONSTRAINT fk_ret_motivo FOREIGN KEY (motivo_rechazo) REFERENCES codigos_error(codigo),
  CONSTRAINT chk_ret_importe CHECK (importe > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Auditoría
-- ---------------------------------------------------------------------

-- Historial de cambios de estado de cualquier operación (doc 02 §7).
CREATE TABLE eventos_operacion (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  entidad         ENUM('TRANSFERENCIA','CREDITO_INTERBANCARIO','AUTORIZACION_CAJERO','RETIRO_CAJERO') NOT NULL,
  referencia_id   CHAR(36)     NOT NULL,
  estado_anterior VARCHAR(20)  NULL,
  estado_nuevo    VARCHAR(20)  NOT NULL,
  codigo_error    VARCHAR(40)  NULL,
  detalle         VARCHAR(255) NULL,       -- nunca PIN ni claves
  creado_en       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_evt_ref (referencia_id, creado_en),
  CONSTRAINT fk_evt_codigo FOREIGN KEY (codigo_error) REFERENCES codigos_error(codigo)
) ENGINE=InnoDB;

-- Solicitudes recibidas de otros bancos, incluidas las rechazadas por
-- firma o timestamp (caso 12). No se guarda el cuerpo: puede traer el PIN.
CREATE TABLE solicitudes_entrantes (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  banco_declarado VARCHAR(10)  NULL,       -- X-Bank-Id tal cual llegó (puede no existir)
  metodo          VARCHAR(7)   NOT NULL,
  ruta            VARCHAR(255) NOT NULL,
  idempotency_key VARCHAR(64)  NULL,
  x_timestamp     VARCHAR(40)  NULL,
  firma_valida    BOOLEAN      NOT NULL,
  http_status     SMALLINT     NOT NULL,
  codigo_error    VARCHAR(40)  NULL,
  recibida_en     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_sol_fecha (recibida_en),
  CONSTRAINT fk_sol_codigo FOREIGN KEY (codigo_error) REFERENCES codigos_error(codigo)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- Vistas
-- ---------------------------------------------------------------------

CREATE VIEW v_cuentas AS
SELECT c.numero_cuenta, t.nombre AS titular, c.estado,
       c.saldo, c.saldo_reservado, c.saldo - c.saldo_reservado AS saldo_disponible
FROM cuentas c JOIN titulares t ON t.id = c.titular_id;

-- Registro consultable de toda operación con efecto económico (caso 20):
-- fecha/hora, cuentas, importe y resultado, sin PIN.
CREATE VIEW v_auditoria AS
SELECT 'TRANSFERENCIA' AS tipo, t.transfer_id AS id_operacion, t.creado_en AS fecha_hora,
       cb.banco_id AS banco_origen, co.numero_cuenta AS cuenta_origen,
       t.banco_destino, t.cuenta_destino, t.importe, t.estado AS resultado, t.motivo_rechazo
FROM transferencias t
JOIN cuentas co ON co.id = t.cuenta_origen_id
CROSS JOIN config_banco cb
UNION ALL
SELECT 'CREDITO_INTERBANCARIO', ci.transfer_id, ci.fecha_hora,
       ci.banco_origen, ci.cuenta_origen, cb.banco_id, ci.cuenta_destino,
       ci.importe, ci.estado, ci.motivo_rechazo
FROM creditos_interbancarios ci CROSS JOIN config_banco cb
UNION ALL
SELECT 'RETIRO_CAJERO', a.operacion_id, a.fecha_hora,
       cb.banco_id, a.numero_cuenta, a.banco_cajero, NULL,
       a.importe, a.estado, a.motivo_rechazo
FROM autorizaciones_cajero a CROSS JOIN config_banco cb;

-- ---------------------------------------------------------------------
-- Datos iniciales
-- ---------------------------------------------------------------------

INSERT INTO codigos_error (codigo, http_status, descripcion, es_oficial) VALUES
 ('CUENTA_NO_EXISTE',         404, 'La cuenta indicada no existe en este banco', TRUE),
 ('CUENTA_INACTIVA',          409, 'La cuenta está bloqueada o cerrada', TRUE),
 ('FONDOS_INSUFICIENTES',     409, 'El saldo disponible no alcanza', TRUE),
 ('LIMITE_EXCEDIDO',          409, 'Se supera el límite por operación o el diario', TRUE),
 ('BANCO_NO_RECONOCIDO',      400, 'El banco_id de origen no está en el registro', TRUE),
 ('SOLICITUD_INVALIDA',       400, 'Falta un campo requerido o el formato es incorrecto', TRUE),
 ('FIRMA_INVALIDA',           401, 'La firma no verifica contra la clave pública declarada', TRUE),
 ('TIMESTAMP_FUERA_DE_RANGO', 401, 'X-Timestamp difiere más de 5 minutos', TRUE),
 ('CONFLICTO_IDEMPOTENCIA',   409, 'La clave de idempotencia ya se usó con datos distintos', TRUE),
 ('NO_ENCONTRADO',            404, 'El identificador consultado no existe', TRUE),
 ('ERROR_INTERNO',            500, 'Error no controlado del banco que responde', TRUE),
 -- El doc 03 pide informar "PIN incorrecto" pero el catálogo no tiene código. A acordar con el BCP.
 ('PIN_INCORRECTO',           409, 'El PIN no es correcto (pendiente de acordar)', FALSE);

-- Reemplazar URLs y claves por las acordadas entre los 4 grupos.
INSERT INTO bancos (banco_id, nombre, url_base, clave_publica_pem) VALUES
 ('BCO1', 'Banco 1', 'http://localhost:8001', 'PEGAR_CLAVE_PUBLICA_BCO1'),
 ('BCO2', 'Banco 2', 'http://localhost:8002', 'PEGAR_CLAVE_PUBLICA_BCO2'),
 ('BCO3', 'Banco 3', 'http://localhost:8003', 'PEGAR_CLAVE_PUBLICA_BCO3'),
 ('BCO4', 'Banco 4', 'http://localhost:8004', 'PEGAR_CLAVE_PUBLICA_BCO4');

-- Cambiar por el banco_id de este grupo.
INSERT INTO config_banco (banco_id) VALUES ('BCO1');

-- Cuentas de prueba para el plan del doc 04. Los pin_hash los carga la
-- aplicación con bcrypt/argon2; aquí quedan en NULL.
INSERT INTO titulares (nombre, documento) VALUES
 ('Ana Pérez',    '1000001'),
 ('Bruno Gómez',  '1000002'),
 ('Carla Benítez','1000003');

INSERT INTO cuentas (numero_cuenta, titular_id, saldo, estado) VALUES
 ('1001', 1,  5000000, 'ACTIVA'),     -- casos 1, 6, 15
 ('1002', 2,  1000000, 'ACTIVA'),     -- destino; caso 2 y 14 con saldo chico
 ('1003', 3, 50000000, 'ACTIVA'),     -- casos 5, 9, 17, 18 (límites)
 ('1004', 1,  2000000, 'BLOQUEADA'),  -- caso 4
 ('1005', 2,  2000000, 'CERRADA');    -- caso 4

INSERT INTO cajeros (codigo, ubicacion) VALUES ('ATM-01', 'Sucursal central');
