"""Instagram Messaging API: проверка вебхука, парсинг входящих, отправка ответов.

Использует официальный Graph API (легально). Подпись вебхука проверяется HMAC-SHA256.
"""
import hashlib
import hmac

import httpx

import config

GRAPH_URL = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/me/messages"


def verify_signature(payload: bytes, header_signature: str) -> bool:
    """Проверка X-Hub-Signature-256 от Meta. Если APP_SECRET не задан — пропускаем (dev)."""
    if not config.IG_APP_SECRET:
        return True
    if not header_signature or not header_signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        config.IG_APP_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature)


def parse_incoming(body: dict) -> list[dict]:
    """Извлекает входящие текстовые DM из webhook-пейлоада.

    Возвращает список {sender_id, text}. Эхо-сообщения (от самого отеля) игнорируются.
    """
    out = []
    for entry in body.get("entry", []):
        for ev in entry.get("messaging", []):
            msg = ev.get("message", {})
            if msg.get("is_echo"):
                continue
            text = msg.get("text")
            sender = ev.get("sender", {}).get("id")
            if text and sender:
                out.append({"sender_id": sender, "text": text})
    return out


async def send_message(recipient_id: str, text: str) -> dict:
    """Отправить ответ гостю через Send API."""
    if not config.IG_ACCESS_TOKEN:
        return {"error": "IG_ACCESS_TOKEN не задан — сообщение не отправлено (dev-режим)."}
    params = {"access_token": config.IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(GRAPH_URL, params=params, json=payload)
        try:
            return r.json()
        except Exception:
            return {"error": f"send failed ({r.status_code})", "body": r.text}


async def send_action(recipient_id: str, action: str) -> dict:
    """Отметка прочтения / индикатор печати: action = mark_seen | typing_on | typing_off."""
    if not config.IG_ACCESS_TOKEN:
        return {"skipped": "no token"}
    params = {"access_token": config.IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "sender_action": action}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(GRAPH_URL, params=params, json=payload)
            return r.json()
    except Exception as e:  # индикаторы не критичны — не валим обработку DM
        return {"error": str(e)}


async def get_user_profile(igsid: str) -> dict:
    """Резолвим профиль гостя по IGSID (имя/username), чтобы менеджер видел человека, а не номер."""
    if not config.IG_ACCESS_TOKEN:
        return {}
    url = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{igsid}"
    params = {"fields": "name,username", "access_token": config.IG_ACCESS_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}
