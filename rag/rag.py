"""
Sistema RAG — agente de Juan José "Toto" Reyes Baquerizo (Guayaquil)
=====================================================================

Ingesta de la BASE DE CONOCIMIENTO PÚBLICA del candidato a la colección de Qdrant
que consulta el agente (tool `buscar_conocimiento`). Copiado de MAD, donde ya corre
en producción sobre la Ayuda Memoria de Madeline Cañizares.

QUÉ SE CARGA: la "Ayuda Memoria Integral" del candidato (rag/data/). Es el mismo
formato de documento que en MAD: secciones numeradas "N. TÍTULO" (quién es, familia,
la persona, formación, vocación social, trayectoria en el servicio público, proyectos
sociales, causas, valores, 360°, concepto central).

⚠️ ESTADO AL 2026-10-21: el documento que entregó el cliente es la PLANTILLA VACÍA.
   Solo la sección 12 (valores) trae texto, y es de otro perfil ("formación médica",
   "ejercicio de la Medicina"). Por eso `main()` se NIEGA a cargar si quedan menos de
   `MIN_SECCIONES_CON_CONTENIDO` secciones con cuerpo, o si aparece texto de otro
   perfil: cargar eso pondría en boca del agente datos falsos sobre el candidato.
   Cuando llegue la versión completa, reemplazar el PDF de rag/data/ y correr de nuevo.

Qué se indexa y qué NO:
    SÍ  -> conocimiento ESTABLE y aprobado: biografía, formación, trayectoria,
           proyectos, causas y valores.
    NO  -> nada DINÁMICO (agenda, eventos, fechas): quedaría congelado.

Pipeline de indexación (4 etapas clásicas de un RAG):

    1. Document Loader    -> PDF de `data/` (pypdf, texto por página)
    2. Document Splitting -> división ESTRUCTURAL por los encabezados numerados
                             ("N. TÍTULO"). Sin `chunk_size` y sin overlap: cada
                             sección ya es una unidad temática autocontenida.
    3. Embedding Model    -> OpenAI `text-embedding-3-large` (3072 dim)
    4. Carga a Qdrant     -> colección `tenant_id_campana_toto_reyes`, con el mismo
                             contrato de payload que lee `tools/conocimiento_tool.py`
                             ("text" + metadatos)

Por qué `text-embedding-3-large` (decisión del 2026-10-21):
    Las consultas son en español coloquial y el large rinde bastante mejor en
    búsqueda multilingüe (MIRACL 54,9 vs 44,0 del small). Con un corpus de pocas
    páginas el costo extra es de centavos. OJO: el modelo de la ingesta y el del
    retrieval deben coincidir SIEMPRE (mismo `EMBEDDING_MODEL` en el .env); si se
    cambia, hay que REINGERIR todo.

Nombre de la colección (skill `rag-tenant-collection-naming`):
    `tenant_id_campana_toto_reyes`. Se valida con `validacion_nombre_tenant_id.py`
    ANTES de cargar documentos o gastar tokens de embeddings. La instancia de Qdrant
    se comparte con MAD: el nombre es lo único que separa las colecciones.

⚠️ La ingesta BORRA y recrea la colección apuntada por QDRANT_COLLECTION, por lo
que pide confirmación interactiva (o el flag --yes) antes de tocar Qdrant.

Ejecución:
    python rag/rag.py --dry-run   # solo loader + splitting: no gasta tokens ni toca Qdrant
    python rag/rag.py             # pedirá confirmación; usa --yes para omitirla
"""

import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv, find_dotenv

from langchain_openai import OpenAIEmbeddings
from pypdf import PdfReader

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

# Guardia de nombrado multi-tenant (validador de la skill, copiado a la raíz).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from validacion_nombre_tenant_id import validar_qdrant  # noqa: E402


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
_ = load_dotenv(find_dotenv())  # Carga OPENAI_API_KEY y la config de Qdrant desde .env

