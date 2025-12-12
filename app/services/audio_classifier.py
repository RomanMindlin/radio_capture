"""
Audio classification service using PANNs CNN14 model.
Classifies audio files as speech, music, or advertisement.
"""
import logging
import os
from typing import Optional

import librosa
import numpy as np

logger = logging.getLogger(__name__)

# Lazy load the model to avoid loading it at import time
_model = None
_labels = None


def _get_model():
    """Lazy load the PANNs model."""
    global _model, _labels
    
    if _model is None:
        try:
            # Set PyTorch to use single thread to avoid conflicts
            import torch
            torch.set_num_threads(1)
            os.environ['OMP_NUM_THREADS'] = '1'
            os.environ['MKL_NUM_THREADS'] = '1'
            
            from panns_inference import AudioTagging
            
            logger.info("Loading PANNs CNN14 model...")
            _model = AudioTagging(checkpoint_path=None, device='cpu')
            
            # AudioSet class labels that we'll use for classification
            # These are indices in the AudioSet ontology
            _labels = {
                'speech_indices': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],  # Speech-related classes
                'music_indices': [137, 138, 139, 140, 141, 142, 143, 144, 145],  # Music-related classes
                'ad_indices': [429, 430, 431]  # Jingle, commercial-like sounds
            }
            
            logger.info("PANNs CNN14 model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load PANNs model: {e}")
            raise
    
    return _model, _labels


def classify_audio(file_path: str) -> Optional[str]:
    """
    Classify an audio file as speech, music, or ad.
    
    Args:
        file_path: Path to the audio file
        
    Returns:
        Classification label: "speech", "music", "ad", or None if classification fails
        
    Raises:
        FileNotFoundError: If the audio file doesn't exist
        Exception: If classification fails
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    try:
        # Load the model
        logger.info("Getting model instance...")
        model, labels = _get_model()
        logger.info("Model loaded successfully")
        
        # Load audio file - librosa returns 1D array
        logger.info(f"Loading audio file: {file_path}")
        try:
            audio, sr = librosa.load(file_path, sr=32000, mono=True, duration=10.0)
            logger.info(f"Audio loaded: shape={audio.shape}, sr={sr}, dtype={audio.dtype}")
        except Exception as e:
            logger.error(f"Failed to load audio file: {e}")
            raise Exception(f"Audio loading failed: {str(e)}")
        
        # Add batch dimension: (batch_size, segment_samples)
        audio = audio[None, :]
        logger.info(f"Audio reshaped for inference: shape={audio.shape}")
        
        # Run inference with error handling
        logger.info("Starting audio classification inference...")
        try:
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
            
            clipwise_output, embedding = model.inference(audio)
            
            logger.info(f"Inference completed successfully: output shape={clipwise_output.shape}")
        except Exception as e:
            logger.error(f"Inference failed with error: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise Exception(f"Model inference failed: {str(e)}")
        
        # clipwise_output shape: (1, 527) - probabilities for each AudioSet class
        probs = clipwise_output[0]
        
        # Calculate aggregate probabilities for each category
        speech_prob = np.mean([probs[i] for i in labels['speech_indices']])
        music_prob = np.mean([probs[i] for i in labels['music_indices']])
        ad_prob = np.mean([probs[i] for i in labels['ad_indices']])
        
        logger.info(f"Classification probabilities - Speech: {speech_prob:.3f}, Music: {music_prob:.3f}, Ad: {ad_prob:.3f}")
        
        # Determine the classification based on highest probability
        max_prob = max(speech_prob, music_prob, ad_prob)
        
        if max_prob == speech_prob:
            result = "speech"
        elif max_prob == music_prob:
            result = "music"
        else:
            result = "ad"
            
        logger.info(f"Classification result: {result}")
        return result
            
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error classifying audio file {file_path}: {e}")
        raise Exception(f"Classification failed: {str(e)}")


def get_classification_with_confidence(file_path: str) -> tuple[Optional[str], float]:
    """
    Classify an audio file and return the classification with confidence score.
    
    Args:
        file_path: Path to the audio file
        
    Returns:
        Tuple of (classification, confidence) where confidence is between 0 and 1
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    
    try:
        model, labels = _get_model()
        
        # Load audio file (first 30 seconds)
        audio, sr = librosa.load(file_path, sr=32000, mono=True, duration=30.0)
        
        # Ensure audio is the right length
        target_length = 32000 * 10
        if len(audio) < target_length:
            audio = np.pad(audio, (0, target_length - len(audio)))
        else:
            audio = audio[:target_length]
        
        # Run inference
        clipwise_output, embedding = model.inference(audio[None, :])
        probs = clipwise_output[0]
        
        # Calculate aggregate probabilities
        speech_prob = np.mean([probs[i] for i in labels['speech_indices']])
        music_prob = np.mean([probs[i] for i in labels['music_indices']])
        ad_prob = np.mean([probs[i] for i in labels['ad_indices']])
        
        # Determine classification and confidence
        max_prob = max(speech_prob, music_prob, ad_prob)
        
        if max_prob == speech_prob:
            classification = "speech"
        elif max_prob == music_prob:
            classification = "music"
        else:
            classification = "ad"
        
        return classification, float(max_prob)
        
    except FileNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error classifying audio file {file_path}: {e}")
        raise Exception(f"Classification failed: {str(e)}")
