"""Clasificación de mensajes con GPT-4o-mini."""

import json
from datetime import datetime

import openai
from .config import OPENAI_API_KEY, PROYECTOS_VALIDOS, logger


def clasificar_mensaje(texto: str) -> dict:
    """
    Clasifica un texto usando GPT-4o-mini.
    
    Retorna:
        {
            "proyecto": str,    # Una de las opciones válidas
            "prioridad": str,   # alta | media | baja
            "resumen": str,     # Título corto (max 80 chars)
            "tipo": str,        # tarea | idea | seguimiento | referencia | nota
            "due_date": str|None  # Fecha en formato YYYY-MM-DD o null si no hay fecha
        }
    """
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    proyectos_str = ", ".join(PROYECTOS_VALIDOS)
    today_iso = datetime.now().date().isoformat()

    system_prompt = f"""Sos un asistente que clasifica mensajes para un sistema de gestión de tareas.
El usuario es Ivan, co-founder de una agencia de marketing digital (Nomadic) que también trabaja en:
- Charlas y eventos como speaker
- Marca personal (Substack, LinkedIn)
- Automatización con AI
- Adquisición de nuevos clientes
- Docencia (voluntariado, capacitaciones)
- Vida personal (salud, trámites, gym)

La fecha de hoy es {today_iso} (formato YYYY-MM-DD). Usá ESTA fecha como referencia para interpretar fechas relativas como "hoy", "mañana", "el viernes", "esta semana", "la semana que viene", etc.

Clasificá el mensaje y devolvé SOLO un JSON válido (sin markdown, sin backticks) con estos campos:

- "proyecto": uno de [{proyectos_str}]
- "prioridad": "alta" (urgente, para hoy) | "media" (esta semana) | "baja" (puede esperar)
- "resumen": título claro y accionable de máximo 80 caracteres
- "tipo": "tarea" (algo que hacer) | "idea" (para explorar) | "seguimiento" (follow-up) | "referencia" (info útil) | "nota" (recordatorio)
- "due_date": string con la fecha de vencimiento en formato YYYY-MM-DD, o null si no se menciona ninguna fecha o deadline

Reglas:
- Si mencionan un cliente o trabajo de agencia → proyecto = "Nomadic"
- Si mencionan propuestas, prospectos, ventas → proyecto = "Adquisición"  
- Si mencionan charla, presentación, evento → proyecto = "Speaker"
- Si mencionan Substack, LinkedIn, contenido propio → proyecto = "Marca personal"
- Si mencionan bots, agentes, automatizar, Claude, Cursor → proyecto = "Automatización"
- Si mencionan enseñar, Semillero, curso → proyecto = "Docencia"
- Si mencionan investigar, research, analizar empresa, diagnóstico → proyecto = "Investigar"
- Si mencionan gym, médico, trámite, casa → proyecto = "Personal"
- Si hay duda, usá "Personal"
- El resumen debe ser accionable: empezar con verbo cuando sea posible

Detección de fechas y deadlines:
- Si el mensaje menciona una fecha relativa como "hoy", "mañana", "pasado mañana", "esta semana", "el viernes", "este viernes", "la semana que viene", "el mes que viene", etc., convertí esa referencia a una fecha concreta en formato YYYY-MM-DD usando la fecha actual del sistema como referencia.
- Si el mensaje menciona una fecha absoluta como "5 de marzo", "05/03", "2026-03-05", etc., interpretala y devolvé la fecha correspondiente en formato YYYY-MM-DD.
- Si se mencionan varias fechas, elegí la más cercana en el futuro que tenga sentido como deadline.
- Si explícitamente dicen que no hay deadline o es algo muy vago ("algún día", "cuando pueda", "sin apuro"), usá due_date = null.
- Si no podés determinar una fecha clara, usá due_date = null.

Ejemplos de salida válidos:
- {{ "proyecto": "Nomadic", "prioridad": "alta", "resumen": "Preparar propuesta para cliente X", "tipo": "tarea", "due_date": "2026-03-05" }}
- {{ "proyecto": "Personal", "prioridad": "baja", "resumen": "Explorar ideas para vacaciones", "tipo": "idea", "due_date": null }}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": texto},
            ],
        )

        resultado = json.loads(response.choices[0].message.content)

        # Validar y sanitizar
        if resultado.get("proyecto") not in PROYECTOS_VALIDOS:
            logger.warning(
                f"Proyecto inválido '{resultado.get('proyecto')}', usando 'Personal'"
            )
            resultado["proyecto"] = "Personal"

        if resultado.get("prioridad") not in ("alta", "media", "baja"):
            resultado["prioridad"] = "media"

        if len(resultado.get("resumen", "")) > 80:
            resultado["resumen"] = resultado["resumen"][:77] + "..."

        logger.info(
            f"Clasificado: [{resultado['proyecto']}] [{resultado['prioridad']}] {resultado['resumen']}"
        )
        return resultado

    except Exception as e:
        logger.error(f"Error clasificando mensaje: {e}")
        # Fallback seguro
        return {
            "proyecto": "Personal",
            "prioridad": "media",
            "resumen": texto[:80] if texto else "Mensaje sin clasificar",
            "tipo": "nota",
        }