BASE_DIR = Path(__file__).resolve().parent
PDF_PATH = BASE_DIR / "data" / "AYUDA_MEMORIA_INTEGRAL_JUAN_JOSE_REYES_BAQUERIZO.pdf"

COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "tenant_id_campana_toto_reyes")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
# Dimensión del vector según el modelo. Si se usa otro, agregarlo aquí.
DIMENSIONES = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}
VECTOR_SIZE = DIMENSIONES.get(EMBEDDING_MODEL, 3072)

# Guardia contra cargar la plantilla vacía (ver docstring).
MIN_SECCIONES_CON_CONTENIDO = 5
# Frases que delatan texto de OTRO perfil copiado en la plantilla.
TEXTO_DE_OTRO_PERFIL = ("formación médica", "Medicina")

# Conexión: si hay QDRANT_URL (Qdrant gestionado/producción) se usa URL + API key;
# si no, host+port (Qdrant local). Debe coincidir con tools/conocimiento_tool.py.
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or os.getenv("QDRAN_API_KEY")
if not os.getenv("QDRANT_API_KEY") and os.getenv("QDRAN_API_KEY"):
    print("⚠️  Usando QDRAN_API_KEY (typo). Renombrá la variable a QDRANT_API_KEY en tu .env.")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_DESTINO = QDRANT_URL or f"{QDRANT_HOST}:{QDRANT_PORT}"

# --- Estructura del documento fuente -------------------------------------
# Encabezado de sección: "1. ¿QUIÉN ES JUAN JOSE REYES BAQUERIZO?" (numeral + punto + título).
# Es el único delimitador de chunk: si se reemplaza el PDF por otra versión, hay
# que mantener ese formato de encabezado o ajustar este regex.
ENCABEZADO = re.compile(r"^[ \t]*(\d{1,2})\.[ \t]+(\S[^\n]*?)[ \t]*$", re.MULTILINE)

# Pie/encabezado de página que pypdf repite en cada página: se descarta para que
# no ensucie el texto de los chunks ni consuma tokens de embedding.
PIE_PAGINA = "Ayuda Memoria Integral · JUAN JOSE REYES BAQUERIZO"

# Bloque de portada (antes de la sección 1). No es un chunk: se usa como
# contexto en el encabezado de CADA chunk para que sea autocontenido.
TITULO_DOC = "Juan José Reyes Baquerizo (Toto Reyes) — Ayuda Memoria Integral"
SUBTITULO_DOC = "Perfil personal, profesional, social y humano"

# Metadatos de gobernanza del conocimiento. Van en el payload de cada punto para
# poder filtrar por visibilidad/estado desde la tool.
META_FUENTE = {
    "source_id": "AYUDA_MEMORIA_TOTO_REYES_2026",
    "content_type": "biography",
    "visibility": "public",
    "language": "es",
    "source_status": "approved",
    "dynamic_data": False,      # este contenido NO cambia; lo dinámico va por API
}


# ---------------------------------------------------------------------------
# 1. DOCUMENT LOADER
# ---------------------------------------------------------------------------
def cargar_pdf(pdf_path: Path) -> list[str]:
    """Carga el PDF y devuelve el texto de cada página (índice 0 = página 1)."""
    print(f"[1/4] Cargando el PDF: {pdf_path.name}")
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"No existe {pdf_path}. Coloca el PDF de la base de conocimiento en "
            f"{pdf_path.parent}/."
        )
    paginas = [(pagina.extract_text() or "") for pagina in PdfReader(str(pdf_path)).pages]
    total = sum(len(p) for p in paginas)
    if not total:
        raise ValueError(
            f"{pdf_path.name} no devolvió texto. ¿Es un PDF escaneado (imagen)? "
            "Ese caso necesitaría OCR, no pypdf."
        )
    print(f"      -> {len(paginas)} páginas, {total} caracteres.")
    return paginas


