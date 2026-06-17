"""FastAPI: Instagram webhook + дашборд менеджера.

Запуск:  uvicorn app.main:app --reload --port 8000
Вебхук:  GET/POST  /webhook
Дашборд: GET        /
"""
import html
import time

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse

import config
from app import agent, instagram, storage

app = FastAPI(title="KAZZHOL Instagram Agent")
storage.init_db()

WINDOW_SECONDS = 24 * 3600  # окно Meta: свободно отвечать можно 24ч после сообщения гостя


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "ai": "live" if config.ANTHROPIC_API_KEY else "mock"}


# ---------- Instagram webhook ----------
@app.get("/webhook")
async def verify(request: Request):
    """Подтверждение вебхука Meta (challenge)."""
    params = request.query_params
    if (params.get("hub.mode") == "subscribe"
            and params.get("hub.verify_token") == config.IG_VERIFY_TOKEN):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/webhook")
async def incoming(request: Request):
    raw = await request.body()
    if not instagram.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        return Response(status_code=403)

    body = await request.json()
    for dm in instagram.parse_incoming(body):
        await _handle_dm(dm["sender_id"], dm["text"])
    return Response(status_code=200)


async def _handle_dm(sender_id: str, text: str):
    conv = storage.get_or_create_conversation(sender_id)

    # резолвим имя гостя один раз (чтобы менеджер видел человека, а не IGSID)
    if not conv.get("username"):
        prof = await instagram.get_user_profile(sender_id)
        uname = prof.get("username") or prof.get("name")
        if uname:
            storage.set_username(sender_id, uname)

    await instagram.send_action(sender_id, "mark_seen")

    if conv["mode"] == "human":
        # менеджер перехватил диалог — бот молчит, только логируем входящее
        storage.add_message(sender_id, "guest", text)
        return

    await instagram.send_action(sender_id, "typing_on")
    # агент синхронный (Anthropic SDK) — уводим с event loop, чтобы не блокировать вебхук
    result = await run_in_threadpool(agent.generate_reply, sender_id, text)
    await instagram.send_message(sender_id, result["reply_text"])
    # эскалация фиксируется флагом escalated на лиде (см. agent.generate_reply) —
    # бот продолжает квалифицировать, а менеджер видит ⚠️ в инбоксе и решает, перехватить ли.


# ---------- Интерактивная демка (живой usage) ----------
@app.post("/api/message")
async def api_message(request: Request):
    """Принять сообщение 'гостя', прогнать через агента, вернуть ответ + состояние лида."""
    body = await request.json()
    ig_user_id = body.get("ig_user_id", "demo_guest")
    text = (body.get("text") or "").strip()
    storage.get_or_create_conversation(ig_user_id, "demo")
    if not text:
        return {"error": "empty"}
    result = agent.generate_reply(ig_user_id, text)
    return {
        "reply": result["reply_text"],
        "language": result.get("language"),
        "intent": result.get("intent"),
        "hotel": result.get("hotel"),
        "check_in": result.get("check_in"),
        "check_out": result.get("check_out"),
        "guests": result.get("guests"),
        "purpose": result.get("purpose"),
        "heat": result.get("heat"),
        "escalate": result.get("escalate"),
        "escalation_reason": result.get("escalation_reason"),
        "summary": result.get("summary"),
        "after_hours": result.get("_after_hours"),
        "mock": not bool(__import__("config").ANTHROPIC_API_KEY),
    }


@app.post("/api/reset")
async def api_reset(request: Request):
    body = await request.json()
    storage.reset_conversation(body.get("ig_user_id", "demo_guest"))
    return {"ok": True}


@app.get("/demo", response_class=HTMLResponse)
async def demo():
    return _DEMO_HTML


# ---------- Дашборд менеджера ----------
HEAT_BADGE = {"hot": "🔥 горячий", "warm": "🟡 тёплый", "cold": "🔵 инфо"}


