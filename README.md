# API del banco (FastAPI) — Sistema Bancario Distribuido Simulado

Implementa el contrato de `05_openapi_v2.yaml` sobre la base `banco_familiar` (MySQL 8.0.16+).

## Puesta en marcha (VS Code)

1. Abrir **esta carpeta** (`banco_api`) en VS Code y aceptar las extensiones recomendadas.
2. Entorno y dependencias (ya hay un `.venv` creado). Para recrearlo: tarea **Crear entorno e instalar dependencias**
   (`Ctrl+Shift+P` → *Tasks: Run Task*), o en la terminal:
   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Base de datos: ejecutar `db/schema_banco_familiar.sql` en MySQL (Workbench o consola). Crea la base y datos de prueba.
4. Configurar `.env` (copiado de `.env.example`): `DB_PASSWORD`, `BANCO_ID`, etc.
5. Claves Ed25519: `python -m scripts.generar_claves` (ya hay un par en `keys/` generado para pruebas locales;
   **regeneralo** para el banco real). La **privada no se comparte**; la pública va a la tabla `bancos`
   (tu fila) y al BCP simulado.
6. Ejecutar: `F5` → *API: uvicorn (debug)* (puerto 8001), o la tarea **Ejecutar API**.
   Documentación interactiva en <http://127.0.0.1:8001/docs>; salud en `/health`.
7. Tests: tarea **Tests** o `python -m pytest`. Los de transferencias necesitan MySQL y se saltean
   si no se define `TEST_MYSQL_PORT`. Usan la base `banco_test`, que **se borra y se recrea**:
   ```powershell
   $env:TEST_MYSQL_PORT=3306; $env:TEST_MYSQL_PASSWORD="tu_clave"; python -m pytest
   ```

## Estructura

```
app/
  main.py            app FastAPI, routers, /health
  config.py          variables de entorno (.env)
  database.py        engine SQLAlchemy, sesión UTC
  models.py          modelos ORM (el esquema real es db/*.sql)
  schemas.py         esquemas Pydantic del OpenAPI
  errors.py          ApiError + formato {"error","mensaje"}
  firma.py           cadena canónica, firmar/verificar Ed25519, hash de solicitud
  dependencies.py    verificar_firma: autentica al banco que llama
  banco_client.py    llamar_banco(): cliente firmado hacia otros bancos
  routers/           cuentas, interbancario, cajero, transferencias (frontend propio)
  services/          lógica de negocio (transferencias)
scripts/             generar_claves, cliente_prueba, crear_usuario, cargar_credenciales_prueba
tests/               firma, errores, login (sin MySQL) y transferencias (con MySQL)
db/                  schema_banco_familiar.sql
```

## Estado

| Endpoint | Estado |
|---|---|
| `GET /cuentas/{numero}` | hecho |
| `GET /interbancario/creditos/{transfer_id}` | hecho |
| `GET /cajero/autorizaciones/{operacion_id}` | hecho |
| `POST /auth/login`, `GET /auth/me`, `POST /auth/cambiar-password` | hecho (login JWT, ver abajo) |
| `POST /interbancario/creditos` | hecho (este banco como destino) |
| `POST /cajero/autorizaciones` | **pendiente** (501) |
| `POST /transferencias` (frontend propio) | **interna hecha**; interbancaria pendiente (501) |

Ya funciona: verificación de firma, ventana de ±5 min, errores del catálogo y registro de rechazos en
`solicitudes_entrantes`.

## Transferencia interna (`POST /transferencias`)

Requiere `Authorization: Bearer <token>` y el header `Idempotency-Key` (se sugiere un UUID nuevo por
cada transferencia que el usuario confirma; los reintentos de esa misma operación usan la misma clave).

```json
{"cuenta_origen": "1001", "banco_destino": "BCO1", "cuenta_destino": "1002", "importe": 150000, "concepto": "opcional"}
```

- Una sola transacción: bloquea ambas cuentas (`FOR UPDATE`) en orden de número de cuenta, valida,
  debita, acredita y registra `movimientos`, `consumo_diario`, `transferencias` y `eventos_operacion`.
- Validaciones, en orden: cuenta origen activa → destino existe y activa → tope por operación
  (10.000.000) → límite diario combinado → saldo disponible.
- Errores: `PROHIBIDO` (403, la cuenta de origen no es del usuario), `CUENTA_NO_EXISTE` (404),
  `CUENTA_INACTIVA` / `LIMITE_EXCEDIDO` / `FONDOS_INSUFICIENTES` / `CONFLICTO_IDEMPOTENCIA` (409),
  `SOLICITUD_INVALIDA` (400, misma cuenta o falta `Idempotency-Key`).
