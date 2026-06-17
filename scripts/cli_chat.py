"""Тест агента в терминале без Instagram.

    python -m scripts.cli_chat

Эмулирует одного гостя: пиши сообщения, смотри ответы и как заполняется лид.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent, storage  # noqa: E402

USER = "cli_test_user"


def main():
    storage.init_db()
    print("KAZZHOL агент — тест в терминале. Пустая строка = выход.\n")
    while True:
        try:
            text = input("Гость: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        res = agent.generate_reply(USER, text)
        print(f"\nAI ({res.get('language')}): {res['reply_text']}")
        flags = []
        if res.get("escalate"):
            flags.append(f"⚠️ ЭСКАЛАЦИЯ: {res.get('escalation_reason', '')}")
        if res.get("_after_hours"):
            flags.append("🌙 после рабочего дня")
        meta = (f"   [intent={res.get('intent')} heat={res.get('heat')} "
                f"hotel={res.get('hotel')} status={res.get('_status')}]")
        print(meta)
        if flags:
            print("   " + " | ".join(flags))
        print()


if __name__ == "__main__":
    main()
