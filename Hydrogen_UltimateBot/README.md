# Hydrogen UltimateBot

**Made by Hydrogen** — modular Discord server management bot.

This is a modular, configurable project. It is not a selfbot and must be run with a Discord **bot token** created in the Discord Developer Portal. Never paste a user account token.

## 1. Included modules

| Module | Included functionality |
|---|---|
| Core / permissions | Configurable prefix, global owner IDs, bot admins, staff/admin/mod roles |
| Audit logging | Message edits/deletes, member joins/leaves/role updates, moderation, ticket, application, payment, and setup events |
| Ticket system | Up to four panels, open/claim/close/reopen, add/remove members, transcript export, persistent buttons |
| Ticket AI | Gemini-assisted ticket replies when the bot is mentioned; staff handoff on missing key or quota/service errors |
| Staff applications | Button panel, application modal, AI advisory summary, owner-only approve/reject, optional staff role assignment |
| Moderation | Manual timeout/warn commands and basic anti-spam timeout |
| Community | Welcome/goodbye, XP/levels, leaderboard, invite-use tracking |
| Slots | Create slot channels, ping limits, info and reset |
| Payments | UPI QR generation and LTC/BTC payment-instruction invoices; staff manual verification |
| Setup wizard | Confirmation-based private log-channel creation; non-destructive by default |

## 2. Requirements

- Python 3.10 or newer (3.11/3.12 recommended)
- A Discord application and bot account
- Bot invited with the permissions needed by enabled features
- Message Content Intent and Server Members Intent enabled in the Developer Portal

## 3. Install

### Windows
1. Extract the ZIP.
2. Open Command Prompt in the extracted `Hydrogen_UltimateBot` folder.
3. Create a virtual environment:
   `py -m venv .venv`
4. Activate it:
   `.venv\Scripts\activate`
5. Install packages:
   `py -m pip install -r requirements.txt`

### Linux hosting
1. Upload and extract the folder.
2. Open a terminal in the project folder.
3. Create a virtual environment:
   `python3 -m venv .venv`
4. Activate it:
   `source .venv/bin/activate`
5. Install:
   `python -m pip install -r requirements.txt`

## 4. Add private credentials

1. Copy `private_config.example.py`.
2. Rename the copy to `private_config.py`.
3. Replace `PASTE_DISCORD_BOT_TOKEN_HERE` with your bot token.
4. Replace the sample owner ID with your Discord numeric user ID.
5. Add your Gemini API key to `GEMINI_API_KEYS`.
6. Keep `private_config.py` private. It is excluded by `.gitignore`.
7. Do not share `private_config.py` with anyone.

The bot uses Gemini only when a key is configured. AI daily request cap is optional (`AI_DAILY_REQUEST_LIMIT = None` means no local cap). The bot does **not** rotate keys to evade provider quotas. If Gemini returns a quota/rate-limit error, the AI function stops and ticket support is handed to staff. You can configure a legitimate alternate provider/project later, but do not use key rotation to bypass service limits.

## 5. Discord Developer Portal

1. Open your Discord application.
2. Go to **Bot**.
3. Enable **Server Members Intent**.
4. Enable **Message Content Intent**.
5. Reset/copy the bot token and put it in `private_config.py`.
6. Invite the bot with the permissions required for the modules you intend to use.

Recommended bot permissions:
- View Channels
- Send Messages
- Read Message History
- Embed Links
- Attach Files
- Manage Channels
- Manage Roles
- Moderate Members
- Manage Messages
- Use External Emojis (only if needed)

Place the bot's highest role above the staff roles it must assign/manage. Do not grant Administrator unless you have a specific reason.

## 6. Run

Windows:
`py main.py`

Linux:
`python main.py`

## 7. First-time server setup

Run these prefix commands in a server (default prefix `!`):

