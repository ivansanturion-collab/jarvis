"""Configuración centralizada desde variables de entorno."""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env
load_dotenv()

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Asana
ASANA_ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
ASANA_WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID", "1135881163792746")
ASANA_PROJECT_GID = os.getenv("ASANA_PROJECT_GID", "1213411524368931")

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PROCESADOS_FILE = DATA_DIR / "procesados.json"
ASANA_IDS_FILE = DATA_DIR / "asana_ids.json"

# Mapeo prioridad → sección
PRIORIDAD_SECCION_MAP = {
    "alta": "Hoy",
    "media": "Semana",
    "baja": "Backlog",
}

# Valores válidos del campo "Proyecto"
PROYECTOS_VALIDOS = [
    "Speaker",
    "Automatización",
    "Marca personal",
    "Nomadic",
    "Adquisición",
    "Docencia",
    "Personal",
]


def validate_config():
    """Verifica que todas las variables requeridas estén presentes."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ASANA_ACCESS_TOKEN": ASANA_ACCESS_TOKEN,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"Faltan variables de entorno: {', '.join(missing)}")
    logger.info("✅ Configuración validada correctamente")
