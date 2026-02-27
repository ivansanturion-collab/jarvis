"""Bot de Telegram — Punto de entrada de captura."""

import json
from datetime import date, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from .config import TELEGRAM_BOT_TOKEN, CHAT_ID_FILE, logger
from .classifier import clasificar_mensaje
from .transcriber import transcribir_audio
from .asana_client import AsanaClient

# Cliente Asana (se inicializa una vez)
asana_client: AsanaClient | None = None

# Estados para /done
DONE_WAITING_SELECTION, DONE_WAITING_CONFIRMATION = range(2)


def _ensure_chat_id_persisted(update: Update):
    """Guarda el chat_id en data/chat_id.json si aún no existe."""
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    try:
        if CHAT_ID_FILE.exists():
            # Si ya existe y es el mismo, no hacemos nada
            data = json.loads(CHAT_ID_FILE.read_text(encoding="utf-8"))
            if data.get("chat_id") == chat_id:
                return

        CHAT_ID_FILE.write_text(
            json.dumps({"chat_id": chat_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"💾 chat_id guardado/actualizado en {CHAT_ID_FILE}")
    except Exception as e:
        logger.error(f"❌ No se pudo guardar chat_id: {e}")


def _formatear_rango_fechas(desde: date, hasta: date) -> str:
    """Devuelve un string tipo 'lunes 24/2 → viernes 28/2'."""
    dias = [
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo",
    ]

    def fmt(d: date) -> str:
        return f"{dias[d.weekday()]} {d.day}/{d.month}"

    return f"{fmt(desde)} → {fmt(hasta)}"


def _formatear_resumen_semanal() -> str:
    """Construye el texto del resumen semanal a partir de Asana."""
    global asana_client
    if asana_client is None:
        raise RuntimeError("AsanaClient no inicializado")

    resumen = asana_client.obtener_resumen_semanal()
    desde: date = resumen["desde"]
    hasta: date = resumen["hasta"]
    completadas = resumen["completadas"]
    vencidas = resumen["vencidas"]
    por_proyecto = resumen["por_proyecto"]

    lineas: list[str] = []
    lineas.append(f"📊 Resumen semanal ({_formatear_rango_fechas(desde, hasta)})")

    # Completadas
    lineas.append(f"\n✅ Completadas ({len(completadas)})")
    if completadas:
        for t in completadas:
            lineas.append(f"• {t['proyecto']} — {t['name']}")
    else:
        lineas.append("• (ninguna)")

    # Vencidas / atrasadas
    lineas.append(f"\n⚠️ Vencidas / atrasadas ({len(vencidas)})")
    if vencidas:
        for t in vencidas:
            d = t["due_on"]
            lineas.append(
                f"• {t['proyecto']} — {t['name']} (venció {d.day}/{d.month})"
            )
    else:
        lineas.append("• (ninguna)")

    # Por proyecto
    if por_proyecto:
        partes = [f"{proj} ({count})" for proj, count in sorted(por_proyecto.items())]
        lineas.append(f"\n📁 Por proyecto: " + ", ".join(partes))
    else:
        lineas.append("\n📁 Por proyecto: (sin tareas completadas)")

    return "\n".join(lineas)

def _formatear_confirmacion(clasificacion: dict) -> str:
    """Formatea el mensaje de confirmación para Telegram."""
    emoji_prioridad = {"alta": "🔥", "media": "📌", "baja": "💤"}.get(
        clasificacion.get("prioridad"), "📌"
    )
    seccion = {"alta": "Hoy", "media": "Semana", "baja": "Backlog"}.get(
        clasificacion.get("prioridad"), "Semana"
    )
    emoji_tipo = {
        "tarea": "✅",
        "idea": "💡",
        "seguimiento": "🔄",
        "referencia": "📎",
        "nota": "📝",
    }.get(clasificacion.get("tipo"), "📝")

    return (
        f"✅ Capturado en Asana\n"
        f"📁 Proyecto: {clasificacion.get('proyecto', 'Personal')}\n"
        f"{emoji_prioridad} Prioridad: {clasificacion.get('prioridad', 'media')} → {seccion}\n"
        f"{emoji_tipo} \"{clasificacion.get('resumen', '')}\""
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa mensajes de texto."""
    _ensure_chat_id_persisted(update)
    texto = update.message.text
    message_id = str(update.message.message_id)

    logger.info(f"📨 Texto recibido: {texto[:100]}...")

    try:
        # Clasificar
        clasificacion = clasificar_mensaje(texto)

        # Crear tarea en Asana
        task = asana_client.crear_tarea(
            texto=texto,
            clasificacion=clasificacion,
            message_id=message_id,
            fuente="telegram",
        )

        if task:
            respuesta = _formatear_confirmacion(clasificacion)
        else:
            respuesta = "⏭️ Este mensaje ya fue procesado anteriormente."

        await update.message.reply_text(respuesta)

    except Exception as e:
        logger.error(f"Error procesando texto: {e}")
        await update.message.reply_text(f"❌ Error procesando mensaje: {str(e)[:100]}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa notas de voz."""
    _ensure_chat_id_persisted(update)
    message_id = str(update.message.message_id)

    logger.info(f"🎤 Nota de voz recibida (message_id: {message_id})")

    try:
        # Descargar audio
        voice = await update.message.voice.get_file()
        audio_bytes = await voice.download_as_bytearray()

        # Notificar que estamos procesando
        processing_msg = await update.message.reply_text("🎤 Transcribiendo audio...")

        # Transcribir
        texto = transcribir_audio(bytes(audio_bytes))

        # Clasificar
        clasificacion = clasificar_mensaje(texto)

        # Crear tarea
        task = asana_client.crear_tarea(
            texto=texto,
            clasificacion=clasificacion,
            message_id=message_id,
            fuente="telegram_voz",
        )

        if task:
            respuesta = (
                f"🎤 Transcripción:\n\"{texto}\"\n\n"
                f"{_formatear_confirmacion(clasificacion)}"
            )
        else:
            respuesta = "⏭️ Este audio ya fue procesado anteriormente."

        # Editar mensaje de "procesando" con el resultado
        await processing_msg.edit_text(respuesta)

    except Exception as e:
        logger.error(f"Error procesando voz: {e}")
        await update.message.reply_text(f"❌ Error procesando audio: {str(e)[:100]}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa archivos de audio adjuntos."""
    _ensure_chat_id_persisted(update)
    message_id = str(update.message.message_id)

    logger.info(f"🎵 Audio recibido (message_id: {message_id})")

    try:
        audio = await update.message.audio.get_file()
        audio_bytes = await audio.download_as_bytearray()
        filename = update.message.audio.file_name or "audio.ogg"

        processing_msg = await update.message.reply_text("🎵 Transcribiendo audio...")

        texto = transcribir_audio(bytes(audio_bytes), filename)
        clasificacion = clasificar_mensaje(texto)

        task = asana_client.crear_tarea(
            texto=texto,
            clasificacion=clasificacion,
            message_id=message_id,
            fuente="telegram_audio",
        )

        if task:
            respuesta = (
                f"🎵 Transcripción:\n\"{texto}\"\n\n"
                f"{_formatear_confirmacion(clasificacion)}"
            )
        else:
            respuesta = "⏭️ Este audio ya fue procesado anteriormente."

        await processing_msg.edit_text(respuesta)

    except Exception as e:
        logger.error(f"Error procesando audio: {e}")
        await update.message.reply_text(f"❌ Error procesando audio: {str(e)[:100]}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start."""
    _ensure_chat_id_persisted(update)
    await update.message.reply_text(
        "🤖 Jarvis activo.\n\n"
        "Mandame texto o notas de voz y los cargo automáticamente como tareas en Asana.\n\n"
        "Comandos:\n"
        "/start — Este mensaje\n"
        "/refresh — Recargar configuración de Asana\n"
        "/hoy — Tareas para hoy\n"
        "/semana — Tareas para esta semana\n"
        "/done — Marcar tareas como realizadas\n"
        "/resumen — Resumen semanal (últimos 7 días)"
    )


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /refresh — recarga IDs de Asana."""
    try:
        asana_client.refresh_ids()
        await update.message.reply_text("🔄 IDs de Asana recargados correctamente.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error recargando: {str(e)[:100]}")


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resumen — envía resumen semanal."""
    _ensure_chat_id_persisted(update)
    try:
        texto = _formatear_resumen_semanal()
        await update.message.reply_text(texto)
    except Exception as e:
        logger.error(f"Error generando resumen semanal: {e}")
        await update.message.reply_text(
            f"❌ Error generando resumen semanal: {str(e)[:150]}"
        )


async def _cmd_listar_seccion(update: Update, nombre_seccion: str, titulo: str):
    """Helper para /hoy y /semana."""
    try:
        tareas = asana_client.listar_tareas_seccion(nombre_seccion)

        if not tareas:
            if nombre_seccion == "Hoy":
                await update.message.reply_text("🎉 No tenés tareas pendientes para hoy")
            elif nombre_seccion == "Semana":
                await update.message.reply_text("🎉 No tenés tareas pendientes para esta semana")
            else:
                await update.message.reply_text("🎉 No tenés tareas pendientes")
            return

        lineas = [f"{titulo} ({len(tareas)})"]
        for t in tareas:
            lineas.append(
                f"{t['emoji_prioridad']} {t['proyecto']} — {t['name']}"
            )

        await update.message.reply_text("\n".join(lineas))

    except Exception as e:
        logger.error(f"Error listando tareas de sección {nombre_seccion}: {e}")
        await update.message.reply_text(f"❌ Error consultando tareas: {str(e)[:100]}")


async def cmd_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /hoy — lista tareas de la sección Hoy."""
    await _cmd_listar_seccion(update, "Hoy", "📋 Tareas para hoy")


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /semana — lista tareas de la sección Semana."""
    await _cmd_listar_seccion(update, "Semana", "📋 Tareas para esta semana")


async def cmd_done_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entrada al flujo /done."""
    texto_args = " ".join(context.args).strip() if getattr(context, "args", None) else ""

    # Construir lista de tareas de Hoy, Semana y Backlog
    tareas = []
    for seccion in ("Hoy", "Semana", "Backlog"):
        tareas.extend(asana_client.listar_tareas_seccion(seccion))

    if not tareas:
        await update.message.reply_text("🎉 No tenés tareas pendientes")
        return ConversationHandler.END

    context.user_data["done_tasks"] = tareas

    # Modo búsqueda por texto
    if texto_args:
        query = texto_args.lower()
        mejor = None
        mejor_score = 0.0

        for t in tareas:
            name_l = t["name"].lower()
            score = 0.0
            if query in name_l:
                # Puntaje simple: proporción de match
                score = len(query) / max(len(name_l), 1)
            if score > mejor_score:
                mejor_score = score
                mejor = t

        if not mejor or mejor_score == 0:
            await update.message.reply_text("❌ No encontré ninguna tarea que matchee ese texto.")
            return ConversationHandler.END

        context.user_data["done_selected_task"] = mejor
        await update.message.reply_text(
            f"¿Confirmás completar: {mejor['name']}? (Sí/No)"
        )
        return DONE_WAITING_CONFIRMATION

    # Modo listado numerado
    lineas = ["📋 ¿Cuál completaste?", ""]
    for idx, t in enumerate(tareas, start=1):
        lineas.append(
            f"{idx}. {t['emoji_prioridad']} {t['seccion']} — {t['name']}"
        )

    await update.message.reply_text("\n".join(lineas))
    return DONE_WAITING_SELECTION


async def done_receive_index(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el número de tarea a completar."""
    tareas = context.user_data.get("done_tasks") or []
    mensaje = (update.message.text or "").strip()

    if not mensaje.isdigit():
        await update.message.reply_text(
            "Decime un número válido (por ejemplo, 1) o /cancel para salir."
        )
        return DONE_WAITING_SELECTION

    idx = int(mensaje)
    if idx < 1 or idx > len(tareas):
        await update.message.reply_text(
            f"El número debe estar entre 1 y {len(tareas)}. Probá de nuevo."
        )
        return DONE_WAITING_SELECTION

    seleccionada = tareas[idx - 1]
    context.user_data["done_selected_task"] = seleccionada

    await update.message.reply_text(
        f"¿Confirmás completar: {seleccionada['name']}? (Sí/No)"
    )
    return DONE_WAITING_CONFIRMATION


async def done_receive_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma o cancela la finalización de la tarea."""
    texto = (update.message.text or "").strip().lower()
    seleccionada = context.user_data.get("done_selected_task")

    if not seleccionada:
        await update.message.reply_text("No hay ninguna tarea seleccionada.")
        return ConversationHandler.END

    positivos = {"sí", "si", "s", "yes", "y"}
    negativos = {"no", "n"}

    if texto in positivos:
        try:
            asana_client.completar_tarea(seleccionada["gid"])
            await update.message.reply_text(f"✅ Completada: {seleccionada['name']}")
        except Exception as e:
            logger.error(f"Error completando tarea {seleccionada['gid']}: {e}")
            await update.message.reply_text(
                f"❌ Error completando la tarea: {str(e)[:100]}"
            )

        context.user_data.pop("done_tasks", None)
        context.user_data.pop("done_selected_task", None)
        return ConversationHandler.END

    if texto in negativos:
        await update.message.reply_text("❌ Cancelado")
        context.user_data.pop("done_tasks", None)
        context.user_data.pop("done_selected_task", None)
        return ConversationHandler.END

    await update.message.reply_text('Respondé "Sí" o "No", por favor.')
    return DONE_WAITING_CONFIRMATION


async def cmd_done_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela el flujo /done."""
    context.user_data.pop("done_tasks", None)
    context.user_data.pop("done_selected_task", None)
    await update.message.reply_text("❌ Cancelado")
    return ConversationHandler.END


async def _enviar_resumen_programado(context: ContextTypes.DEFAULT_TYPE):
    """Job de JobQueue: envía el resumen semanal al chat configurado."""
    try:
        if not CHAT_ID_FILE.exists():
            logger.info("ℹ️ No hay chat_id configurado, no se envía resumen semanal.")
            return

        data = json.loads(CHAT_ID_FILE.read_text(encoding="utf-8"))
        chat_id = data.get("chat_id")
        if not chat_id:
            logger.warning("⚠️ chat_id.json no contiene 'chat_id'")
            return

        texto = _formatear_resumen_semanal()
        await context.bot.send_message(chat_id=chat_id, text=texto)
        logger.info("✅ Resumen semanal enviado automáticamente por JobQueue")
    except Exception as e:
        logger.error(f"❌ Error enviando resumen semanal automático: {e}")


def run_bot():
    """Inicia el bot de Telegram en modo polling."""
    global asana_client

    logger.info("🚀 Inicializando Jarvis...")

    # Inicializar cliente Asana (auto-discover IDs)
    asana_client = AsanaClient()

    # Callback post_init para registrar jobs en el JobQueue
    async def _post_init(app: Application):
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        # Viernes (4) a las 18:00 hora Argentina
        app.job_queue.run_daily(
            _enviar_resumen_programado,
            time(hour=18, minute=0, tzinfo=tz),
            days=(4,),
            name="resumen_semanal_telegram",
        )

    # Construir app de Telegram
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("done", cmd_done_entry)],
            states={
                DONE_WAITING_SELECTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, done_receive_index)
                ],
                DONE_WAITING_CONFIRMATION: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, done_receive_confirmation
                    )
                ],
            },
            fallbacks=[CommandHandler("cancel", cmd_done_cancel)],
        )
    )
    app.add_handler(CommandHandler("hoy", cmd_hoy))
    app.add_handler(CommandHandler("semana", cmd_semana))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))

    logger.info("🤖 Jarvis escuchando en Telegram...")
    app.run_polling(allowed_updates=["message"])
