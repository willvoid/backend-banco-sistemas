-- Para bases ya creadas: agrega la tabla sesiones sin tocar nada más.
USE banco_familiar;

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
