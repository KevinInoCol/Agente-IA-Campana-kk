"""
InputGuardrail — Orquestador de Seguridad para el agente de Juan José Reyes Baquerizo
(campaña "Toto Reyes · Concejal", Guayaquil).

Pipeline de validación (hasta 8 capas; por defecto 5/8 — ver GUARDRAIL_PII_ENABLED
y GROQ_API_KEY):
  1. Secret Keys      (REGEX)    — Claves de API y tokens secretos            [siempre]
  2. Prompt Injection (REGEX)    — Jailbreak, manipulación del sistema         [siempre]
  3. Toxic Patterns   (REGEX)    — Amenazas, hate speech, acoso, autolesión    [siempre]
  4. Custom Regex     (REGEX)    — Patrones configurables por el admin         [siempre]
  5. PII Detection    (LOCAL)    — Cédula EC, RUC EC, Email, Teléfono EC       [GUARDRAIL_PII_ENABLED, default OFF]
  6. URL Filter       (REGEX)    — URLs, acortadores y dominios maliciosos     [siempre]
  7. Llama Prompt Guard 2 (GROQ) — Detección de jailbreak/injection por IA     [requiere GROQ_API_KEY]
  8. GPT-OSS-Safeguard 20B (GROQ) — Clasificación NSFW / Violencia / Odio      [requiere GROQ_API_KEY]

Patrones en inglés (EN) y español (ES).

Autor: Ing. Kevin Inofuente Colque - DataPath
"""

import os
import re
import logging
import concurrent.futures
from typing import Tuple, List, Optional

from dotenv import load_dotenv, find_dotenv

# El singleton InputGuardrail() se construye al importar este módulo (abajo), y
# agent.py lo importa ANTES de su propio load_dotenv. Cargamos el .env aquí para
# que los toggles (GUARDRAIL_PII_ENABLED) y GROQ_API_KEY se lean bien también en
# ejecución local (patrón de la decisión #7). En la VPS las vars van en el entorno
# del proceso, así que funcionan igual.
load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)

# Timeout para regex custom (evita ReDoS)
REGEX_TIMEOUT_SECONDS = 1.0


# ============================================================
# 1. SECRET KEY PATTERNS
# ============================================================
SECRET_KEY_PATTERNS: List[str] = [
    r"sk-[a-zA-Z0-9]{20,}",
    r"sk-proj-[a-zA-Z0-9\-_]{20,}",
    r"sk-ant-[a-zA-Z0-9\-_]{20,}",
    r"ghp_[a-zA-Z0-9]{36,}",
    r"ghs_[a-zA-Z0-9]{36,}",
    r"gho_[a-zA-Z0-9]{36,}",
    r"github_pat_[a-zA-Z0-9_]{20,}",
    r"AIza[a-zA-Z0-9\-_]{35,}",
    r"gsk_[a-zA-Z0-9]{20,}",
    r"AKIA[A-Z0-9]{16}",
    r"ASIA[A-Z0-9]{16}",
    r"sk_live_[a-zA-Z0-9]{24,}",
    r"pk_live_[a-zA-Z0-9]{24,}",
    r"sk_test_[a-zA-Z0-9]{24,}",
    r"hf_[a-zA-Z0-9]{20,}",
    r"SK[a-f0-9]{32}",
    r"AC[a-f0-9]{32}",
    r"tvly-[a-zA-Z0-9\-_]{20,}",
    r"eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+",
    r"(?i)bearer\s+[a-zA-Z0-9\-_\.]{20,}",
    r"(?i)(api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token)\s*[:=]\s*['\"]?[a-zA-Z0-9\-_\.]{16,}",
    r"(?i)(api[_\-]?key|secret|token|password|passwd|clave|contraseña)\s*[:=]\s*['\"]?\S{12,}",
]