- Misma clave y mismos datos → mismo resultado sin volver a debitar (también si era un rechazo).
- Si `banco_destino` es otro banco responde 501 hasta que se implemente la interbancaria.

## Crédito interbancario recibido (`POST /interbancario/creditos`)

Lo llama otro banco, firmado con Ed25519, para acreditar una transferencia que salió de él
(doc 02, sección 4, paso 2). Se consulta con `GET /interbancario/creditos/{transfer_id}`.

- Siempre responde **200** con `estado` `ACREDITADO` o `RECHAZADO` + `motivo_rechazo`
  (`CUENTA_NO_EXISTE`, `CUENTA_INACTIVA`), como indica el OpenAPI. Los rechazos también se guardan.
- Idempotencia por `(banco_origen, transfer_id)`: un reintento devuelve lo mismo sin acreditar dos
  veces; el mismo `transfer_id` con otros datos → `409 CONFLICTO_IDEMPOTENCIA`.
- `banco_origen` del cuerpo debe coincidir con `X-Bank-Id` (quien firma) → si no, `400 SOLICITUD_INVALIDA`.
- Acredita bajo bloqueo de la cuenta (`FOR UPDATE`) y registra `movimientos` y `eventos_operacion`.
- Cada banco solo puede consultar sus propios créditos.

## Login del frontend (JWT)

Es la API propia de este banco (no es parte del contrato entre bancos). El cliente inicia sesión con
**documento (C.I.) + contraseña** y recibe un token que manda como `Authorization: Bearer <token>`.

1. Aplicar el esquema actualizado (tabla `usuarios`) y poner `JWT_SECRET` en `.env`
   (mínimo 32 caracteres; generar con `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
2. Crear usuarios: `python -m scripts.crear_usuario <documento>` (pide la contraseña), o, solo en desarrollo,
   `python -m scripts.cargar_credenciales_prueba` (da login a los titulares de ejemplo y PIN a las cuentas;
   los valores están como constantes dentro del script).
3. `POST /auth/login` con `{"documento": "...", "password": "..."}`. En `/docs` usar **Authorize** y pegar el token.

**Sesión e inactividad.** El token identifica una sesión guardada en la tabla `sesiones` (claim `jti`).
Cada request autenticado renueva su última actividad (expiración deslizante):
- Sin actividad durante `INACTIVIDAD_SEG` (300 s por defecto) la sesión se cierra → `401 SESION_EXPIRADA`.
- A los `JWT_MINUTOS` (30) del login se cierra aunque haya actividad (tope absoluto) → `401 SESION_EXPIRADA`.
- `POST /auth/logout` la cierra; `POST /auth/cambiar-password` cierra las demás sesiones del usuario.
- Un token de una sesión ya cerrada da `401 NO_AUTENTICADO`.
El login devuelve `inactividad_seg` para que el frontend pueda mostrar un aviso antes del cierre.
Si la base ya existía, aplicar `db/migracion_sesiones.sql`.

Detalles: contraseñas y PIN con bcrypt; token HS256 de 30 min (`JWT_MINUTOS`); sin refresh token;
tras 5 fallos seguidos se bloquea el usuario 15 min (`MAX_INTENTOS_LOGIN`, `BLOQUEO_LOGIN_MIN`); el error es el
mismo si el usuario no existe o la clave es mala. Para proteger otro endpoint:
`usuario: Usuario = Depends(usuario_actual)`.

## Probar una llamada firmada

Cargar la clave pública en la fila del banco que vas a simular (ej. `BCO1`) y:

```powershell
python -m scripts.cliente_prueba GET /cuentas/1001 --banco BCO1
```

(En Git Bash anteponer `MSYS_NO_PATHCONV=1`, si no convierte `/cuentas/...` en una ruta de Windows.)

## A acordar con los otros grupos / el BCP

- **Cadena canónica en GET:** el cuerpo es vacío y la cadena termina en `\n`. El contrato no lo aclara.
- **Código para PIN incorrecto:** no está en el catálogo (se usa `PIN_INCORRECTO`, no oficial).
- **Plan de pruebas, caso 12:** habla de `X-Api-Key`, pero el contrato usa firma Ed25519.
- **Topes de importe:** no se validan en Pydantic; superarlos debe dar `LIMITE_EXCEDIDO` (409) y no `SOLICITUD_INVALIDA`.
