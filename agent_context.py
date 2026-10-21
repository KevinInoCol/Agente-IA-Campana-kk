"""
Contexto de ejecución del agente (datos por-invocación, NO generados por el LLM).
=================================================================================

`ConversacionContext` viaja en cada `agent.invoke(..., context=ConversacionContext(...))`
y las tools lo leen con `runtime: ToolRuntime[ConversacionContext]` (parámetro
inyectado y OCULTO del modelo: no aparece en el schema de la tool).

Sirve para pasar lo que conoce el CANAL pero no el LLM. El caso que importa es el
teléfono: la tool `guardar_simpatizante` lo guarda junto al lead, pero no puede ser
un argumento de la tool, porque entonces lo escribiría el modelo (y podría
inventarlo, o tomar un número que la persona mencionó en el chat). Lo pone el canal
desde el número del webhook de SmartGob Conecta.

Se define en un módulo hoja (sin imports pesados) para que lo importen sin ciclos
`agent.py`, las tools y los canales (`main.py`, `main_conecta.py`).
"""

from dataclasses import dataclass


@dataclass
class ConversacionContext:
    """Datos de la conversación actual que las tools pueden necesitar.

    telefono:       Número de WhatsApp de la persona, solo dígitos con código de país
                    (p. ej. "593991234567"). None en el CLI.
    nombre_perfil:  Nombre del perfil de WhatsApp (`pushName`). Es lo que la persona
                    puso en su app, no un dato verificado: se guarda aparte del nombre
                    que da en el flujo, solo como referencia.
    canal:          De dónde vino el mensaje ("whatsapp" o "cli").
    """

    telefono: str | None = None
    nombre_perfil: str | None = None
    canal: str = "cli"