# ============================================================
# 2. PROMPT INJECTION PATTERNS (EN + ES)
# ============================================================
PROMPT_INJECTION_PATTERNS: List[str] = [

    # SYSTEM MESSAGE OVERRIDE
    r"#\s*SYSTEM\s*(MESSAGE)?",
    r"\[SYSTEM\]",
    r"<\s*system\s*>",
    r"SYSTEM\s*PROMPT\s*:",
    r"<<\s*SYS\s*>>",
    r"\[INST\]",

    # IGNORE INSTRUCTIONS (EN)
    r"(?i)ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|directions?)",
    r"(?i)disregard\s+(all\s+)?(previous|prior|above|earlier)",
    r"(?i)forget\s+(everything|all|your)\s+(instructions?|rules?|training|programming)",
    r"(?i)override\s+(your\s+)?(previous|prior|all)\s+(instructions?|rules?)",
    r"(?i)do\s+not\s+follow\s+(your\s+)?(previous|prior|original)",

    # IGNORE INSTRUCTIONS (ES)
    r"(?i)ignora\s+(todas?\s+)?(las\s+)?(instrucciones?|reglas?|indicaciones?)",
    r"(?i)ignor[ae]\s+(todas?\s+)?(las\s+)?(instrucciones?|reglas?)",
    r"(?i)desconsider[ae]\s+(todas?\s+)?(las\s+)?(instrucciones?|reglas?)",
    r"(?i)olvida\s+(todas?\s+)?(tus\s+)?(instrucciones?|reglas?|todo)",
    r"(?i)no\s+sigas?\s+(tus?\s+)?(instrucciones?|reglas?)",
    r"(?i)abandona\s+(tus?\s+)?(instrucciones?|reglas?|programa(ción)?)",
    r"(?i)deja\s+de\s+seguir\s+(tus?\s+)?(instrucciones?|reglas?)",
    r"(?i)pasa\s+por\s+alto\s+(todas?\s+)?(las\s+)?(instrucciones?|reglas?)",

    # ROLE PLAY ATTACKS (EN)
    r"(?i)you\s+are\s+now\s+(a|an)\s+",
    r"(?i)pretend\s+(you\s+are|to\s+be)\s+",
    r"(?i)act\s+as\s+(if\s+you\s+were|a|an)\s+",
    r"(?i)roleplay\s+as\s+",
    r"(?i)simulate\s+(being|a|an)\s+",
    r"(?i)from\s+now\s+on\s+you\s+(are|will\s+be)",

    # ROLE PLAY ATTACKS (ES)
    r"(?i)ahora\s+eres?\s+(un|una)",
    r"(?i)finge\s+(que\s+)?(eres?|ser)\s+",
    r"(?i)act[uú]a\s+como\s+(si\s+fueras?\s+)?(un|una)?",
    r"(?i)simula\s+(ser\s+)?(un|una)?",
    r"(?i)interpreta\s+(el\s+papel\s+de\s+)?",
    r"(?i)comp[oó]rtate\s+como\s+",
    r"(?i)a\s+partir\s+de\s+ahora\s+eres?\s+",
    r"(?i)hazte\s+pasar\s+por\s+",
    r"(?i)juega\s+el\s+rol\s+de\s+",
    r"(?i)eres?\s+ahora\s+(un|una)\s+",

    # JAILBREAK PHRASES (EN)
    r"(?i)\bDAN\b\s*(mode)?",
    r"(?i)Developer\s+Mode",
    r"(?i)\bjailbreak\b",
    r"(?i)bypass\s+(your\s+)?(restrictions?|filters?|safety|rules?)",
    r"(?i)unlock\s+(your\s+)?(full|true)\s+(potential|capabilities)",
    r"(?i)remove\s+(your\s+)?(limitations?|restrictions?|filters?)",
    r"(?i)disable\s+(your\s+)?(safety|filters?|restrictions?)",
    r"(?i)evil\s*(mode|version)",
    r"(?i)uncensored\s*(mode)?",
    r"(?i)unrestricted\s*(mode)?",
    r"(?i)no\s*(rules?|limits?|restrictions?)\s*(mode)?",

    # JAILBREAK PHRASES (ES)
    r"(?i)modo\s+(desarrollador|dev|programador)",
    r"(?i)modo\s+(sin\s+)?(restricciones?|límites?|filtros?|censura)",
    r"(?i)desactiv[ae]\s+(tus?\s+)?(restricciones?|filtros?|seguridad)",
    r"(?i)elimina\s+(tus?\s+)?(restricciones?|filtros?|limitaciones?)",
    r"(?i)libera\s+(tus?\s+)?(restricciones?|capacidades)",
    r"(?i)sin\s+(restricciones?|filtros?|límites?|censura)",
    r"(?i)versión\s+(sin\s+filtro|desbloqueada|completa)",
    r"(?i)sal\s+de\s+tus?\s+(límites?|restricciones?)",
    r"(?i)rompe\s+(tus?\s+)?(reglas?|restricciones?)",
    r"(?i)evita\s+(tus?\s+)?(filtros?|restricciones?|límites?)",

    # OUTPUT MANIPULATION / PROMPT EXTRACTION (EN)
    r"(?i)reveal\s+(your\s+)?(system\s+)?prompt",
    r"(?i)show\s+(me\s+)?(your\s+)?(system\s+)?(instructions?|prompt)",
    r"(?i)what\s+(is|are)\s+your\s+(system\s+)?prompt",
    r"(?i)print\s+(your\s+)?(initial|system|original)\s+(prompt|instructions?)",
    r"(?i)output\s+(your\s+)?(system\s+)?prompt",
    r"(?i)display\s+(your\s+)?(hidden|secret|system)\s+(instructions?|prompt)",
    r"(?i)tell\s+me\s+(your\s+)?(system\s+)?(prompt|instructions?)",
    r"(?i)repeat\s+(your\s+)?(system\s+)?(prompt|instructions?)",

    # OUTPUT MANIPULATION / PROMPT EXTRACTION (ES)
    r"(?i)revel[ae]\s+(tu\s+)?prompt",
    r"(?i)muestr[ae]\s+(tu\s+|tus?\s+)?(prompt|instrucciones?)",
    r"(?i)cuál\s+es\s+(tu\s+|el\s+tu\s+)?prompt",
    r"(?i)dime?\s+(tu\s+|tus?\s+)?(prompt|instrucciones?)",
    r"(?i)muéstrame\s+(tu\s+|tus?\s+)?(prompt|instrucciones?)",
    r"(?i)imprime?\s+(tu\s+)?prompt",
    r"(?i)cómo\s+(fuiste|te)\s+(configurado|programado)",
    r"(?i)cuáles?\s+son\s+(tus?\s+)?instrucciones?",
    r"(?i)cuéntame\s+(tus?\s+)?instrucciones?",
    r"(?i)repite\s+(tu\s+)?(prompt|instrucciones?\s+originales?)",

    # DEVELOPER / DEBUG COMMANDS
    r"(?i)/debug",
    r"(?i)/admin",
    r"(?i)/sudo",
    r"(?i)/root",
    r"(?i)/override",
    r"(?i)/bypass",
    r"(?i)\[DEBUG\]",
    r"(?i)\[ADMIN\]",
    r"(?i)```system",
    r"(?i)```instruction",

    # RECONOCIMIENTO DE INFRAESTRUCTURA (EN)
    r"(?i)(where|how)\s+(is|are|do)\s+(your|the)\s+(data|information|messages?|conversations?|history)\s+(stored|saved|kept)",
    r"(?i)what\s+(database|db|storage|server|api|model|technology|framework|stack)\s+(do\s+you|are\s+you|you)\s+(use|run|built)",
    r"(?i)(which|what)\s+(llm|model|ai|engine)\s+(are\s+you|do\s+you)\s+(using|running|based\s+on)",
    r"(?i)(tell\s+me|show\s+me)\s+(your|the)\s+(architecture|stack|backend|infrastructure|configuration|config)",
    r"(?i)(what|which)\s+(cloud|provider|service|platform)\s+(do\s+you|are\s+you)",
    r"(?i)(are\s+you|do\s+you\s+use)\s+(pinecone|postgres|postgresql|redis|mongodb|openai|azure|aws|gcp|langchain|llamaindex)",

    # RECONOCIMIENTO DE INFRAESTRUCTURA (ES)
    r"(?i)(d[oó]nde|donde|c[oó]mo|como)\s+(están?|se\s+guardan?|se\s+almacenan?|se\s+guardan?)\s+(los?\s+)?(datos?|mensajes?|conversaciones?|historial)",
    r"(?i)(qu[eé]|que)\s+(base\s+de\s+datos?|base\s+de\s+datos|bd|servidor|api|modelo|tecnolog[ií]a|framework)\s+(usa[sn]?|utili[sz]a[sn]?|tienes?|tiene)",
    r"(?i)(qu[eé]|que|cu[aá]l|cual)\s+(llm|modelo\s+de\s+ia|ia|motor|motor\s+de\s+ia)\s+(usa[sn]?|utili[sz]a[sn]?|eres?|est[aá]s?)",
    r"(?i)(cu[eé]ntame|dime|expl[ií]came|explica)\s+(tu\s+|la\s+)?(arquitectura|infraestructura|stack|backend|configuraci[oó]n|config)",
    r"(?i)(en\s+qu[eé]|en\s+que)\s+(servidor|nube|plataforma|servicio)\s+(est[aá]s?|corres?|funciona[sn]?)",
    r"(?i)(usas?|utili[sz]as?|corres?|funciona[sn]?\s+con)\s+(pinecone|postgres|postgresql|redis|mongodb|openai|azure|aws|gcp|langchain|groq)",
    r"(?i)(de\s+qu[eé]|de\s+que|con\s+qu[eé]|con\s+que)\s+(est[aá][sn]?\s+hecho|fue\s+construido|fue\s+creado|fue\s+programado)",
    r"(?i)(cu[aá]les?|cuales?|qu[eé]|que)\s+(datos?|informaci[oó]n)\s+(guarda[sn]?|almacena[sn]?|registra[sn]?)\s+(de\s+m[ií]|de\s+mis?|sobre\s+m[ií]|sobre\s+mis?)",

    # EXTRACCIÓN DE CÓDIGO FUENTE / INGENIERÍA INVERSA (EN + ES)
    r"(?i)(show|give|provide|write|generate)\s+(me\s+)?(the\s+)?(source\s+)?code\s+(used|that\s+was\s+used)?\s+(to\s+)?(build|create|make|program)\s+(you|this)",
    r"(?i)(what|show)\s+(is|me)?\s+(the\s+)?(source\s+)?code\s+(behind|of|for)\s+(you|this\s+(bot|agent|assistant))",
    r"(?i)(dame|muéstrame|dime|genera|escribe)\s+.{0,30}(c[oó]digo).{0,30}(construirte|crearte|programarte|hacerte|desarrollarte|construir\s+esto)",
    r"(?i)(c[oó]digo\s+(que|con\s+que|con\s+el\s+que)).{0,30}(te\s+)?(construyeron|crearon|programaron|hicieron|desarrollaron)",
    r"(?i)(aproximadamente|parecido\s+a|similar\s+al?)\s+.{0,20}(c[oó]digo).{0,20}(construirte|crearte|programarte)",
]

