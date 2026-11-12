from pathlib import Path

PROJECT_NAME = "Hydrogen UltimateBot"
DEVELOPER_NAME = "Hydrogen"
BRAND_FOOTER = "Hydrogen UltimateBot • Made by Hydrogen"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "hydrogen.db"

# New default command prefix. Guilds can still override it in settings.
DEFAULT_PREFIX = "$"
MAX_TICKET_PANELS = 4
MAX_OPEN_TICKETS_PER_USER = 1

# Optional limits. None means no local cap; provider limits still apply.
AI_DAILY_REQUEST_LIMIT = None
AI_RESERVED_REVIEW_REQUESTS = 10
AI_MAX_TICKET_HISTORY_MESSAGES = 18

# Moderation defaults; tune per server with $settings.
SPAM_MESSAGE_COUNT = 7
SPAM_WINDOW_SECONDS = 8
SPAM_TIMEOUT_SECONDS = 60
ABUSE_TIMEOUT_SECONDS = 300

# Safe setup defaults. Bulk channel/permission changes are preview-only
# until an owner explicitly confirms them.
CHANNEL_SETUP_DRY_RUN_DEFAULT = True

# UPI/crypto values are configured by the owner in the database/settings.
PAYMENT_CURRENCY = "INR"
