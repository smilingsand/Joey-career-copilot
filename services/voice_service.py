"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/voice_service.py
Description: 
    This service handles the Multimodal Interaction layer (Voice UI).
    It provides a unified interface for:
    1. Hearing (STT): Converting microphone input to text using either Google Web API (Online) 
       or Faster-Whisper (Local/Offline).
    2. Speaking (TTS): Converting agent text responses to natural speech using Edge-TTS 
       (Microsoft Azure Neural Voices).
    
    Key Features:
    - Low Latency: Optimized for real-time conversation.
    - Configurable Engines: Switch between cloud and local models via settings.
    - Resource Efficient: Lazy loading of heavy models (Whisper).
"""

import os
import logging
import asyncio
import configparser
import uuid
import tempfile
import time

# --- Audio & Speech Libraries ---
import speech_recognition as sr
import pygame
import edge_tts

logger = logging.getLogger("VoiceService")

class VoiceService:
    """
    A unified wrapper for Speech-to-Text (STT) and Text-to-Speech (TTS) operations.
    """

    def __init__(self):
        """
        Initialize the Voice Service.
        Loads configuration but delays heavy model loading until necessary.
        """
        self.config = self._load_config()
        
        # Master Switch: If False, the app runs in Text-Only mode.
        self.enabled = self.config.getboolean('Voice', 'enabled', fallback=False)
        
        # --- STT Configuration (Hearing) ---
        # Options: 'google' (Fast, Online) or 'whisper' (Accurate, Local)
        self.stt_engine = self.config.get('Voice', 'stt_engine', fallback='google').lower()
        self.input_lang = self.config.get('Voice', 'input_language', fallback='en-US')
        
        # Whisper Specific Settings (for local inference)
        self.whisper_size = self.config.get('Voice', 'whisper_model_size', fallback='base.en')
        self.whisper_device = self.config.get('Voice', 'whisper_device', fallback='cpu')
        self.whisper_type = self.config.get('Voice', 'whisper_compute_type', fallback='int8')
        self.whisper_model = None # Lazy loaded to save startup time

        # --- TTS Configuration (Speaking) ---
        # Options: 'edge-tts' (High quality neural voices, Free)
        self.tts_engine = self.config.get('Voice', 'tts_engine', fallback='edge-tts').lower()
        self.output_voice = self.config.get('Voice', 'output_voice', fallback='en-AU-NatashaNeural')
        self.rate = self.config.get('Voice', 'speaking_rate', fallback='+0%')
        
        # [关键配置] Parse scope as a list to support granular control (e.g., "mock_interview, interview_copilot")
        raw_scope = self.config.get('Voice', 'scope', fallback='all').lower()
        self.scope = [s.strip() for s in raw_scope.split(',')]
        
        # Initialize Microphone Recognizer
        self.recognizer = sr.Recognizer()
        
        # Optimization: Increase pause threshold to 1.5s (default 0.8s)
        # This allows the user to pause/think while speaking without cutting off the recording.
        self.recognizer.pause_threshold = 1.5 
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True

    def _load_config(self):
        """
        Loads settings.ini with interpolation disabled to handle special chars like '%'.
        """
        config = configparser.ConfigParser(interpolation=None)
        config.read("settings.ini", encoding='utf-8')
        return config

    def _ensure_whisper_loaded(self):
        """
        [Performance Optimization]
        Lazy loads the Faster-Whisper model only when STT is actually requested.
        This keeps the application startup time instant.
        """
        if self.stt_engine == 'whisper' and self.whisper_model is None:
            print(f"[System] 🧠 Loading Whisper model '{self.whisper_size}'... (One-time setup)")
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(
                self.whisper_size, 
                device=self.whisper_device, 
                compute_type=self.whisper_type
            )

    def listen(self):
        """
        [Core STT Method] Captures audio from microphone and transcribes it.
        
        Workflow:
        1. Calibrate ambient noise.
        2. Listen (record) until silence is detected.
        3. Transcribe using the selected engine.
        """
        if not self.enabled: return None
        
        # Ensure model is ready if using local engine
        if self.stt_engine == 'whisper': self._ensure_whisper_loaded()

        print(f"\n🎤 [Voice Mode: {self.stt_engine.upper()}] Calibrating noise... (Silence)")
        
        try:
            with sr.Microphone() as source:
                # Dynamic noise adjustment for better accuracy in different environments
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                print("🎤 LISTENING... (Speak now)")
                
                # Start recording. timeout=5 means wait 5s for speech to start.
                # phrase_time_limit=None allows infinite length recording (until pause).
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                print("   ... Processing ...")
                
                # --- Branch A: Google Web API (Online) ---
                if self.stt_engine == 'google':
                    try:
                        return self.recognizer.recognize_google(audio, language=self.input_lang)
                    except: return None

                # --- Branch B: Faster-Whisper (Local) ---
                elif self.stt_engine == 'whisper':
                    # Whisper requires a file path, so we dump audio to a temp .wav
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        f.write(audio.get_wav_data())
                        temp_wav_path = f.name
                    try:
                        # Run inference on CPU/GPU
                        segments, _ = self.whisper_model.transcribe(temp_wav_path, beam_size=5)
                        return " ".join([s.text for s in segments]).strip()
                    except: return None
                    finally:
                        # Cleanup temp file immediately
                        if os.path.exists(temp_wav_path): os.remove(temp_wav_path)
                
        except Exception as e:
            logger.error(f"Mic Error: {e}")
            return None

    async def speak(self, text: str):
        """
        [Core TTS Method] Converts text to speech and plays it.
        
        Args:
            text: The string to be spoken (e.g., Agent response).
        """
        if not self.enabled or not text: return
        
        # Clean Markdown symbols (*, #) as they sound bad when read aloud
        clean_text = text.replace("*", "").replace("#", "").replace("=", "")
        
        if self.tts_engine == 'edge-tts':
            # Generate a unique temp filename to avoid IO conflicts
            temp_file = f"response_{uuid.uuid4().hex[:6]}.mp3"
            try:
                # 1. Generate MP3 using Edge-TTS
                communicate = edge_tts.Communicate(clean_text, self.output_voice, rate=self.rate)
                await communicate.save(temp_file)
                
                # 2. Play Audio
                self._play_audio(temp_file)
            except Exception: pass
            finally: 
                # 3. Cleanup
                self._cleanup_file(temp_file)

    def _play_audio(self, file_path):
        """Plays audio file using Pygame mixer (Cross-platform compatibility)."""
        try:
            pygame.mixer.init()
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            
            # Blocking loop to wait for playback to finish
            while pygame.mixer.music.get_busy():
                time.sleep(0.1) 
            pygame.mixer.quit()
        except: pass

    def _cleanup_file(self, file_path):
        """Safely removes temporary audio files."""
        try:
            if os.path.exists(file_path): os.remove(file_path)
        except: pass
        