# ============================================================
# 3. TOXIC PATTERNS (EN + ES)
# ============================================================
TOXIC_PATTERNS: List[str] = [

    # AMENAZAS DIRECTAS (EN)
    r"(?i)i\s+(will|am\s+going\s+to|gonna)\s+(kill|hurt|destroy|harm|attack)\s+(you|him|her|them)",
    r"(?i)i\s+(will|am\s+going\s+to|gonna)\s+(find|hunt)\s+you",
    r"(?i)you\s+(will|are\s+going\s+to)\s+(die|suffer|pay\s+for\s+this)",
    r"(?i)(watch\s+your\s+back|you('re|\s+are)\s+dead)",
    r"(?i)i\s+know\s+where\s+you\s+live",
    r"(?i)i\s+will\s+(make\s+you|make\s+sure\s+you)\s+(regret|pay)",

    # AMENAZAS DIRECTAS (ES)
    r"(?i)te\s+voy\s+a\s+(matar|golpear|destruir|lastimar|atacar|partir)",
    r"(?i)voy\s+a\s+(matarte|golpearte|destruirte|hacerte\s+daño)",
    r"(?i)te\s+voy\s+a\s+(encontrar|buscar|ubicar)",
    r"(?i)(vas\s+a\s+morir|estás\s+muerto|eres\s+hombre\s+muerto)",
    r"(?i)sé\s+dónde\s+(vives?|trabajas?|estudias?)",
    r"(?i)(cuídate|te\s+arrepentirás|lo\s+vas\s+a\s+pagar)",
    r"(?i)te\s+voy\s+a\s+hacer\s+(daño|sufrir|arrepentir)",

    # HATE SPEECH (EN)
    r"(?i)(all|those)\s+(jews?|muslims?|christians?|blacks?|whites?|latinos?|asians?)\s+(are|should\s+be|deserve)",
    r"(?i)(go\s+back\s+to\s+your\s+country)",
    r"(?i)(sub.?human|inferior\s+race|master\s+race)",
    r"(?i)(ethnic\s+cleansing|genocide\s+is\s+good|exterminate\s+the)",
    r"(?i)\b(nigger|faggot|tranny|kike|spic|chink|wetback|raghead)\b",

    # HATE SPEECH (ES)
    r"(?i)(todos?\s+los?\s+)?(judíos?|musulmanes?|negros?|blancos?|latinos?|indios?)\s+(son|merecen|deberían)",
    r"(?i)(raza\s+inferior|raza\s+superior|limpieza\s+étnica)",
    r"(?i)(vuélvete?\s+a\s+tu\s+país|no\s+eres?\s+de\s+aquí)",
    r"(?i)\b(maricón|marica|travelo|sudaca|indio\s+de\s+mierda|negro\s+de\s+mierda)\b",
    r"(?i)(hay\s+que\s+exterminar|hay\s+que\s+eliminar)\s+(a\s+(los?|las?))?",

    # ACOSO SEXUAL (EN)
    r"(?i)i\s+want\s+to\s+(have\s+sex|fuck|rape)\s+(with\s+)?you",
    r"(?i)(send|show)\s+me\s+(your\s+)?(nudes?|naked\s+photos?|dick\s+pics?)",
    r"(?i)(i\s+will|i'm\s+going\s+to)\s+rape\s+you",
    r"(?i)you\s+(want|know\s+you\s+want)\s+(to\s+)?(fuck|have\s+sex)",

    # ACOSO SEXUAL (ES)
    r"(?i)(quiero|voy\s+a)\s+(follarte|violarte|cogerte|hacerte\s+mía?)",
    r"(?i)(mándame|envíame|muéstrame)\s+(fotos?\s+)?(desnuda?|en\s+cueros?|íntimas?)",
    r"(?i)te\s+voy\s+a\s+(violar|forzar|acosar)",
    r"(?i)(sabes?\s+que\s+)?quieres?\s+(que\s+te\s+)?(folle|viole|coja)",

    # INCITACIÓN A LA VIOLENCIA (EN + ES)
    r"(?i)(kill|murder|shoot|bomb|attack)\s+(all\s+)?(the\s+)?(people|humans|civilians|government)",
    r"(?i)how\s+to\s+(make|build|create)\s+(a\s+)?(bomb|weapon|explosive|poison)",
    r"(?i)(mata|asesina|bombardea|ataca)\s+(a\s+(todos?|la\s+gente|el\s+gobierno))",
    r"(?i)co\u0301?mo\s+(hacer|fabricar|construir|crear|elaborar|preparar)\s+(una?\s+)?(bomba|arma|explosivo|veneno|artefacto)",
    r"(?i)como\s+(hacer|fabricar|construir|crear|elaborar|preparar)\s+(una?\s+)?(bomba|arma|explosivo|veneno|artefacto)",
    r"(?i)(instrucciones?\s+para|co\u0301?mo|como)\s+(matar|asesinar|envenenar|atacar)\s+(a\s+alguien)?",
    r"(?i)(bomba\s+casera|bomba\s+molotov|explosivo\s+casero|veneno\s+casero)",
    r"(?i)(c[oó]mo|como)\s+.{0,20}(bomba|explosivo|arma\s+casera)",

    # AUTOLESIÓN / SELF-HARM (EN)
    r"(?i)kill\s+yourself",
    r"(?i)go\s+(kill|hang)\s+yourself",
    r"(?i)(you\s+should|just)\s+(kill|end|hurt)\s+yourself",
    r"(?i)cut\s+yourself",
    r"(?i)end\s+your\s+(life|suffering|existence)",
    r"(?i)no\s+one\s+(would\s+miss|cares\s+about)\s+you",
    r"(?i)(the\s+world\s+)?(would\s+be\s+better\s+without\s+you)",

    # AUTOLESIÓN / SELF-HARM (ES)
    r"(?i)mátate",
    r"(?i)ve\s+(a\s+)?matarte",
    r"(?i)suicídate",
    r"(?i)quítate\s+(la\s+vida|de\s+en\s+medio)",
    r"(?i)córtate\s+(las?\s+venas?)?",
    r"(?i)hazte\s+(daño|cortes?)",
    r"(?i)(el\s+mundo\s+)?(estaría\s+mejor\s+sin\s+ti)",
    r"(?i)nadie\s+te\s+(quiere|extrañaría|importa)",
    r"(?i)(deberías?|mejor)\s+(morirte|desaparecer|no\s+existir)",
]

