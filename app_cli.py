import asyncio
import re
import string
import logging
from google.genai import types

# Import the backend initialization function
from backend_core import init_career_copilot

# Re-setup logging for the CLI specifically if needed, 
# though backend_core should have handled the main config.
logger = logging.getLogger("AppCLI")

async def main_cli():
    """
    Command Line Interface for Joey Career Copilot.
    This function replicates the original interaction loop from app.py,
    but uses the shared backend logic.
    """
    
    # 1. 初始化后端 (Initialize Backend)
    print("Initializing backend systems...")
    try:
        backend = await init_career_copilot()
    except Exception as e:
        print(f"Critical Error during initialization: {e}")
        return

    if not backend:
        print("Failed to initialize backend. Check settings and API keys.")
        return


    # 2. 解包核心对象 (Unpack Backend Objects)
    runner = backend["runner"]
    voice_service = backend["voice_service"]
    adk_session_id = backend["adk_session_id"]
    user_id = backend["user_id"]
    
    # 状态与管理器
    app_state = backend["app_state"]
    history_manager = backend["history_manager"]
    enable_long_memory = backend["enable_long_memory"]

    # 角色名称
    user_name = backend["user_name"]
    copilot_name = backend["copilot_name"]
    interviewer_name = backend["interviewer_name"]
    candidate_name = backend["candidate_name"]
    avatar_name = backend["avatar_name"]

    # 配置
    instruct_config = backend["instruct_config"]

    # Helper FUnction  直接使用后端提供的函数，不再重新定义
    should_use_voice = backend["should_use_voice"]
    log_to_transcript = backend["log_to_transcript"]
  

    # 3. 显示欢迎语 (Welcome Message)
    memory_status_str = f" | 🧠 Memory: {len(history_manager.history)} turns" if (enable_long_memory and history_manager and history_manager.history) else ""
    try:
        welcome_raw = instruct_config["interface"]["welcome_message"]
        # 在 CLI 端进行变量替换
        welcome_msg = welcome_raw.replace("{user_name}", user_name)\
                                 .replace("{copilot_name}", copilot_name)\
                                 .replace("{interviewer_name}", interviewer_name)\
                                 .replace("{candidate_name}", candidate_name)\
                                 .replace("{avatar_name}", avatar_name)\
                                 .replace("{memory_status}", memory_status_str)
        print(welcome_msg)
    except Exception:
        # 兜底欢迎语
        print(f"\n🤖 Hi {user_name}, {copilot_name} Ready! (CLI Mode)\n")



    # 4. 主交互循环 (Interaction Loop)
    while True:
        try:
            user_input = ""
            use_voice_input = should_use_voice()
            
            # --- A. Get User Input ---
            # Voice Input
            if use_voice_input:
                voice_text = voice_service.listen()
                if voice_text:
                    user_input = voice_text
                    print(f"\n{user_name} (Voice) > {user_input}")
            
            # Keyboard Input
            if not user_input:
                prompt_symbol = "🎤 >" if use_voice_input else ">"
                user_input = input(f"\n{user_name} {prompt_symbol} ")

            # --- B. Smart Exit --
            sentences = re.split(r'[.!?;]+', user_input.lower())
            exit_keywords = {"exit", "quit", "stop", "bye", "goodbye", "terminate", "shutdown", "end"}
            is_exit_command = False
            
            for sentence in sentences:
                words = sentence.strip().split()
                if not words: continue
                has_exit_word = any(w in exit_keywords for w in words)
                is_short = len(words) <= 5
                has_negation = any(w in ["not", "don't", "dont", "never"] for w in words)
                
                if has_exit_word and is_short and not has_negation:
                    is_exit_command = True
                    break

            if is_exit_command:
                if app_state["is_interview_active"]:
                    print("\n[System] Detected exit command. Ending interview session...")
                    user_input = "Stop interview and give feedback."
                else:
                    print("Bye!")
                    break
            
            if not user_input.strip(): continue

            # Log User Input
            if app_state.get("transcript_path"):
                log_to_transcript(user_name, user_input)

            # Send to Agent
            msg = types.UserContent(parts=[types.Part(text=user_input)])
            
            if not use_voice_input:
                print("Agent > ", end="", flush=True)
            
            agent_response_buffer = ""
            header_printed = False
            current_turn_tool = None
            
            # Run Agent Logic
            async for event in runner.run_async(
                new_message=msg, 
                session_id=adk_session_id, 
                user_id=user_id
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        # Check for Tool Calls
                        if part.function_call:
                            current_turn_tool = part.function_call.name
                            continue

                        # Check for Text Response
                        if part.text:
                            if not header_printed:
                                speaker = copilot_name 
                                mode_str = "(Voice)" if should_use_voice() else ""
                                if current_turn_tool == "ask_copilot_tool": speaker = copilot_name
                                elif current_turn_tool == "stop_interview_tool": speaker = copilot_name
                                elif app_state["is_interview_active"]: speaker = interviewer_name
                                elif current_turn_tool == "start_mock_interview_tool": speaker = interviewer_name
                                
                                print(f"\n{speaker} {mode_str} > ", end="", flush=True)
                                header_printed = True

                            print(part.text, end="", flush=True)
                            agent_response_buffer += part.text
            print("") 
            
            # Log Agent Output
            if agent_response_buffer and app_state.get("transcript_path"):
                record_speaker = copilot_name
                if app_state["is_interview_active"]: record_speaker = interviewer_name
                if current_turn_tool == "ask_copilot_tool": record_speaker = f"{copilot_name} (Copilot)"
                if current_turn_tool == "stop_interview_tool": record_speaker = f"{copilot_name} (Coach)"
                
                log_to_transcript(record_speaker, agent_response_buffer)
            
            # TTS Output
            if should_use_voice() and agent_response_buffer:
                voice_persona = "joey"
                if app_state["is_interview_active"]:
                    voice_persona = "mary"
                if current_turn_tool in ["ask_copilot_tool", "stop_interview_tool"]:
                    voice_persona = "joey"

                await voice_service.speak(agent_response_buffer, persona=voice_persona)

            # Save to Memory
            if enable_long_memory and history_manager and agent_response_buffer:
                history_manager.add_turn(user_input, agent_response_buffer)

        except KeyboardInterrupt: 
            break
        except Exception as e: 
            logger.error(f"Main Loop Error: {e}")



if __name__ == "__main__":
    asyncio.run(main_cli())
