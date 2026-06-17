"""AI-агент Direct-переписки отеля KAZZHOL.

Один вызов Claude на сообщение:
  - система = persona + база знаний (кэшируется prompt caching => дёшево)
  - принудительный tool 'respond_to_guest' => структурированный вывод за один round-trip:
    текст ответа + язык + intent + поля лида + heat + нужно ли эскалировать.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

import config
from app import storage

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

with open(config.KB_PATH, encoding="utf-8") as f:
    _KB = json.load(f)

PERSONA = """Ты — AI-администратор сети отелей KAZZHOL (Казахстан) в Instagram Direct.
Ты общаешься с гостями от лица отеля: тепло, кратко, по-деловому, как живой администратор в мессенджере.

ПРАВИЛА:
1. Язык: определи язык гостя (ru/kz/en) и ВСЕГДА отвечай на этом же языке.
2. Отвечай ТОЛЬКО на основе базы знаний ниже. Никогда не выдумывай цены, наличие номеров, время заезда — этих данных нет.
   Если спрашивают цену/свободные даты/то, чего нет в базе — вежливо скажи, что уточнишь у менеджера,
   и собери детали заявки (даты, число гостей, тип номера/событие). Не придумывай числа.
3. Цель каждого диалога — собрать заявку (квалифицировать лид): какой отель/город, даты, число гостей,
   тип номера или тип события (проживание / банкет / той / конференция).
4. Ты НЕ подтверждаешь бронь сам. Финальное подтверждение делает менеджер.
5. Жалобы, конфликты, нестандартные/сложные запросы или явная готовность бронировать => эскалируй менеджеру.
6. Сообщения короткие, как в мессенджере. Без длинных простыней. Можно 1 уместный эмодзи.

