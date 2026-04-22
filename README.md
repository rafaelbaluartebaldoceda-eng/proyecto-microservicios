# Microservicio de Reportes y Tareas Asincronas

Backend corporativo para la generacion desacoplada de reportes Excel y PDF mediante procesamiento asincrono. El servicio expone una API con FastAPI para registrar solicitudes, delega la ejecucion pesada a Celery, utiliza Redis como broker, persiste trazabilidad operativa en PostgreSQL y publica los archivos resultantes en storage local o S3-compatible.

## Estado del proyecto

El proyecto se encuentra implementado con una base funcional completa para entorno de desarrollo y despliegue reproducible:

- API REST para alta, consulta, listado, cancelacion y descarga de reportes.
- Procesamiento asincrono con Celery y Redis.
- Persistencia de solicitudes, eventos, archivos e intentos de procesamiento.
- Generacion real de archivos Excel y PDF.
- Control de acceso mediante JWT y autorizacion por roles.
- Descarga protegida con enlaces firmados.
- Retencion aplicada sobre consultas y listados de reportes vencidos.
- Monitoreo operativo con Flower.
- Contenerizacion con Docker y orquestacion local con Docker Compose.
- Cobertura automatizada de flujos principales y casos borde con pytest.

## Objetivo del sistema

Este microservicio resuelve un problema comun en plataformas empresariales: la generacion de reportes pesados no debe bloquear la API principal ni degradar la experiencia del usuario. En lugar de mantener al cliente esperando mientras se construye un archivo, el sistema registra la solicitud, devuelve inmediatamente un identificador de seguimiento y procesa el reporte en segundo plano.

El resultado es un servicio preparado para:

- soportar picos de carga sin congelar el canal HTTP;
- mantener trazabilidad del estado de cada solicitud;
- ofrecer reintentos y observabilidad operativa;
- desacoplar la generacion de archivos del backend principal;
- evolucionar hacia nuevas fuentes de datos y nuevos tipos de reporte.

## Caracteristicas implementadas

### Gestion de solicitudes de reportes

- Creacion de solicitudes con filtros de negocio y formato de salida.
- Soporte para tipos de reporte:
  - `sales_summary`
  - `operations_kpis`
  - `audit_log`
- Soporte para formatos:
  - `excel`
  - `pdf`
- Validacion de filtros con control de rango de fechas y saneamiento de entradas.
- Registro inmediato del estado inicial y del identificador de seguimiento.

### Procesamiento asincrono

- Publicacion de tareas en Celery usando Redis como broker.
- Workers desacoplados del ciclo HTTP.
- Registro de intentos de ejecucion, inicio, finalizacion, error y reintentos.
- Ruta especial para modo `eager` en desarrollo y testing, lo que permite validar el flujo completo sin infraestructura adicional.

### Generacion de archivos

- Excel generado con `openpyxl` y apoyo de `pandas` para tabulados.
- PDF generado con `reportlab` con encabezados y tablas legibles.
- Construccion de datasets deterministas por solicitud.

Nota importante:
El proyecto no recibe en esta version una fuente corporativa externa ya definida en el PDF. Por eso el servicio genera datos sinteticos deterministas a partir de los filtros y del `report_id`. Esta decision deja el backend listo para sustituir esa capa por consultas reales a ERP, data warehouse o servicios internos sin modificar el contrato principal de la API.

### Seguridad

- Autenticacion con JWT.
- Autorizacion por roles para restringir que tipo de reportes puede generar cada usuario.
- Visibilidad por propietario o administrador.
- Descargas protegidas con token firmado y expiracion.
- Validacion de acceso previa a reglas de expiracion para evitar fuga de existencia de recursos ajenos.
- Registro de eventos de auditoria para solicitudes, procesamiento y descargas.

### Retencion y ciclo de vida

