"""Тесты слоя хранения: диалоги, сообщения, лиды, 24ч-окно."""
from app import storage


def test_conversation_and_messages(temp_db):
    conv = storage.get_or_create_conversation("u1", "alice")
    assert conv["mode"] == "bot"
    storage.add_message("u1", "guest", "привет")
    storage.add_message("u1", "agent", "здравствуйте")
    hist = storage.get_history("u1")
    assert [m["sender"] for m in hist] == ["guest", "agent"]


def test_username_and_mode(temp_db):
    storage.get_or_create_conversation("u2")
    storage.set_username("u2", "@bob")
    storage.set_mode("u2", "human")
    c = storage.get_conversation("u2")
    assert c["username"] == "@bob"
    assert c["mode"] == "human"


def test_lead_upsert_merges_fields(temp_db):
    storage.get_or_create_conversation("u3")
    storage.upsert_lead("u3", {"hotel": "almaty", "heat": "hot"})
    storage.upsert_lead("u3", {"guests": "2"})  # не должно затирать hotel/heat
    l = storage.get_lead("u3")
    assert l["hotel"] == "almaty"
    assert l["heat"] == "hot"
    assert l["guests"] == "2"


def test_last_guest_ts(temp_db):
    storage.get_or_create_conversation("u4")
    assert storage.last_guest_ts("u4") is None
    storage.add_message("u4", "guest", "hi")
    assert storage.last_guest_ts("u4") is not None
    # сообщение агента не двигает окно гостя
    before = storage.last_guest_ts("u4")
    storage.add_message("u4", "agent", "reply")
    assert storage.last_guest_ts("u4") == before


def test_list_conversations(temp_db):
    storage.get_or_create_conversation("u5", "carol")
    storage.add_message("u5", "guest", "hello")
    convs = storage.list_conversations()
    row = next(c for c in convs if c["ig_user_id"] == "u5")
    assert row["last_text"] == "hello"
    assert row["last_sender"] == "guest"


def test_reset_conversation(temp_db):
    storage.get_or_create_conversation("u6")
    storage.add_message("u6", "guest", "hi")
    storage.upsert_lead("u6", {"heat": "warm"})
    storage.reset_conversation("u6")
    assert storage.get_conversation("u6") is None
    assert storage.get_lead("u6") is None
    assert storage.get_history("u6") == []
