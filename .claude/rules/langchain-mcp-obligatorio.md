# Regla: consultar el MCP de docs de LangChain ANTES de escribir código LangChain

**Ámbito:** este proyecto (agente de WhatsApp de Juan José "Toto" Reyes Baquerizo).

## Regla (obligatoria)

Antes de **escribir, modificar, refactorizar o depurar** cualquier código que use
LangChain / LangGraph / LangSmith, es OBLIGATORIO consultar primero la
documentación oficial vía el **MCP `docs-langchain`** instalado en este entorno.

- **Herramientas del MCP:**
  - `mcp__docs-langchain__search_docs_by_lang_chain` — buscar por palabras clave.
  - `mcp__docs-langchain__query_docs_filesystem_docs_by_lang_chain` — leer/consultar
    páginas concretas de la doc.
- **No** escribas la API de memoria: verifícala contra la doc del MCP en cada caso.
  No confíes en el conocimiento previo del modelo (las APIs de LangChain v1 cambian
  seguido y difieren de las versiones legacy).

## Cuándo aplica

Siempre que el código toque (importe o use):
`langchain`, `langchain_core`, `langchain_openai`, `langchain_community`,
`langgraph`, `langsmith` — incluyendo agentes (`create_agent`), tools (`@tool`),
middleware, checkpointers/memoria, retrievers, RAG, grafos, o cualquier API de
estos paquetes.

## Cómo cumplirla (flujo mínimo)

1. Identifica la pieza de LangChain que vas a tocar (p. ej. `create_agent`,
   `PIIMiddleware`, `PostgresSaver`, `@tool`).
2. Consulta el MCP `docs-langchain` (search + query) para confirmar la firma, los
   parámetros y el patrón vigente ANTES de escribir.
3. Recién entonces escribe/edita el código, alineado a lo que devolvió la doc.
4. Si la doc contradice tu memoria, gana la doc. Si detectas una errata en la doc,
   verifica también con `inspect` contra la versión instalada.

## Prohibido (APIs legacy)

No introducir APIs viejas: `AgentExecutor`, `create_react_agent` (legacy),
`ConversationChain`, `RetrievalQA`, `RunnableWithMessageHistory`,
`PostgresChatMessageHistory`, ni el parámetro `prompt` de patrones antiguos.
Usar las APIs v1 verificadas (`create_agent`, `ToolRuntime`, `PostgresSaver`).

## Motivo

Las APIs de LangChain v1 rompen con las versiones anteriores y con el conocimiento
previo del modelo. Escribir "de memoria" ha producido erratas reales en los
proyectos de campaña anteriores (AVANZA, MAD). La doc oficial vía MCP es la
fuente de verdad.
