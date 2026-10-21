"""Guardia de la ingesta (no cargar la plantilla vacía) y config de la tool de búsqueda."""

from rag import rag
from tools import conocimiento_tool


def _chunk(seccion, texto):
    return {"seccion": seccion, "titulo": f"Sección {seccion}", "text": texto}


def test_plantilla_con_pocas_secciones_se_rechaza():
    problemas = rag.revisar_contenido([_chunk(1, "Familia."), _chunk(13, "Concepto central.")])
    assert any("plantilla vacía" in p for p in problemas)


def test_texto_de_otro_perfil_se_rechaza():
    chunks = [_chunk(i, "Ingeniero por vocación.") for i in range(1, 7)]
    chunks.append(_chunk(12, "Su formación médica fortaleció su empatía."))
    problemas = rag.revisar_contenido(chunks)
    assert len(problemas) == 1 and "sección 12" in problemas[0]


def test_documento_completo_pasa():
    chunks = [_chunk(i, "Juan José es guayaquileño e ingeniero.") for i in range(1, 9)]
    assert rag.revisar_contenido(chunks) == []


def test_ingesta_y_busqueda_usan_el_mismo_modelo_y_coleccion():
    assert rag.EMBEDDING_MODEL == conocimiento_tool.EMBEDDING_MODEL
    assert rag.COLLECTION_NAME == conocimiento_tool.QDRANT_COLLECTION
    assert rag.VECTOR_SIZE == rag.DIMENSIONES[rag.EMBEDDING_MODEL]