1. `!setup` — preview and confirm creation of private log channels.
2. `!createstaffrole support Support Team` — optional least-privilege role preset. Also supports `moderator` and `admin`; none grants Administrator.
3. `!setrole staff @StaffRole`
4. `!setrole admin @AdminRole`
5. `!setrole mod @ModeratorRole`
6. `!setreview #application-review`
7. `!applicationpanel` — post staff application panel.
8. `!ticketconfig support #ticket-category @StaffRole Support | Tell us how we can help.`
9. `!ticketpanel support` — publish ticket panel.
10. `!setwelcome #welcome`
11. `!setgoodbye #goodbye`
12. `!prefix ?` — optional prefix change.

Role management:
- `!grantrole @member @role`
- `!revokerole @member @role`
- `!owneradd @user` and `!adminadd @user` are restricted to configured global owners.

## 8. Ticket AI

- A member opens a ticket using a panel button.
- AI responds only when a user mentions the bot in that ticket.
- AI has a limited recent-message context.
- AI cannot approve payments, issue refunds, grant roles, or perform moderation actions.
- If the API key is missing or quota/service errors occur, the bot posts a staff handoff and attempts to DM configured global owners.
- The AI is advisory. Staff remain responsible for decisions.

## 9. Staff applications

- `!applicationpanel` posts the panel.
- Applicants submit experience, skills, availability, a moderation scenario, and motivation.
- Gemini may produce a neutral summary and follow-up suggestions.
- AI never makes the approval decision.
- Only the server owner or configured global bot owner can approve/reject.
- If a staff role is configured, approval attempts to assign it, subject to Discord role hierarchy.

## 10. Payments

Commands:
- `!paymentconfig upi your-upi-id YourPayeeName`
- `!paymentconfig ltc YOUR_LTC_ADDRESS`
- `!paymentconfig btc YOUR_BTC_ADDRESS`
- `!invoice 250 upi`
- `!invoice 0.01 ltc`
- `!invoice 0.001 btc`
- `!paymentpending`
- `!paymentverify INVOICE_DATABASE_ID`

UPI QR is generated from the configured UPI ID and amount. LTC/BTC are instruction-only address messages. This project does not monitor blockchains or automatically confirm transfers. Staff must independently verify receipt before marking an invoice verified. Confirm the amount, currency/coin denomination, network, address, and payment details before sharing them. Never store seed phrases or private keys in this bot.

## 11. Slots and community

- `!createslot @member 5 optional-channel-name`
- `!slotinfo` (inside a registered slot)
- `!slotreset [#channel]`
- `!rank [@member]`
- `!leaderboard`
- `!invites [@member]`

XP and invite tracking are approximate/basic implementations. Invite attribution requires the bot to have permission to view invites and may not be available for every invite type.

## 12. Moderation

- `!timeout @member 10m reason`
- `!warn @member reason`

Basic anti-spam uses a configurable in-code threshold. Review false positives and adjust `SPAM_MESSAGE_COUNT`, `SPAM_WINDOW_SECONDS`, and timeout values in `config.py` before production use.

## 13. Emoji placeholders

Open `emojis.py`. Every value is intentionally blank (`= ""`). Paste your custom Discord emoji string into the matching setting, for example:
`success = "<:success:123456789012345678>"`

## 14. Safety and limitations

- The setup wizard is deliberately non-destructive: it creates missing log channels only. It does not delete or recreate every server channel, and it does not overwrite arbitrary role/channel permissions.
- Bulk permission changes should be previewed and explicitly approved by the server owner before implementation.
- The bot does not run arbitrary AI-generated code or give Gemini unrestricted server-owner authority.
- Payment confirmation is manual. No private wallet keys or seed phrases are accepted.
- Do not use user account tokens or selfbot functionality.
- Keep backups of `data/hydrogen.db`.
- This package has been syntax-checked, but it has not been tested against a live Discord server, live Gemini API, real payment rails, or your hosting provider. Review permissions and test in a private server before production.

## 15. Troubleshooting

