# Instrucciones para Claude Code — Proyecto Jarvis

## Qué es Jarvis
Sistema de captura automática de tareas personales. Recibe mensajes (texto y notas de voz) desde un bot de Telegram, los clasifica con GPT, y los carga como tareas en Asana con el proyecto y sección correctos.

## Contexto del usuario
- Ivan es co-founder de una agencia de marketing digital (Nomadic)
- Usa Asana como sistema operativo personal con un proyecto llamado "Cockpit"
- El bot solo lo usa Ivan (1 usuario), no es multi-tenant

## Asana: Proyecto Cockpit
- **Workspace GID**: `1135881163792746`
- **Project GID**: `1213411524368931`
- **URL**: https://app.asana.com/1/1135881163792746/project/1213411524368931/board/1213411716030487

### Secciones (columnas del board)
| Sección | Uso | Mapping prioridad |
|---------|-----|-------------------|
| Hoy | Tareas urgentes para hoy | prioridad = "alta" |
| Semana | Tareas para esta semana | prioridad = "media" |
| Incendios | Emergencias (solo manual) | — |
| Hecho | Completadas | — |
| Backlog | Ideas, baja prioridad | prioridad = "baja" |

### Campo personalizado "Proyecto" (selección única)
Las tareas se clasifican en uno de estos proyectos:
| Valor | Descripción |
|-------|-------------|
| Speaker | Charlas, presentaciones, eventos |
| Automatización | Agentes AI, bots, scripts, Claude Code |
| Marca personal | Substack, LinkedIn, contenido propio |
| Nomadic | Agencia: clientes, operaciones, equipo |
| Adquisición | Prospección, propuestas comerciales, nuevos clientes |
| Docencia | Semillero Digital, capacitaciones, cursos |
| Personal | Salud, trámites, gym, vida personal |

> **IMPORTANTE**: Los GIDs de secciones y opciones del campo personalizado se descubren automáticamente via API en el primer run. El script `src/asana_client.py` tiene una función `discover_asana_ids()` que mapea nombres a GIDs. No hardcodear GIDs.

## Stack técnico
- Python 3.11+
- `python-telegram-bot` >= 20.0 (async)
- `openai` >= 1.0.0 (Whisper + GPT-4o-mini)
- `asana` >= 5.0.0
- `python-dotenv`

## Reglas de código
1. **Credenciales**: Todo en `.env`, NUNCA hardcodeado
2. **Modularidad**: Cada archivo en `src/` funciona independientemente
3. **Deduplicación**: Antes de crear tarea, verificar en `data/procesados.json`
4. **Clasificación**: Usar `gpt-4o-mini` con `temperature=0.3`
5. **Whisper**: Siempre con `language="es"`
6. **Errores**: Toda llamada a API externa con try/except y logging claro
7. **Logging**: A stdout, no a archivos
8. **Telegram**: Usar polling, no webhooks

## Flujo principal
```
Telegram mensaje → ¿es voz? → Whisper transcribe → GPT clasifica → Asana crea tarea → Telegram confirma
                   ¿es texto? ────────────────────→ GPT clasifica → Asana crea tarea → Telegram confirma
```

## Clasificación GPT
GPT debe devolver:
```json
{
  "proyecto": "Speaker|Automatización|Marca personal|Nomadic|Adquisición|Docencia|Personal",
  "prioridad": "alta|media|baja",
  "resumen": "Título corto de máximo 80 caracteres",
  "tipo": "tarea|idea|seguimiento|referencia|nota"
}
```

## Confirmación en Telegram
El bot responde al usuario con un resumen de la clasificación:
```
✅ Capturado en Asana
📁 Proyecto: Marca personal
🔥 Prioridad: alta → Hoy
📝 "Escribir post sobre SEO técnico para Substack"
```

## Al hacer cambios
- Verificar que dependencias nuevas estén en `requirements.txt`
- No romper el flujo de deduplicación
- Testear con un mensaje de texto simple antes de probar voz
- Si se agrega una nueva opción de "Proyecto" en Asana, correr `discover_asana_ids()` de nuevo