# ---------------------------------------------------------------------------
# 2. DOCUMENT SPLITTING (estructural: una sección = un chunk)
# ---------------------------------------------------------------------------
def _reflujo(texto: str) -> str:
    """Reconstruye los párrafos que el PDF partió por ancho de línea.

    pypdf marca la CONTINUACIÓN de un párrafo dejando espacios al final de la
    línea, y no los deja al cerrarlo. Se aprovecha esa señal: una línea que
    termina en espacio se une con la siguiente; el resto conserva su salto.
    Así los párrafos quedan enteros (mejor para el embedding) sin pegar entre sí
    los ítems de las listas ni los encabezados. Verificado contra este PDF: 90
    líneas de continuación y 76 de cierre, sin falsos positivos.
    """
    salida: list[str] = []
    for linea in texto.split("\n"):
        continuacion = bool(linea.strip()) and linea != linea.rstrip()
        if salida and salida[-1] == "\x00":        # la línea previa pedía unión
            salida.pop()
            salida[-1] = f"{salida[-1]} {linea.strip()}"
        else:
            salida.append(linea.strip())
        if continuacion:
            salida.append("\x00")                  # marca: unir con la siguiente
    return "\n".join(l for l in salida if l != "\x00")


def _limpiar(paginas: list[str]) -> tuple[str, dict[int, int]]:
    """Devuelve el texto completo limpio y el mapa {offset de página -> nº de página}.

    Quita el pie de página repetido y reconstruye los párrafos. El mapa de
    offsets permite luego saber en qué página empieza cada sección (metadato
    `page`, que `tools/conocimiento_tool.py` muestra en cada resultado).
    """
    partes: list[str] = []
    offsets: dict[int, int] = {}
    posicion = 0
    for numero, pagina in enumerate(paginas, start=1):
        limpia = _reflujo(pagina.replace(PIE_PAGINA, ""))
        limpia = re.sub(r"\n{3,}", "\n\n", limpia).strip()
        offsets[posicion] = numero
        partes.append(limpia)
        posicion += len(limpia) + 1        # +1 por el "\n" con el que se unen
    return "\n".join(partes), offsets


def _pagina_de(offset: int, offsets: dict[int, int]) -> int:
    """Número de página al que pertenece un offset del texto completo."""
    return max((p for o, p in offsets.items() if o <= offset), default=1)


