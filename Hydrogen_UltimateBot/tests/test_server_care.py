from datetime import timedelta
import pytest

from cogs.server_care import parse_duration


def test_busy_duration_hours_and_days():
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("2d") == timedelta(days=2)


@pytest.mark.parametrize("value", ["0h", "31d", "abc", "2w", "-1d", "1.5h", "721h"])
def test_busy_duration_rejects_invalid_or_excessive_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)



from cogs.server_care import validate_backup_payload


def test_backup_payload_accepts_supported_structure():
    roles, channels = validate_backup_payload(
        {
            "format": 1,
            "guild_id": "123",
            "roles": [{"name": "Members", "permissions": 1024, "color": 0}],
            "channels": [{
                "name": "general",
                "type": "text",
                "overwrites": [{
                    "target_type": "role",
                    "target_name": "Members",
                    "allow": 1024,
                    "deny": 0,
                }],
            }],
        },
        123,
    )
    assert roles[0]["name"] == "Members"
    assert channels[0]["name"] == "general"


@pytest.mark.parametrize("payload", [
    None,
    [],
    {"format": 2, "guild_id": "123", "roles": [], "channels": []},
    {"format": 1, "guild_id": "999", "roles": [], "channels": []},
    {"format": 1, "guild_id": "123", "roles": "not-a-list", "channels": []},
    {"format": 1, "guild_id": "123", "roles": [], "channels": [{"name": "bad", "type": "forum"}]},
    {"format": 1, "guild_id": "123", "roles": [], "channels": [{"name": "general", "type": "text", "overwrites": "bad"}]},
])
def test_backup_payload_rejects_malformed_or_unsupported_data(payload):
    with pytest.raises(ValueError):
        validate_backup_payload(payload, 123)


def test_backup_payload_rejects_negative_permission_bits():
    payload = {
        "format": 1, "guild_id": "123", "roles": [],
        "channels": [{
            "name": "general", "type": "text",
            "overwrites": [{
                "target_type": "role", "target_name": "Members",
                "allow": -1, "deny": 0,
            }],
        }],
    }
    with pytest.raises(ValueError):
        validate_backup_payload(payload, 123)



def test_backup_payload_rejects_duplicate_names_and_invalid_category_reference():
    duplicate_roles = {
        "format": 1, "guild_id": "123",
        "roles": [{"name": "Staff"}, {"name": "Staff"}],
        "channels": [],
    }
    with pytest.raises(ValueError):
        validate_backup_payload(duplicate_roles, 123)

    invalid_category = {
        "format": 1, "guild_id": "123", "roles": [],
        "channels": [{
            "name": "general", "type": "text", "category": [],
            "overwrites": [],
        }],
    }
    with pytest.raises(ValueError):
        validate_backup_payload(invalid_category, 123)


def test_backup_payload_rejects_oversized_collections_and_bad_boolean_types():
    oversized = {
        "format": 1, "guild_id": "123", "roles": [],
        "channels": [{"name": f"channel-{i}", "type": "text"} for i in range(501)],
    }
    with pytest.raises(ValueError):
        validate_backup_payload(oversized, 123)

    invalid_boolean = {
        "format": 1, "guild_id": "123", "roles": [],
        "channels": [{"name": "general", "type": "text", "nsfw": 1}],
    }
    with pytest.raises(ValueError):
        validate_backup_payload(invalid_boolean, 123)


@pytest.mark.parametrize("role", [
    {"name": "BadColor", "color": 0x1000000},
    {"name": "BadPermissions", "permissions": 1 << 53},
])
def test_backup_payload_rejects_out_of_range_role_fields(role):
    payload = {"format": 1, "guild_id": "123", "roles": [role], "channels": []}
    with pytest.raises(ValueError):
        validate_backup_payload(payload, 123)


@pytest.mark.parametrize("channel", [
    {"name": "general", "type": "text", "topic": []},
    {"name": "general", "type": "text", "topic": "x" * 1025},
    {"name": "voice", "type": "voice", "bitrate": 7999},
    {"name": "voice", "type": "voice", "user_limit": 100},
    {"name": "general", "type": "text", "overwrites": [{
        "target_type": "role", "target_name": "Members",
        "allow": 1 << 53, "deny": 0,
    }]},
])
def test_backup_payload_rejects_invalid_channel_api_fields(channel):
    payload = {"format": 1, "guild_id": "123", "roles": [], "channels": [channel]}
    with pytest.raises(ValueError):
        validate_backup_payload(payload, 123)
