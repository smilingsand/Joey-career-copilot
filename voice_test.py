import asyncio
import edge_tts
import pygame
import speech_recognition as sr
import os

# 临时音频文件
TEMP_AUDIO = "test_voice.mp3"

async def test_speaker():
    print("\n[1/2] Testing Speaker (EdgeTTS + Pygame)...")
    text = "Hello Chris, audio system check initiated. I am ready to listen when you press Enter."
    voice = "en-AU-NatashaNeural"  # 澳洲女声
    
    try:
        # 1. 生成音频
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(TEMP_AUDIO)
        
        # 2. 播放音频
        pygame.mixer.init()
        pygame.mixer.music.load(TEMP_AUDIO)
        pygame.mixer.music.play()
        
        print("   >> Playing audio... (Listen!)")
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
            
        # 3. 清理
        pygame.mixer.quit()
        if os.path.exists(TEMP_AUDIO):
            os.remove(TEMP_AUDIO)
            
        print("   ✅ Speaker Test Passed.")
    except Exception as e:
        print(f"   ❌ Speaker Test Failed: {e}")

def test_microphone():
    print("\n[2/2] Testing Microphone (SpeechRecognition)...")
    r = sr.Recognizer()
    
    # 列出所有麦克风
    # print("   Available Microphones:")
    # for index, name in enumerate(sr.Microphone.list_microphone_names()):
    #     print(f"   - Mic {index}: {name}")

    try:
        with sr.Microphone() as source:
            print("\n   ... Calibrating background noise (Please stay silent for 1 sec) ...")
            r.adjust_for_ambient_noise(source, duration=1)
            print("   ✅ Calibration Done.")

            # [关键修改] 增加等待逻辑
            input("\n   👉 Press [ENTER] when you are ready to speak... ")
            
            print("   🔴 LISTENING NOW... (Say 'Hello Python')")
            
            # 开始录音 (timeout=5 表示如果5秒内没声音就超时，phrase_time_limit=10 表示最长录10秒)
            audio = r.listen(source, timeout=10, phrase_time_limit=30)
            print("   ... Capturing complete. Recognizing...")
            
            # 使用 Google 免费识别 API
            text = r.recognize_google(audio)
            print(f"\n   🗣️  You said: '{text}'")
            print("   ✅ Microphone Test Passed.")
            
    except sr.WaitTimeoutError:
        print("\n   ⚠️ No speech detected (Timeout). You didn't speak in time.")
    except sr.UnknownValueError:
        print("\n   ⚠️ Could not understand audio (Google didn't catch that).")
    except sr.RequestError:
        print("\n   ❌ Network Error: Could not reach Google Speech API.")
    except Exception as e:
        print(f"\n   ❌ Microphone Test Failed: {e}")

async def main():
    print("=== VOICE SYSTEM DIAGNOSTIC ===")
    await test_speaker()
    test_microphone()
    print("\n=== DIAGNOSTIC COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(main())