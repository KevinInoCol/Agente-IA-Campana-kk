create schema if not exists campana_toto_reyes;

create table if not exists campana_toto_reyes.simpatizantes (
    id              bigint generated always as identity primary key,
    telefono        text,
    nombre          text not null,
    barrio          text not null,
    ayuda           text not null,
    nombre_perfil   text,
    canal           text not null default 'whatsapp',
    creado_en       timestamptz not null default now()
);

create index if not exists simpatizantes_telefono_idx
    on campana_toto_reyes.simpatizantes (telefono);

create index if not exists simpatizantes_creado_en_idx
    on campana_toto_reyes.simpatizantes (creado_en desc);
