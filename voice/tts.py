"""
voice/tts.py — Text to Speech using Piper TTS (local).

Converts Ghost's text responses to audio and plays them
through the speaker. Runs 100% locally — no internet needed.

Voice model: en_US-lessac-medium
  - Downloaded to voice/models/
  - Good quality neural voice
  - Fast generation on CPU
"""

import io
import logging
import wave

import numpy as np
import sounddevice as sd
from piper import PiperVoice

import config

logger = logging.getLogger(__name__)

# Load the voice model once at module level — singleton pattern
# Loading takes ~1 second so we do it once, reuse forever
_voice: PiperVoice = None


def _get_voice() -> PiperVoice:
    """
    Return the loaded Piper voice — load it once, reuse forever.

    Same singleton pattern as get_memory() in memory.py.
    Avoids reloading the model on every speak() call.

    Returns:
        loaded PiperVoice instance
    """
    global _voice
    if _voice is None:
        logger.info("Loading Piper voice model from %s", config.PIPER_MODEL_PATH)
        _voice = PiperVoice.load(config.PIPER_MODEL_PATH)
        logger.info("Piper voice model loaded.")
    return _voice


def speak(text: str) -> None:
    """
    Convert text to speech and play it through the speaker.

    Synthesizes audio chunks from Piper, collects them into
    a numpy array, then plays through sounddevice.

    Args:
        text: the text to speak — Ghost's response
    """
    if not text or not text.strip():
        return

    try:
        voice = _get_voice()

        # Collect all audio chunks into one buffer
        audio_chunks = []
        for chunk in voice.synthesize(text):
            audio_chunks.append(chunk.audio_int16_bytes)

        if not audio_chunks:
            logger.warning("Piper returned no audio chunks for text: %s", text[:50])
            return

        # Join all chunks into one bytes object
        audio_bytes = b"".join(audio_chunks)

        # Convert bytes to numpy int16 array for sounddevice
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)

        # Play audio — blocking=True means wait until finished
        # before returning. Ghost won't respond again until
        # the current response has been fully spoken.
        sd.play(audio_array, samplerate=voice.config.sample_rate)
        sd.wait()

        logger.info("Spoke: %s", text[:50])

    except Exception as e:
        logger.error("TTS failed: %s", e)


def speak_async(text: str) -> None:
    """
    Speak without blocking — fire and forget.

    Used when Ghost needs to keep processing while speaking.
    Currently just calls speak() but can be extended with
    threading if needed.

    Args:
        text: the text to speak
    """
    speak(text)
