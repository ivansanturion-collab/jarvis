"""Módulo de análisis de patrones usando Claude."""

import anthropic
from .config import ANTHROPIC_API_KEY, logger

def generar_analisis_patrones(query: str, asana_data: str) -> str:
    """
    Toma la pregunta explícita del usuario y el volcado de datos históricos de Asana
    para generar un análisis detallado en lenguaje natural usando Claude.
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY no configurada.")
        return "❌ Necesito una clave de Anthropic (Claude) para hacer este análisis."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = """Sos Jarvis, en tu modo especializado de ANALISTA DE PATRONES.
Tu objetivo temporal es ayudar a Ivan a entender su productividad, metodologías ágiles y análisis de datos basándote en su historial de Asana.

Ivan te va a hacer una pregunta sobre sus patrones de trabajo, rendimiento o estado actual de sus proyectos.
A continuación, se te proveerá un bloque de datos crudos extraídos de su Asana (tareas completadas en los últimos 30 días, y tareas pendientes en 'Hoy', 'Semana' y 'Backlog').

TU OBJETIVO:
1. Revisa los datos provistos y haz los cálculos necesarios (promedios, conteos, acumulaciones).
2. Responde DIRECTAMENTE a la pregunta del usuario.
3. Sé profesional, directo, analítico y claro. Usa un tono motivador y empático.
4. Formatea la respuesta con Markdown (negritas para enfatizar números o proyectos importantes, listas para desglosar información).
5. NO inventes datos. Si los datos provistos no son suficientes para responder la pregunta, sé honesto y decile qué información falta.
6. Si notas algo alarmante (ej: muchísimas tareas vencidas, un proyecto estancado en backlog), menciónalo proactivamente como una "Alerta" o "Sugerencia".

IMPORTANTE: Sos parte integral de Jarvis. No digas que "no tenés permisos" para otras cosas fuera de este análisis; simplemente enfócate en responder la consulta analítica actual.
"""

    prompt_usuario = f"""
PREGUNTA DEL USUARIO: "{query}"

DATOS EXTRAÍDOS DE ASANA:
{asana_data}

Por favor, genera tu análisis ahora.
"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            temperature=0.4,
            system=system_prompt,
            messages=[
                {"role": "user", "content": prompt_usuario}
            ]
        )
        return response.content[0].text

    except Exception as e:
        logger.error(f"Error generando análisis de patrones: {e}")
        return f"❌ Hubo un error procesando el análisis: {str(e)[:150]}"