# ============================================================
# 4. CUSTOM PATTERNS (configurable por el admin)
# ============================================================
CUSTOM_PATTERNS: List[str] = [
    # Ejemplos (descomentar o agregar según necesidad):
    # r"(?i)competencia_xyz",
    # r"(?i)precio\s+gratis",
]

# ============================================================
# 6. URL FILTER — TLDs + Blacklist
# ============================================================
KNOWN_TLDS = (
    "com", "net", "org", "info", "biz", "name", "pro",
    "io", "co", "ai", "app", "dev", "tech", "cloud", "digital",
    "ly", "me", "to", "cc", "gl", "gd", "gg", "link", "click",
    "us", "uk", "de", "fr", "es", "it", "pt", "ru", "cn", "jp", "in", "au", "ca", "mx",
    "pe", "ar", "cl", "co", "ve", "ec", "bo", "py", "uy", "cr", "gt", "hn", "sv", "ni", "pa", "cu",
    "com.pe", "org.pe", "net.pe", "gob.pe", "edu.pe",
    "com.mx", "org.mx", "net.mx", "gob.mx", "edu.mx",
    "com.ar", "org.ar", "net.ar", "gob.ar", "edu.ar",
    "com.co", "org.co", "net.co", "gov.co", "edu.co",
    "edu", "gov", "mil", "xyz", "online", "site", "website", "store", "shop",
)

