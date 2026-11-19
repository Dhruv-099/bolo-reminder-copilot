"""Sarvam AI service layer for the Bolo reminder copilot.

Three pieces, all failure-safe:

- translate_message():    colloquial translation of reminder text (mayura:v1)
- generate_voice_audio(): Bulbul text-to-speech; saves a WAV under
                          MEDIA_ROOT/reminders and returns its path
- _post_with_retries():   bounded exponential backoff around every API call

An in-process cache dedupes identical translations and voice generations, so a
preview followed by a dispatch (or a re-preview) does not pay the API twice.
The cache lives for the process lifetime only - fine for a dispatch worker.

A missing SARVAM_API_KEY or a failed API call logs the error and returns
None, so the dispatch workflow can report a per-event failure instead of
crashing. Secrets live only in the environment (.env / process env), never
in code.
"""

import base64
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# BCP-47 codes understood by both the Translate and Bulbul TTS APIs.
SUPPORTED_LANGUAGES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

DEFAULT_TRANSLATE_MODEL = "mayura:v1"

TRANSLATE_CACHE_MAX = 512
TTS_CACHE_MAX = 256
_translate_cache = OrderedDict()
_tts_cache = OrderedDict()


def clear_service_caches():
    """Empty the in-process translation/TTS caches (used by tests)."""
    _translate_cache.clear()
    _tts_cache.clear()


def _headers():
    """Auth headers for Sarvam, or None when SARVAM_API_KEY is not set."""
    key = os.getenv("SARVAM_API_KEY", "")
    if not key:
        logger.error("SARVAM_API_KEY is not set - Sarvam calls will fail.")
        return None
    return {"api-subscription-key": key, "Content-Type": "application/json"}


