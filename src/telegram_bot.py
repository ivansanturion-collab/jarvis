"""Bot de Telegram — Punto de entrada de captura."""

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from .config import TELEGRAM_BOT_TOKEN, logger
from .classifier import clasificar_mensaje
from .transcriber import transcribir_audio
from .asana_client import AsanaClient

# Cliente Asana (se inicializa una vez)
asana_client: AsanaClient | None = None


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
    await update.message.reply_text(
        "🤖 Jarvis activo.\n\n"
        "Mandame texto o notas de voz y los cargo automáticamente como tareas en Asana.\n\n"
        "Comandos:\n"
        "/start — Este mensaje\n"
        "/refresh — Recargar configuración de Asana"
    )


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /refresh — recarga IDs de Asana."""
    try:
        asana_client.refresh_ids()
        await update.message.reply_text("🔄 IDs de Asana recargados correctamente.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error recargando: {str(e)[:100]}")


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


def run_bot():
    """Inicia el bot de Telegram en modo polling."""
    global asana_client

    logger.info("🚀 Inicializando Jarvis...")

    # Inicializar cliente Asana (auto-discover IDs)
    asana_client = AsanaClient()

    # Construir app de Telegram
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("hoy", cmd_hoy))
    app.add_handler(CommandHandler("semana", cmd_semana))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))

    logger.info("🤖 Jarvis escuchando en Telegram...")
    app.run_polling(allowed_updates=["message"])