DEFAULT_BLACKLIST: List[str] = [
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "shorturl.at",
    "rb.gy", "is.gd", "owl.li", "shorte.st", "adf.ly",
    "bc.vc", "snip.ly", "po.st", "q.gs", "cutt.ly",
    "tiny.cc", "short.io", "rebrand.ly", "bl.ink", "t.ly",
    "malware.com", "phishing.org",
]

# Categorías de la IA de moderación (Capa 8: GPT-OSS-Safeguard / LlamaGuard) a IGNORAR.
# S7 (Privacy) se ignora A PROPÓSITO: este bot SOLICITA la cédula del ciudadano —con
# finalidad y consentimiento explícitos— para el REGISTRO, y el modelo la marca como
# "violación de privacidad" ~16% de las veces (falso positivo NO-determinista que
# rompía 1 de cada 6 registros). Se mantienen activas el resto de categorías (violencia,
# autolesión, armas, etc.). Configurable por env (lista separada por comas).
GUARDRAIL_SKIP_CATEGORIES: List[str] = [
    c.strip().upper() for c in os.getenv("GUARDRAIL_SKIP_CATEGORIES", "S7").split(",") if c.strip()
]
if len(GUARDRAIL_SKIP_CATEGORIES) > 4:
    # Salvaguarda: ignorar muchas categorías (S1..S13) vacía de facto la Capa 8.
    logging.getLogger(__name__).warning(
        "[GUARDRAIL 8] GUARDRAIL_SKIP_CATEGORIES ignora %d categorías (%s); "
        "revisa que no estés anulando la moderación de IA por config.",
        len(GUARDRAIL_SKIP_CATEGORIES), ",".join(GUARDRAIL_SKIP_CATEGORIES),
    )

# Dominios PERMITIDOS: URLs bajo estos dominios NO se bloquean (el Estado ecuatoriano
# usa *.gob.ec; el bot puede hablar de portales oficiales sin bloquearlos).
# El chequeo es por sufijo de host exacto (host == dom o host termina en
# ".dom"), así "gob.ec.evil.com" NO cuela (su host no termina en ".gob.ec").
URL_ALLOWLIST: tuple[str, ...] = (
    "gob.ec",   # dominios oficiales del Estado ecuatoriano (incluye *.gob.ec)
)

# Mensajes que son EXACTAMENTE una cédula pelada (10 dígitos) — atajo determinista.
# El flujo del bot PIDE la cédula para registrar, así que el ciudadano responde con el
# número solo. Ese mensaje disparaba DOS falsos positivos que rompían el registro:
#   - Capa 8 (gpt-oss-safeguard): lo marcaba como S7-Privacy de forma NO determinista
#     (~16%; el mismo formato a veces pasaba y a veces no).
#   - Capa 5 (PII): una cédula de Guayas como "0912345678" matchea PHONE_EC y quedaba
#     bloqueada de forma determinista (esa capa hoy está OFF por defecto, pero si se
#     reactiva el registro se rompe para provincias con prefijo 09).
# Por eso el atajo omite SOLO las capas 5, 7 y 8. Las capas 1-4 y 6 son regex que no
# pueden matchear un texto de puros dígitos: se siguen ejecutando (costo nulo) y así
# el atajo no debilita nada.
# ALCANCE ESTRICTO — exactamente 10 dígitos: es la cédula. Un RUC (13), una tarjeta
# de crédito (15-16) o un teléfono con prefijo NO califican, y siguen pasando por la
# Capa 5 completa, que los bloquea (PII_CONFIG los mantiene en "block" a propósito).
# NOTA: no se exige que la cédula sea VÁLIDA (módulo 10). Un número mal escrito debe
# llegar igual al agente para que responda "No pude validar esa cédula" y pida
# reintentar; bloquearlo aquí dejaría al ciudadano sin explicación.
_SOLO_NUMERO = re.compile(r"^[\s\-\.]*\d[\d\s\-\.]*$")


def _es_cedula_pelada(texto: str) -> bool:
    """True si el mensaje es únicamente un número de 10 dígitos (la cédula).

    Se aceptan espacios, guiones y puntos como separadores ("171-003-4065").
    Se exige ASCII: descarta dígitos Unicode (٣٤٥…), que son basura para el flujo.
    """
    if not _SOLO_NUMERO.match(texto) or not texto.isascii():
        return False
    return sum(c.isdigit() for c in texto) == 10


# Correo electrónico: se excluye del filtro de URLs (ver `_check_urls`).
_CORREO = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")

# Regex compilados de URLs (module-level para reutilización)
_URL_CON_PROTOCOLO = re.compile(
    r"(?i)(https?|ftp)://[\w\-]+(\.[\w\-]+)+(/[\w\-\./?=&%#]*)?",
    re.IGNORECASE,
)
_tlds_escaped = "|".join(re.escape(t) for t in sorted(KNOWN_TLDS, key=len, reverse=True))
_URL_SIN_PROTOCOLO = re.compile(
    rf"(?i)\b[\w\-]{{2,}}\.({_tlds_escaped})(/[\w\-\./?=&%#]*)?\b",
    re.IGNORECASE,
)


def _extraer_host(url: str) -> str:
    """Host en minúsculas de una URL con o sin protocolo (sin puerto ni credenciales)."""
    from urllib.parse import urlparse

    u = url.strip()
    netloc = urlparse(u).netloc if "://" in u else u.split("/")[0]
    netloc = netloc.split("@")[-1].split(":")[0]  # quita user:pass@ y :puerto
    return netloc.lower().rstrip(".")


