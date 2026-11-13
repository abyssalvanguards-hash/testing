"""Gemini API client with actionable errors and safe single-key behavior."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import aiohttp
from config import AI_DAILY_REQUEST_LIMIT
from core import db

class AIUnavailable(Exception):
    """Raised when AI is unavailable; message is safe to show to server staff."""

class GeminiService:
    def __init__(self, keys, model="gemini-3.8-flash", fallback_model="gemini-3.5-flash-lite"):
        if isinstance(keys, str):
            keys = [keys]
        self.keys = [str(k).strip() for k in (keys or []) if k and "PASTE_" not in str(k)]
        self.model = model or "gemini-3.8-flash"
        self.fallback_model = (fallback_model or "").strip() or None
        self.selection_file = Path(__file__).resolve().parents[1] / "data" / "gemini_selection.json"
        self.active_key_index = 0
        try:
            saved = json.loads(self.selection_file.read_text(encoding="utf-8"))
            index = int(saved.get("active_key_index", 0))
            if 0 <= index < len(self.keys):
                self.active_key_index = index
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        # Server-builder prompts can take longer during Gemini capacity spikes.
        # Keep connection establishment bounded while allowing a longer generation window.
        self.timeout = aiohttp.ClientTimeout(total=90, connect=10)
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

        key = self.active_key
        body = {
            "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096}
        }
        models = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models.append(self.fallback_model)

        try:
            async with self._semaphore:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    data = {}
                    answer = ""
                    last_503_detail = ""
                    for model_index, model in enumerate(models):
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                        for attempt, delay in enumerate((0, 1.5, 3.5)):
                            async with session.post(url, params={"key": key}, json=body) as response:
                                raw = await response.text()
                                try:
                                    data = json.loads(raw) if raw else {}
                                except json.JSONDecodeError:
                                    data = {}
                                if response.status == 503:
                                    last_503_detail = ((data.get("error") or {}).get("message") or raw or "No error details returned")
                                    logging.warning(
                                        "Gemini model %s returned HTTP 503 (attempt %s/3).",
                                        model, attempt + 1
                                    )
                                    if attempt < 2:
                                        await asyncio.sleep(delay if delay else 0)
                                        continue
                                    if model_index + 1 < len(models):
                                        logging.warning("Gemini primary model %s remains unavailable; trying fallback model %s.", model, models[model_index + 1])
                                        break
                                    detail = " ".join(str(last_503_detail).split())[:400]
                                    raise AIUnavailable(f"Gemini models are temporarily overloaded (HTTP 503). Tried {', '.join(models)}. Details: {detail}")
                                if response.status >= 400:
                                    detail = ((data.get("error") or {}).get("message") or raw or "No error details returned")
                                    detail = " ".join(str(detail).split())[:500]
                                    if response.status == 400 and ("API_KEY_INVALID" in detail or "API key not valid" in detail.lower()):
                                        raise AIUnavailable("Gemini rejected the API key (HTTP 400). Check that the key in private_config.py is correct and enabled for the Gemini API.")
                                    if response.status in (401, 403):
                                        raise AIUnavailable(f"Gemini authorization failed (HTTP {response.status}). Check the API key, API access, and project restrictions. Details: {detail}")
                                    if response.status == 404:
                                        raise AIUnavailable(f"Gemini model/API endpoint not found (HTTP 404). Check the configured model name `{model}`. Details: {detail}")
                                    if response.status == 429:
                                        raise AIUnavailable("Gemini quota or rate limit reached (HTTP 429). Check the project's quota and try again later.")
                                    raise AIUnavailable(f"Gemini API returned HTTP {response.status} for model {model}: {detail}")

                                candidates = data.get("candidates") or []
                                parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
                                answer = "\
".join(str(part.get("text", "")) for part in parts if part.get("text"))
                                if not answer:
                                    reason = ((candidates[0] or {}).get("finishReason") if candidates else None) or data.get("promptFeedback", {}).get("blockReason") or "unknown response"
                                    raise AIUnavailable(f"Gemini returned no text ({reason}). Try a shorter request or check the API's safety/availability status.")
                                logging.info("Gemini request succeeded using model %s.", model)
                                break
                            # The inner loop breaks after a successful response or final 503.
                            if answer or (response.status == 503 and model_index + 1 < len(models) and attempt == 2):
                                break
                        if answer:
                            break
                    if not answer:
                        raise AIUnavailable("Gemini did not return a response from the configured models.")
        except AIUnavailable:
            raise
        except asyncio.TimeoutError as exc:
            logging.exception("Gemini request timed out (primary=%s, fallback=%s).", self.model, self.fallback_model or "disabled")
            raise AIUnavailable("Gemini request timed out after 90 seconds. Check Gemini availability and hosting network diagnostics.") from exc
        except aiohttp.ClientError as exc:
            logging.exception("Gemini network request failed (%s).", type(exc).__name__)
            raise AIUnavailable(f"Could not connect to Gemini ({type(exc).__name__}). Check hosting network access and try again.") from exc
        except Exception as exc:
            logging.exception("Unexpected Gemini integration error.")
            raise AIUnavailable(f"Unexpected Gemini integration error ({type(exc).__name__}): {str(exc)[:250]}") from exc

        if guild_id is not None:
            await db.execute(
                "INSERT INTO ai_usage(guild_id,user_id,purpose,created_at) VALUES(?,?,?,?)",
                (guild_id, user_id, purpose, datetime.now(timezone.utc).isoformat())
            )
        return answer[:7000]
