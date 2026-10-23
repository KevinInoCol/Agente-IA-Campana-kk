create schema if not exists campana_toto_reyes;

create table if not exists campana_toto_reyes.ciudadanos (
    id              bigint generated always as identity primary key,
    telefono        text,
    cedula          text,
    nombres         text,
    correo          text,
    barrio          text,
    nombre_perfil   text,
    canal           text not null default 'whatsapp',
    creado_en       timestamptz not null default now(),
    actualizado_en  timestamptz not null default now()
);

create unique index if not exists ciudadanos_telefono_uidx
    on campana_toto_reyes.ciudadanos (telefono)
    where telefono is not null;

create index if not exists ciudadanos_cedula_idx
    on campana_toto_reyes.ciudadanos (cedula);