def dividir_por_secciones(paginas: list[str]) -> list[dict]:
    """Divide el documento en chunks: una sección numerada = un chunk.

    Cada chunk se guarda AUTOCONTENIDO: lleva el título del documento y el de su
    sección. Sin esto, una sección como "12. VALORES Y RASGOS" nunca menciona al
    candidato y la búsqueda semántica por "valores de Toto" la recuperaría peor.
    """
    print("[2/4] Dividiendo por encabezados de sección ('N. TÍTULO')...")
    texto, offsets = _limpiar(paginas)
    encabezados = list(ENCABEZADO.finditer(texto))
    if not encabezados:
        raise ValueError(
            "No se encontró ningún encabezado 'N. TÍTULO' en el PDF. Si cambió el "
            "formato del documento, ajustá el regex ENCABEZADO."
        )

    chunks = []
    for i, encabezado in enumerate(encabezados):
        numero = int(encabezado.group(1))
        titulo = encabezado.group(2).strip()
        inicio = encabezado.end()
        fin = encabezados[i + 1].start() if i + 1 < len(encabezados) else len(texto)
        cuerpo = texto[inicio:fin].strip()
        if not cuerpo:
            print(f"      ⚠ Sección {numero} ('{titulo}') sin cuerpo: se omite.")
            continue

        chunks.append(
            {
                "chunk_id": f"kb-toto-reyes-{numero:02d}",
                "titulo": titulo,
                # Encabezado de contexto + cuerpo: chunk autocontenido.
                "text": (
                    f"# {TITULO_DOC}\n"
                    f"({SUBTITULO_DOC})\n\n"
                    f"## {numero}. {titulo}\n\n"
                    f"{cuerpo}"
                ),
                "tipo": "perfil_biografico",
                "seccion": numero,
                "page": _pagina_de(encabezado.start(), offsets),
                "tags": ["toto-reyes", "perfil", "biografia", "conocimiento-estable"],
                "source": PDF_PATH.name,
            }
        )

    largos = sorted(len(c["text"]) for c in chunks)
    mediana = largos[len(largos) // 2]
    print(f"      -> {len(chunks)} chunks (secciones {chunks[0]['seccion']}-{chunks[-1]['seccion']}); "
          f"mediana {mediana} caracteres, máx {largos[-1]}.")
    return chunks


def revisar_contenido(chunks: list[dict]) -> list[str]:
    """Motivos para NO cargar el documento (lista vacía = se puede cargar).

    Existe porque el cliente entregó la plantilla vacía: cargarla haría que el
    agente "supiera" solo una sección, y además con datos de otra persona.
    """
    problemas = []
    if len(chunks) < MIN_SECCIONES_CON_CONTENIDO:
        problemas.append(
            f"Solo {len(chunks)} sección(es) con contenido (mínimo "
            f"{MIN_SECCIONES_CON_CONTENIDO}). ¿Es la plantilla vacía de la Ayuda Memoria?"
        )
    for chunk in chunks:
        ajenas = [f for f in TEXTO_DE_OTRO_PERFIL if f.lower() in chunk["text"].lower()]
        if ajenas:
            problemas.append(
                f"La sección {chunk['seccion']} ('{chunk['titulo']}') menciona {ajenas}: "
                "parece texto de otro perfil, no del candidato."
            )
    return problemas


# ---------------------------------------------------------------------------
# 3. EMBEDDING MODEL (OpenAI text-embedding-3-large)
# ---------------------------------------------------------------------------
def generar_embeddings(chunks: list[dict]) -> list[list[float]]:
    """Genera los vectores de cada chunk. Debe usar el MISMO modelo que el retrieval."""
    print(f"[3/4] Generando embeddings con el modelo '{EMBEDDING_MODEL}'...")
    modelo = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    vectores = modelo.embed_documents([chunk["text"] for chunk in chunks])
    dim = len(vectores[0])
    print(f"      -> {len(vectores)} vectores generados ({dim} dimensiones c/u).")
    if dim != VECTOR_SIZE:
        raise ValueError(
            f"El modelo '{EMBEDDING_MODEL}' devolvió {dim} dimensiones, pero la "
            f"colección se crea con {VECTOR_SIZE}. Ajustá VECTOR_SIZE (y reingerí)."
        )
    return vectores


# ---------------------------------------------------------------------------
# 4. CARGA DE LOS VECTORES A QDRANT
# ---------------------------------------------------------------------------
def _cliente_qdrant() -> QdrantClient:
    """Cliente de Qdrant (misma lógica que tools/conocimiento_tool.py)."""
    if not QDRANT_URL:
        return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    parsed = urlparse(QDRANT_URL)
    # URL https sin puerto explícito -> 443 (proxy estándar tipo easypanel).
    if parsed.port is None and parsed.scheme == "https":
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, port=443)
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def confirmar_recreacion() -> None:
    """Pide confirmación antes de borrar/recrear la colección (o acepta --yes)."""
    if "--yes" in sys.argv:
        return
    if not sys.stdin.isatty():
        raise SystemExit(
            f"Ejecución no interactiva: la ingesta BORRA la colección "
            f"'{COLLECTION_NAME}'. Vuelve a ejecutar con --yes para autorizarla."
        )
    respuesta = input(
        f"⚠ Se BORRARÁ y recreará la colección '{COLLECTION_NAME}' en "
        f"{QDRANT_DESTINO}. ¿Continuar? [si/no]: "
    )
    if respuesta.strip().lower() not in ("si", "sí", "s", "yes", "y"):
        raise SystemExit("Ingesta cancelada por el usuario.")


