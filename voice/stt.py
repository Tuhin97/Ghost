"""
voice/stt.py — Speech to Text using Whisper (local).

Two modes:
  1. Wake word — always listening for "Hey Ghost"
               — uses tiny model (fast, lightweight)
               — when detected, records full command
               — transcribes with medium model (accurate)

  2. Push to talk — press Enter to start recording
                  — press Enter again to stop
                  — transcribes with medium model

Both modes run 100% locally — no internet needed.

Flow:
  Wake word mode:
    tiny model listens → "hey ghost" detected
        → record command → medium model transcribes
        → send to Ghost orchestrator

  Push to talk mode:
    Enter pressed → record → Enter pressed
        → medium model transcribes → send to Ghost orchestrator
"""

import queue
import threading
import logging
from typing import Optional, Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

import config

logger = logging.getLogger(__name__)

# Sample rate for recording — standard for speech recognition
SAMPLE_RATE = 16000
INPUT_DEVICE = 12   # default — routes through Bluetooth headset via PipeWire

# How long each wake word listening chunk is in seconds
# tiny model transcribes 1 second of audio very fast
WAKE_CHUNK_SECONDS = 1

# How long to record after wake word detected
COMMAND_SECONDS = 6

# Wake word phrases to listen for
WAKE_WORDS = ["hey ghost", "ghost", "hi ghost"]

# Load models once at module level — singleton pattern
_tiny_model   = None
_medium_model = None

def _get_tiny_model():
    """
    Return the tiny faster-whisper model — load once, reuse forever.
    Used for wake word detection — needs to be fast.
    """
    global _tiny_model
    if _tiny_model is None:
        logger.info("Loading faster-whisper tiny model...")
        _tiny_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info("faster-whisper tiny model loaded.")
    return _tiny_model


def _get_small_model():
    """
    Return the small faster-whisper model — load once, reuse forever.
    Used for command transcription — good accuracy, ~5 seconds on CPU.
    """
    global _medium_model
    if _medium_model is None:
        logger.info("Loading faster-whisper small model...")
        _medium_model = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("faster-whisper small model loaded.")
    return _medium_model



def _record_audio(seconds: float) -> np.ndarray:
    """
    Record audio from the microphone for a fixed duration.

    Args:
        seconds: how long to record

    Returns:
        numpy array of audio samples (float32, mono)
    """
    logger.debug("Recording for %.1f seconds...", seconds)

    # sd.rec() records audio into a numpy array
    # channels=1 means mono — one microphone channel
    # dtype="float32" — Whisper expects float32 arrays
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=INPUT_DEVICE,
    )

    # Wait until recording is complete
    sd.wait()

    # Flatten from shape (N, 1) to (N,) — Whisper needs 1D array
    return audio.flatten()


def _transcribe(audio: np.ndarray, model) -> str:
    """
    Transcribe audio array to text using Whisper.

    Args:
        audio: numpy float32 array from _record_audio()
        model: whisper model to use (tiny or small)

    Returns:
        transcribed text — lowercase, stripped
    """

    segments, info = model.transcribe(
        audio,
        language="en",
    )

    return " ".join(s.text for s in segments).strip().lower()




def _contains_wake_word(text: str) -> bool:
    """
    Check if transcribed text contains a wake word.

    Args:
        text: lowercase transcribed text from tiny model

    Returns:
        True if wake word detected, False otherwise
    """
    for wake_word in WAKE_WORDS:
        if wake_word in text:
            return True
    return False


def listen_for_wake_word(callback: Callable[[str], None]) -> None:
    """
    Continuously listen for wake word in a background thread.

    When wake word detected:
      1. Play a confirmation beep (or log)
      2. Record the full command (COMMAND_SECONDS)
      3. Transcribe with small model
      4. Call callback with the transcribed command

    Args:
        callback: function to call with transcribed command
                  e.g. lambda text: send_to_ghost(text)

    This function blocks forever — run it in a thread.
    """
    tiny  = _get_tiny_model()
    small = _get_small_model()


    logger.info("Wake word detection started. Say 'Hey Ghost' to activate.")

    while True:
        try:
            # Record a short chunk and check for wake word
            chunk = _record_audio(WAKE_CHUNK_SECONDS)
            text  = _transcribe(chunk, tiny)

            if text:
                logger.debug("Heard: %s", text)

            if _contains_wake_word(text):
                logger.info("Wake word detected! Recording command...")

                # Record the full command
                command_audio = _record_audio(COMMAND_SECONDS)

                # Transcribe with small model for accuracy
                command_text = _transcribe(command_audio, small)

                logger.info("Command: %s", command_text)

                # Pass to callback if we got something
                if command_text.strip():
                    callback(command_text)

        except Exception as e:
            logger.error("Wake word loop error: %s", e)
            continue


def push_to_talk() -> Optional[str]:
    """
    Record audio while user holds Enter, then transcribe.

    Press Enter to start recording.
    Press Enter again to stop.

    Returns:
        transcribed text — or None if nothing recorded
    """
    small = _get_small_model()

    print("\n[Ghost] Press Enter to start speaking...")
    input()  # Wait for Enter

    print("[Ghost] Listening... Press Enter to stop.")

    # Record in a separate thread so we can stop on Enter
    audio_queue = queue.Queue()
    stop_event  = threading.Event()

    def record_thread():
        frames = []
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=INPUT_DEVICE,
        ) as stream:
            while not stop_event.is_set():
                frame, _ = stream.read(SAMPLE_RATE // 10)
                frames.append(frame)
        audio_queue.put(np.concatenate(frames).flatten())

    thread = threading.Thread(target=record_thread)
    thread.start()

    input()  # Wait for second Enter to stop
    stop_event.set()
    thread.join()

    audio = audio_queue.get()

    print("[Ghost] Transcribing...")
    text = _transcribe(audio, small)

    if text:
        print(f"[Ghost] You said: {text}")
        return text

    return None


def start_wake_word_listener(callback: Callable[[str], None]) -> threading.Thread:
    """
    Start wake word listener in a background thread.

    Returns the thread so caller can manage it.

    Args:
        callback: function to call with transcribed command

    Returns:
        running Thread object
    """
    thread = threading.Thread(
        target=listen_for_wake_word,
        args=(callback,),
        daemon=True,   # daemon=True means thread dies when main program exits
    )
    thread.start()
    logger.info("Wake word listener thread started.")
    return thread