def _post_with_retries(url, payload, headers, timeout, attempts=3, base_delay=1.0):
    """POST with bounded exponential backoff. Returns parsed JSON or None.

    Never raises: the last failure is logged and None is returned so callers
    can degrade per-event instead of crashing a batch.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            if attempt == attempts:
                logger.error("Sarvam call to %s failed after %d attempts: %s", url, attempts, exc)
                return None
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Sarvam call to %s failed (%s); retry %d/%d in %.0fs.",
                url, exc, attempt, attempts, delay,
            )
            time.sleep(delay)
        except ValueError as exc:
            logger.error("Sarvam returned invalid JSON from %s: %s", url, exc)
            return None
    return None


def translate_message(text, target_language_code, source_language_code="en-IN", mode="modern-colloquial"):
    """Translate text into target_language_code.

    Uses mayura:v1 in a colloquial mode by default, so reminders read the way
    people actually speak. English passes through unchanged. Returns the
    translated string, or None on any failure. Identical requests are served
    from the in-process cache.
    """
    if target_language_code == "en-IN":
        return text
    if target_language_code not in SUPPORTED_LANGUAGES:
        logger.error("Unsupported target language code: %s", target_language_code)
        return None

    headers = _headers()
    if headers is None:
        return None

    cache_key = (text[:1000], target_language_code, mode)  # mayura:v1 request limit
    cached = _translate_cache.get(cache_key)
    if cached is not None:
        return cached

    payload = {
        "input": text[:1000],
        "source_language_code": source_language_code,
        "target_language_code": target_language_code,
        "mode": mode,
        "model": DEFAULT_TRANSLATE_MODEL,
    }
    data = _post_with_retries(SARVAM_TRANSLATE_URL, payload, headers, timeout=15)
    if data is None:
        return None

    translated = (data.get("translated_text") or "").strip()
    if not translated:
        logger.error("Sarvam translate returned no text for %s.", target_language_code)
        return None

    _translate_cache[cache_key] = translated
    if len(_translate_cache) > TRANSLATE_CACHE_MAX:
        _translate_cache.popitem(last=False)
    return translated


def default_tts_model():
    """Bulbul model to use (env override: SARVAM_TTS_MODEL)."""
    return os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")


# Persona voices for bulbul:v4-flash, chosen per language for a warm
# customer-reminder tone. Malayalam and Odia have no v4-flash voices yet;
# generate_voice_audio() falls those back to bulbul:v3.
V4_SPEAKERS = {
    "en-IN": "neha_en_customer",
    "hi-IN": "aparna_hi_customer",
    "te-IN": "kavitha_te_conversation",
    "ta-IN": "gokul_ta_narration",
    "mr-IN": "ishita_mr_conversational",
    "kn-IN": "chaitra_kn_conversation",
    "bn-IN": "roopa_bn_conversational",
    "gu-IN": "bhavik_gu_conversation",
    "pa-IN": "anand_pa_customer",
}


def default_tts_speaker(model=None, language_code="en-IN"):
    """Speaker voice for the given model and language (env override:
    SARVAM_TTS_SPEAKER).

    v3 uses language-agnostic short names (shubh); v4-flash uses
    per-language persona ids (see V4_SPEAKERS).
    """
    override = os.getenv("SARVAM_TTS_SPEAKER")
    if override:
        return override
    if (model or default_tts_model()).startswith("bulbul:v4"):
        return V4_SPEAKERS.get(language_code, "shubh_enhi_ads")
    return "shubh"  # v3 default


def generate_voice_audio(text, language_code, speaker=None, model=None, event_id=None):
    """Generate spoken audio for text with Sarvam Bulbul TTS.

    Writes a WAV into MEDIA_ROOT/reminders and returns the path relative to
    MEDIA_ROOT, or None on any failure. Identical requests are served from
    the in-process cache, so a preview followed by a dispatch reuses the same
    audio file instead of paying the API twice.
    """
    if language_code not in SUPPORTED_LANGUAGES:
        logger.error("Unsupported TTS language code: %s", language_code)
        return None

    headers = _headers()
    if headers is None:
        return None

    model = model or default_tts_model()
    if model.startswith("bulbul:v4") and language_code not in V4_SPEAKERS:
        logger.warning(
            "bulbul:v4-flash has no %s voices yet; falling back to bulbul:v3.",
            language_code,
        )
        model = "bulbul:v3"
    speaker = speaker or default_tts_speaker(model, language_code)

    cache_key = (text[:2500], language_code, model, speaker)  # Bulbul request limit
    cached = _tts_cache.get(cache_key)
    if cached is not None:
        return cached

    payload = {
        "text": text[:2500],
        "language_code": language_code,
        "model": model,
        "speaker": speaker,
        "pace": 1.0,
        "speech_sample_rate": 22050,
        "output_audio_codec": "wav",
    }
    data = _post_with_retries(SARVAM_TTS_URL, payload, headers, timeout=60)
    if data is None:
        return None

    audios = data.get("audios") or []
    if not audios:
        logger.error("Sarvam TTS returned no audio for %s.", language_code)
        return None
    try:
        audio_bytes = base64.b64decode(audios[0])
    except (ValueError, TypeError) as exc:
        logger.error("Could not decode Sarvam TTS audio: %s", exc)
        return None

    audio_dir = Path(settings.MEDIA_ROOT) / "reminders"
    audio_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"event_{event_id}_" if event_id is not None else ""
    file_name = f"{prefix}{language_code}_{stamp}.wav"
    (audio_dir / file_name).write_bytes(audio_bytes)

    relative_path = f"reminders/{file_name}"
    logger.info("Generated voice audio: %s (%d bytes)", relative_path, len(audio_bytes))

    _tts_cache[cache_key] = relative_path
    if len(_tts_cache) > TTS_CACHE_MAX:
        _tts_cache.popitem(last=False)
    return relative_path


def audio_absolute_path(relative_path):
    """Absolute filesystem path for an audio path returned above (for playback)."""
    return str(Path(settings.MEDIA_ROOT) / relative_path)
