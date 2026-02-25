"""Transcripción de audio con OpenAI Whisper."""

import os
import tempfile
import openai
from .config import OPENAI_API_KEY, logger


def transcribir_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """
    Transcribe audio usando OpenAI Whisper API.
    
    Args:
        audio_bytes: Contenido del archivo de audio en bytes
        filename: Nombre del archivo (para detectar extensión)
    
    Returns:
        Texto transcrito
    """
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    extension = filename.split(".")[-1] if "." in filename else "ogg"

    with tempfile.NamedTemporaryFile(suffix=f".{extension}", delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name

    try:
        with open(temp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="es",  # Forzar español para mejor accuracy
            )

        texto = transcription.text.strip()
        logger.info(f"Audio transcrito ({len(audio_bytes)} bytes): {texto[:100]}...")
        return texto

    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        raise

    finally:
        os.unlink(temp_path)