def _window_label(last_guest_ts) -> str:
    """Статус 24-часового окна Meta для строки инбокса."""
    if not last_guest_ts:
        return '<span class="win closed">нет сообщений</span>'
    left = WINDOW_SECONDS - (time.time() - last_guest_ts)
    if left <= 0:
        return '<span class="win closed">⛔ окно закрыто</span>'
    hrs = int(left // 3600)
    return f'<span class="win open">🟢 окно {hrs}ч</span>'


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    convs = storage.list_conversations()
    leads = storage.list_leads()
    escalated = sum(1 for l in leads if l.get("escalated"))

    # --- инбокс диалогов ---
    inbox = ""
    for c in convs:
        esc = ' <span class="tag esc">⚠️ эскалация</span>' if c.get("escalated") else ""
        mode = ' <span class="tag human">🙋 менеджер</span>' if c.get("mode") == "human" else ""
        last_who = {"guest": "гость", "agent": "AI", "manager": "вы"}.get(c.get("last_sender"), "")
        preview = html.escape((c.get("last_text") or "")[:60])
        inbox += f"""
        <tr>
          <td>{HEAT_BADGE.get(c.get('heat'), c.get('heat') or '—')}{esc}{mode}</td>
          <td><a href="/chat/{c['ig_user_id']}">{html.escape(c.get('username') or c['ig_user_id'])}</a></td>
          <td><small>{last_who}:</small> {preview}</td>
          <td>{_window_label(c.get('last_guest_ts'))}</td>
        </tr>"""

    # --- таблица лидов ---
    rows = ""
    for l in leads:
        esc = " ⚠️" if l.get("escalated") else ""
        rows += f"""
        <tr>
          <td>{HEAT_BADGE.get(l.get('heat'), l.get('heat') or '')}{esc}</td>
          <td>{html.escape(l.get('username') or l['ig_user_id'])}</td>
          <td>{html.escape(l.get('hotel') or '—')}</td>
          <td>{html.escape(l.get('intent') or '—')}</td>
          <td>{html.escape(l.get('check_in') or '—')} → {html.escape(l.get('check_out') or '—')}</td>
          <td>{html.escape(l.get('guests') or '—')}</td>
          <td>{html.escape(l.get('purpose') or '—')}</td>
          <td>{html.escape(l.get('summary') or '—')}</td>
          <td><a href="/chat/{l['ig_user_id']}">открыть</a></td>
        </tr>"""

    return _page("KAZZHOL · инбокс", f"""
      <div class=hd>
        <h1>Инбокс Instagram</h1>
        <div class=stat>Диалогов: {len(convs)} · Лидов: {len(leads)} · ⚠️ Эскалаций: {escalated}
          · <a href="/demo">демка</a></div>
      </div>
      <h2 class=sec>Диалоги</h2>
      <table>
        <tr><th>Статус</th><th>Гость</th><th>Последнее сообщение</th><th>Окно 24ч</th></tr>
        {inbox or '<tr><td colspan=4>Пока нет диалогов.</td></tr>'}
      </table>
      <h2 class=sec>Лиды (заявки)</h2>
      <table>
        <tr><th>Теплота</th><th>Гость</th><th>Отель</th><th>Запрос</th><th>Даты</th>
            <th>Гости</th><th>Цель</th><th>Саммари</th><th></th></tr>
        {rows or '<tr><td colspan=9>Пока нет лидов.</td></tr>'}
      </table>""", refresh=15)


@app.get("/chat/{ig_user_id}", response_class=HTMLResponse)
async def chat(ig_user_id: str):
    history = storage.get_history(ig_user_id, limit=100)
    conv = storage.get_or_create_conversation(ig_user_id)
    lead = storage.get_lead(ig_user_id) or {}
    bubbles = ""
    for m in history:
        who = {"guest": "Гость", "agent": "AI", "manager": "Менеджер"}.get(m["sender"], m["sender"])
        side = "right" if m["sender"] != "guest" else "left"
        bubbles += f'<div class="msg {side}"><b>{who}:</b> {html.escape(m["text"])}</div>'

    mode = conv["mode"]
    toggle = "human" if mode == "bot" else "bot"
    toggle_label = "🙋 Перехватить (выключить бота)" if mode == "bot" else "🤖 Вернуть боту"

    # 24-часовое окно Meta
    lg = storage.last_guest_ts(ig_user_id)
    left = (WINDOW_SECONDS - (time.time() - lg)) if lg else -1
    window_open = left > 0
    if not lg:
        win = '<div class="winbar closed">Гость ещё не писал — окно не открыто.</div>'
    elif window_open:
        win = f'<div class="winbar open">🟢 Окно ответа открыто · осталось ~{int(left // 3600)}ч {int((left % 3600) // 60)}м</div>'
    else:
        win = ('<div class="winbar closed">⛔ 24ч-окно закрыто. По правилам Meta свободный '
               'ответ недоступен — нужен платный message tag или новое сообщение гостя.</div>')

    disabled = "" if window_open else "disabled"
    send_hint = "" if window_open else ' title="Окно закрыто"'
    title = html.escape(conv.get("username") or ig_user_id)
    esc_banner = ('<div class="winbar closed">⚠️ Лид помечен на эскалацию: '
                  + html.escape(lead.get("summary") or "горячая заявка") + '</div>') if lead.get("escalated") else ""

    return _page("Диалог · " + title, f"""
      <a href="/">← в инбокс</a>
      <div class=hd>
        <h1>{title}</h1>
        <div class=stat>режим: <b>{mode}</b> · теплота: {HEAT_BADGE.get(lead.get('heat'), '—')}</div>
      </div>
      {esc_banner}
      {win}
      <form method="post" action="/chat/{ig_user_id}/mode" style="margin:8px 0">
        <input type="hidden" name="mode" value="{toggle}">
        <button class=ghost>{toggle_label}</button>
      </form>
      <div class="chat" id=chat>{bubbles or 'Пусто.'}</div>
      <form method="post" action="/chat/{ig_user_id}/send" class=replybar>
        <input name="text" placeholder="Ответить гостю от лица отеля…" autocomplete=off {disabled}{send_hint}>
        <button {disabled}>Отправить</button>
      </form>
      <script>var ch=document.getElementById('chat');if(ch)ch.scrollTop=ch.scrollHeight;</script>""",
      refresh=10)


@app.post("/chat/{ig_user_id}/mode")
async def set_mode(ig_user_id: str, request: Request):
    form = await request.form()
    storage.set_mode(ig_user_id, form.get("mode", "bot"))
    return Response(status_code=303, headers={"Location": f"/chat/{ig_user_id}"})


@app.post("/chat/{ig_user_id}/send")
async def manager_send(ig_user_id: str, request: Request):
    """Менеджер отвечает гостю напрямую через Instagram. Автоматически берёт диалог на себя."""
    form = await request.form()
    text = (form.get("text") or "").strip()
    if text:
        storage.set_mode(ig_user_id, "human")  # менеджер вступил — бот замолкает
        await instagram.send_action(ig_user_id, "typing_on")
        await instagram.send_message(ig_user_id, text)
        storage.add_message(ig_user_id, "manager", text)
    return Response(status_code=303, headers={"Location": f"/chat/{ig_user_id}"})


def _page(title: str, body: str, refresh: int = 0) -> str:
    meta_refresh = f'<meta http-equiv=refresh content="{refresh}">' if refresh else ""
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">{meta_refresh}<title>{title}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a;background:#fafafa}}
 h1{{font-size:20px;margin:0}} h2.sec{{font-size:14px;color:#65676b;margin:22px 0 8px;text-transform:uppercase;letter-spacing:.03em}}
 .hd{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}}
 .stat{{color:#777;font-size:13px}}
 table{{border-collapse:collapse;width:100%;background:#fff}}
 th,td{{border:1px solid #e3e3e3;padding:8px;font-size:13px;text-align:left;vertical-align:top}}
 th{{background:#f3f3f3}} a{{color:#0a58ca;text-decoration:none}}
 small{{color:#999}}
 .tag{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;margin-left:4px}}
 .tag.esc{{background:#ffe3e3;color:#c92a2a}} .tag.human{{background:#e7f5ff;color:#1971c2}}
 .win{{font-size:12px;font-weight:600}} .win.open{{color:#2b8a3e}} .win.closed{{color:#c92a2a}}
 .winbar{{padding:9px 12px;border-radius:8px;font-size:13px;margin:8px 0}}
 .winbar.open{{background:#ebfbee;color:#2b8a3e;border:1px solid #b2f2bb}}
 .winbar.closed{{background:#fff5f5;color:#c92a2a;border:1px solid #ffc9c9}}
 .chat{{max-width:680px;max-height:50vh;overflow-y:auto;padding:4px}}
 .msg{{padding:8px 12px;margin:6px 0;border-radius:10px;background:#fff;border:1px solid #eee;max-width:88%}}
 .msg.right{{background:#eef6ff;margin-left:auto}}
 button{{padding:8px 14px;border:0;border-radius:8px;background:#0a58ca;color:#fff;cursor:pointer;font-weight:600}}
 button.ghost{{background:#eee;color:#333}} button:disabled{{opacity:.45;cursor:not-allowed}}
 .replybar{{display:flex;gap:8px;max-width:680px;margin-top:10px}}
 .replybar input{{flex:1;border:1px solid #dbdbdb;border-radius:8px;padding:10px 12px;font-size:14px;outline:none}}
</style></head><body>{body}</body></html>"""


_DEMO_HTML = """<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>KAZZHOL · интерактивная демка</title>
<style>
 *{box-sizing:border-box} body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
   background:#f0f2f5;color:#15171a}
 .wrap{display:flex;gap:24px;padding:24px;max-width:1100px;margin:0 auto;flex-wrap:wrap}
 .col{flex:1;min-width:340px}
 h2{font-size:15px;margin:0 0 10px;color:#65676b;text-transform:uppercase;letter-spacing:.04em}
 /* phone */
 .phone{background:#fff;border-radius:22px;box-shadow:0 8px 30px rgba(0,0,0,.12);overflow:hidden;
   display:flex;flex-direction:column;height:600px}
 .ig-head{display:flex;align-items:center;gap:10px;padding:12px 16px;
   background:linear-gradient(90deg,#feda75,#d62976,#4f5bd5);color:#fff}
 .ig-head .av{width:34px;height:34px;border-radius:50%;background:#fff;color:#d62976;
   display:flex;align-items:center;justify-content:center;font-weight:700}
 .ig-head b{font-size:14px} .ig-head small{opacity:.85;font-size:11px;display:block}
 .feed{flex:1;overflow-y:auto;padding:14px;background:#fafafa;display:flex;flex-direction:column;gap:8px}
 .b{max-width:78%;padding:9px 13px;border-radius:18px;font-size:14px;line-height:1.35;white-space:pre-wrap}
 .b.guest{align-self:flex-end;background:#3797f0;color:#fff;border-bottom-right-radius:5px}
 .b.bot{align-self:flex-start;background:#efefef;color:#000;border-bottom-left-radius:5px}
 .b.sys{align-self:center;background:transparent;color:#9aa;font-size:11px}
 .typing{align-self:flex-start;color:#9aa;font-size:12px;padding:4px 10px}
 .compose{display:flex;gap:8px;padding:10px;border-top:1px solid #eee;background:#fff}
 .compose input{flex:1;border:1px solid #dbdbdb;border-radius:20px;padding:10px 14px;font-size:14px;outline:none}
 .compose button{border:0;background:#3797f0;color:#fff;border-radius:20px;padding:0 18px;cursor:pointer;font-weight:600}
 .chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
 .chips button{font-size:12px;background:#fff;border:1px solid #dbdbdb;color:#333;border-radius:16px;
   padding:6px 10px;cursor:pointer}
 /* manager panel */
 .mgr{background:#fff;border-radius:14px;box-shadow:0 4px 16px rgba(0,0,0,.06);padding:18px}
 .field{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #f0f0f0;font-size:14px}
 .field span:first-child{color:#888} .field b{text-align:right}
 .badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:13px;font-weight:600}
 .hot{background:#ffe3e3;color:#c92a2a} .warm{background:#fff3bf;color:#a8850b} .cold{background:#e7f5ff;color:#1971c2}
 .escal{margin-top:12px;padding:10px 12px;border-radius:10px;background:#fff5f5;border:1px solid #ffc9c9;
   color:#c92a2a;font-size:13px;display:none}
 .escal.show{display:block}
 .topbar{max-width:1100px;margin:0 auto;padding:16px 24px 0;display:flex;justify-content:space-between;align-items:center}
 .topbar a{color:#0a58ca;text-decoration:none;font-size:14px}
 .mockbar{background:#fff8e1;color:#8a6d00;font-size:12px;padding:6px 24px;text-align:center}
 .reset{background:#eee;color:#333!important;font-weight:500}
</style></head><body>
<div id=mockbar class=mockbar></div>
<div class=topbar>
  <h1 style="font-size:18px;margin:0">KAZZHOL · интерактивная демка</h1>
  <a href="/">→ Дашборд менеджера (все лиды)</a>
</div>
<div class=wrap>
  <div class=col>
    <h2>👤 Гость · Instagram Direct</h2>
    <div class=phone>
      <div class=ig-head><div class=av>K</div><div><b>kazzhol.hotel</b><small>обычно отвечает сразу</small></div></div>
      <div class=feed id=feed></div>
      <div class=compose>
        <input id=inp placeholder="Напишите как гость…" autocomplete=off>
        <button onclick=send()>›</button>
      </div>
    </div>
    <div class=chips>
      <button onclick="quick('Свободно на 12-14 июля, 2 взрослых в Алматы?')">бронь номера</button>
      <button onclick="quick('Той на 150 человек в Астане сколько стоит?')">той/банкет</button>
      <button onclick="quick('Сәлеметсіз бе, бассейн бар ма?')">казахский</button>
      <button onclick="quick('Hi, do you have a room this weekend in Almaty?')">english</button>
      <button onclick="quick('Это ужасный сервис, номер был грязный!')">жалоба</button>
      <button class=reset onclick=reset()>↺ Новый диалог</button>
    </div>
  </div>
  <div class=col>
    <h2>🧑‍💼 Что видит менеджер (в реальном времени)</h2>
    <div class=mgr>
      <div class=field><span>Теплота лида</span><b id=f_heat>—</b></div>
      <div class=field><span>Тип запроса</span><b id=f_intent>—</b></div>
      <div class=field><span>Язык гостя</span><b id=f_lang>—</b></div>
      <div class=field><span>Отель</span><b id=f_hotel>—</b></div>
      <div class=field><span>Даты</span><b id=f_dates>—</b></div>
      <div class=field><span>Гости</span><b id=f_guests>—</b></div>
      <div class=field><span>Цель</span><b id=f_purpose>—</b></div>
      <div class=field><span>Саммари для менеджера</span><b id=f_sum>—</b></div>
      <div class=escal id=escal></div>
    </div>
    <p style="color:#999;font-size:12px">Пиши в чат слева — панель обновляется на каждое сообщение. Так выглядит реальный usage: гость видит ответ, менеджер — готовую заявку.</p>
  </div>
</div>
<script>
const guestId = 'demo_' + Math.random().toString(36).slice(2,9);
const feed = document.getElementById('feed'), inp = document.getElementById('inp');
function bubble(text, cls){const d=document.createElement('div');d.className='b '+cls;d.textContent=text;feed.appendChild(d);feed.scrollTop=feed.scrollHeight;return d;}
function quick(t){inp.value=t;send();}
function setF(id,v){document.getElementById(id).textContent=v||'—';}
const HEAT={hot:['🔥 горячий','hot'],warm:['🟡 тёплый','warm'],cold:['🔵 инфо','cold']};
async function send(){
  const text=inp.value.trim(); if(!text)return; inp.value='';
  bubble(text,'guest');
  const t=document.createElement('div');t.className='typing';t.textContent='печатает…';feed.appendChild(t);feed.scrollTop=feed.scrollHeight;
  try{
    const r=await fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ig_user_id:guestId,text})});
    const d=await r.json(); t.remove();
    bubble(d.reply,'bot');
    if(d.after_hours) bubble('🌙 сейчас нерабочее время — раньше этот лид бы потерялся','sys');
    const h=HEAT[d.heat]||['—',''];
    const hb=document.getElementById('f_heat'); hb.innerHTML='<span class="badge '+h[1]+'">'+h[0]+'</span>';
    setF('f_intent',d.intent); setF('f_lang',d.language);
    setF('f_hotel',d.hotel==='unknown'?'—':d.hotel); setF('f_dates',d.check_in);
    setF('f_guests',d.guests); setF('f_purpose',d.purpose); setF('f_sum',d.summary);
    const e=document.getElementById('escal');
    if(d.escalate){e.className='escal show';e.textContent='⚠️ Эскалация менеджеру: '+(d.escalation_reason||'горячий лид');}
    else{e.className='escal';}
  }catch(err){t.remove();bubble('Ошибка соединения с сервером','sys');}
}
async function reset(){
  await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ig_user_id:guestId})});
  feed.innerHTML=''; ['f_intent','f_lang','f_hotel','f_dates','f_guests','f_purpose','f_sum'].forEach(i=>setF(i,'—'));
  document.getElementById('f_heat').textContent='—'; document.getElementById('escal').className='escal';
  bubble('Новый диалог. Напишите сообщение как гость 👋','sys');
}
inp.addEventListener('keydown',e=>{if(e.key==='Enter')send();});
fetch('/api/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ig_user_id:guestId,text:''})})
  .then(r=>r.json()).then(d=>{document.getElementById('mockbar').textContent =
    d.mock ? '⚙️ MOCK-режим (без API-ключа): ответы по правилам. Вставь ANTHROPIC_API_KEY → включится настоящий Claude.' :
             '✅ Живой Claude подключён.';});
bubble('Напишите сообщение как гость 👋 или нажмите подсказку ниже','sys');
</script></body></html>"""
