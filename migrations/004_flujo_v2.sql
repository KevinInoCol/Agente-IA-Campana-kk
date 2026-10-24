-- Flujo funcional v2 del cliente (Flujo_Agente_Juan_Jose.docx, 2026-10-24).
-- Solo AGREGA columnas e índices: el código del guion v1 sigue funcionando encima.

-- Registro inicial: términos, nombres recuperados de la DINARDAP, teléfono de
-- contacto (distinto del WhatsApp) y confirmación del perfil (F06).
alter table campana_toto_reyes.ciudadanos
    add column if not exists telefono_contacto       text,
    add column if not exists terminos_aceptados      boolean,
    add column if not exists terminos_version        text,
    add column if not exists terminos_respondido_en  timestamptz,
    add column if not exists nombres_confirmados     boolean not null default false,
    add column if not exists registro_confirmado_en  timestamptz;

-- Colaboraciones ("Quiero sumarme"): borrador -> confirmada | cancelada. Las filas
-- del guion v1 quedan como 'confirmada' (default). Al confirmar se guarda una copia
-- de los datos que la persona revisó en el resumen (F11).
alter table campana_toto_reyes.simpatizantes
    add column if not exists estado             text not null default 'confirmada',
    add column if not exists correo             text,
    add column if not exists telefono_contacto  text,
    add column if not exists confirmada_en      timestamptz,
    add column if not exists actualizado_en     timestamptz not null default now();

-- Un solo borrador abierto por número de WhatsApp.
create unique index if not exists simpatizantes_borrador_uidx
    on campana_toto_reyes.simpatizantes (telefono)
    where estado = 'borrador';