- `PrivilegedIntentsRequired`: enable Server Members and Message Content intents in Developer Portal.
- `Missing Permissions`: grant the specific bot permission and check role hierarchy.
- `ApplicationCommand...`: this project primarily uses prefix commands; restart and check logs.
- `Gemini quota/rate limit`: wait for provider quota reset or review your billing/limits; the bot falls back to staff.
- `ModuleNotFoundError`: activate the virtual environment and rerun `pip install -r requirements.txt`.

## New AI server-management modules

These commands are included in this upgraded build:

- `!airestructure` — asks Gemini to review the current server and produce a proposed layout. The plan may create categories/channels and rename existing channels. It never deletes channels or categories.
- `!aipermissions` — asks Gemini for least-privilege channel/role permission recommendations. The allow-list blocks Administrator and other high-risk permissions.
- `!showplan <id>` — inspect the exact stored proposal before applying it.
- `!applyplan <id> CONFIRM` — applies a pending plan. Only the actual Discord server owner can run this command. The bot also needs the relevant Discord permissions and role hierarchy.

Both AI commands are preview-only. No server changes occur when a plan is generated. Review the proposal carefully before confirming. Discord may reject some operations, and a partially applied plan may require manual inspection. Back up important server settings before applying large changes.

## Automatic BTC/LTC transaction verification

- Create a BTC or LTC invoice with `!invoice <amount> btc` or `!invoice <amount> ltc`. The amount is denominated in the selected coin (up to 8 decimal places), not INR.
- A staff member checks it with `!paymentcheck <invoice_id> <64-character-txid>`.
- Hydrogen queries the BlockCypher public transaction endpoint and checks the transaction hash, the configured receiving address, the amount received, and at least 2 confirmations.
- A transaction ID can only be used once in this bot database. UPI remains manual.

Important limitations: this is a read-only public explorer lookup, not custody or a payment processor. Public explorer availability/rate limits can interrupt checks. Verify the configured wallet address independently before sharing it, test with a small amount, and reconcile payments against your own wallet records. Never treat a screenshot or user-provided claim as proof. The `!paymentverify` command remains a manual override and should only be used after independently confirming receipt. The bot does not send, hold, or refund cryptocurrency.

## Database upgrade

On startup, Hydrogen creates the new plan/transaction tables and adds the `txid` and `confirmations` invoice columns if they are missing. Existing database rows are preserved. Back up `data/hydrogen.db` before upgrading.

## Discord permissions

For AI restructuring, the bot needs Manage Channels. For permission plans, it needs Manage Roles and must have a role hierarchy position above the roles it edits. Keep the bot's permissions minimal; do not grant Administrator just to make setup easier.


## Slash and prefix commands
All text commands are registered as hybrid commands: use either `/command` or your configured prefix (default `!`). Use `/help` or `!help` for the command list. Invite the bot with both `bot` and `applications.commands` OAuth2 scopes. Slash commands are synced per guild at startup for faster availability.

## Gemini API troubleshooting
The Gemini integration uses the configured `GEMINI_MODEL` (default `gemini-3.8-flash`) and now reports the provider HTTP status and safe error detail. Check the key, Gemini API enablement, project restrictions, model name, and quota based on the returned status. Multiple configured keys are not rotated to evade quotas; the first configured key is used.


## Optional web panel (in development)

The web panel is a separate, optional service under `web_panel/`. It is not needed for normal bot operation.

- The bot remains on your existing Python host; the panel can be deployed later on a web host.
- The panel is designed for outbound bot polling, so the bot host does not need a public web port.
- Leave `PANEL_ENABLED=false` (the default) until the bot connector is installed and the panel is deployed.
- Panel deployment variables are documented in `web_panel/.env.example`.
- The initial panel service provides Discord OAuth2 sign-in for server owners and a request queue. Bot-side validation, action previews, confirmations, and command-result display are still being developed; do not use it for live server changes yet.
- Use HTTPS and unique random values for `PANEL_SHARED_SECRET` and `PANEL_SESSION_SECRET`. Never reuse your Discord bot token or Gemini API key.

The panel is intentionally separate so a panel outage cannot prevent the Discord bot from starting or handling Discord events.