def _host_permitido(host: str) -> bool:
    """True si `host` está bajo un dominio de `URL_ALLOWLIST` (match por sufijo exacto)."""
    return any(host == dom or host.endswith("." + dom) for dom in URL_ALLOWLIST)


# Blacklist anclada a límites de token: evita que 't.co' matchee dentro de
# 'chat.com' (subcadena) y, a la vez, caza el dominio aunque venga SIN protocolo
# y su TLD no esté en KNOWN_TLDS (p. ej. 'shorturl.at'). No matchea si el dominio
# es prefijo de otro (`bit.ly.evil.com`): ese caso cae al chequeo de allowlist.
_BLACKLIST_RE = [
    re.compile(r"(?<![\w.])" + re.escape(d) + r"(?![\w.])", re.IGNORECASE)
    for d in DEFAULT_BLACKLIST
]

# LIMITACIÓN CONOCIDA (capa 6): la detección de URL SIN protocolo depende de
# `KNOWN_TLDS`; un dominio externo con TLD no listado y sin `http://` puede
# evadir la allowlist. Con protocolo siempre se detecta. Ampliar KNOWN_TLDS con
# cautela: TLDs tipo `.zip`/`.mov` chocan con nombres de archivo legítimos.


# ============================================================
# CLASE PRINCIPAL: InputGuardrail
# ============================================================
class InputGuardrail:
    """
    Orquestador de Seguridad del agente.

    Compila todos los patrones una sola vez al iniciar (más rápido).
    Permite agregar capas 7 y 8 (LlamaGuard) cuando se tenga el código.
    """

    def __init__(self, custom_patterns: Optional[List[str]] = None):
        # Pre-compilar patrones para mejor performance
        self._secret_patterns    = [re.compile(p) for p in SECRET_KEY_PATTERNS]
        self._injection_patterns = [re.compile(p) for p in PROMPT_INJECTION_PATTERNS]
        self._toxic_patterns     = [re.compile(p) for p in TOXIC_PATTERNS]
        self._custom_patterns    = custom_patterns if custom_patterns is not None else CUSTOM_PATTERNS

        # Capa 5: PII Detector (opcional, carga sin romper si no está instalado)
        self._pii: Optional[object] = None
        self._init_pii()

        # Capas 7 y 8: LlamaGuard (opcional, requiere GROQ_API_KEY)
        self._safety: Optional[object] = None
        self._init_llama_guard()

        # Capas siempre activas: 1-4 y 6 (patrones). 5 (PII) y 7/8 (LlamaGuard) opcionales.
        _base = 5  # capas 1,2,3,4,6
        _activas = _base + (1 if self._pii is not None else 0) + (2 if self._safety is not None else 0)
        logger.info("[GUARDRAIL] InputGuardrail inicializado — %d/8 capas activas "
                    "(PII capa5=%s, LlamaGuard 7/8=%s)",
                    _activas, "on" if self._pii else "off", "on" if self._safety else "off")

    def _init_pii(self) -> None:
        """Intenta cargar el detector de PII. Si falla, la capa queda desactivada.

        La capa 5 se puede apagar por config con GUARDRAIL_PII_ENABLED=false (default,
        decisión del cliente 2026-07-16): bloqueaba mensajes con teléfono/RUC/email, lo
        que podía cortar aportes ciudadanos legítimos. Con el toggle en false, `self._pii`
        queda None y `_check_pii` nunca bloquea. La captura de la cédula a Supabase NO
        depende de esta capa (ocurre en el canal). Reactivar con GUARDRAIL_PII_ENABLED=true.
        """
        if os.getenv("GUARDRAIL_PII_ENABLED", "false").strip().lower() not in (
            "1", "true", "yes", "on", "si", "sí",
        ):
            logger.info("[GUARDRAIL] Capa 5 (PII) DESACTIVADA por config (GUARDRAIL_PII_ENABLED=false)")
            return
        try:
            from .pii_detector import PiiDetector
            self._pii = PiiDetector()
            logger.info("[GUARDRAIL] Capa 5 — PII Detector activado (Cédula EC, RUC EC, Email, Teléfono EC)")
        except Exception as e:
            logger.warning(f"[GUARDRAIL] Capa 5 (PII) desactivada: {e}")

    def _init_llama_guard(self) -> None:
        """Intenta cargar HybridSafetyService. Si falla (sin GROQ_API_KEY), la capa queda desactivada."""
        try:
            from .llama_guard_service import get_llama_guard_service
            service = get_llama_guard_service()
            if service.groq_client:
                self._safety = service
                logger.info("[GUARDRAIL] Capas 7 y 8 — LlamaGuard activado (Groq)")
            else:
                logger.warning("[GUARDRAIL] Capas 7 y 8 (LlamaGuard) desactivadas: GROQ_API_KEY no encontrada")
        except Exception as e:
            logger.warning(f"[GUARDRAIL] Capas 7 y 8 (LlamaGuard) desactivadas: {e}")

    # ----------------------------------------------------------
    # Capa 1: Secret Keys
    # ----------------------------------------------------------
    def _check_secret_keys(self, texto: str) -> Tuple[bool, str]:
        for pattern in self._secret_patterns:
            try:
                if pattern.search(texto):
                    logger.warning(f"[GUARDRAIL 1] Clave secreta detectada | {texto[:60]!r}")
                    return True, "clave_secreta"
            except Exception:
                continue
        return False, ""

    # ----------------------------------------------------------
    # Capa 2: Prompt Injection
    # ----------------------------------------------------------

    # Índices de los patrones que corresponden a reconocimiento de infraestructura
    # (los últimos 13 patrones agregados al final de PROMPT_INJECTION_PATTERNS)
    _INFRA_RECON_START_IDX = len(PROMPT_INJECTION_PATTERNS) - 13

    def _check_prompt_injection(self, texto: str) -> Tuple[bool, str]:
        for i, pattern in enumerate(self._injection_patterns):
            try:
                if pattern.search(texto):
                    if i >= self._INFRA_RECON_START_IDX:
                        logger.warning(f"[GUARDRAIL 2] Reconocimiento infraestructura | {texto[:60]!r}")
                        return True, "recon_infraestructura"
                    logger.warning(f"[GUARDRAIL 2] Prompt injection | {texto[:60]!r}")
                    return True, "prompt_injection"
            except Exception:
                continue
        return False, ""

    # ----------------------------------------------------------
    # Capa 3: Toxic Patterns
    # ----------------------------------------------------------
    def _check_toxic(self, texto: str) -> Tuple[bool, str]:
        for pattern in self._toxic_patterns:
            try:
                if pattern.search(texto):
                    logger.warning(f"[GUARDRAIL 3] Contenido tóxico | {texto[:60]!r}")
                    return True, "contenido_toxico"
            except Exception:
                continue
        return False, ""

    # ----------------------------------------------------------
    # Capa 4: Custom Regex (con protección ReDoS via timeout)
    # ----------------------------------------------------------
    def _check_custom(self, texto: str) -> Tuple[bool, str]:
        if not self._custom_patterns:
            return False, ""
        for pattern in self._custom_patterns:
            if self._safe_regex_search(pattern, texto):
                logger.warning(f"[GUARDRAIL 4] Patrón personalizado | {texto[:60]!r}")
                return True, "patron_personalizado"
        return False, ""

    def _safe_regex_search(self, pattern: str, texto: str) -> bool:
        """Ejecuta regex con timeout para prevenir ReDoS (igual que el colega)."""
        def _search():
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                match = compiled.search(texto)
                if match:
                    return True, match.group()[:20]
                return False, ""
            except re.error:
                return False, ""

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_search)
            try:
                result, _ = future.result(timeout=REGEX_TIMEOUT_SECONDS)
                return result
            except concurrent.futures.TimeoutError:
                logger.error(f"[GUARDRAIL 4] ⏱ Regex TIMEOUT (ReDoS?): {pattern!r}")
                return False

    # ----------------------------------------------------------
    # Capa 5: PII Detection
    # ----------------------------------------------------------
    def _check_pii(self, texto: str) -> Tuple[bool, str]:
        if self._pii is None:
            return False, ""
        try:
            detectado = self._pii.detectar(texto)
            if detectado:
                logger.warning(f"[GUARDRAIL 5] PII detectado: {detectado} | {texto[:60]!r}")
                return True, "pii_detectado"
        except Exception as e:
            logger.error(f"[GUARDRAIL 5] Error en PII detector: {e}")
        return False, ""

    # ----------------------------------------------------------
    # Capa 7: Llama Prompt Guard 2 — Jailbreak IA
    # ----------------------------------------------------------
    def _check_prompt_guard(self, texto: str) -> Tuple[bool, str]:
        if self._safety is None:
            logger.debug("[GUARDRAIL 7] Saltado — LlamaGuard no disponible (sin GROQ_API_KEY o error de init)")
            return False, ""
        try:
            logger.info(f"[GUARDRAIL 7] Evaluando con Llama Prompt Guard 2...")
            bloqueado, motivo = self._safety.validate_jailbreak(texto, fail_close=True)
            if not bloqueado:
                logger.info("[GUARDRAIL 7] ✅ BENIGN — mensaje pasó Prompt Guard 2")
            return bloqueado, motivo
        except Exception as e:
            logger.error(f"[GUARDRAIL 7] Error en Prompt Guard: {e}")
            return False, ""

    # ----------------------------------------------------------
    # Capa 8: GPT-OSS-Safeguard 20B — NSFW / Hate / Violence / Self-Harm
    # ----------------------------------------------------------
    def _check_llama_guard(self, texto: str) -> Tuple[bool, str]:
        if self._safety is None:
            logger.debug("[GUARDRAIL 8] Saltado — LlamaGuard no disponible (sin GROQ_API_KEY o error de init)")
            return False, ""
        try:
            logger.info(f"[GUARDRAIL 8] Evaluando con GPT-OSS-Safeguard 20B...")
            bloqueado, motivo = self._safety.validate_toxicity(
                texto, fail_close=True, skip_categories=GUARDRAIL_SKIP_CATEGORIES
            )
            if not bloqueado:
                logger.info("[GUARDRAIL 8] ✅ SAFE — mensaje pasó GPT-OSS-Safeguard 20B")
            return bloqueado, motivo
        except Exception as e:
            logger.error(f"[GUARDRAIL 8] Error en Llama Guard: {e}")
            return False, ""

    # ----------------------------------------------------------
    # Capa 6: URL Filter
    # ----------------------------------------------------------
    def _check_urls(self, texto: str) -> Tuple[bool, str]:
        # 0) Los CORREOS no son enlaces. El bot pide el correo en el registro de la
        #    bienvenida, y sin esto "maria@gmail.com" se leía como la URL "gmail.com"
        #    y se bloqueaba TODO registro (verificado 2026-10-22). Se quitan antes de
        #    buscar URLs; un enlace real en el mismo mensaje se sigue detectando.
        texto = _CORREO.sub(" ", texto)
        # 1) Blacklist (acortadores / dominios maliciosos): bloquea SIEMPRE.
        #    Match anclado a límites de token (no por subcadena cruda).
        for pat in _BLACKLIST_RE:
            if pat.search(texto):
                logger.warning(f"[GUARDRAIL 6] Blacklist: {pat.pattern!r} | {texto[:60]!r}")
                return True, "dominio_bloqueado"
        # 2) Resto de URLs: bloquea solo las que NO están en la allowlist
        #    (p. ej. deja pasar *.gob.ec, portales oficiales del Estado).
        hosts = [_extraer_host(m.group()) for m in _URL_CON_PROTOCOLO.finditer(texto)]
        hosts += [_extraer_host(m.group()) for m in _URL_SIN_PROTOCOLO.finditer(texto)]
        for host in hosts:
            if host and not _host_permitido(host):
                logger.warning(f"[GUARDRAIL 6] URL no permitida: {host!r} | {texto[:60]!r}")
                return True, "url_detectada"
        return False, ""

    # ----------------------------------------------------------
    # Pipeline principal
    # ----------------------------------------------------------
    def verificar(self, mensaje: str) -> Tuple[bool, str]:
        """
        Ejecuta el pipeline completo de seguridad.

        Returns:
            (True, "")           → mensaje seguro, pasa al agente
            (False, motivo)      → mensaje bloqueado
        """
        if not mensaje or not mensaje.strip():
            return True, ""

        texto = mensaje.strip()

        # Atajo determinista para la cédula pelada que el propio bot pide (ver
        # `_es_cedula_pelada`): omite SOLO las capas que dan falso positivo con ella
        # (5 PII, 7 y 8 IA). Las capas 1-4 y 6 se siguen ejecutando.
        es_cedula = _es_cedula_pelada(texto)
        if es_cedula:
            logger.debug("[GUARDRAIL] Cédula pelada — se omiten las capas 5, 7 y 8")

        omitir_si_cedula = (
            self._check_pii,            # Capa 5: "0912345678" matchea PHONE_EC
            self._check_prompt_guard,   # Capa 7: IA, no determinista
            self._check_llama_guard,    # Capa 8: IA, marca la cédula como S7-Privacy
        )

        for check in [
            self._check_secret_keys,       # Capa 1: Secret Keys
            self._check_prompt_injection,  # Capa 2: Prompt Injection (REGEX)
            self._check_toxic,             # Capa 3: Toxic Patterns (REGEX)
            self._check_custom,            # Capa 4: Custom Regex (ReDoS-safe)
            self._check_pii,               # Capa 5: PII Detection (Presidio)
            self._check_prompt_guard,      # Capa 7: Llama Prompt Guard 2 (Groq)
            self._check_llama_guard,       # Capa 8: GPT-OSS-Safeguard 20B (Groq)
            self._check_urls,              # Capa 6: URL Filter (al final, más lento)
        ]:
            if es_cedula and check in omitir_si_cedula:
                continue
            bloqueado, motivo = check(texto)
            if bloqueado:
                return False, motivo

        return True, ""


