"""
CLI interactivo para conversar con el agente de Toto Reyes.

Uso:
    python main.py                      # sesión nueva, sin teléfono
    python main.py --telefono 593991234567   # simula un número de WhatsApp

Usa la misma memoria y el mismo guardado que el canal de WhatsApp: si hay base de
datos configurada, un "Quiero sumarme" de prueba queda en la tabla de simpatizantes.
"""

import argparse
import os
import traceback
import uuid

from agent import construir_agente, flush_langfuse, traza
from agent_context import ConversacionContext


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--telefono", help="Número a simular (solo dígitos, con 593)")
    args = parser.parse_args()

    agente = construir_agente()
    thread_id = f"cli:{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    contexto = ConversacionContext(telefono=args.telefono, canal="cli")

    print("=" * 60)
    print("  Asistente de Juan José Reyes Baquerizo (Toto Reyes)")
    print("  Escribe 'salir' para terminar.")
    print("=" * 60)

    try:
        while True:
            try:
                mensaje = input("\nTú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n¡Hasta luego!")
                break
            if mensaje.lower() in ("salir", "exit", "quit"):
                print("¡Hasta luego!")
                break
            if not mensaje:
                continue
            try:
                with traza(session_id=thread_id):
                    resultado = agente.invoke(
                        {"messages": [{"role": "user", "content": mensaje}]},
                        config,
                        context=contexto,
                    )
                print("\nAgente:", resultado["messages"][-1].content)
            except Exception as e:  # noqa: BLE001 — un fallo transitorio no tumba la sesión
                print(f"\n⚠️  No pude procesar ese mensaje: {e}")
                if os.getenv("DEBUG"):
                    traceback.print_exc()
    finally:
        flush_langfuse()


if __name__ == "__main__":
    main()
