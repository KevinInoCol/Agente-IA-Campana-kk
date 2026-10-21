-- =============================================================================
-- Bootstrap de un TENANT en Postgres / Supabase (un esquema por inquilino)
-- =============================================================================
-- Skill: postgres-tenant-schema-naming
--
-- CÓMO USARLO
--   1. Reemplazá TODAS las apariciones de  campana_tenant  por el esquema del
--      tenant (validalo antes con `python validacion_esquema_tenant.py <nombre>`).
--   2. Reemplazá  app_usuario  por el rol con el que conecta tu aplicación
--      (en Supabase suele ser `postgres`).
--   3. Ejecutalo en el SQL Editor de Supabase, o con psql.
--   Es IDEMPOTENTE: se puede correr de nuevo sin efecto.
--
--   Con psql podés evitar el buscar-y-reemplazar:
--       \set esquema campana_tenant
--       \set app_rol  postgres
--   y usar :"esquema" / :"app_rol" donde correspondan.
-- =============================================================================

-- 1) El esquema del tenant -----------------------------------------------------
create schema if not exists campana_tenant;

comment on schema campana_tenant is
    'Datos de un tenant (una campaña/cliente). Aislado del resto: ninguna consulta '
    'de la app debe cruzar de esquema. Ver la skill postgres-tenant-schema-naming.';

-- 2) Permisos ------------------------------------------------------------------
-- El rol de la app necesita `usage` sobre el esquema y permisos sobre lo que ya
-- existe. `alter default privileges` cubre lo que se cree DESPUÉS, para no tener
-- que repetir grants en cada migración.
grant usage, create on schema campana_tenant to app_usuario;

grant all on all tables    in schema campana_tenant to app_usuario;
grant all on all sequences in schema campana_tenant to app_usuario;

alter default privileges in schema campana_tenant
    grant all on tables to app_usuario;
alter default privileges in schema campana_tenant
    grant all on sequences to app_usuario;

-- 3) search_path del rol (LA PIEZA CLAVE) --------------------------------------
-- Fija el esquema del lado del SERVIDOR. Es lo que hace que funcione incluso con
-- PgBouncer / Supabase Supavisor en modo TRANSACCIÓN (puerto 6543), donde el
-- parámetro `options=-c search_path=...` que manda el cliente PUEDE ser ignorado
-- —y entonces las tablas terminan en `public` sin ningún aviso—.
--
-- A propósito NO se agrega `, public` como respaldo: si el esquema del tenant
-- estuviera vacío, una consulta sin calificar caería en las tablas de `public` y
-- leeríamos los datos de OTRO tenant. Es preferible que falle.
--
-- OJO: aplica a las conexiones NUEVAS del rol, no a las ya abiertas. Reiniciá la
-- app (o el pool) después de ejecutarlo.
alter role app_usuario in database postgres
    set search_path = campana_tenant;

-- 4) (Opcional) Exponer el esquema a la API REST de Supabase -------------------
-- SOLO si vas a consultar este esquema por PostgREST (supabase-js / REST). Si tu
-- backend conecta por Postgres directo, NO lo expongas: menos superficie.
-- Además del grant de abajo hay que añadir el esquema en
--   Settings -> API -> Exposed schemas
-- grant usage on schema campana_tenant to anon, authenticated;
-- grant all on all tables in schema campana_tenant to anon, authenticated;
--
-- Y si lo expones, activá RLS en cada tabla: con la clave `anon` sí aplica.
--   alter table campana_tenant.mi_tabla enable row level security;
-- Recordá que el DUEÑO de la tabla y la clave `service_role` NO pasan por RLS
-- (el dueño solo con `force row level security`). Para un backend que conecta
-- como dueño, el aislamiento real lo da el ESQUEMA, no RLS.

-- =============================================================================
-- VERIFICACIÓN — correrla siempre después del bootstrap y tras cada deploy
-- =============================================================================

-- a) ¿El rol quedó apuntando al esquema correcto?
select rolname, rolconfig
  from pg_roles
 where rolname = 'app_usuario';

-- b) Desde una conexión NUEVA de la app: ¿en qué esquema escribe?
select current_schema(), current_user;

-- c) ¿Las tablas cayeron donde debían? (esperado: todas en campana_tenant)
select table_schema, table_name
  from information_schema.tables
 where table_schema in ('public', 'campana_tenant')
 order by table_schema, table_name;

-- d) Inventario de tenants de esta base
select nspname as esquema
  from pg_namespace
 where nspname not like 'pg_%'
   and nspname not in ('information_schema', 'public')
 order by 1;

-- =============================================================================
-- BORRAR UN TENANT (destructivo — leer dos veces)
-- =============================================================================
-- `cascade` borra TODAS las tablas, secuencias y datos del tenant, sin vuelta
-- atrás. Hacé backup antes y confirmá el nombre del esquema.
--   drop schema campana_tenant cascade;
--   alter role app_usuario in database postgres reset search_path;
