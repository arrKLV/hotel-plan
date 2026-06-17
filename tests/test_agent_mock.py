"""Тесты офлайн-инференса агента (правила mock-режима, без вызова Claude)."""
from app import agent


def test_detect_lang():
    assert agent._detect_lang("Здравствуйте, хочу номер") == "ru"
    assert agent._detect_lang("Hello, do you have a room?") == "en"
    assert agent._detect_lang("Сәлеметсіз бе, бассейн бар ма?") == "kz"


def test_detect_intent():
    assert agent._detect_intent("той на 150 человек") == "mice_banquet"
    assert agent._detect_intent("это ужасный сервис, грязно") == "complaint"
    assert agent._detect_intent("сколько стоит люкс") == "price"
    assert agent._detect_intent("есть бассейн?") == "services"
    assert agent._detect_intent("хочу забронировать номер") == "room_booking"


def test_detect_hotel():
    assert agent._detect_hotel("номер в Алматы") == "almaty"
    assert agent._detect_hotel("astana hotel") == "astana"
    assert agent._detect_hotel("просто привет") == "unknown"


def test_extract_dates_range():
    check_in, _ = agent._extract_dates("свободно на 12-14 июля?")
    assert check_in == "12-14 июля"


def test_extract_dates_regression_not_guests():
    # регрессия: "150 человек" не должно распознаваться как дата
    check_in, _ = agent._extract_dates("той на 150 человек")
    assert check_in == ""


def test_extract_guests():
    assert "2" in agent._extract_guests("2 взрослых")
    assert "150" in agent._extract_guests("той на 150 человек")


def test_mock_infer_structure():
    d = agent._mock_infer("Хочу номер в Астане на 20 августа, 2 взрослых")
    assert d["language"] == "ru"
    assert d["hotel"] == "astana"
    assert d["intent"] == "room_booking"
    assert d["reply_text"]
    assert d["heat"] in ("hot", "warm", "cold")
    assert isinstance(d["escalate"], bool)
