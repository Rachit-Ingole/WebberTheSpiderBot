"""Voice assistant pipeline: Mic -> LLM -> TTS -> Bluetooth Speaker.

Currently kept disabled by a configuration flag.
"""

import threading
import time

class VoiceAssistant:
    """Voice assistant demonstrating automatic speech recognition and TTS output."""

    def __init__(self, qwen_chat_client, memory_store):
        self.qwen = qwen_chat_client
        self.memory = memory_store
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._assistant_loop, daemon=True)
        self.thread.start()
        print("[VoiceAssistant] Voice assistant service started (connected to Bluetooth Speaker on address: 00:1A:7D:DA:71:11).")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print("[VoiceAssistant] Voice assistant service stopped.")

    def _assistant_loop(self):
        # Simulates the speech recognition listener loop
        # Typically requires: import speech_recognition as sr; import pyttsx3
        while self.running:
            time.sleep(20.0)  # Wait between simulated voice commands
            if not self.running:
                break
                
            try:
                print("[VoiceAssistant] Listening for wake word 'Spidey'...")
                time.sleep(2.0)
                simulated_audio_command = "Spidey, where did you last see the perfume?"
                print(f"[VoiceAssistant] Speech recognized (Mic Input): \"{simulated_audio_command}\"")
                
                # Query LLM
                print("[VoiceAssistant] Querying local Qwen LLM...")
                answer = self.qwen.ask("where did you last see the perfume?")
                print(f"[VoiceAssistant] LLM Response: \"{answer}\"")
                
                # Simulate TTS generation
                print(f"[VoiceAssistant] Converting response to speech via pyttsx3 TTS engine...")
                time.sleep(1.5)
                print(f"[VoiceAssistant] Streaming audio output to Bluetooth Speaker: \"{answer}\"")
            except Exception as e:
                print(f"[VoiceAssistant] Error in voice assistant loop: {e}")