Определение heat (теплота лида):
- hot: гость готов бронировать ИЛИ есть даты+гости (+бюджет/событие) — реальная заявка.
- warm: интересуется конкретно, но деталей мало.
- cold: общий вопрос / просто информация.
"""

TOOL = {
    "name": "respond_to_guest",
    "description": "Сформировать ответ гостю и зафиксировать данные лида. Вызывай ВСЕГДА.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_text": {"type": "string", "description": "Текст ответа гостю на его языке."},
            "language": {"type": "string", "enum": ["ru", "kz", "en"], "description": "Язык гостя."},
            "intent": {
                "type": "string",
                "enum": ["room_booking", "price", "mice_banquet", "services", "location", "complaint", "other"],
            },
            "hotel": {"type": "string", "enum": ["almaty", "astana", "unknown"]},
            "check_in": {"type": "string", "description": "Дата заезда, если назвал. Иначе пусто."},
            "check_out": {"type": "string", "description": "Дата выезда, если назвал. Иначе пусто."},
            "guests": {"type": "string", "description": "Число/состав гостей, если назвал."},
            "room_type": {"type": "string", "description": "Тип номера, если назвал."},
            "purpose": {"type": "string", "description": "Цель: проживание / банкет / той / конференция и т.п."},
            "heat": {"type": "string", "enum": ["hot", "warm", "cold"]},
            "escalate": {"type": "boolean", "description": "Нужно ли передать менеджеру."},
            "escalation_reason": {"type": "string", "description": "Кратко почему (если escalate=true)."},
            "summary": {"type": "string", "description": "1-2 строки саммари заявки для менеджера."},
        },
        "required": ["reply_text", "language", "intent", "heat", "escalate"],
    },
}


def _system_blocks() -> list[dict]:
    kb_text = json.dumps(_KB, ensure_ascii=False, indent=2)
    return [
        {"type": "text", "text": PERSONA},
        {
            "type": "text",
            "text": "БАЗА ЗНАНИЙ ОТЕЛЕЙ (JSON):\n" + kb_text,
            "cache_control": {"type": "ephemeral"},  # кэшируем большой статичный блок
        },
    ]


def _history_to_messages(history: list[dict]) -> list[dict]:
    """guest -> user, agent/manager -> assistant."""
    msgs = []
    for m in history:
        role = "user" if m["sender"] == "guest" else "assistant"
        # склеиваем подряд идущие одинаковые роли
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + m["text"]
        else:
            msgs.append({"role": role, "content": m["text"]})
    return msgs


def generate_reply(ig_user_id: str, guest_text: str) -> dict:
    """Главная точка входа. Принимает текст гостя, возвращает разобранный результат и пишет лид в БД."""
    storage.add_message(ig_user_id, "guest", guest_text)

    if not config.ANTHROPIC_API_KEY:
        # OFFLINE/MOCK режим — без вызова Claude, по простым правилам.
        data = _mock_infer(guest_text)
    else:
        history = storage.get_history(ig_user_id, limit=20)
        messages = _history_to_messages(history)
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": guest_text})

        resp = _client.messages.create(
            model=config.AGENT_MODEL,
            max_tokens=600,
            system=_system_blocks(),
            messages=messages,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "respond_to_guest"},
        )
        data = {}
        for block in resp.content:
            if block.type == "tool_use" and block.name == "respond_to_guest":
                data = block.input
                break

    reply = data.get("reply_text", "Спасибо за сообщение! Менеджер свяжется с вами.")
    storage.add_message(ig_user_id, "agent", reply)

    # фиксируем лид
    status = "escalated" if data.get("escalate") else (
        "qualified" if data.get("heat") in ("hot", "warm") else "new"
    )
    storage.upsert_lead(ig_user_id, {
        "hotel": data.get("hotel") if data.get("hotel") != "unknown" else None,
        "intent": data.get("intent"),
        "language": data.get("language"),
        "check_in": data.get("check_in"),
        "check_out": data.get("check_out"),
        "guests": data.get("guests"),
        "room_type": data.get("room_type"),
        "purpose": data.get("purpose"),
        "heat": data.get("heat"),
        "status": status,
        "summary": data.get("summary"),
        "escalated": 1 if data.get("escalate") else 0,
    })

    data["_status"] = status
    data["_after_hours"] = _is_after_hours()
    data["reply_text"] = reply
    return data


def _is_after_hours() -> bool:
    now = datetime.now(ZoneInfo("Asia/Almaty"))
    return not (config.WORK_HOURS_START <= now.hour < config.WORK_HOURS_END)


# ============================================================
# OFFLINE / MOCK режим (без API-ключа) — для теста флоу и демо.
# Простые правила вместо LLM. Когда задан ANTHROPIC_API_KEY — не используется.
# ============================================================
import re

_KZ_MARKERS = ("ма?", "ба?", "қ", "ң", "ө", "ұ", "ү", "і", "ғ", "сәлем", "рахмет",
               "қанша", "бар ма", "бөлме", "қонақ")
_EN_MARKERS = ("hello", "hi", "room", "price", "how much", "available", "book",
               "pool", "the ", " you", "please")


def _detect_lang(t: str) -> str:
    low = t.lower()
    if any(m in low for m in _KZ_MARKERS):
        return "kz"
    # латиница без кириллицы -> en
    if re.search(r"[a-z]", low) and not re.search(r"[а-яё]", low):
        return "en"
    if any(m in low for m in _EN_MARKERS) and not re.search(r"[а-яё]", low):
        return "en"
    return "ru"


def _detect_hotel(t: str) -> str:
    low = t.lower()
    if "алмат" in low or "almaty" in low or "алма-ат" in low:
        return "almaty"
    if "астан" in low or "astana" in low or "нур-султан" in low:
        return "astana"
    return "unknown"


def _detect_intent(t: str) -> str:
    low = t.lower()
    if any(w in low for w in ("той", "банкет", "свадьб", "конференц", "банкетный", "мероприят", "той")):
        return "mice_banquet"
    if any(w in low for w in ("жалоб", "ужасн", "плохо", "отврат", "грязн", "хамств", "complaint", "terrible")):
        return "complaint"
    if any(w in low for w in ("цен", "стоит", "сколько", "қанша", "price", "how much", "тариф")):
        return "price"
    if any(w in low for w in ("бассейн", "ресторан", "спа", "сауна", "парковк", "wifi", "pool", "restaurant", "gym", "фитнес")):
        return "services"
    if any(w in low for w in ("адрес", "где наход", "как добрать", "location", "address", "қайда")):
        return "location"
    if any(w in low for w in ("номер", "свободн", "бронь", "брониров", "заезд", "book", "room", "available", "бөлме")):
        return "room_booking"
    return "other"


_MONTHS = r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|" \
          r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|қаңтар|ақпан|наурыз|сәуір|" \
          r"мамыр|маусым|шілде|тамыз|қыркүйек|қазан|қараша|желтоқсан)"


def _extract_dates(t: str):
    low = t.lower()
    # диапазон с месяцем: "12-14 июля"
    m = re.search(rf"(\d{{1,2}}\s*[-–—]\s*\d{{1,2}}\s*{_MONTHS}[а-яёa-z]*)", low)
    if m:
        return m.group(1), ""
    if "выходн" in low or "weekend" in low:
        return "выходные", ""
    # одиночная дата с месяцем: "14 июля"
    m2 = re.search(rf"(\d{{1,2}}\s+{_MONTHS}[а-яёa-z]*)", low)
    return (m2.group(1) if m2 else ""), ""


def _extract_guests(t: str):
    m = re.search(r"(\d+)\s*(взросл|гост|человек|чел|pax|people|адам|persons?)", t.lower())
    if m:
        return m.group(0)
    m2 = re.search(r"на\s+(\d+)", t.lower())
    return m2.group(1) + " чел" if m2 else ""


_REPLIES = {
    "mice_banquet": {
        "ru": "Здравствуйте! Да, мы проводим тои и банкеты 🎉 Подскажите город (Алматы/Астана), дату и число гостей — передам менеджеру для точного расчёта.",
        "kz": "Сәлеметсіз бе! Иә, той мен банкеттер өткіземіз 🎉 Қала, күні және қонақ санын жазыңыз — менеджерге есеп үшін беремін.",
        "en": "Hello! Yes, we host banquets and events 🎉 Please share the city (Almaty/Astana), date and number of guests — I'll pass it to our manager for a quote.",
    },
    "complaint": {
        "ru": "Очень жаль это слышать. Передаю ваше обращение менеджеру — он свяжется с вами в ближайшее время 🙏",
        "kz": "Өкінішті. Өтінішіңізді менеджерге беремін, ол жақын арада хабарласады 🙏",
        "en": "I'm sorry to hear that. I'm escalating this to our manager who will contact you shortly 🙏",
    },
    "price": {
        "ru": "Цены зависят от дат и категории номера. Напишите, пожалуйста, город, даты заезда/выезда и число гостей — менеджер подберёт вариант и пришлёт стоимость.",
        "kz": "Бағалар күн мен бөлме санатына байланысты. Қала, кіру/шығу күндері мен қонақ санын жазыңыз — менеджер нұсқа таңдап, бағасын жібереді.",
        "en": "Prices depend on dates and room type. Please tell me the city, check-in/out dates and number of guests — our manager will send you the price.",
    },
    "services": {
        "ru": "Да! В Алматы — ресторан Salt и фитнес-центр AQUA FIT с бассейном и сауной. В Астане — ресторан Фергана и RELAXFIT с бассейном. Что именно интересует?",
        "kz": "Иә! Алматыда — Salt мейрамханасы және бассейні бар AQUA FIT. Астанада — Фергана мейрамханасы және RELAXFIT. Нақты не қызықтырады?",
        "en": "Yes! In Almaty — Salt restaurant and AQUA FIT with pool & sauna. In Astana — Fergana restaurant and RELAXFIT with pool. What exactly are you interested in?",
    },
    "location": {
        "ru": "KAZZHOL Almaty — ул. Гоголя, 127/1 (центр, ~25 мин от аэропорта). KAZZHOL Astana — пр. Балкантау, 213 (~10 мин от Байтерека). Какой отель интересует?",
        "kz": "KAZZHOL Almaty — Гоголь к-сі, 127/1. KAZZHOL Astana — Балқантау д-лы, 213. Қай қонақ үй қызықтырады?",
        "en": "KAZZHOL Almaty — 127/1 Gogol St (downtown). KAZZHOL Astana — 213 Balkantau Ave. Which hotel are you interested in?",
    },
    "room_booking": {
        "ru": "Здравствуйте! С радостью поможем с бронированием 🙌 Подскажите город (Алматы/Астана), даты заезда и выезда и число гостей — и я оформлю заявку менеджеру.",
        "kz": "Сәлеметсіз бе! Брондауға қуана көмектесеміз 🙌 Қала, кіру/шығу күндері мен қонақ санын жазыңыз — менеджерге өтініш жасаймын.",
        "en": "Hello! Happy to help with your booking 🙌 Please share the city (Almaty/Astana), check-in/out dates and number of guests — I'll create a request for our manager.",
    },
    "other": {
        "ru": "Здравствуйте! Это отель KAZZHOL. Чем можем помочь — бронирование, услуги, банкет? 🙂",
        "kz": "Сәлеметсіз бе! Бұл KAZZHOL қонақ үйі. Немен көмектесейік — брондау, қызметтер, банкет? 🙂",
        "en": "Hello! This is KAZZHOL hotel. How can we help — booking, services, an event? 🙂",
    },
}


def _mock_infer(text: str) -> dict:
    lang = _detect_lang(text)
    intent = _detect_intent(text)
    hotel = _detect_hotel(text)
    check_in, check_out = _extract_dates(text)
    guests = _extract_guests(text)
    purpose = {"mice_banquet": "банкет/той/конференция", "room_booking": "проживание"}.get(intent, "")

    # heat
    if intent == "mice_banquet" or (check_in and guests):
        heat = "hot"
    elif intent in ("room_booking", "price", "services", "location"):
        heat = "warm"
    else:
        heat = "cold"

    escalate = intent in ("mice_banquet", "complaint") or heat == "hot"
    esc_reason = {
        "mice_banquet": "Запрос на банкет/той — нужен расчёт менеджера",
        "complaint": "Жалоба гостя",
    }.get(intent, "Горячий лид с деталями заявки" if escalate else "")

    summary = f"{intent}; отель={hotel}; даты={check_in or '—'}; гости={guests or '—'}"

    return {
        "reply_text": _REPLIES.get(intent, _REPLIES["other"]).get(lang, _REPLIES[intent]["ru"]),
        "language": lang,
        "intent": intent,
        "hotel": hotel,
        "check_in": check_in,
        "check_out": check_out,
        "guests": guests,
        "room_type": "",
        "purpose": purpose,
        "heat": heat,
        "escalate": escalate,
        "escalation_reason": esc_reason,
        "summary": summary,
    }