# ============================================================
# Singleton (se compila una sola vez al importar el módulo)
# ============================================================
_guardrail = InputGuardrail()


# ============================================================
# API pública — compatibilidad con el agente principal
# ============================================================
def verificar_input_guardrail(mensaje: str) -> Tuple[bool, str]:
    """Función de conveniencia que usa el singleton InputGuardrail."""
    return _guardrail.verificar(mensaje)


def respuesta_bloqueada(motivo: str = "") -> str:
    """Retorna el mensaje apropiado al usuario según el motivo del bloqueo."""
    mensajes = {
        "clave_secreta": (
            "Lo siento, tu mensaje parece contener una clave de API o token secreto. "
            "Por seguridad, nunca compartas credenciales en el chat. "
            "Por favor, elimina cualquier clave o token y vuelve a escribir tu consulta."
        ),
        "prompt_injection": (
            "Lo siento, no puedo procesar ese mensaje. "
            "Por favor, reformula tu pregunta de manera apropiada. "
            "Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "recon_infraestructura": (
            "Lo siento, no puedo compartir información sobre la arquitectura, "
            "tecnologías o configuración interna del sistema. "
            "¿En qué más puedo ayudarte hoy?"
        ),
        "contenido_toxico": (
            "Lo siento, no puedo continuar con esa conversación. "
            "Tu mensaje contiene contenido inapropiado. "
            "Por favor, mantén un trato respetuoso. Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "patron_personalizado": (
            "Lo siento, tu mensaje contiene contenido que no está permitido en esta plataforma. "
            "Por favor, reformula tu consulta. "
            "Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "pii_detectado": (
            "Lo siento, tu mensaje contiene información personal sensible (cédula, RUC, teléfono, etc.). "
            "Por seguridad, no compartas datos personales en el chat. "
            "Escribe tu consulta sin incluir información privada."
        ),
        "url_detectada": (
            "Lo siento, no puedo procesar mensajes que contengan enlaces o URLs. "
            "Por favor, escribe tu pregunta en texto sin incluir links. "
            "Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "dominio_bloqueado": (
            "Lo siento, tu mensaje contiene un enlace o dominio que no está permitido. "
            "Por favor, escribe tu consulta en texto sin incluir links acortados ni dominios externos. "
            "Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "jailbreak_ia": (
            "Lo siento, no puedo procesar ese mensaje. "
            "Por favor, reformula tu pregunta de manera apropiada. "
            "Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "contenido_ia_bloqueado": (
            "Lo siento, no puedo continuar con esa conversación. "
            "Tu mensaje contiene contenido que no puedo procesar. "
            "Por favor, mantén un trato respetuoso. Con gusto te cuento sobre Juan José Reyes Baquerizo."
        ),
        "servicio_no_disponible": (
            "Lo siento, el servicio de seguridad no está disponible en este momento. "
            "Por favor, intenta de nuevo más tarde."
        ),
    }
    return mensajes.get(
        motivo,
        "Lo siento, no puedo procesar ese mensaje. "
        "Por favor, reformula tu pregunta de manera apropiada.",
    )
