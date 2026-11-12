import json
from pathlib import Path

from cogs.server_builder import (
    SAFE_ROLE_PERMISSIONS,
    CLASSIFICATION_ROLES,
    category_name,
    channel_name,
    role_name,
)

ROOT = Path(__file__).resolve().parents[1]


def test_exact_server_template_has_six_categories_and_25_channels():
    template = json.loads((ROOT / "data" / "server_template.json").read_text(encoding="utf-8"))
    assert len(template["categories"]) == 6
    assert sum(len(item["channels"]) for item in template["categories"]) == 25


def test_requested_role_names_are_in_template():
    template = json.loads((ROOT / "data" / "server_template.json").read_text(encoding="utf-8"))
    assert {
        "Founder", "Owner", "Manager", "Admin", "Moderator", "Staff",
        "Trial Staff", "Senior Staff", "Senior Moderator", "Senior Manager",
        "Members", "1 Invite", "2 Invites", "5 Invites", "10 Invites", "15 Invites",
    }.issubset(set(template["roles"]))


def test_naming_formats():
    assert role_name("Founder") == "W X・Founder"
    assert category_name("general chat") == "━━━「 GENERAL CHAT 」━━━"
    assert channel_name("general chat") == "▫-ɢᴇɴᴇʀᴀʟ-ᴄʜᴀᴛ"


def test_ai_permission_allowlist_excludes_privileged_server_control():
    assert "administrator" not in SAFE_ROLE_PERMISSIONS
    assert "manage_guild" not in SAFE_ROLE_PERMISSIONS
    assert "manage_roles" not in SAFE_ROLE_PERMISSIONS
    assert "manage_channels" not in SAFE_ROLE_PERMISSIONS


def test_channel_access_config_is_per_guild_and_uses_placeholders_only():
    config = json.loads((ROOT / "data" / "channel_access.json").read_text(encoding="utf-8"))
    assert isinstance(config.get("guilds"), dict)
    assert "YOUR_TEST_GUILD_ID" in config["guilds"]
    item = config["guilds"]["YOUR_TEST_GUILD_ID"]
    assert set(item) == {"verified_role_id", "verification_channel_id", "protected_channel_ids"}



def test_classification_roles_cover_hydrogen_bots_and_humans():
    assert CLASSIFICATION_ROLES == ("Hydrogen", "Bots", "Members")
    assert role_name("Hydrogen") == "W X・Hydrogen"
    assert role_name("Bots") == "W X・Bots"
    assert role_name("Members") == "W X・Members"
