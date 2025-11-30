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
    # [NEW] 5. Persona Management: Handles switching voices between Joey (fixed) and Mary (randomized pool).
"""
import os
import logging
import asyncio
import configparser
import uuid
import tempfile
import time
import random # [新增] 用于随机选择声音

# 音频底层库
import speech_recognition as sr
import pygame
import edge_tts
import io # 用于内存流处理

# 延迟加载 Whisper 以加快启动
# from faster_whisper import WhisperModel

logger = logging.getLogger("VoiceService")

class VoiceService:
    def __init__(self):
        self.config = self._load_config()
        # 使用 getboolean 安全读取开关
        self.enabled = self.config.getboolean('Voice', 'enabled', fallback=False)

        # [新增] 临时文件目录管理
        self.temp_dir = os.path.join(os.getcwd(), "temp")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        
        # STT 配置
        self.stt_engine = self.config.get('Voice', 'stt_engine', fallback='google').lower()
        self.input_lang = self.config.get('Voice', 'input_language', fallback='en-US')
        
        # Whisper 专属配置
        self.whisper_size = self.config.get('Voice', 'whisper_model_size', fallback='base.en')
        self.whisper_device = self.config.get('Voice', 'whisper_device', fallback='cpu')
        self.whisper_type = self.config.get('Voice', 'whisper_compute_type', fallback='int8')
        self.whisper_model = None   # Lazy loaded to save startup time

        # TTS 配置
        self.tts_engine = self.config.get('Voice', 'tts_engine', fallback='edge-tts').lower()
        self.rate = self.config.get('Voice', 'speaking_rate', fallback='+0%')
        
        # Persona Voices
        # Joey: fixed voice
        self.joey_voice = self.config.get('Voice', 'joey_voice', fallback='en-AU-NatashaNeural')
        
        # Mary: voice pool (comma to seperate -> list)
        mary_pool_str = self.config.get('Voice', 'mary_voices_pool', fallback='en-US-JennyNeural, en-GB-SoniaNeural')
        self.mary_voices_pool = [v.strip() for v in mary_pool_str.split(',') if v.strip()]
        self.current_mary_voice = None
        
        # Scope
        raw_scope = self.config.get('Voice', 'scope', fallback='all').lower()
        self.scope = [s.strip() for s in raw_scope.split(',')]
        
        # Initialize Microphone Recognizer
        self.recognizer = sr.Recognizer()

        # Optimization: Increase pause threshold to 1.5s (default 0.8s)
        # This allows the user to pause/think while speaking without cutting off the recording.
        self.recognizer.pause_threshold = 1.5 
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        
        # Initialize Pygame Mixer for Playback
        try:
            pygame.mixer.init()
        except Exception as e:
            logger.error(f"Pygame mixer init failed: {e}")

    def _load_config(self):
        # 禁用插值以支持 % 符号
        config = configparser.ConfigParser(interpolation=None)
        config.read("settings.ini", encoding='utf-8')
        return config

    def _ensure_whisper_loaded(self):
        """仅当使用 Whisper 时才加载模型"""
        if self.stt_engine == 'whisper' and self.whisper_model is None:
            print(f"[System] 🧠 Loading Whisper model '{self.whisper_size}'... (One-time setup)")
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(
                self.whisper_size, 
                device=self.whisper_device, 
                compute_type=self.whisper_type
            )

    def pick_new_interviewer_voice(self):
        """
        [Persona Management]
        Selects a random voice for Mary from the pool to vary the interview experience.
        """
        if self.mary_voices_pool:
            # 尝试选一个和上次不一样的
            new_voice = random.choice(self.mary_voices_pool)
            if len(self.mary_voices_pool) > 1 and new_voice == self.current_mary_voice:
                 new_voice = random.choice(self.mary_voices_pool)
            self.current_mary_voice = new_voice
            # print(f"[System] 🎤 New Interviewer Voice Selected: {self.current_mary_voice}")
        else:
            self.current_mary_voice = self.joey_voice

    def listen(self):
        """
        [Core STT Method] Captures audio from microphone and transcribes it.
        
        Workflow:
        1. Calibrate ambient noise.
        2. Listen (record) until silence is detected.
        3. Transcribe using the selected engine (Google or Whisper).
        """
        if not self.enabled: return None

        # Ensure model is ready if using local engine
        if self.stt_engine == 'whisper': self._ensure_whisper_loaded()

        print(f"\n🎤 [Voice Mode: {self.stt_engine.upper()}] Calibrating noise... (Silence)")
        
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                print("🎤 LISTENING... (Speak now)")
                
                # Start recording. timeout=5 means wait 5s for speech to start.
                # phrase_time_limit=None allows infinite length recording (until pause).
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=None)
                print("   ... Processing ...")
                
                # --- Branch A: Google Web API (Online) ---
                if self.stt_engine == 'google':
                    try:
                        # [NOTE] Google uses full locale code like 'en-US'
                        text = self.recognizer.recognize_google(audio, language=self.input_lang)
                        return text
                    except sr.UnknownValueError:
                        print("   ⚠️ Google could not understand audio.")
                        return None
                    except sr.RequestError:
                        print("   ❌ Google API Error.")
                        return None

                # --- Branch B: Faster-Whisper (Local) ---
                elif self.stt_engine == 'whisper':
                    # Dump audio to temp file for Whisper
                    temp_wav_path = os.path.join(self.temp_dir, f"stt_{uuid.uuid4().hex[:6]}.wav")
                    
                    try:
                        with open(temp_wav_path, "wb") as f:
                            f.write(audio.get_wav_data())

                        # [Fix] Parse language code for Whisper
                        # Google uses 'en-US', Whisper expects just 'en'
                        whisper_lang = self.input_lang.split('-')[0]

                        segments, _ = self.whisper_model.transcribe(
                            temp_wav_path, 
                            beam_size=5,
                            language=whisper_lang # [Added] Explicitly pass language
                        )
                        text = " ".join([segment.text for segment in segments]).strip()
                        return text
                    except Exception as e:
                        logger.error(f"Whisper Error: {e}")
                        return None
                    finally:
                        if os.path.exists(temp_wav_path): os.remove(temp_wav_path)
                
                else:
                    print(f"   ❌ Unknown STT Engine: {self.stt_engine}")
                    return None

        except sr.WaitTimeoutError:
            print("   ⚠️ Timeout: No speech detected.")
            return None
        except Exception as e:
            logger.error(f"Mic error: {e}")
            return None

    # [修改] 增加 persona 参数，支持动态声音
    async def speak(self, text: str, persona: str = "joey"):
        """
        [Core TTS Method] Converts text to speech and plays it.
        
        Args:
            text: The string to be spoken.
            persona: 'joey' (default) or 'mary'.
        """
        if not self.enabled or not text: return

        # Clean Markdown symbols (*, #) as they sound bad when read aloud
        clean_text = text.replace("*", "").replace("#", "").replace("=", "").replace("-", " ")
        
        # Select Voice based on Persona
        selected_voice = self.joey_voice # 默认 Joey
        
        if persona.lower() == "mary":
            if self.current_mary_voice is None:
                self.pick_new_interviewer_voice()
            selected_voice = self.current_mary_voice
        
        if self.tts_engine == 'edge-tts':
            # Generate a unique temp filename
            temp_file = os.path.join(self.temp_dir, f"tts_{uuid.uuid4().hex[:6]}.mp3")

            try:
                # Generate MP3 using Edge-TTS
                communicate = edge_tts.Communicate(clean_text, selected_voice, rate=self.rate)
                await communicate.save(temp_file)

                # Play Audio
                self._play_audio(temp_file)
            except Exception as e:
                logger.error(f"EdgeTTS Error: {e}")
            finally:
                # Cleanup
                self._cleanup_file(temp_file)
        else:
            logger.warning(f"Unknown TTS Engine: {self.tts_engine}")

    def _play_audio(self, file_path):
        """Plays audio file using Pygame mixer."""
        try:
            # ensure mixer initialization
            if not pygame.mixer.get_init():
                pygame.mixer.init()
                
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            
            # Blocking loop to wait for playback to finish
            while pygame.mixer.music.get_busy():
                time.sleep(0.1) 

            # Unload to release file lock (crucial on Windows), quit() 可以强制释放
            pygame.mixer.music.unload()
                
        except Exception as e:
            logger.error(f"Audio Playback Error: {e}")

    def _cleanup_file(self, file_path):
        """Safely removes temporary audio files."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except PermissionError:
            # File might still be locked by OS/Player, ignore for now
            pass
        except Exception: pass
