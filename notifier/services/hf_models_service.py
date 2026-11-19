from transformers import SpeechT5Processor,SpeechT5ForTextToSpeech, SpeechT5HifiGan
from datasets import load_dataset
import torch
import soundfile as sf
from django.conf import settings
import os
import logging
import uuid

logger=logging.getLogger(__name__)
MODELS_LOADED=False
processor,model,vocoder,SPEAKER_EMBEDDINGS=None,None,None,None

def load_hf_models():
    global MODELS_LOADED, processor, model, vocoder, SPEAKER_EMBEDDINGS
    if MODELS_LOADED:
        return
    try:
        logger.info("Loading Hugging Face models...")
        processor = SpeechT5Processor.from_pretrained("microsoft/speecht5_tts")
        model = SpeechT5ForTextToSpeech.from_pretrained("microsoft/speecht5_tts")
        vocoder = SpeechT5HifiGan.from_pretrained("microsoft/speecht5_hifigan")
        
        # Load speaker embeddings
        embeddings_dataset = load_dataset("Matthijs/cmu-arctic-xvectors", split="validation")
        SPEAKER_EMBEDDINGS = torch.tensor(embeddings_dataset[7306]["xvector"]).unsqueeze(0)
        
        MODELS_LOADED = True
        logger.info("Hugging Face models loaded successfully.")
    except Exception as e:
        logger.error(f"Error loading Hugging Face models: {e}")

def text_to_speech_huggingface(text_to_speak):
    load_hf_models()
    inputs= processor(text=text_to_speak, return_tensors="pt")
    speech= model.generate_speech(inputs['input_ids'],SPEAKER_EMBEDDINGS, vocoder=vocoder)
    audio_dir = os.path.join(settings.MEDIA_ROOT, 'tts_audio')
    os.makedirs(audio_dir, exist_ok=True)
    filename = f"speech_{uuid.uuid4().hex}.wav"
    output_path = os.path.join(audio_dir, filename)
    sf.write(output_path, speech.numpy(), samplerate=16000)
    logger.info(f"Audio saved to {output_path}")
    return os.path.join('tts_audio', filename) # Relative path for URL

