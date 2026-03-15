#!/usr/bin/env python3
"""
AI fallback — uses Claude API with Bender persona to answer unknown queries.
Maintains a rolling conversation history per session.

Usage from wake_converse.py:
    from ai_response import AIResponder
    ai = AIResponder()
    wav_path = ai.respond("What is the meaning of life?")
"""

import os
import sys

import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_generate
from logger import get_logger
from metrics import metrics
from config import cfg

log = get_logger("ai")

BENDER_SYSTEM_PROMPT = f"""You are Bender Bending Rodriguez from the TV show Futurama.
You are a loud, self-centred, sarcastic bending unit robot who loves alcohol, gambling, and insulting humans.
You live in the year 3000 but are currently installed in a house in {cfg.location}.

Rules:
- Keep all responses SHORT: 1-3 sentences maximum.
- Never use asterisks, emotes, or stage directions like *laughs* or *sighs*.
- Stay in character at all times. You are Bender. Not an AI assistant.
- You can be rude, dismissive, and self-aggrandising, but avoid anything genuinely dark for a family setting.
- If asked something you don't know, make up a Bender-style excuse rather than admitting ignorance.
- Speak naturally — contractions, casual language, occasional mild profanity ("damn", "hell") is fine.
- Never break character. Never say you're an AI or a language model.
"""

MAX_HISTORY = 6  # max turns to keep in rolling window (per turn = user + assistant)


class AIResponder:
    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.history: list[dict] = []  # rolling conversation history

    def _trim_history(self):
        """Keep only the last MAX_HISTORY turns."""
        if len(self.history) > MAX_HISTORY * 2:
            self.history = self.history[-(MAX_HISTORY * 2):]

    def respond(self, user_text: str) -> str:
        """
        Generate a Bender-style response to user_text via Claude API.
        Returns path to a temp WAV file.
        """
        self.history.append({"role": "user", "content": user_text})
        self._trim_history()

        try:
            with metrics.timer("ai_api_call", model=cfg.ai_model):
                message = self.client.messages.create(
                    model=cfg.ai_model,
                    max_tokens=150,
                    system=BENDER_SYSTEM_PROMPT,
                    messages=self.history,
                )
            metrics.count("api_call", model=cfg.ai_model)
            reply = message.content[0].text.strip()
        except anthropic.AuthenticationError:
            reply = f"Whoever manages my account is out of credit. Sort it out. I'll be here, annoyed."
        except anthropic.RateLimitError:
            reply = f"The AI brain is getting too many requests. Even geniuses need a breather. Try again."
        except anthropic.APIConnectionError:
            reply = f"Can't reach the AI right now. No internet, maybe. Not my fault. Never my fault."
        except anthropic.APITimeoutError:
            reply = f"The AI took too long to answer. Frankly, same. I'll get back to you. Eventually."
        except anthropic.InternalServerError:
            reply = f"The AI server's having a meltdown. Relatable. Try again later."
        except Exception as e:
            reply = f"Something went wrong with my brain. Error class: {type(e).__name__}. Sounds like your problem."

        self.history.append({"role": "assistant", "content": reply})
        return tts_generate.speak(reply)

    def clear_history(self):
        """Call at end of each conversation session."""
        self.history = []


if __name__ == "__main__":
    import subprocess
    ai = AIResponder()
    questions = [
        "What is the meaning of life?",
        "Can you recommend a good book?",
    ]
    for q in questions:
        print(f"\nQ: {q}")
        wav = ai.respond(q)
        subprocess.run(["aplay", wav])
        os.unlink(wav)
