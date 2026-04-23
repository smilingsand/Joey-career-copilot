"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/voice_service.py
Description:
    This service acts as the central hub for all voice interaction capabilities.
    It abstracts the underlying TTS (Text-to-Speech) and STT (Speech-to-Text)
    technologies, providing a unified, high-level interface for the main application
    to "speak" and "listen".

    Key Responsibilities:
    1. Configuration Management: Loads voice settings (engine choice, language, voice model, speed, scope) from settings.ini.
    2. Engine Initialization: Instantiates the appropriate concrete client wrappers (e.g., EdgeTTSClient, WhisperSTTClient) based on configuration.
    3. Unified API: Provides simple `speak(text, persona)` and `listen()` async methods for the rest of the system.
    4. Scope Control: Manages when voice features are active based on the configured 'scope' (e.g., 'all', 'mock_interview').
    5. Persona Management: Handles switching voices between Joey (fixed), Mary (randomized pool), and Tom (fixed).
"""
import os
import logging
import asyncio
import configparser
import uuid
import tempfile
import time
import random # Used for random voice selection for Mary

# Audio Libraries
import speech_recognition as sr
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
import edge_tts
import io 

# Lazy load Whisper to speed up startup
# from faster_whisper import WhisperModel

logger = logging.getLogger("VoiceService")

class VoiceService:
    """
    Central service for handling voice input (STT) and output (TTS).
    Now handles switching between different underlying engines and manages 
    voice personas (Joey, Mary, Tom).
    """

    def __init__(self):
        """
        Initialize the Voice Service.
        Loads configuration and sets up TTS/STT clients if voice is enabled.
        """
        self.config = self._load_config()
        # Use getboolean for safe boolean parsing
        self.enabled = self.config.getboolean('Voice', 'enabled', fallback=False)
        
        # [NEW] Temp directory management
        self.temp_dir = os.path.join(os.getcwd(), "temp")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        
        # STT Config
        self.stt_engine = self.config.get('Voice', 'stt_engine', fallback='google').lower()
        self.input_lang = self.config.get('Voice', 'input_language', fallback='en-US') # Google uses en-US
        
        # Whisper Config
        self.whisper_size = self.config.get('Voice', 'whisper_model_size', fallback='base.en').lower()
        self.whisper_device = self.config.get('Voice', 'whisper_device', fallback='cpu').lower()
        self.whisper_type = self.config.get('Voice', 'whisper_compute_type', fallback='int8').lower()
        self.whisper_model = None # Lazy load

        # TTS Config
        self.tts_engine = self.config.get('Voice', 'tts_engine', fallback='edge-tts').lower()
        # [Fix] Use raw config reading to avoid % interpolation error
        # The _load_config helper already handles this, so direct access is safe here if loaded correctly
        self.rate = self.config.get('Voice', 'speaking_rate', fallback='+0%')
        
        # --- Persona Voice Configuration ---
        # Joey: Fixed voice (System/Copilot)
        self.joey_voice = self.config.get('Voice', 'joey_voice', fallback='en-AU-NatashaNeural').strip()
        
        # [NEW] Tom: Fixed Voice (Candidate)
        self.tom_voice = self.config.get('Voice', 'tom_voice', fallback='zh-CN-YunxiNeural').strip()

        # Mary: Voice Pool (Interviewer)
        mary_pool_str = self.config.get('Voice', 'mary_voices_pool', fallback='en-US-JennyNeural, en-GB-SoniaNeural')
        self.mary_voices_pool = [v.strip() for v in mary_pool_str.split(',') if v.strip()]
        
        # Current Interviewer Voice (Dynamically determined at runtime)
        self.current_mary_voice = None
        
        # Scope (Lowercase for easier matching)
        raw_scope = self.config.get('Voice', 'scope', fallback='all').lower()
        self.scope = [s.strip() for s in raw_scope.split(',')]
        
        # Recognizer Init
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 1.5 
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        
        # Pygame Mixer Init
        try:
            pygame.mixer.init()
        except Exception as e:
            logger.error(f"Pygame mixer init failed: {e}")


    def _load_config(self):
        # Disable interpolation to support '%' symbol in speaking_rate
        config = configparser.ConfigParser(interpolation=None)
        config.read("settings.ini", encoding='utf-8')
        return config

    def _ensure_whisper_loaded(self):
        """Lazy load Whisper model to save memory."""
        if self.stt_engine == 'whisper' and self.whisper_model is None:
            print(f"[System] 🧠 Loading Whisper model '{self.whisper_size}'... (One-time setup)")
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(
                self.whisper_size, 
                device=self.whisper_device, 
                compute_type=self.whisper_type
            )

    def pick_new_interviewer_voice(self):
        """Select a random voice for Mary from the pool."""
        if self.mary_voices_pool:
            # Try to pick a different voice than the last one
            new_voice = random.choice(self.mary_voices_pool)
            if len(self.mary_voices_pool) > 1 and new_voice == self.current_mary_voice:
                 new_voice = random.choice(self.mary_voices_pool)
            
            self.current_mary_voice = new_voice
        else:
            self.current_mary_voice = self.joey_voice # Fallback

    # for local Microphone Only, 阻塞式的麦克风监听
    def listen(self):
        """
        Listen and transcribe using Google or Whisper.
        """
        if not self.enabled: return None

        if self.stt_engine == 'whisper':
            self._ensure_whisper_loaded()

        print(f"\n🎤 [Voice Mode: {self.stt_engine.upper()}] Calibrating noise... (Silence)")
        
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                
                print("🎤 LISTENING... (Speak now)")
                
                # Recording strategy
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                
                print("   ... Processing ...")
                
                # === Branch A: Google Web API ===
                if self.stt_engine == 'google':
                    try:
                        # Google needs full locale code (e.g., en-US)
                        text = self.recognizer.recognize_google(audio, language=self.input_lang)
                        return text
                    except sr.UnknownValueError:
                        print("   ⚠️ Google could not understand audio.")
                        return None
                    except sr.RequestError:
                        print("   ❌ Google API Error.")
                        return None

                # === Branch B: Local Whisper ===
                elif self.stt_engine == 'whisper':
                    # Dump audio to temp file in 'temp' directory
                    temp_wav_path = os.path.join(self.temp_dir, f"stt_{uuid.uuid4().hex[:6]}.wav")
                    
                    try:
                        with open(temp_wav_path, "wb") as f:
                            f.write(audio.get_wav_data())
                        
                        # [Fix] Parse language code for Whisper (en-US -> en)
                        whisper_lang = self.input_lang.split('-')[0].lower()
                        
                        segments, _ = self.whisper_model.transcribe(
                            temp_wav_path, 
                            beam_size=5,
                            language=whisper_lang 
                        )
                        text = " ".join([segment.text for segment in segments]).strip()
                        return text
                    except Exception as e:
                        logger.error(f"Whisper Error: {e}")
                        return None
                    finally:
                        self._cleanup_file(temp_wav_path)
                
                else:
                    print(f"   ❌ Unknown STT Engine: {self.stt_engine}")
                    return None

        except sr.WaitTimeoutError:
            print("   ⚠️ Timeout: No speech detected.")
            return None
        except Exception as e:
            logger.error(f"Mic error: {e}")
            return None

    # [新增] 专门给 Streamlit 用的接口
    def transcribe_file(self, audio_file_path):
        """
        直接转录一个音频文件（而不是从麦克风听）
        """
        if self.stt_engine == 'whisper':
            self._ensure_whisper_loaded()
            segments, _ = self.whisper_model.transcribe(audio_file_path)
            return " ".join([s.text for s in segments]).strip()
            
        elif self.stt_engine == 'google':
            # 使用 speech_recognition 读取文件
            with sr.AudioFile(audio_file_path) as source:
                audio = self.recognizer.record(source)
                return self.recognizer.recognize_google(audio, language=self.input_lang)

    async def speak(self, text: str, persona: str = "joey"):
        """
        Speak text using the specified persona's voice.
        Args:
            text: Content to speak.
            persona: 'joey', 'mary', or 'tom'.
        """
        if not self.enabled or not text: return

        # Clean text for TTS
        clean_text = text.replace("*", "").replace("#", "").replace("=", "").replace("-", " ")
        
        # 1. Determine Voice based on Persona
        selected_voice = self.joey_voice # Default (Joey)
        
        if persona.lower() == "mary":
            if self.current_mary_voice is None:
                self.pick_new_interviewer_voice()
            selected_voice = self.current_mary_voice
            
        elif persona.lower() == "tom":
            # [NEW] Use Tom's voice from config
            selected_voice = self.tom_voice

        else:
            selected_voice = self.joey_voice # Default (Joey)
        
        # 2. Execute TTS (Edge-TTS)
        if self.tts_engine == 'edge-tts':
            # Use temp file in 'temp' directory
            temp_file = os.path.join(self.temp_dir, f"tts_{uuid.uuid4().hex[:6]}.mp3")
            
            try:
                # Generate MP3
                communicate = edge_tts.Communicate(clean_text, selected_voice, rate=self.rate)
                await communicate.save(temp_file)
                
                # Play Audio
                self._play_audio(temp_file)
            except Exception as e:
                logger.error(f"EdgeTTS Error: {e}")
            finally:
                self._cleanup_file(temp_file)
        else:
            logger.warning(f"Unknown TTS Engine: {self.tts_engine}")

    def _play_audio(self, file_path):
        """Plays audio file using Pygame mixer."""
        try:
            # Ensure mixer is initialized
            if not pygame.mixer.get_init():
                pygame.mixer.init()
                
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            
            # Blocking wait
            while pygame.mixer.music.get_busy():
                time.sleep(0.1) 
            
            # Unload to release file lock
            pygame.mixer.music.unload()
        except Exception as e:
            logger.error(f"Audio Playback Error: {e}")

    def _cleanup_file(self, file_path):
        """Safely removes temporary audio files."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except PermissionError:
            pass
        except Exception: pass

