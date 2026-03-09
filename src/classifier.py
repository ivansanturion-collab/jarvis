"""Clasificación de mensajes con GPT-4o-mini."""

import json
from datetime import datetime

import anthropic
from .config import ANTHROPIC_API_KEY, PROYECTOS_VALIDOS, logger

# Tool definition para Claude
TOOL_GUARDAR_TAREA = {
    "name": "guardar_tarea_asana",
    "description": "Extrae la información estructurada de un mensaje para crear o actualizar una tarea en Asana.",
    "input_schema": {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["crear", "actualizar"],
                "description": "Si el usuario está dando una nueva tarea, usá 'crear'. Si está agregando contexto, modificando o hablando sobre una tarea anterior, usá 'actualizar'.",
            },
            "task_gid": {
                "type": ["string", "null"],
                "description": "Si la accion es 'actualizar', DEBES incluir acá el ID de la tarea a actualizar (suele estar en el historial como 'ID: 123456...'). Si la accion es 'crear', mandá null.",
            },
            "proyecto": {
                "type": "string",
                "enum": PROYECTOS_VALIDOS,
                "description": "El proyecto al que pertenece la tarea.",
            },
            "prioridad": {
                "type": "string",
                "enum": ["alta", "media", "baja"],
                "description": "La prioridad de la acción. Alta: urgente/hoy, Media: esta semana, Baja: puede esperar.",
            },
            "resumen": {
                "type": "string",
                "description": "Un título corto, claro y accionable para la tarea (máximo 80 caracteres). Comenzar con un verbo.",
            },
            "tipo": {
                "type": "string",
                "enum": ["tarea", "idea", "seguimiento", "referencia", "nota"],
                "description": "Clasificación general de la solicitud.",
            },
            "due_date": {
                "type": ["string", "null"],
                "description": "Fecha de vencimiento en formato YYYY-MM-DD, o null si no se menciona una fecha concreta. Usar la fecha del sistema para resolver textos relativos (hoy, mañana).",
            },
        },
        "required": ["accion", "task_gid", "proyecto", "prioridad", "resumen", "tipo", "due_date"],
    },
}

TOOLS_VISTA = [
    {
        "name": "ver_tareas_hoy",
        "description": "Lista las tareas pendientes de la sección Hoy. Usá esta herramienta si el usuario pregunta 'qué tengo para hoy', 'tareas de hoy', etc.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ver_tareas_semana",
        "description": "Lista las tareas pendientes de la sección Semana. Usá esta herramienta si el usuario pregunta 'qué tengo para esta semana', 'tareas de la semana', etc.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ver_backlog",
        "description": "Lista las tareas pendientes de la sección Backlog. Usá esta herramienta si el usuario pregunta 'qué tengo en el backlog', 'ideas pendientes', etc.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ver_deadlines",
        "description": "Muestra las tareas con vencimiento hoy o mañana. Usá esta herramienta si el usuario pregunta 'qué se vence', 'deadlines', 'urgencias para hoy y mañana', etc.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ver_resumen",
        "description": "Muestra el resumen de las tareas completadas y vencidas en la semana. Usá esta herramienta si el usuario pide 'resumen semanal', 'cómo me fue', 'qué completamos', etc.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

def clasificar_mensaje(historial_mensajes: list[dict]) -> dict:
    """
    Clasifica el contexto de una conversación usando Claude 3.5 Sonnet.
    
    Toma un historial de mensajes con formato de Anthropic:
    [{"role": "user"|"assistant", "content": "..."}]
    
    Retorna:
        {
            "accion": str,
            "task_gid": str|None,
            "proyecto": str,
            "prioridad": str, 
            "resumen": str,
            "tipo": str,
            "due_date": str|None
        }
    """
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY no configurada.")
        return _fallback_invalido(historial_mensajes[-1].get("content", ""))

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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

Analizá el historial de la conversación. USÁ OBLIGATORIAMENTE LA HERRAMIENTA `guardar_tarea_asana` con la información de la tarea.

IMPORTANTE SOBRE ACCIONES (CREAR vs ACTUALIZAR):
Por defecto, si el usuario manda una idea o tarea nueva, la accion es 'crear' y task_gid es null.
PERO si el usuario manda un mensaje de SEGUIMIENTO CORTO que claramente hace referencia a la tarea inmediatamente anterior (por ejemplo, cambiando la prioridad, la fecha o el nombre), DEBES emitir la accion 'actualizar' y proveer el `task_gid` de esa tarea anterior. Lo encontrarás en tu respuesta previa, formateado como "ID: 123456...".

EJEMPLOS DE ACTUALIZACIONES:
- Usuario: "Anotar la idea de hacer un newsletter semanal"
  Asistente: Tarea registrada exitosamente. ID: 120555333
  Usuario: "Ponela con prioridad alta para el viernes"
  -> Tu acción: actualizar, task_gid="120555333", prioridad="alta", due_date="[el_viernes]"
- Usuario: "Hacer deploy"
  Asistente: ID: 99999
  Usuario: "Mejor ponele de titulo Hacer deploy en Render"
  -> Tu acción: actualizar, task_gid="99999", resumen="Hacer deploy en Render"

Cuando actualices, mantén el resumen original a menos que pida explícitamente cambiarlo, y aplica SOLO los nuevos cambios al contexto que ya tenés.

Reglas de Proyecto:
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
- Si no podés determinar una fecha clara, o dicen sin apuro, usá due_date = null.
"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            temperature=0.2,
            system=system_prompt,
            messages=historial_mensajes,
            tools=[TOOL_GUARDAR_TAREA] + TOOLS_VISTA,
            tool_choice={"type": "auto"},
        )

        # Buscar el bloque de la herramienta en la respuesta
        tool_call = next(
            (block for block in response.content if block.type == "tool_use"), None
        )

        if not tool_call:
            logger.warning("Claude no devolvió el uso de la herramienta. Usando fallback.")
            return _fallback_invalido(historial_mensajes[-1].get("content", ""))

        intent = tool_call.name
        
        # Si es una tool de vista, retornamos directamente la intención
        if intent in ["ver_tareas_hoy", "ver_tareas_semana", "ver_backlog", "ver_deadlines", "ver_resumen"]:
            logger.info(f"Clasificado (Claude): Solicitud de vista -> {intent}")
            return {"intent": intent}
        
        # Si es guardar tarea
        resultado = tool_call.input
        resultado["intent"] = "guardar_tarea_asana"

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
            f"Clasificado (Claude): [{resultado['proyecto']}] [{resultado['prioridad']}] {resultado['resumen']}"
        )
        return resultado

    except Exception as e:
        logger.error(f"Error clasificando mensaje con Claude: {e}")
        return _fallback_invalido(historial_mensajes[-1].get("content", ""))

def _fallback_invalido(texto: str) -> dict:
    return {
        "intent": "guardar_tarea_asana",
        "accion": "crear",
        "proyecto": "Personal",
        "prioridad": "media",
        "resumen": texto[:80] if texto else "Mensaje sin clasificar",
        "tipo": "nota",
        "due_date": None
    }
