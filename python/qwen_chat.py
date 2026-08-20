"""Small local Ollama client for the Uno Q web chat."""

import json
import os
import urllib.error
import urllib.request

from memory_store import MemoryStore, load_project_env


class QwenChat:
    def __init__(self, memory=None):
        load_project_env()
        self.url = os.getenv("SPIDEY_LLM_URL", "http://127.0.0.1:11434/api/chat")
        self.model = os.getenv("SPIDEY_LLM_MODEL", "qwen3:0.6b")
        self.memory = memory or MemoryStore()

    def _memory_context(self):
        events = self.memory.recent_events(limit=12)
        if not events:
            return "No Spidey events have been recorded yet."
        lines = []
        for event in events:
            person = event.get("person_name") or "unknown person"
            lines.append(
                f"{event['occurred_at']} | {event['event_type']} | "
                f"person={person} | room={event.get('room', 'unknown')}"
            )
        return "\n".join(lines)

    def ask(self, question):
        question = str(question or "").strip()
        if not question:
            return "Please ask me something about Spidey."
        prompt = (
            "You are Spidey, a small home robot. Answer briefly and honestly. "
            "Do not show or perform extended reasoning; give a direct answer in 1-3 sentences. "
            "Use only the event memory below for claims about people, rooms, and times. "
            "If the memory does not contain the answer, say that clearly.\n\n"
            f"EVENT MEMORY:\n{self._memory_context()}\n\n"
            f"USER QUESTION: {question}"
        )
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": 80},
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            answer = result.get("message", {}).get("content", "").strip()
            return answer or "Qwen returned an empty response."
        except urllib.error.URLError:
            return (
                "Qwen is not running yet. On the Uno Q Linux terminal, run "
                "`ollama serve` and install `qwen3:0.6b`."
            )
        except Exception as exc:
            return f"The local Qwen service could not answer: {exc}"

    def plan_task(self, request):
        """Turn a natural-language request into a small validated-by-caller plan."""
        prompt = (
            "Convert the user's robot task into JSON only. No markdown. "
            "Allowed actions are: set_room(name), set_face(face), "
            "move(direction,duration), scan_room(duration), run_circles(count). "
            "Directions: forward, backward, left, right. Faces: idle, happy, alert, left, right. "
            "Use seconds for duration. Keep each move at 1-10 seconds, scan at 1-30 seconds, "
            "and circles at 1-3. If the request is unclear, return {\"actions\":[],\"message\":\"...\"}. "
            "Return exactly {\"actions\":[{\"tool\":\"...\",\"args\":{...}}],\"message\":\"short summary\"}.\n\n"
            f"USER TASK: {str(request).strip()}"
        )
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 180},
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        request_obj = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request_obj, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "{}")
            return json.loads(content)
        except Exception as exc:
            return {"actions": [], "message": f"I could not create a safe task plan: {exc}"}