def cargar_a_qdrant(chunks: list[dict], vectores: list[list[float]]) -> None:
    """Crea la colección de cero y sube los vectores con su payload a Qdrant."""
    print(f"[4/4] Conectando a Qdrant en {QDRANT_DESTINO}...")
    client = _cliente_qdrant()

    if client.collection_exists(COLLECTION_NAME):
        anterior = client.count(COLLECTION_NAME).count
        print(f"      -> La colección ya existía con {anterior} puntos: se elimina y recrea.")
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=rest.VectorParams(size=VECTOR_SIZE, distance=rest.Distance.COSINE),
    )
    print(f"      -> Colección '{COLLECTION_NAME}' lista ({VECTOR_SIZE} dim, cosine).")

    # Payload: "text" es la clave que lee tools/conocimiento_tool.py; el resto son
    # metadatos que la tool muestra o filtra. META_FUENTE añade la gobernanza
    # del conocimiento.
    payloads = [
        {
            "text": chunk["text"],
            "chunk_id": chunk["chunk_id"],
            "titulo": chunk["titulo"],
            "tipo": chunk["tipo"],
            "seccion": chunk["seccion"],
            "page": chunk["page"],
            "tags": chunk["tags"],
            "source": chunk["source"],
            "embedding_model": EMBEDDING_MODEL,   # trazabilidad del espacio vectorial
            **META_FUENTE,
        }
        for chunk in chunks
    ]

    # ID determinista por chunk_id: reingerir produce los mismos IDs, sin duplicados.
    ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"])) for chunk in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("chunk_id duplicado: los IDs deterministas colisionarían.")

    client.upload_collection(
        collection_name=COLLECTION_NAME, vectors=vectores, payload=payloads, ids=ids
    )
    print(f"      -> {client.count(COLLECTION_NAME).count} registros almacenados en Qdrant.")


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def main():
    seco = "--dry-run" in sys.argv

    # Guardia de nombrado ANTES de cargar documentos o gastar tokens.
    validacion = validar_qdrant(COLLECTION_NAME)
    if not validacion.ok:
        raise SystemExit(f"Nombre de colección inválido: {validacion.motivos}")
    print(f"✅ Nombre de colección válido para Qdrant: '{COLLECTION_NAME}'")

    if not seco and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "No se encontró OPENAI_API_KEY. Defínela en el archivo .env antes de ejecutar."
        )

    chunks = dividir_por_secciones(cargar_pdf(PDF_PATH))   # 1-2. Loader + Splitting
    problemas = revisar_contenido(chunks)

    if seco:
        for p in problemas:
            print(f"\n⛔ {p}")
        print("\n🔎 Modo --dry-run: no se generan embeddings ni se toca Qdrant.")
        for chunk in chunks:
            print(f"\n--- {chunk['chunk_id']} | pág. {chunk['page']} | "
                  f"{len(chunk['text'])} chars | {chunk['titulo']}")
            print("    " + chunk["text"].split("\n\n", 2)[-1][:220].replace("\n", " ") + "…")
        return

    if problemas:
        raise SystemExit("No se carga nada a Qdrant:\n  - " + "\n  - ".join(problemas))

    confirmar_recreacion()                          # Autorización ANTES de gastar tokens

    vectores = generar_embeddings(chunks)           # 3. Embedding Model
    cargar_a_qdrant(chunks, vectores)               # 4. Carga a Qdrant

    print(f"\n✅ Ingesta completada en '{COLLECTION_NAME}' ({QDRANT_DESTINO}).")
    print(f"   Modelo de embeddings: {EMBEDDING_MODEL} ({VECTOR_SIZE} dim). "
          "El retrieval DEBE usar el mismo modelo.")


if __name__ == "__main__":
    main()
