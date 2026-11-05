-- Flujo v4 del cliente (TOTOBOT.docx, "FLUJO PROPUESTA V4", 2026-11-05).
-- No cambia columnas: el v4 ya no pide confirmar nombres ni revisar un resumen, y el
-- código marca `nombres_confirmados` y `registro_confirmado_en` solo cuando la
-- persona completa cédula, correo, celular y barrio.
--
-- OPCIONAL: quien completó sus datos con el flujo v2 pero no llegó a confirmar el
-- resumen queda registrado en el v4 (el bot ya lo trata así). Esto le pone la marca
-- para que el equipo lo cuente como registrado. Idempotente.
update campana_toto_reyes.ciudadanos
set nombres_confirmados    = true,
    registro_confirmado_en = actualizado_en
where registro_confirmado_en is null
  and cedula is not null and nombres is not null
  and correo is not null and telefono_contacto is not null and barrio is not null;
