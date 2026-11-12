"""Gemini API client with actionable errors and safe single-key behavior."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import aiohttp
from config import AI_DAILY_REQUEST_LIMIT
from core import db

class AIUnavailable(Exception):
    """Raised when AI is unavailable; message is safe to show to server staff."""

class GeminiService:
    def __init__(self, keys, model="gemini-3.8-flash"):
        if isinstance(keys, str):
            keys = [keys]
        self.keys = [str(k).strip() for k in (keys or []) if k and "PASTE_" not in str(k)]
        self.model = model or "gemini-3.8-flash"
        self.selection_file = Path(__file__).resolve().parents[1] / "data" / "gemini_selection.json"
        self.active_key_index = 0
        try:
            saved = json.loads(self.selection_file.read_text(encoding="utf-8"))
            index = int(saved.get("active_key_index", 0))
            if 0 <= index < len(self.keys):
                self.active_key_index = index
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        self.timeout = aiohttp.ClientTimeout(total=50, connect=10)
        self._semaphore = asyncio.Semaphore(2)

    @property
    def active_key(self):
        return self.keys[self.active_key_index] if self.keys else None

    def switch_key(self, index):
        """Select a configured key by 1-based position and persist selection."""
        if not 1 <= int(index) <= len(self.keys):
            raise ValueError("That key number is not configured.")
        self.active_key_index = int(index) - 1
        self.selection_file.parent.mkdir(parents=True, exist_ok=True)
        self.selection_file.write_text(
            json.dumps({"active_key_index": self.active_key_index}, indent=2),
            encoding="utf-8"
        )
        return self.active_key_index + 1

    async def _check_budget(self, guild_id, purpose):
        today = datetime.now(timezone.utc).date().isoformat()
        row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM ai_usage WHERE guild_id=? AND substr(created_at,1,10)=?",
            (guild_id, today)
        )
        used = row["n"] if row else 0
        if AI_DAILY_REQUEST_LIMIT is not None and used >= AI_DAILY_REQUEST_LIMIT:
            raise AIUnavailable("Configured daily AI budget reached.")

    async def ask(self, prompt, guild_id=None, user_id=None, purpose="general"):
        if not self.keys:
            raise AIUnavailable("Gemini API key is not configured. Add GEMINI_API_KEYS in private_config.py.")
        if guild_id is not None:
            await self._check_budget(guild_id, purpose)

        # Use the manually selected key. No automatic quota-based rotation.
        key = self.active_key
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096}
        }
        try:
            async with self._semaphore:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(url, params={"key": key}, json=body) as response:
                        raw = await response.text()
                        try:
                            data = json.loads(raw) if raw else {}
                        except json.JSONDecodeError:
                            data = {}
                        if response.status >= 400:
                            detail = ((data.get("error") or {}).get("message") or raw or "No error details returned")
                            detail = " ".join(str(detail).split())[:500]
                            if response.status == 400 and ("API_KEY_INVALID" in detail or "API key not valid" in detail.lower()):
                                raise AIUnavailable("Gemini rejected the API key (HTTP 400). Check that the key in private_config.py is correct and enabled for the Gemini API.")
                            if response.status in (401, 403):
                                raise AIUnavailable(f"Gemini authorization failed (HTTP {response.status}). Check the API key, API access, and project restrictions. Details: {detail}")
                            if response.status == 404:
                                raise AIUnavailable(f"Gemini model/API endpoint not found (HTTP 404). Check the configured model name `{self.model}`. Details: {detail}")
                            if response.status == 429:
                                raise AIUnavailable("Gemini quota or rate limit reached (HTTP 429). Wait for the provider limit to reset; no backup-key quota rotation is performed.")
                            raise AIUnavailable(f"Gemini API returned HTTP {response.status}: {detail}")

                        candidates = data.get("candidates") or []
                        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
                        answer = "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
                        if not answer:
                            reason = ((candidates[0] or {}).get("finishReason") if candidates else None) or data.get("promptFeedback", {}).get("blockReason") or "unknown response"
                            raise AIUnavailable(f"Gemini returned no text ({reason}). Try a shorter request or check the API's safety/availability status.")
        except AIUnavailable:
            raise
        except asyncio.TimeoutError as exc:
            raise AIUnavailable("Gemini request timed out after 50 seconds. Try again shortly.") from exc
        except aiohttp.ClientError as exc:
            raise AIUnavailable(f"Could not connect to Gemini ({type(exc).__name__}). Check hosting network access and try again.") from exc
        except Exception as exc:
            raise AIUnavailable(f"Unexpected Gemini integration error ({type(exc).__name__}): {str(exc)[:250]}") from exc

        if guild_id is not None:
            await db.execute(
                "INSERT INTO ai_usage(guild_id,user_id,purpose,created_at) VALUES(?,?,?,?)",
                (guild_id, user_id, purpose, datetime.now(timezone.utc).isoformat())
            )
        return answer[:7000]
