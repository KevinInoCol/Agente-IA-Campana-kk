# Migraciones

SQL puro e idempotente. Se pega tal cual en el SQL Editor de Supabase (o se corre con
`psql`), en este orden:

| Orden | Archivo | Qué hace |
|---|---|---|
| 0 | `000_bootstrap_tenant.sql` | Plantilla de la skill `postgres-tenant-schema-naming`: reemplazar `campana_tenant` por `campana_toto_reyes` y `app_usuario` por el rol de la app. Fija el `search_path` del rol del lado del servidor. Solo hace falta si se usa el pooler de Supabase en modo transacción (6543). |
| 1 | `001_simpatizantes.sql` | Esquema `campana_toto_reyes` y la tabla `simpatizantes` (los leads del flujo "Quiero sumarme"). |
| 2 | `002_ciudadanos.sql` | Tabla `ciudadanos`: el registro de la bienvenida (guion v1), una fila por número de WhatsApp. |

El esquema está escrito a mano en `001` porque el SQL se pega en el editor. En el código
nunca: sale de `DB_SCHEMA`. Si se cambia el esquema, cambiarlo en los dos lados.

Las 4 tablas de memoria del agente (`checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, `checkpoint_migrations`) **no** van aquí: las crea
`PostgresSaver.setup()` al arrancar, dentro de `DB_SCHEMA`.
