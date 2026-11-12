# Optional Hydrogen Web Panel

The web panel is an optional companion service. It is **not required** to run Hydrogen UltimateBot.

## Deployment model

- Run the Discord bot on the existing Python bot host.
- Deploy the panel web app separately when desired.
- The bot initiates outbound HTTPS polling to the panel. The bot host does not need a public web port or inbound firewall rule.
- The bot does no panel networking when `PANEL_ENABLED=false` (the default).
- The panel must authenticate the bot with a separate high-entropy shared secret. Never reuse the Discord token or Gemini API key.

## Resource policy

- Disabled by default.
- No permanent websocket or busy loop.
- Polling uses a configurable interval (default 45 seconds; minimum 15 seconds).
- HTTP requests have strict timeouts and bounded response sizes.
- Panel failures are logged and retried later; they must not stop Discord gateway events or commands.

## Security requirements

- Discord OAuth2 login and server-owner authorization for each selected guild.
- CSRF-protected login flow and secure, HTTP-only session cookies.
- HTTPS in production.
- The panel must never expose bot tokens, Gemini keys, or the shared secret to browser JavaScript.
- Natural-language requests are treated as untrusted input. The bot may only translate them into allow-listed actions.
- Permission changes, destructive actions, and backup restores require a fresh confirmation by the actual Discord server owner.
- Never execute AI-generated Python, shell commands, SQL, or arbitrary code.

## Planned panel features

- Mobile-friendly dashboard, guild selector, status, and action history.
- Gemini command request queue with preview/confirmation before execution.
- Busy mode with an expiry and explicitly configured server-care behavior.
- Server structure backups and safe, confirmation-gated restore. Discord message history cannot be restored through the normal bot API.

## Configuration

See the root `.env.example`. Keep `PANEL_ENABLED=false` until the panel service has been deployed and tested. The bot-side connector will be added as an optional extension so normal bot startup remains independent of the panel.
