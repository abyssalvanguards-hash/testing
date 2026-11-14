"""
Copy this file to private_config.py and fill in your private credentials.
Never share secrets.py or upload it publicly.
"""
DISCORD_TOKEN = "PASTE_DISCORD_BOT_TOKEN_HERE"

# Use one Gemini project/key initially. Add another key only for legitimate
# independent projects/accounts; quota errors do NOT trigger key rotation.
GEMINI_MODEL = "gemini-3.8-flash"
# Used with the SAME API key only after repeated HTTP 503 responses.
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"

GEMINI_API_KEYS = [
    "PASTE_GEMINI_API_KEY_HERE",
]

# Discord user IDs allowed to administer the bot across servers.
OWNER_IDS = [
    123456789012345678,
]