- Cada solicitud calcula `expires_at` al momento de creacion.
- Los reportes vencidos dejan de estar disponibles en detalle y descarga.
- Los listados excluyen reportes vencidos sin requerir una consulta previa del detalle.
- El servicio responde `410 Gone` cuando un usuario autorizado intenta acceder a un reporte ya vencido.

### Observabilidad y operacion

- Logs estructurados con `request_id`.
- Endpoint de salud (`/health`) para base de datos y broker.
- Flower para monitoreo de workers y tareas.
- Estados persistidos del ciclo de vida:
  - `PENDING`
  - `STARTED`
  - `SUCCESS`
  - `FAILURE`
  - `RETRY`
  - `CANCELED`

## Arquitectura

La solucion sigue una arquitectura por capas:

- `FastAPI`
  - Recibe solicitudes, autentica usuarios, valida entradas y expone el contrato HTTP.
- `Celery`
  - Ejecuta los trabajos pesados en segundo plano.
- `Redis`
  - Funciona como broker de mensajeria para las tareas asincronas.
- `PostgreSQL`
  - Conserva las solicitudes, estados, archivos asociados, eventos e intentos.
- `Storage`
  - Almacena los archivos finales. Puede ser local o S3-compatible.
- `Flower`
  - Proporciona monitoreo de workers y cola.

## Flujo funcional

1. El cliente solicita un reporte indicando tipo, formato y filtros.
2. La API valida autenticacion, permisos y estructura del pedido.
3. Se crea el registro de la solicitud en la base de datos.
4. La API responde de inmediato con un `report_id`.
5. Celery toma la tarea y ejecuta el procesamiento.
6. El worker genera el archivo Excel o PDF.
7. El archivo se guarda en storage.
8. El sistema actualiza el estado final y los metadatos.
9. El cliente consulta el estado y obtiene una URL firmada para la descarga.

## Comportamiento de descarga

El endpoint `GET /reports/{report_id}/download` trabaja en dos fases:

1. Sin `token`, genera una URL interna firmada con expiracion corta.
2. Con `token`, valida autorizacion, vigencia del reporte y existencia real del archivo antes de entregarlo.

Comportamiento segun backend de storage:

- `local`: el servicio entrega el archivo directamente y registra la descarga completada.
- `s3`: el servicio valida existencia y responde con redireccion `307` a una URL presignada; en este caso no marca una descarga completada que no controla directamente.

## Modelo de datos

El proyecto implementa las siguientes entidades principales:

- `report_requests`
  - solicitud principal del reporte, usuario, filtros, formato, estado y tiempos;
- `report_files`
  - archivo final generado y sus metadatos;
- `report_events`
  - auditoria del flujo operativo;
- `task_attempts`
  - control de intentos, tiempos y errores.

## API disponible

### `POST /reports`

Registra una solicitud de reporte y devuelve respuesta inmediata.

### `GET /reports/{report_id}`

Devuelve el detalle de una solicitud, su estado, eventos, intentos y archivo asociado si ya existe.

### `GET /reports`

Lista solicitudes segun permisos del usuario autenticado, con filtros, paginacion basica y exclusion de reportes vencidos.

### `DELETE /reports/{report_id}`

Cancela una solicitud si todavia no ha finalizado exitosamente.

### `GET /reports/{report_id}/download`

Primer paso:

- devuelve un enlace firmado interno para la descarga.

Segundo paso:

- con el token firmado, entrega el archivo local o redirige a una URL presignada de storage despues de validar existencia del objeto.

### `GET /health`

Verifica disponibilidad de base de datos y Redis.

## Tipos de autorizacion por reporte

La implementacion actual define reglas de autorizacion por rol:

- `sales_summary`: `admin`, `finance`, `sales`
- `operations_kpis`: `admin`, `operations`
- `audit_log`: `admin`, `auditor`, `security`

## Configuracion

Las variables principales de entorno se encuentran documentadas en `.env.example`.

Configuraciones destacadas:

