"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: app_streamlit.py
"""

import streamlit as st
import asyncio
import os
import tempfile
import uuid
import contextlib
import io
import re
import sys
from google.genai import types
from audiorecorder import audiorecorder
import speech_recognition as sr

# 引入后端
from backend_core import init_career_copilot

# ==========================================
# Page Configuration
# ==========================================
st.set_page_config(
    page_title="Joey | Career Copilot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# CSS Styling (The "Nuclear Option")
# ==========================================
st.markdown("""
<style>
    /* 1. 统一聊天气泡样式 */
    .stChatMessage {
        background-color: transparent !important;
        border: 1px solid rgba(250, 250, 250, 0.1);
    }
    
    /* 2. 强制段落文本样式 (不限制宽度，只控制对齐和大小) */
    .stChatMessage p {
        line-height: 1.6 !important;
        text-align: justify !important;
        margin-bottom: 0.8rem !important;
    }
    
    .stButton button { width: 100%; }
    
    /* 3. 隐藏掉不需要的装饰 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# [JS 滚动函数 - 锚点版]
def scroll_to_bottom():
    js = """
    <script>
        // 定义滚动函数
        function forceScroll() {
            // 1. 寻找我们埋下的锚点
            const element = window.parent.document.getElementById("end-of-chat");
            if (element) {
                // 2. 滚动到该元素，使其出现在视图底部
                element.scrollIntoView({behavior: "smooth", block: "end", inline: "nearest"});
            }
        }
        // 3. 延迟执行多次，确保 DOM 渲染完成后能捉到锚点
        setTimeout(forceScroll, 100);
        setTimeout(forceScroll, 500);
    </script>
    """
    st.components.v1.html(js, height=0)



# [辅助函数] 渲染自动换行的日志
def render_wrapping_log(text):
    if not text: return
    
    # 给这个 div 一个唯一的 ID
    div_id = "auto-interview-log-container"
    
    style = """
    background-color: #1e1e1e;
    color: #e0e0e0;
    padding: 15px;
    border-radius: 8px;
    font-family: "Source Sans Pro", -apple-system, sans-serif;
    font-size: 15px;
    white-space: pre-wrap;
    word-wrap: break-word;
    line-height: 1.5;
    border: 1px solid #444;
    max-height: 400px;
    overflow-y: auto;
    """
    
    # [关键] 注入一段 JS，找到这个 ID 的元素并将其 scrollTop 设置为 scrollHeight
    # 注意：这段 JS 会在 HTML 渲染时执行
    scroll_script = f"""
    <script>
        var element = document.getElementById("{div_id}");
        if (element) {{
            element.scrollTop = element.scrollHeight;
        }}
    </script>
    """
    
    html = f'<div id="{div_id}" style="{style}">{text}</div>{scroll_script}'
    st.markdown(html, unsafe_allow_html=True)


# ==========================================
# 1. Init & State
# ==========================================
if "loop" not in st.session_state:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    st.session_state.loop = loop
else:
    asyncio.set_event_loop(st.session_state.loop)

active_loop = st.session_state.loop

if "auto_interview_logs" not in st.session_state:
    st.session_state.auto_interview_logs = ""

class StreamToStreamlit(io.StringIO):
    def __init__(self, placeholder):
        super().__init__()
        self.placeholder = placeholder
        self.terminal = sys.__stdout__

    def write(self, s):
        if self.terminal: self.terminal.write(s)
        if s.strip():
            st.session_state.auto_interview_logs += s + "\n"
            self.placeholder.empty()
            with self.placeholder.container():
                 render_wrapping_log(st.session_state.auto_interview_logs)
    
    def flush(self): 
        if self.terminal: self.terminal.flush()

@st.cache_resource
def get_backend_driver():
    try:
        return active_loop.run_until_complete(init_career_copilot())
    except Exception as e:
        st.error(f"Failed to initialize backend: {e}")
        return None

if "backend" not in st.session_state:
    with st.spinner("🚀 Booting up Joey's Brain & Voice Engines..."):
        st.session_state.backend = get_backend_driver()
        st.session_state.messages = [] 

backend = st.session_state.backend
if not backend: st.stop()

runner = backend["runner"]
voice_service = backend["voice_service"]
app_state = backend["app_state"]
user_name = backend["user_name"]
copilot_name = backend["copilot_name"]
interviewer_name = backend["interviewer_name"]
user_id = backend["user_id"]
session_id = backend["adk_session_id"]

# ==========================================
# 2. Helper Functions
# ==========================================
def transcribe_uploaded_audio(audio_bytes):
    if not audio_bytes or not voice_service: return None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
        tmp_file.write(audio_bytes.export().read())
        tmp_path = tmp_file.name
    text = None
    try:
        if voice_service.stt_engine == 'whisper':
            voice_service._ensure_whisper_loaded()
            if voice_service.stt_client and voice_service.stt_client.model:
                segments, _ = voice_service.whisper_model.transcribe(tmp_path)
                text = " ".join([s.text for s in segments]).strip()
        elif voice_service.stt_engine == 'google':
            r = sr.Recognizer()
            with sr.AudioFile(tmp_path) as source:
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language=voice_service.input_lang)
    except Exception as e: st.error(f"Transcription Error: {e}")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)
    return text

# ==========================================
# 3. Sidebar
# ==========================================
with st.sidebar:
    st.title(f"⚙️ Control Panel")
    
    if app_state["is_interview_active"]:
        st.error(f"🔴 **INTERVIEW IN PROGRESS**\n\nInterviewer: {interviewer_name}")
        if st.button("🛑 Stop Interview", type="primary"):
            st.session_state.injected_prompt = "Stop interview and give feedback."
            st.rerun()
    else:
        st.success(f"🟢 **Standby Mode**\n\nAssistant: {copilot_name}")

    st.markdown("---")
    
    if voice_service:
        voice_on = st.toggle("🔊 Enable Voice Output", value=voice_service.enabled)
        voice_service.enabled = voice_on
        if voice_on:
            display_voice = getattr(voice_service, 'joey_voice', 'Unknown')
            st.caption(f"TTS Voice: {display_voice}")
            st.caption(f"Scope: {voice_service.scope}")
    else:
        st.warning("Voice Service Unavailable")

    st.markdown("---")
    st.subheader("📂 Workspace")
    try:
        jd_dir = "jd"
        cv_dir = "cv"
        with st.expander("📄 Job Descriptions"):
            if os.path.exists(jd_dir):
                files = [f for f in os.listdir(jd_dir) if not f.startswith('.')]
                for f in files: st.text(f"• {f}")
            else: st.caption("No files found.")
        with st.expander("📝 Generated CVs"):
            if os.path.exists(cv_dir):
                files = [f for f in os.listdir(cv_dir) if not f.startswith('.')]
                for f in files: st.text(f"• {f}")
            else: st.caption("No files found.")
    except: st.caption("File browser unavailable")

# ==========================================
# 4. Main Chat Area
# ==========================================
st.title(f"🤖 {copilot_name}")
st.caption("Your Voice-Enabled AI Career Agent")

try:
    welcome_template = backend["instruct_config"]["interface"]["welcome_message"]
    memory_status_str = f"🧠 Memory: {len(backend['history_manager'].history)} turns" if (backend['enable_long_memory'] and backend['history_manager'].history) else "🧠 Memory: Off"
    
    welcome_msg = welcome_template.replace("{user_name}", user_name)\
                                  .replace("{copilot_name}", copilot_name)\
                                  .replace("{interviewer_name}", interviewer_name)\
                                  .replace("{candidate_name}", backend.get("candidate_name", "Tom"))\
                                  .replace("{avatar_name}", backend.get("avatar_name", "Richard"))\
                                  .replace("{memory_status}", memory_status_str)

    skill_pattern = re.compile(r"\[([A-Z\?\/])\]\s+(.*?)\s*[:：]\s+(.*)")
    skills = skill_pattern.findall(welcome_msg)
    
    command_pattern = re.compile(r"\d+\.\s+\"(.*?)\"")
    commands = command_pattern.findall(welcome_msg)

    with st.expander("ℹ️  User Guide & Quick Commands", expanded=True):
        greeting_line = welcome_msg.strip().split('\n')[1]
        if "[+]" in greeting_line: 
            st.info(greeting_line.replace("[+]", "👋").strip())
        
        if skills:
            col1, col2 = st.columns(2)
            
            # 将功能拆分为两组 (Core vs Advanced)
            # 这里我们根据图标简单分类，或者直接对半分
            core_skills = []
            advanced_skills = []
            
            for icon, name, desc in skills:
                # 映射 CLI 符号到 Emoji
                emoji = "🔹"
                if icon == "O": emoji = "🕵️‍♂️"
                elif icon == "/": emoji = "📝"
                elif icon == "M": emoji = "🎤"
                elif icon == "S": emoji = "🤖"
                elif icon == "R": emoji = "🕴️"
                elif icon == "A": emoji = "🧠"
                elif icon == "?": emoji = "💬"
                
                item_md = f"- **{emoji} {name.strip()}**: {desc.strip()}"
                
                # 简单的分类逻辑：前两个放左边，后面放右边 (根据您 instruction 的顺序)
                # 或者根据图标判断
                if icon in ["O", "/", "?"]: # Scout, Writer, Consultant
                    core_skills.append(item_md)
                else: # Interview related (M, S, R, A)
                    advanced_skills.append(item_md)
            
            with col1:
                st.markdown("### 🛠️ Core Skills")
                st.markdown("\n".join(core_skills))
                
            with col2:
                st.markdown("### 🚀 Interview Modes")
                st.markdown("\n".join(advanced_skills))
            
        if commands:
            st.markdown("---")
            st.markdown("### ⚡ Quick Start Commands (Copy & Paste)")
            command_text = "\n".join([f'{i+1}. "{cmd}"' for i, cmd in enumerate(commands)])
            st.code(command_text, language="text")
except: pass

# ==========================================
# 5. Render Chat History & Log (顺序修复)
# ==========================================

# A. 先渲染所有历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🧑‍💻" if msg["role"]=="user" else "🤖"):
        st.markdown(msg["content"])

# B. [位置修复] 在历史消息之后，渲染最新的 Log
if st.session_state.auto_interview_logs:
    with st.expander("📺 Last Auto-Interview Log (Review)", expanded=True):
        render_wrapping_log(st.session_state.auto_interview_logs)

# [新增] 页面底部锚点 (用于 JS 滚动)
st.markdown('<div id="end-of-chat"></div>', unsafe_allow_html=True)


# ==========================================
# 6. Input Logic
# ==========================================
st.markdown("---") 
user_input = None
if "injected_prompt" in st.session_state:
    user_input = st.session_state.pop("injected_prompt")

col_mic, col_text = st.columns([0.15, 0.85])
with col_mic:
    if voice_service and voice_service.enabled:
        audio_bytes = audiorecorder("🎤", "⏹️")
    else:
        audio_bytes = None
        st.caption("🔇")
with col_text:
    text_input = st.chat_input("Type your message here...")

if audio_bytes and len(audio_bytes) > 0:
    user_input = transcribe_uploaded_audio(audio_bytes)
elif text_input:
    user_input = text_input

# ==========================================
# 7. Processing Loop
# ==========================================
# ==========================================
# 6. Processing Loop (修正版)
# ==========================================
if user_input:
    # 1. 显示用户输入
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(user_input)
        
    if "log_to_transcript" in backend:
        backend["log_to_transcript"](user_name, user_input)

    # 2. 生成 Agent 回复
    with st.chat_message("assistant", avatar="🤖"):
        response_container = st.empty()
        live_log_placeholder = st.empty()
        
        async def run_agent_cycle():
            internal_full_response = ""
            internal_tool_called = False
            
            # 如果是开启新面试，清空旧日志
            if "simulate" in user_input.lower() or "auto" in user_input.lower():
                 st.session_state.auto_interview_logs = ""
            
            msg = types.UserContent(parts=[types.Part(text=user_input)])
            stream_capture = StreamToStreamlit(live_log_placeholder)
            
            try:
                with contextlib.redirect_stdout(stream_capture):
                    async for event in runner.run_async(new_message=msg, session_id=session_id, user_id=user_id):
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.function_call:
                                    tool_name = part.function_call.name
                                    # 打印工具调用日志
                                    print(f"\n[System] 🛠️ Executing Tool: {tool_name}...") 
                                    internal_tool_called = True

                                if part.text:
                                    internal_full_response += part.text
                                    response_container.markdown(internal_full_response + "▌")
            except Exception as e:
                st.error(f"Runner Error: {e}")
            
            # 清空实时日志占位符
            live_log_placeholder.empty() 
            response_container.markdown(internal_full_response)
            return internal_full_response, internal_tool_called

        with st.spinner("Thinking..."):
            full_response, tool_called = active_loop.run_until_complete(run_agent_cycle())
    
    # 3. 保存 Agent 回复
    st.session_state.messages.append({"role": "assistant", "content": full_response})
    
    if "log_to_transcript" in backend:
        speaker_name = copilot_name
        if app_state["is_interview_active"]: speaker_name = interviewer_name
        backend["log_to_transcript"](speaker_name, full_response)

    # 4. TTS Output (语音播放核心修正)
    should_speak = False
    
    # [关键修正 A] 重新从 backend 读取最新的面试状态
    # 因为 start_mock_interview_tool 刚刚可能修改了它
    current_interview_active = backend["app_state"]["is_interview_active"]
    
    if voice_service and voice_service.enabled and full_response:
        if "all" in voice_service.scope:
            should_speak = True
        elif current_interview_active: # 使用最新状态判断
             allowed = ["mock_interview", "mock_interview_service", "interview_copilot"]
             if any(s in voice_service.scope for s in allowed):
                 should_speak = True
    
    if should_speak:
        temp_mp3 = f"temp_tts_{uuid.uuid4().hex}.mp3"
        
        async def generate_audio_file():
            import edge_tts
            clean_text = full_response.replace("*", "").replace("#", "").replace("=", "")
            
            # 确定声音
            voice_to_use = getattr(voice_service, 'joey_voice', 'en-AU-NatashaNeural')
            
            # 如果在面试中，且不是 Copilot 的回答，则使用 Mary 的声音
            # (简单判断：Copilot 只有在显式呼叫时才出现，通常面试流中默认是 Mary)
            if current_interview_active:
                 mary_voice = getattr(voice_service, 'current_mary_voice', None)
                 if mary_voice: voice_to_use = mary_voice
            
            comm = edge_tts.Communicate(clean_text, voice_to_use)
            await comm.save(temp_mp3)
        
        try:
            active_loop.run_until_complete(generate_audio_file())
            if os.path.exists(temp_mp3):
                # 自动播放音频
                st.audio(temp_mp3, format="audio/mp3", autoplay=True)
        except Exception as e:
            st.error(f"TTS Error: {e}")
            
    # [关键修正 B] 增加用户操作引导
    # 如果面试正在进行，弹出提示告诉用户该说话了
    if current_interview_active:
        st.toast("🎙️ **It's your turn!** Click the microphone below to answer.", icon="🗣️")


    # [关键] 强制滚动到底部
    scroll_to_bottom()
    
    st.rerun()

