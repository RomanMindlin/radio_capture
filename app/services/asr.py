"""
ASR (Automatic Speech Recognition) service using OpenAI Whisper.
Transcribes Hebrew audio files and returns structured results with timestamps.
"""
import logging
import os
import time
from datetime import timedelta
from typing import Optional

import librosa
import numpy as np
import whisper

logger = logging.getLogger(__name__)

# Global model cache
_whisper_model = None
_current_model_name = None


def _load_model(model_name: str = "tiny"):
    """
    Lazy load Whisper model.
    
    Args:
        model_name: Model size ("tiny", "small", "medium", "large")
    
    Returns:
        Loaded Whisper model
    """
    global _whisper_model, _current_model_name
    
    if _whisper_model is None or _current_model_name != model_name:
        logger.info(f"Loading Whisper model: {model_name}")
        try:
            # Set cache directory for model downloads to persistent volume
            cache_dir = os.environ.get('WHISPER_CACHE_DIR', '/data/models/whisper')
            os.makedirs(cache_dir, exist_ok=True)
            
            # Whisper uses XDG_CACHE_HOME or TORCH_HOME for model cache
            os.environ['XDG_CACHE_HOME'] = cache_dir
            
            logger.info(f"Using Whisper cache directory: {cache_dir}")
            _whisper_model = whisper.load_model(model_name, download_root=cache_dir)
            _current_model_name = model_name
            logger.info(f"Whisper model {model_name} loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise
    
    return _whisper_model


def _format_timestamp(seconds: float) -> str:
    """
    Format seconds as HH:MM:SS.mm timestamp.
    
    Args:
        seconds: Time in seconds
    
    Returns:
        Formatted timestamp string
    """
    td = timedelta(seconds=seconds)
    hours = int(td.total_seconds() // 3600)
    minutes = int((td.total_seconds() % 3600) // 60)
    secs = td.total_seconds() % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.2f}"


def transcribe(file_path: str, model: str = "tiny", language: str = "he") -> dict:
    """
    Transcribe audio file using OpenAI Whisper.
    
    Args:
        file_path: Path to audio file
        model: Whisper model size ("tiny", "small", "medium", "large")
        language: ISO 639-1 language code (e.g., "he", "en", "ar")
    
    Returns:
        {
            "transcript": str,  # Full transcription
            "segments": [       # Segment-level timestamps
                {
                    "start": "00:00:05.12",
                    "end": "00:00:12.34",
                    "speaker": null,
                    "text": "..."
                }
            ],
            "model": "whisper-small",
            "confidence": 0.87
        }
    
    Raises:
        FileNotFoundError: If audio file doesn't exist
        Exception: If transcription fails
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    start_time = time.time()
    
    try:
        # Load Whisper model
        logger.info(f"Transcribing file: {file_path} with model: {model}")
        whisper_model = _load_model(model)
        
        # Load audio file
        # Whisper expects 16kHz mono audio
        logger.info("Loading audio file...")
        audio, sr = librosa.load(file_path, sr=16000, mono=True)
        logger.info(f"Audio loaded: duration={len(audio)/sr:.2f}s, sr={sr}")
        
        # Run Whisper transcription
        # Use configured language for transcription
        logger.info(f"Running Whisper transcription with language={language}...")
        result = whisper_model.transcribe(
            audio,
            language=language,
            task="transcribe",
            verbose=False,
            fp16=False  # Disable fp16 for CPU compatibility
        )
        
        # Extract segments with timestamps
        segments = []
        confidences = []
        
        for seg in result.get("segments", []):
            segment_data = {
                "start": _format_timestamp(seg["start"]),
                "end": _format_timestamp(seg["end"]),
                "speaker": None,  # No diarization
                "text": seg["text"].strip()
            }
            segments.append(segment_data)
            
            # Collect confidence scores (if available)
            # Whisper doesn't always provide per-segment confidence,
            # but we can use the average log probability as a proxy
            if "avg_logprob" in seg:
                # Convert log probability to approximate confidence (0-1)
                # avg_logprob typically ranges from -1 to 0
                confidence = np.exp(seg["avg_logprob"])
                confidences.append(confidence)
        
        # Calculate overall confidence
        if confidences:
            overall_confidence = float(np.mean(confidences))
        else:
            # Fallback: use a default confidence if not available
            overall_confidence = 0.85
        
        # Get full transcript
        transcript = result.get("text", "").strip()
        
        processing_time = time.time() - start_time
        logger.info(f"Transcription completed in {processing_time:.2f}s")
        logger.info(f"Transcript length: {len(transcript)} characters, {len(segments)} segments")
        
        return {
            "transcript": transcript,
            "segments": segments,
            "model": f"whisper-{model}",
            "confidence": overall_confidence
        }
        
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise Exception(f"ASR failed: {str(e)}")