- `DATABASE_URL`
- `SYNC_DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `TASK_ALWAYS_EAGER`
- `JWT_SECRET_KEY`
- `JWT_AUDIENCE`
- `JWT_ISSUER`
- `DOWNLOAD_TOKEN_SECRET`
- `DOWNLOAD_TOKEN_TTL_SECONDS`
- `STORAGE_BACKEND`
- `LOCAL_STORAGE_PATH`

## Ejecucion local

### Opcion recomendada: Docker Compose

1. Crear el archivo `.env` a partir de `.env.example`.
2. Ejecutar:

```bash
docker compose up --build
```

Servicios disponibles:

- API: `http://localhost:8000`
- Flower: `http://localhost:5555`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

### Generar token de desarrollo

Para pruebas locales se incluye un script utilitario:

```bash
python scripts/create_demo_token.py
```

El token generado corresponde a un usuario administrador de desarrollo.

## Ejemplo de solicitud

```bash
curl --request POST http://localhost:8000/reports \
  --header "Authorization: Bearer TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "report_type": "sales_summary",
    "format": "excel",
    "filters": {
      "start_date": "2026-04-01",
      "end_date": "2026-04-15",
      "area": "Finanzas",
      "status": "closed",
      "category": "Q2",
      "requested_user": "ana"
    }
  }'
```

## Estructura del proyecto

```text
app/
  api/
    routes/
  core/
  models/
  repositories/
  schemas/
  services/
  tasks/
scripts/
tests/
```

Descripcion breve:

- `app/api`
  - contratos HTTP, dependencias y rutas;
- `app/core`
  - configuracion, base de datos, seguridad, logging y Celery;
- `app/models`
  - entidades ORM;
- `app/repositories`
  - acceso a datos;
- `app/schemas`
  - modelos de entrada y salida;
- `app/services`
  - logica de negocio, storage y generacion de reportes;
- `app/tasks`
  - workers y tareas asincronas;
- `tests`
  - pruebas automatizadas;
- `scripts`
  - utilitarios de soporte.

## Estrategia de testing

El proyecto incluye pruebas automatizadas sobre:

- generacion de reportes Excel;
- generacion de reportes PDF;
- flujo principal de creacion, consulta y descarga;
- listado de solicitudes;
- fallo de encolado en Celery;
- reintentos y fallo final por storage;
- cancelacion concurrente;
- retencion y ocultamiento de reportes vencidos;
- descarga local con encabezados esperados;
- semantica de descarga mediante redirect para storage S3.

Estado de validacion actual:

- `13 passed` en la suite principal.

Ejecucion:

```bash
pytest -q
```

## Consideraciones de despliegue

- En desarrollo puede usarse `local storage`.
- En entornos corporativos se recomienda `S3-compatible storage`.
- Redis debe mantenerse disponible para la cola.
- PostgreSQL debe ser el origen persistente de estados y auditoria.
- Flower debe exponerse solo en redes internas o protegidas.
- Las claves JWT y secretos de descarga deben administrarse mediante secretos seguros del entorno.
- Para entornos productivos conviene complementar `create_all()` con migraciones formales de base de datos.

## Documentacion para cliente

Se incluye una guia adicional en formato texto plano pensada para instalacion y uso por parte del cliente final:

- `GUIA_INSTALACION_Y_USO_CLIENTE.txt`

## Roadmap recomendado

Mejoras naturales para siguientes iteraciones:

- conectar fuentes de datos reales;
- agregar plantillas corporativas avanzadas por tipo de reporte;
- incorporar limpieza fisica automatica de archivos vencidos por politicas de retencion;
- ampliar metricas y alertas;
- agregar versionado de plantillas y trazabilidad de dataset origen.

## Resumen ejecutivo

Este proyecto ya ofrece una base backend seria, extensible y lista para evolucionar en entorno empresarial. Su valor principal es separar la solicitud del reporte de su procesamiento, manteniendo rapidez en la API, trazabilidad operativa, seguridad en la descarga y una arquitectura preparada para escalar.
