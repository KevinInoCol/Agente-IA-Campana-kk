-- Resultado de la consulta al registro civil (DINARDAP) para la cédula guardada.
--   true  = la cédula existe en el registro civil
--   false = no se pudo consultar (fuente caída): la cédula se guardó igual, sin verificar
--   null  = no se consultó (PERSONA_FUENTE=ninguna, o registro anterior a esta columna)
-- Una cédula que NO existe no llega a guardarse.
alter table campana_toto_reyes.ciudadanos
    add column if not exists cedula_verificada boolean;
