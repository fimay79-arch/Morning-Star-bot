"""
Morning Star — Telegram-бот: заявки, жалобы, новости, админ-панель.
Стек: Python 3.10+, aiogram 3.x, aiosqlite. Запуск: python bot.py
"""
import asyncio
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)


# ───────────────────────── Конфигурация ─────────────────────────
def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_IDS = {int(x) for x in re.findall(r"\d+", os.environ.get("OWNER_ID", ""))}
TZ = timezone(timedelta(hours=int(os.environ.get("TZ_OFFSET", "3"))))
DB_PATH = os.environ.get("DB_PATH", "morning_star.db")
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner.jpg")
BANNER_ID = None  # кэш file_id баннера
DIV = "━━━━━━━━━━━━━━"
LANG = "ru"  # язык по умолчанию; новые языки добавляются в TEXTS

APP_LIMIT_PER_DAY = 3
CMP_LIMIT_PER_DAY = 5
MAX_FILES = 10

POSITIONS = ["Хелпер", "Билдер", "Ивентолог", "Модератор", "Стример / ютубер"]
QUESTIONS = [
    "Сколько вам лет?",
    "Ваш часовой пояс и сколько часов в день вы готовы уделять серверу?",
    "Расскажите о себе и своём опыте игры на Minecraft-серверах.",
    "Был ли у вас опыт на этой должности? Где и как долго?",
    "Почему вы хотите занять эту должность?",
    "Как вы поступите, если заметите нарушение правил другим игроком?",
]
EXTRA_QUESTIONS = {
    "Билдер": ["Опишите ваши лучшие постройки или пришлите ссылки на скриншоты."],
    "Ивентолог": ["Опишите идею ивента, который вы бы провели на сервере."],
}
CATEGORIES = ["На игрока", "На администрацию", "Нарушение правил", "Обращение к руководству"]
TARGET_CATEGORIES = (0, 1)

APP_ST = {"new": "🕓 Новая", "rework": "✏️ Требует доработки", "interview": "🎤 Собеседование назначено",
          "approved": "✅ Одобрена", "rejected": "❌ Отклонена"}
ACTIVE_APP = ("new", "rework", "interview")
CMP_ST = {"new": "🕓 Новая", "review": "🔎 На рассмотрении", "need_info": "📎 Запрошены материалы",
          "resolved": "✅ Решена", "rejected": "❌ Отклонена"}
FINAL_CMP = ("resolved", "rejected")

ALL_PERMS = {"apps": "Заявки", "complaints": "Жалобы", "news": "Новости", "broadcast": "Рассылка",
             "search": "Поиск", "logs": "Журнал", "cleanup": "Очистка данных"}
ROLES = {
    "management": ("Руководство", set(ALL_PERMS) - {"cleanup"}),
    "chief": ("Главная администрация", {"apps", "search"}),
    "reviewer": ("Проверяющий", set()),
}

TEXTS = {
    "ru": {
        "welcome": "<b>MORNING STAR</b>\n━━━━━━━━━━━━━━\nОфициальный бот сервера.\nВыберите раздел ниже.",
        "help": ("<b>ПОМОЩЬ</b>\n━━━━━━━━━━━━━━\n/start — главное меню\n/profile — профиль\n/application — подать заявку\n"
                 "/complaint — отправить жалобу\n/news — новости\n/status — статус заявок и жалоб\n"
                 "/help — эта справка\n\nВсе решения принимает администрация сервера вручную. "
                 "Об изменении статуса вы получите уведомление."),
        "ask_nick": "Введите ваш игровой ник Minecraft (3–16 символов: латиница, цифры, _).",
        "bad_nick": "Некорректный ник. Допустимы латиница, цифры и _, длина 3–16 символов.",
        "no_nick": "Для подачи заявки укажите игровой ник.",
        "back": "← Назад", "menu": "🏠 Главное меню", "cancel": "✖ Отмена",
        "b_profile": "👤 Профиль", "b_app": "📝 Подать заявку", "b_cmp": "⚠️ Жалоба", "b_news": "📰 Новости",
        "b_status": "📋 Статус", "b_help": "❓ Помощь", "b_admin": "🔒 Админ-панель",
        "cancelled": "Действие отменено.",
    }
}


def t(key, **kw):
    s = TEXTS.get(LANG, TEXTS["ru"]).get(key) or TEXTS["ru"][key]
    return s.format(**kw) if kw else s


# ───────────────────────── Утилиты ─────────────────────────
FMT = "%Y-%m-%d %H:%M:%S"
DB: aiosqlite.Connection
TASKS = set()


def now():
    return datetime.now(TZ).strftime(FMT)


def ago(**kw):
    return (datetime.now(TZ) - timedelta(**kw)).strftime(FMT)


def e(s):
    return html.escape(str(s if s is not None else ""))


def clamp(s, n=4000):
    return s if len(s) <= n else s[: n - 1] + "…"


def anum(i):
    return f"З{i}"


def cnum(i):
    return f"Ж{i}"


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=a, callback_data=b) for a, b in row] for row in rows])


async def fetch(sql, *a):
    cur = await DB.execute(sql, a)
    return await cur.fetchall()


async def fetch1(sql, *a):
    cur = await DB.execute(sql, a)
    return await cur.fetchone()


async def run(sql, *a):
    cur = await DB.execute(sql, a)
    await DB.commit()
    return cur.lastrowid


async def show(ev, text, markup=None):
    text = clamp(text)
    if isinstance(ev, CallbackQuery):
        if ev.message.photo:
            try:
                await ev.message.delete()
            except Exception:
                pass
            await ev.message.answer(text, reply_markup=markup)
            return
        try:
            await ev.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as ex:
            if "not modified" in str(ex):
                return
        except Exception:
            pass
        await ev.message.answer(text, reply_markup=markup)
    else:
        await ev.answer(text, reply_markup=markup)


async def safe_send(bot, uid, text, markup=None):
    try:
        await bot.send_message(uid, clamp(text), reply_markup=markup)
        return True
    except Exception:
        return False


async def send_file(bot, uid, ftype, file_id, caption=None):
    fn = {"photo": bot.send_photo, "video": bot.send_video}.get(ftype, bot.send_document)
    key = {"photo": "photo", "video": "video"}.get(ftype, "document")
    await fn(uid, **{key: file_id}, caption=caption)


async def init_db():
    global DB
    DB = await aiosqlite.connect(DB_PATH)
    DB.row_factory = aiosqlite.Row
    await DB.executescript("""
    CREATE TABLE IF NOT EXISTS users(tg_id INTEGER PRIMARY KEY, username TEXT, mc_nick TEXT,
        lang TEXT DEFAULT 'ru', registered_at TEXT);
    CREATE TABLE IF NOT EXISTS admins(tg_id INTEGER PRIMARY KEY, role TEXT, perms TEXT,
        added_by INTEGER, added_at TEXT);
    CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        position TEXT, answers TEXT, status TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS complaints(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        category TEXT, target TEXT, text TEXT, hash TEXT, status TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_id INTEGER,
        file_id TEXT, ftype TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT, ref_id INTEGER,
        author_id INTEGER, kind TEXT, text TEXT, visible INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, text TEXT,
        file_id TEXT, ftype TEXT, author_id INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, admin_id INTEGER,
        action TEXT, details TEXT);
    CREATE INDEX IF NOT EXISTS i_app_user ON applications(user_id);
    CREATE INDEX IF NOT EXISTS i_cmp_user ON complaints(user_id);
    CREATE INDEX IF NOT EXISTS i_cmp_hash ON complaints(hash);
    """)
    await DB.commit()


async def log(admin_id, action, details=""):
    await run("INSERT INTO logs(ts,admin_id,action,details) VALUES(?,?,?,?)", now(), admin_id, action, details)


# ───────────────────────── Права доступа ─────────────────────────
async def get_admin(uid):
    if uid in OWNER_IDS:
        return {"tg_id": uid, "role": "owner", "perms": set(ALL_PERMS) | {"admins"}}
    row = await fetch1("SELECT * FROM admins WHERE tg_id=?", uid)
    if not row:
        return None
    return {"tg_id": uid, "role": row["role"], "perms": set(filter(None, (row["perms"] or "").split(",")))}


async def guard(ev, perm=None):
    """Возвращает данные администратора или None (и пишет попытку в журнал)."""
    uid = ev.from_user.id
    a = await get_admin(uid)
    if a and (perm is None or perm in a["perms"]):
        return a
    data = ev.data if isinstance(ev, CallbackQuery) else (ev.text or "")
    await log(uid, "unauthorized", f"Попытка доступа [{perm or 'admin'}]: {data[:100]}")
    return None


def role_name(role):
    return "Владелец" if role == "owner" else ROLES.get(role, (role,))[0]


async def staff_ids(perm):
    ids = set(OWNER_IDS)
    for row in await fetch("SELECT tg_id, perms FROM admins"):
        if perm in (row["perms"] or "").split(","):
            ids.add(row["tg_id"])
    return ids


async def notify_staff(bot, perm, text, markup=None):
    for uid in await staff_ids(perm):
        await safe_send(bot, uid, text, markup)


# ───────────────────────── FSM ─────────────────────────
class S(StatesGroup):
    nick = State()
    app_q = State()
    app_fix = State()
    cmp_target = State()
    cmp_text = State()
    cmp_files = State()
    cmp_add = State()
    a_text = State()
    news_title = State()
    news_text = State()
    news_media = State()
    bc = State()
    search = State()
    ad_id = State()


class Base(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = event.from_user
        if u and not u.is_bot:
            await DB.execute(
                "INSERT INTO users(tg_id,username,registered_at) VALUES(?,?,?) "
                "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username",
                (u.id, u.username or "", now()))
            await DB.commit()
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except Exception:
                pass
        return await handler(event, data)


rt = Router()
rt.message.filter(F.chat.type == "private")


# ───────────────────────── Главное меню ─────────────────────────
async def main_menu(uid):
    rows = [[(t("b_profile"), "profile"), (t("b_app"), "app")],
            [(t("b_cmp"), "cmp"), (t("b_news"), "news")],
            [(t("b_status"), "status"), (t("b_help"), "help")]]
    if await get_admin(uid):
        rows.append([(t("b_admin"), "adm")])
    return kb(rows)


async def send_menu(ev):
    """Главное меню с баннером (если файл banner.jpg на месте)."""
    global BANNER_ID
    markup = await main_menu(ev.from_user.id)
    cb_msg = ev.message if isinstance(ev, CallbackQuery) else None
    if cb_msg and cb_msg.photo:
        try:
            await cb_msg.edit_caption(caption=t("welcome"), reply_markup=markup)
            return
        except TelegramBadRequest:
            return
    if cb_msg:
        try:
            await cb_msg.delete()
        except Exception:
            pass
    target = cb_msg or ev
    if os.path.exists(BANNER_PATH):
        try:
            sent = await target.answer_photo(BANNER_ID or FSInputFile(BANNER_PATH),
                                             caption=t("welcome"), reply_markup=markup)
            BANNER_ID = sent.photo[-1].file_id
            return
        except Exception:
            BANNER_ID = None
    await target.answer(t("welcome"), reply_markup=markup)


@rt.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await send_menu(m)


@rt.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_menu(cb)


@rt.message(Command("help"))
@rt.callback_query(F.data == "help")
async def f_help(ev, state: FSMContext):
    await state.clear()
    await show(ev, t("help"), kb([[(t("menu"), "menu")]]))


@rt.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, t("cancelled"), kb([[(t("menu"), "menu")]]))


# ───────────────────────── Профиль ─────────────────────────
@rt.message(Command("profile"))
@rt.callback_query(F.data == "profile")
async def f_profile(ev, state: FSMContext):
    await state.clear()
    uid = ev.from_user.id
    u = await fetch1("SELECT * FROM users WHERE tg_id=?", uid)
    apps = await fetch("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 5", uid)
    cmps = await fetch("SELECT * FROM complaints WHERE user_id=? ORDER BY id DESC LIMIT 5", uid)
    text = (f"<b>👤 ПРОФИЛЬ</b>\n{DIV}\n"
            f"▪ Telegram ID: <code>{uid}</code>\n"
            f"▪ Username: {('@' + e(u['username'])) if u['username'] else '—'}\n"
            f"▪ Игровой ник: {e(u['mc_nick']) if u['mc_nick'] else 'не указан'}\n"
            f"▪ Регистрация: {u['registered_at'][:10]}\n\n<b>📝 ЗАЯВКИ</b>\n{DIV}\n")
    text += "\n".join(f"{anum(a['id'])} · {e(a['position'])} · {APP_ST[a['status']]}" for a in apps) or "нет"
    text += f"\n\n<b>⚠️ ЖАЛОБЫ</b>\n{DIV}\n"
    text += "\n".join(f"{cnum(c['id'])} · {e(c['category'])} · {CMP_ST[c['status']]}" for c in cmps) or "нет"
    rows = [[("Изменить ник" if u["mc_nick"] else "Указать ник", "nick")]]
    rows += [[(f"Заявка {anum(a['id'])}", f"app:view:{a['id']}")] for a in apps]
    rows += [[(f"Жалоба {cnum(c['id'])}", f"cmp:view:{c['id']}")] for c in cmps]
    rows.append([(t("menu"), "menu")])
    await show(ev, text, kb(rows))


@rt.callback_query(F.data.in_({"nick", "nick:app"}))
async def cb_nick(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S.nick)
    await state.update_data(back="app" if cb.data == "nick:app" else "profile")
    await show(cb, t("ask_nick"), kb([[(t("cancel"), "cancel")]]))


@rt.message(S.nick, F.text)
async def msg_nick(m: Message, state: FSMContext):
    nick = m.text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", nick):
        return await m.answer(t("bad_nick"))
    back = (await state.get_data()).get("back")
    await run("UPDATE users SET mc_nick=? WHERE tg_id=?", nick, m.from_user.id)
    await state.clear()
    await m.answer(f"Ник сохранён: <b>{e(nick)}</b>")
    await (f_app(m, state) if back == "app" else f_profile(m, state))


# ───────────────────────── Заявки (игрок) ─────────────────────────
@rt.message(Command("application"))
@rt.callback_query(F.data == "app")
async def f_app(ev, state: FSMContext):
    await state.clear()
    u = await fetch1("SELECT * FROM users WHERE tg_id=?", ev.from_user.id)
    if not u["mc_nick"]:
        return await show(ev, t("no_nick"), kb([[("Указать ник", "nick:app")], [(t("menu"), "menu")]]))
    rows = [[(p, f"app:pos:{i}")] for i, p in enumerate(POSITIONS)] + [[(t("menu"), "menu")]]
    await show(ev, f"<b>📝 ЗАЯВКА</b>\n{DIV}\nВыберите должность.", kb(rows))


@rt.callback_query(F.data.startswith("app:pos:"))
async def cb_app_pos(cb: CallbackQuery, state: FSMContext):
    pos = POSITIONS[int(cb.data.split(":")[2])]
    uid = cb.from_user.id
    back = kb([[(t("menu"), "menu")]])
    cnt = (await fetch1("SELECT COUNT(*) c FROM applications WHERE user_id=? AND created_at>?", uid, ago(days=1)))["c"]
    if cnt >= APP_LIMIT_PER_DAY:
        return await show(cb, f"Лимит: не более {APP_LIMIT_PER_DAY} заявок в сутки. Попробуйте позже.", back)
    act = await fetch1("SELECT id FROM applications WHERE user_id=? AND position=? AND status IN "
                       "('new','rework','interview')", uid, pos)
    if act:
        return await show(cb, f"У вас уже есть активная заявка на эту должность: {anum(act['id'])}.", back)
    qs = QUESTIONS + EXTRA_QUESTIONS.get(pos, [])
    await state.set_state(S.app_q)
    await state.update_data(pos=pos, idx=0, ans=[], qs=qs)
    await show(cb, f"<b>{e(pos)}</b>\n\nВопрос 1/{len(qs)}\n{e(qs[0])}", kb([[(t("cancel"), "cancel")]]))


@rt.message(S.app_q, F.text)
async def msg_app_q(m: Message, state: FSMContext):
    d = await state.get_data()
    txt = m.text.strip()
    if len(txt) < 2 or len(txt) > 400:
        return await m.answer("Ответ должен содержать от 2 до 400 символов.")
    d["ans"].append(txt)
    d["idx"] += 1
    await state.update_data(ans=d["ans"], idx=d["idx"])
    qs = d["qs"]
    if d["idx"] < len(qs):
        return await m.answer(f"Вопрос {d['idx'] + 1}/{len(qs)}\n{e(qs[d['idx']])}",
                              reply_markup=kb([[(t("cancel"), "cancel")]]))
    text = f"<b>Проверьте анкету — {e(d['pos'])}</b>\n\n" + "\n\n".join(
        f"<b>{e(q)}</b>\n{e(a)}" for q, a in zip(qs, d["ans"]))
    await state.set_state(None)
    await m.answer(text, reply_markup=kb([[("Отправить", "app:send"), (t("cancel"), "cancel")]]))


@rt.callback_query(F.data == "app:send")
async def cb_app_send(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("ans") or len(d["ans"]) != len(d.get("qs", [])):
        return await show(cb, "Анкета не найдена. Начните заново.", kb([[(t("menu"), "menu")]]))
    uid = cb.from_user.id
    cnt = (await fetch1("SELECT COUNT(*) c FROM applications WHERE user_id=? AND created_at>?", uid, ago(days=1)))["c"]
    act = await fetch1("SELECT id FROM applications WHERE user_id=? AND position=? AND status IN "
                       "('new','rework','interview')", uid, d["pos"])
    if cnt >= APP_LIMIT_PER_DAY or act:
        await state.clear()
        return await show(cb, "Заявка не отправлена: превышен лимит или уже есть активная заявка.",
                          kb([[(t("menu"), "menu")]]))
    answers = json.dumps(list(zip(d["qs"], d["ans"])), ensure_ascii=False)
    aid = await run("INSERT INTO applications(user_id,position,answers,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)", uid, d["pos"], answers, "new", now(), now())
    await state.clear()
    await show(cb, f"Заявка <b>{anum(aid)}</b> отправлена. О решении вы получите уведомление.",
               kb([[("Статус", "status"), (t("menu"), "menu")]]))
    await notify_staff(cb.bot, "apps", f"Новая заявка {anum(aid)}: {e(d['pos'])}",
                       kb([[("Открыть", f"adm:app:{aid}")]]))


async def events_text(ref, rid, admin):
    sql = "SELECT * FROM events WHERE ref=? AND ref_id=? " + ("" if admin else "AND visible=1 ") + "ORDER BY id DESC LIMIT 6"
    rows = list(reversed(await fetch(sql, ref, rid)))
    labels = {"comment": "Комментарий", "decision": "Решение", "request": "Запрос", "player": "Игрок"}
    out = []
    for x in rows:
        if admin:
            who = f"{labels.get(x['kind'], x['kind'])}, {x['author_id']}"
        else:
            who = "Вы" if x["kind"] == "player" else "Администрация"
        out.append(f"<i>{x['created_at'][:16]} · {who}</i>\n{e(x['text'])}")
    return "\n\n".join(out)


async def app_card(aid, admin):
    a = await fetch1("SELECT a.*, u.username, u.mc_nick FROM applications a "
                     "LEFT JOIN users u ON u.tg_id=a.user_id WHERE a.id=?", aid)
    if not a:
        return None, None
    text = (f"<b>📝 ЗАЯВКА {anum(aid)}</b>\n{DIV}\n▪ Должность: <b>{e(a['position'])}</b>\n"
            f"▪ Статус: <b>{APP_ST[a['status']]}</b>\n")
    if admin:
        text += (f"▪ Игрок: {e(a['mc_nick'])} · {('@' + e(a['username'])) if a['username'] else '—'} · "
                 f"<code>{a['user_id']}</code>\n")
    text += f"▪ Подана: {a['created_at'][:16]}\n{DIV}\n"
    text += "\n\n".join(f"<b>{e(q)}</b>\n{e(x)}" for q, x in json.loads(a["answers"]))
    ev = await events_text("app", aid, admin)
    if ev:
        text += f"\n\n{DIV}\n<b>ИСТОРИЯ</b>\n\n" + ev
    return a, text


@rt.callback_query(F.data.startswith("app:view:"))
async def cb_app_view(cb: CallbackQuery):
    aid = int(cb.data.split(":")[2])
    a, text = await app_card(aid, False)
    if not a or a["user_id"] != cb.from_user.id:
        return await show(cb, "Заявка не найдена.", kb([[(t("menu"), "menu")]]))
    rows = []
    if a["status"] == "rework":
        rows.append([("Доработать заявку", f"app:fix:{aid}")])
    rows.append([(t("back"), "status")])
    await show(cb, text, kb(rows))


@rt.callback_query(F.data.startswith("app:fix:"))
async def cb_app_fix(cb: CallbackQuery, state: FSMContext):
    aid = int(cb.data.split(":")[2])
    a = await fetch1("SELECT * FROM applications WHERE id=? AND user_id=? AND status='rework'", aid, cb.from_user.id)
    if not a:
        return await show(cb, "Заявка недоступна для доработки.", kb([[(t("menu"), "menu")]]))
    await state.set_state(S.app_fix)
    await state.update_data(id=aid)
    await show(cb, "Отправьте одним сообщением исправления и дополнения к заявке.", kb([[(t("cancel"), "cancel")]]))


@rt.message(S.app_fix, F.text)
async def msg_app_fix(m: Message, state: FSMContext):
    aid = (await state.get_data())["id"]
    await state.clear()
    await run("INSERT INTO events(ref,ref_id,author_id,kind,text,visible,created_at) VALUES('app',?,?,?,?,1,?)",
              aid, m.from_user.id, "player", m.text[:1500], now())
    await run("UPDATE applications SET status='new', updated_at=? WHERE id=?", now(), aid)
    await m.answer(f"Заявка {anum(aid)} отправлена на повторное рассмотрение.", reply_markup=kb([[(t("menu"), "menu")]]))
    await notify_staff(m.bot, "apps", f"Заявка {anum(aid)} доработана игроком.", kb([[("Открыть", f"adm:app:{aid}")]]))


# ───────────────────────── Жалобы (игрок) ─────────────────────────
@rt.message(Command("complaint"))
@rt.callback_query(F.data == "cmp")
async def f_cmp(ev, state: FSMContext):
    await state.clear()
    rows = [[(c, f"cmp:cat:{i}")] for i, c in enumerate(CATEGORIES)] + [[(t("menu"), "menu")]]
    await show(ev, f"<b>⚠️ ЖАЛОБА</b>\n{DIV}\nВыберите тип обращения.", kb(rows))


@rt.callback_query(F.data.startswith("cmp:cat:"))
async def cb_cmp_cat(cb: CallbackQuery, state: FSMContext):
    i = int(cb.data.split(":")[2])
    cnt = (await fetch1("SELECT COUNT(*) c FROM complaints WHERE user_id=? AND created_at>?",
                        cb.from_user.id, ago(days=1)))["c"]
    if cnt >= CMP_LIMIT_PER_DAY:
        return await show(cb, f"Лимит: не более {CMP_LIMIT_PER_DAY} жалоб в сутки.", kb([[(t("menu"), "menu")]]))
    await state.update_data(cat=i, target="", files=[], lg=None)
    cancel = kb([[(t("cancel"), "cancel")]])
    if i in TARGET_CATEGORIES:
        await state.set_state(S.cmp_target)
        return await show(cb, "Укажите ник игрока или администратора, на которого подаётся жалоба.", cancel)
    await state.set_state(S.cmp_text)
    await show(cb, "Опишите ситуацию подробно (до 1500 символов).", cancel)


@rt.message(S.cmp_target, F.text)
async def msg_cmp_target(m: Message, state: FSMContext):
    if len(m.text) > 64:
        return await m.answer("Слишком длинно. Укажите только ник.")
    await state.update_data(target=m.text.strip())
    await state.set_state(S.cmp_text)
    await m.answer("Опишите ситуацию подробно (до 1500 символов).", reply_markup=kb([[(t("cancel"), "cancel")]]))


@rt.message(S.cmp_text, F.text)
async def msg_cmp_text(m: Message, state: FSMContext):
    if not 10 <= len(m.text) <= 1500:
        return await m.answer("Описание должно содержать от 10 до 1500 символов.")
    await state.update_data(text=m.text.strip())
    await state.set_state(S.cmp_files)
    await m.answer(f"Прикрепите доказательства: скриншоты, видео или файлы (до {MAX_FILES}). "
                   "Когда закончите, нажмите «Отправить жалобу».",
                   reply_markup=kb([[("Отправить жалобу", "cmp:send"), (t("cancel"), "cancel")]]))


def extract_file(m: Message):
    if m.photo:
        return m.photo[-1].file_id, "photo"
    if m.video:
        return m.video.file_id, "video"
    if m.document:
        return m.document.file_id, "document"
    return None, None


@rt.message(S.cmp_files, F.photo | F.video | F.document)
async def msg_cmp_files(m: Message, state: FSMContext):
    d = await state.get_data()
    files = d["files"]
    if len(files) >= MAX_FILES:
        return await m.answer(f"Максимум {MAX_FILES} файлов.")
    fid, ft = extract_file(m)
    files.append([fid, ft])
    reply = not (m.media_group_id and m.media_group_id == d.get("lg"))
    await state.update_data(files=files, lg=m.media_group_id)
    if reply:
        await m.answer(f"Прикреплено файлов: {len(files)}",
                       reply_markup=kb([[("Отправить жалобу", "cmp:send"), (t("cancel"), "cancel")]]))


@rt.callback_query(F.data == "cmp:send")
async def cb_cmp_send(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "text" not in d:
        return await show(cb, "Черновик не найден. Начните заново.", kb([[(t("menu"), "menu")]]))
    uid = cb.from_user.id
    norm = re.sub(r"\W+", "", d["text"].lower())
    h = hashlib.sha256(f"{uid}|{d['cat']}|{d['target'].lower()}|{norm}".encode()).hexdigest()
    dup = await fetch1("SELECT id FROM complaints WHERE hash=? AND user_id=?", h, uid)
    await state.clear()
    if dup:
        return await show(cb, f"Такая жалоба уже отправлена: {cnum(dup['id'])}. Дополнить её можно в разделе «Статус».",
                          kb([[("Статус", "status"), (t("menu"), "menu")]]))
    cid = await run("INSERT INTO complaints(user_id,category,target,text,hash,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)", uid, CATEGORIES[d["cat"]], d["target"], d["text"], h, "new", now(), now())
    for fid, ft in d["files"]:
        await run("INSERT INTO files(complaint_id,file_id,ftype,created_at) VALUES(?,?,?,?)", cid, fid, ft, now())
    await show(cb, f"Жалоба принята. Номер: <b>{cnum(cid)}</b>\nО ходе рассмотрения вы получите уведомление.",
               kb([[("Статус", "status"), (t("menu"), "menu")]]))
    await notify_staff(cb.bot, "complaints", f"Новая жалоба {cnum(cid)}: {e(CATEGORIES[d['cat']])}",
                       kb([[("Открыть", f"adm:cmp:{cid}")]]))


async def cmp_card(cid, admin):
    c = await fetch1("SELECT c.*, u.username, u.mc_nick FROM complaints c "
                     "LEFT JOIN users u ON u.tg_id=c.user_id WHERE c.id=?", cid)
    if not c:
        return None, None
    nf = (await fetch1("SELECT COUNT(*) n FROM files WHERE complaint_id=?", cid))["n"]
    text = (f"<b>⚠️ ЖАЛОБА {cnum(cid)}</b>\n{DIV}\n▪ Тип: <b>{e(c['category'])}</b>\n"
            f"▪ Статус: <b>{CMP_ST[c['status']]}</b>\n")
    if admin:
        text += (f"▪ Автор: {e(c['mc_nick'])} · {('@' + e(c['username'])) if c['username'] else '—'} · "
                 f"<code>{c['user_id']}</code>\n")
    if c["target"]:
        text += f"▪ Объект жалобы: {e(c['target'])}\n"
    text += f"▪ Подана: {c['created_at'][:16]} · файлов: {nf}\n{DIV}\n{e(c['text'])}"
    ev = await events_text("cmp", cid, admin)
    if ev:
        text += f"\n\n{DIV}\n<b>ИСТОРИЯ</b>\n\n" + ev
    return c, text


@rt.callback_query(F.data.startswith("cmp:view:"))
async def cb_cmp_view(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    c, text = await cmp_card(cid, False)
    if not c or c["user_id"] != cb.from_user.id:
        return await show(cb, "Жалоба не найдена.", kb([[(t("menu"), "menu")]]))
    rows = []
    if c["status"] not in FINAL_CMP:
        rows.append([("Дополнить", f"cmp:add:{cid}")])
    rows.append([(t("back"), "status")])
    await show(cb, text, kb(rows))


@rt.callback_query(F.data.startswith("cmp:add:"))
async def cb_cmp_add(cb: CallbackQuery, state: FSMContext):
    cid = int(cb.data.split(":")[2])
    c = await fetch1("SELECT * FROM complaints WHERE id=? AND user_id=?", cid, cb.from_user.id)
    if not c or c["status"] in FINAL_CMP:
        return await show(cb, "Жалоба недоступна для дополнения.", kb([[(t("menu"), "menu")]]))
    await state.set_state(S.cmp_add)
    await state.update_data(id=cid)
    await show(cb, "Отправьте текст, скриншоты или файлы. Каждое сообщение будет добавлено к жалобе.",
               kb([[("Готово", "menu")]]))


@rt.message(S.cmp_add, F.text | F.photo | F.video | F.document)
async def msg_cmp_add(m: Message, state: FSMContext):
    cid = (await state.get_data())["id"]
    fid, ft = extract_file(m)
    if fid:
        await run("INSERT INTO files(complaint_id,file_id,ftype,created_at) VALUES(?,?,?,?)", cid, fid, ft, now())
    text = (m.text or m.caption or "").strip()
    if text or fid:
        await run("INSERT INTO events(ref,ref_id,author_id,kind,text,visible,created_at) VALUES('cmp',?,?,?,?,1,?)",
                  cid, m.from_user.id, "player", text or "Добавлен файл", now())
    c = await fetch1("SELECT status FROM complaints WHERE id=?", cid)
    if c and c["status"] == "need_info":
        await run("UPDATE complaints SET status='review', updated_at=? WHERE id=?", now(), cid)
    await m.answer("Добавлено.", reply_markup=kb([[("Готово", "menu")]]))
    await notify_staff(m.bot, "complaints", f"Жалоба {cnum(cid)} дополнена игроком.",
                       kb([[("Открыть", f"adm:cmp:{cid}")]]))


# ───────────────────────── Статус ─────────────────────────
@rt.message(Command("status"))
@rt.callback_query(F.data == "status")
async def f_status(ev, state: FSMContext):
    await state.clear()
    uid = ev.from_user.id
    apps = await fetch("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 8", uid)
    cmps = await fetch("SELECT * FROM complaints WHERE user_id=? ORDER BY id DESC LIMIT 8", uid)
    if not apps and not cmps:
        return await show(ev, "У вас пока нет заявок и жалоб.", kb([[(t("menu"), "menu")]]))
    rows = [[(f"{anum(a['id'])} · {a['position']} · {APP_ST[a['status']]}", f"app:view:{a['id']}")] for a in apps]
    rows += [[(f"{cnum(c['id'])} · {c['category']} · {CMP_ST[c['status']]}", f"cmp:view:{c['id']}")] for c in cmps]
    rows.append([(t("menu"), "menu")])
    await show(ev, f"<b>📋 СТАТУС ОБРАЩЕНИЙ</b>\n{DIV}\nВыберите заявку или жалобу.", kb(rows))


# ───────────────────────── Новости (игрок) ─────────────────────────
@rt.message(Command("news"))
@rt.callback_query(F.data == "news")
async def f_news(ev, state: FSMContext):
    await state.clear()
    rows = await fetch("SELECT id,title,created_at FROM news ORDER BY id DESC LIMIT 8")
    if not rows:
        return await show(ev, "Новостей пока нет.", kb([[(t("menu"), "menu")]]))
    btns = [[(f"{n['created_at'][:10]} · {n['title']}"[:60], f"news:view:{n['id']}")] for n in rows]
    await show(ev, f"<b>📰 НОВОСТИ</b>\n{DIV}", kb(btns + [[(t("menu"), "menu")]]))


@rt.callback_query(F.data.startswith("news:view:"))
async def cb_news_view(cb: CallbackQuery):
    n = await fetch1("SELECT * FROM news WHERE id=?", int(cb.data.split(":")[2]))
    if not n:
        return await show(cb, "Новость не найдена.", kb([[(t("back"), "news")]]))
    await send_news(cb.bot, cb.from_user.id, n, kb([[(t("back"), "news")]]))


async def send_news(bot, uid, n, markup=None):
    text = clamp(f"<b>{e(n['title'])}</b>\n{n['created_at'][:10]}\n{DIV}\n{e(n['text'])}", 1020 if n["file_id"] else 4000)
    if n["file_id"]:
        fn = bot.send_photo if n["ftype"] == "photo" else bot.send_document
        key = "photo" if n["ftype"] == "photo" else "document"
        await fn(uid, **{key: n["file_id"]}, caption=text, reply_markup=markup)
    else:
        await bot.send_message(uid, text, reply_markup=markup)


# ═════════════════════════ АДМИН-ПАНЕЛЬ ═════════════════════════
ADM_BACK = kb([[("Админ-панель", "adm")]])


@rt.message(Command("admin"))
@rt.callback_query(F.data == "adm")
async def f_adm(ev, state: FSMContext):
    await state.clear()
    a = await guard(ev)
    if not a:
        if isinstance(ev, Message):
            await ev.answer("Команда недоступна.")
        return
    items = [("apps", "Заявки", "adm:apps"), ("complaints", "Жалобы", "adm:cmps"), ("news", "Новости", "adm:news"),
             ("broadcast", "Рассылка", "adm:bc"), ("search", "Поиск", "adm:search"),
             ("logs", "Журнал", "adm:logs:all:0"), ("cleanup", "Очистка данных", "adm:clean"),
             ("admins", "Администраторы", "adm:admins")]
    btns = [(n, d) for k, n, d in items if k in a["perms"]]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)] + [[(t("menu"), "menu")]]
    await show(ev, f"<b>🔒 АДМИН-ПАНЕЛЬ</b>\n{DIV}\nРоль: <b>{role_name(a['role'])}</b>", kb(rows))


# ── подтверждение действий ──
async def ask_confirm(ev, state, pending, desc):
    await state.set_state(None)
    await state.update_data(pending=pending)
    await show(ev, f"{desc}\n\n<b>Подтвердить действие?</b>", kb([[("Подтвердить", "cf:yes"), (t("cancel"), "cf:no")]]))


@rt.callback_query(F.data == "cf:no")
async def cb_cf_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, "Действие отменено.", ADM_BACK)


@rt.callback_query(F.data == "cf:yes")
async def cb_cf_yes(cb: CallbackQuery, state: FSMContext):
    p = (await state.get_data()).get("pending")
    await state.clear()
    if not p:
        return await show(cb, "Нет действия для подтверждения.", ADM_BACK)
    a = await guard(cb, p["perm"])
    if not a:
        return await show(cb, "Недостаточно прав.", ADM_BACK)
    res = await execute(cb.bot, p, a)
    await show(cb, res, kb(p.get("back", [[("Админ-панель", "adm")]])))


async def execute(bot, p, a):
    adm = a["tg_id"]
    ty = p["type"]
    if ty == "app":
        return await set_app_status(bot, p["id"], p["status"], adm, p.get("text", ""))
    if ty == "cmp":
        return await set_cmp_status(bot, p["id"], p["status"], adm, p.get("text", ""))
    if ty == "bc":
        task = asyncio.create_task(do_broadcast(bot, adm, text=p["text"]))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
        return "Рассылка запущена. По завершении придёт отчёт."
    if ty == "admin_add":
        name, perms = ROLES[p["role"]]
        await run("INSERT OR REPLACE INTO admins(tg_id,role,perms,added_by,added_at) VALUES(?,?,?,?,?)",
                  p["uid"], p["role"], ",".join(sorted(perms)), adm, now())
        await log(adm, "admin_add", f"Добавлен {p['uid']} — {name}")
        await safe_send(bot, p["uid"], f"Вам назначена роль: {name}. Откройте /admin.")
        return f"Администратор {p['uid']} добавлен: {name}."
    if ty == "admin_role":
        name, perms = ROLES[p["role"]]
        await run("UPDATE admins SET role=?, perms=? WHERE tg_id=?", p["role"], ",".join(sorted(perms)), p["uid"])
        await log(adm, "admin_role", f"{p['uid']} → {name}, права сброшены по умолчанию")
        return f"Роль {p['uid']} изменена: {name}."
    if ty == "admin_del":
        await run("DELETE FROM admins WHERE tg_id=?", p["uid"])
        await log(adm, "admin_del", f"Удалён администратор {p['uid']}")
        await safe_send(bot, p["uid"], "Ваш доступ к админ-панели отозван.")
        return f"Администратор {p['uid']} удалён."
    if ty == "clean":
        cut = ago(days=p["days"])
        aids = [x["id"] for x in await fetch(
            "SELECT id FROM applications WHERE status IN ('approved','rejected') AND updated_at<?", cut)]
        cids = [x["id"] for x in await fetch(
            "SELECT id FROM complaints WHERE status IN ('resolved','rejected') AND updated_at<?", cut)]
        for i in aids:
            await DB.execute("DELETE FROM events WHERE ref='app' AND ref_id=?", (i,))
            await DB.execute("DELETE FROM applications WHERE id=?", (i,))
        for i in cids:
            await DB.execute("DELETE FROM events WHERE ref='cmp' AND ref_id=?", (i,))
            await DB.execute("DELETE FROM files WHERE complaint_id=?", (i,))
            await DB.execute("DELETE FROM complaints WHERE id=?", (i,))
        await DB.commit()
        await log(adm, "cleanup", f"Старше {p['days']} дн.: заявок {len(aids)}, жалоб {len(cids)}")
        return f"Удалено: заявок — {len(aids)}, жалоб — {len(cids)}."
    return "Неизвестное действие."


async def set_app_status(bot, aid, status, adm, text):
    a = await fetch1("SELECT * FROM applications WHERE id=?", aid)
    if not a:
        return "Заявка не найдена."
    await run("UPDATE applications SET status=?, updated_at=? WHERE id=?", status, now(), aid)
    await run("INSERT INTO events(ref,ref_id,author_id,kind,text,visible,created_at) VALUES('app',?,?,?,?,1,?)",
              aid, adm, "decision", text or APP_ST[status], now())
    await log(adm, "app_status", f"{anum(aid)}: {APP_ST[a['status']]} → {APP_ST[status]}. {text}"[:500])
    msg = f"Статус заявки <b>{anum(aid)}</b> ({e(a['position'])}): <b>{APP_ST[status]}</b>"
    if text:
        msg += f"\n\n{e(text)}"
    await safe_send(bot, a["user_id"], msg, kb([[("Открыть", f"app:view:{aid}")]]))
    return f"Заявка {anum(aid)}: {APP_ST[status]}. Игрок уведомлён."


async def set_cmp_status(bot, cid, status, adm, text, kind="decision"):
    c = await fetch1("SELECT * FROM complaints WHERE id=?", cid)
    if not c:
        return "Жалоба не найдена."
    await run("UPDATE complaints SET status=?, updated_at=? WHERE id=?", status, now(), cid)
    if text:
        await run("INSERT INTO events(ref,ref_id,author_id,kind,text,visible,created_at) VALUES('cmp',?,?,?,?,1,?)",
                  cid, adm, kind, text, now())
    await log(adm, "cmp_status", f"{cnum(cid)}: {CMP_ST[c['status']]} → {CMP_ST[status]}. {text}"[:500])
    msg = f"Жалоба <b>{cnum(cid)}</b>: <b>{CMP_ST[status]}</b>"
    if text:
        msg += f"\n\n{e(text)}"
    await safe_send(bot, c["user_id"], msg, kb([[("Открыть", f"cmp:view:{cid}")]]))
    return f"Жалоба {cnum(cid)}: {CMP_ST[status]}. Игрок уведомлён."


# ── заявки (админ) ──
@rt.callback_query(F.data.startswith("adm:apps"))
async def adm_apps(cb: CallbackQuery):
    if not await guard(cb, "apps"):
        return
    parts = cb.data.split(":")
    if len(parts) == 2:
        cnt = {r_["status"]: r_["c"] for r_ in await fetch("SELECT status, COUNT(*) c FROM applications GROUP BY status")}
        rows = [[(f"{name} ({cnt.get(k, 0)})", f"adm:apps:{k}")] for k, name in APP_ST.items()]
        rows += [[("История решений", "adm:apps:hist")], [("Админ-панель", "adm")]]
        return await show(cb, "<b>Заявки</b>", kb(rows))
    st = parts[2]
    if st == "hist":
        lst = await fetch("SELECT a.*, u.mc_nick FROM applications a LEFT JOIN users u ON u.tg_id=a.user_id "
                          "WHERE status IN ('approved','rejected') ORDER BY a.updated_at DESC LIMIT 15")
    else:
        lst = await fetch("SELECT a.*, u.mc_nick FROM applications a LEFT JOIN users u ON u.tg_id=a.user_id "
                          "WHERE status=? ORDER BY a.id DESC LIMIT 15", st)
    rows = [[(f"{anum(a['id'])} · {a['position']} · {a['mc_nick']}", f"adm:app:{a['id']}")] for a in lst]
    rows.append([(t("back"), "adm:apps")])
    await show(cb, "Список пуст." if not lst else "Выберите заявку:", kb(rows))


@rt.callback_query(F.data.startswith("adm:app:"))
async def adm_app_view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await guard(cb, "apps"):
        return
    aid = int(cb.data.split(":")[2])
    a, text = await app_card(aid, True)
    if not a:
        return await show(cb, "Заявка не найдена.", kb([[(t("back"), "adm:apps")]]))
    rows = [[("Принять", f"adm:a:ok:{aid}"), ("Отклонить", f"adm:a:no:{aid}")],
            [("На доработку", f"adm:a:fx:{aid}"), ("Собеседование", f"adm:a:iv:{aid}")],
            [("Комментарий", f"adm:a:cm:{aid}"), ("Игрок", f"adm:pl:{a['user_id']}")],
            [(t("back"), "adm:apps")]]
    await show(cb, text, kb(rows))


APP_ACTS = {"no": ("rejected", "Причина отказа (будет отправлена игроку):"),
            "fx": ("rework", "Что нужно доработать (будет отправлено игроку):"),
            "iv": ("interview", "Дата, время и способ проведения собеседования (будет отправлено игроку):"),
            "cm": (None, "Внутренний комментарий (игрок его не увидит):")}


@rt.callback_query(F.data.startswith("adm:a:"))
async def adm_app_act(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "apps"):
        return
    _, _, act, aid = cb.data.split(":")
    aid = int(aid)
    back = [[("К заявке", f"adm:app:{aid}")]]
    if act == "ok":
        return await ask_confirm(cb, state, {"type": "app", "id": aid, "status": "approved", "perm": "apps", "back": back},
                                 f"Одобрить заявку <b>{anum(aid)}</b>?")
    await state.set_state(S.a_text)
    await state.update_data(act=f"app_{act}", id=aid)
    await show(cb, APP_ACTS[act][1], kb([[("К заявке", f"adm:app:{aid}")]]))


# ── жалобы (админ) ──
@rt.callback_query(F.data.startswith("adm:cmps"))
async def adm_cmps(cb: CallbackQuery):
    if not await guard(cb, "complaints"):
        return
    parts = cb.data.split(":")
    if len(parts) == 2:
        cnt = {r_["status"]: r_["c"] for r_ in await fetch("SELECT status, COUNT(*) c FROM complaints GROUP BY status")}
        rows = [[(f"{name} ({cnt.get(k, 0)})", f"adm:cmps:{k}")] for k, name in CMP_ST.items()]
        return await show(cb, "<b>Жалобы</b>", kb(rows + [[("Админ-панель", "adm")]]))
    lst = await fetch("SELECT * FROM complaints WHERE status=? ORDER BY id DESC LIMIT 15", parts[2])
    rows = [[(f"{cnum(c['id'])} · {c['category']}", f"adm:cmp:{c['id']}")] for c in lst] + [[(t("back"), "adm:cmps")]]
    await show(cb, "Список пуст." if not lst else "Выберите жалобу:", kb(rows))


@rt.callback_query(F.data.startswith("adm:cmp:"))
async def adm_cmp_view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await guard(cb, "complaints"):
        return
    cid = int(cb.data.split(":")[2])
    c, text = await cmp_card(cid, True)
    if not c:
        return await show(cb, "Жалоба не найдена.", kb([[(t("back"), "adm:cmps")]]))
    rows = [[("Взять в работу", f"adm:c:take:{cid}"), ("Запросить материалы", f"adm:c:ask:{cid}")],
            [("Решена", f"adm:c:ok:{cid}"), ("Отклонить", f"adm:c:no:{cid}")],
            [("Комментарий", f"adm:c:cm:{cid}"), ("Доказательства", f"adm:c:files:{cid}")],
            [("Игрок", f"adm:pl:{c['user_id']}"), (t("back"), "adm:cmps")]]
    await show(cb, text, kb(rows))


CMP_ACTS = {"ask": "Какие материалы или пояснения нужны (будет отправлено игроку):",
            "ok": "Итог рассмотрения (будет отправлен игроку):",
            "no": "Причина отклонения (будет отправлена игроку):",
            "cm": "Внутренний комментарий (игрок его не увидит):"}


@rt.callback_query(F.data.startswith("adm:c:"))
async def adm_cmp_act(cb: CallbackQuery, state: FSMContext):
    a = await guard(cb, "complaints")
    if not a:
        return
    _, _, act, cid = cb.data.split(":")
    cid = int(cid)
    if act == "take":
        res = await set_cmp_status(cb.bot, cid, "review", a["tg_id"], "Жалоба принята в работу.")
        return await show(cb, res, kb([[("К жалобе", f"adm:cmp:{cid}")]]))
    if act == "files":
        files = await fetch("SELECT * FROM files WHERE complaint_id=? ORDER BY id LIMIT 30", cid)
        for f in files:
            try:
                await send_file(cb.bot, cb.from_user.id, f["ftype"], f["file_id"])
            except Exception:
                pass
        await log(a["tg_id"], "cmp_files", f"Просмотр доказательств {cnum(cid)}")
        return await cb.message.answer(f"Файлов: {len(files)}", reply_markup=kb([[("К жалобе", f"adm:cmp:{cid}")]]))
    await state.set_state(S.a_text)
    await state.update_data(act=f"cmp_{act}", id=cid)
    await show(cb, CMP_ACTS[act], kb([[("К жалобе", f"adm:cmp:{cid}")]]))


# ── обработка вводимого админом текста ──
@rt.message(S.a_text, F.text)
async def msg_a_text(m: Message, state: FSMContext):
    d = await state.get_data()
    act, rid = d["act"], d["id"]
    perm = "apps" if act.startswith("app") else "complaints"
    a = await guard(m, perm)
    if not a:
        await state.clear()
        return await m.answer("Недостаточно прав.")
    text = m.text.strip()[:1500]
    if act == "app_cm" or act == "cmp_cm":
        ref = "app" if act == "app_cm" else "cmp"
        await run("INSERT INTO events(ref,ref_id,author_id,kind,text,visible,created_at) VALUES(?,?,?,?,?,0,?)",
                  ref, rid, a["tg_id"], "comment", text, now())
        await log(a["tg_id"], "comment", f"{'Заявка ' + anum(rid) if ref == 'app' else 'Жалоба ' + cnum(rid)}: комментарий")
        await state.clear()
        link = f"adm:app:{rid}" if ref == "app" else f"adm:cmp:{rid}"
        return await m.answer("Комментарий сохранён.", reply_markup=kb([[("Открыть", link)]]))
    if act == "cmp_ask":
        res = await set_cmp_status(m.bot, rid, "need_info", a["tg_id"], text, kind="request")
        await state.clear()
        return await m.answer(res, reply_markup=kb([[("К жалобе", f"adm:cmp:{rid}")]]))
    if act.startswith("app_"):
        status = APP_ACTS[act[4:]][0]
        pend = {"type": "app", "id": rid, "status": status, "text": text, "perm": "apps",
                "back": [[("К заявке", f"adm:app:{rid}")]]}
        desc = f"Заявка <b>{anum(rid)}</b> → <b>{APP_ST[status]}</b>\n\n{e(text)}"
    else:
        status = "resolved" if act == "cmp_ok" else "rejected"
        pend = {"type": "cmp", "id": rid, "status": status, "text": text, "perm": "complaints",
                "back": [[("К жалобе", f"adm:cmp:{rid}")]]}
        desc = f"Жалоба <b>{cnum(rid)}</b> → <b>{CMP_ST[status]}</b>\n\n{e(text)}"
    await ask_confirm(m, state, pend, desc)


# ── новости (админ) ──
@rt.callback_query(F.data.startswith("adm:news"))
async def adm_news(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await guard(cb, "news"):
        return
    parts = cb.data.split(":")
    sub = parts[2] if len(parts) > 2 else ""
    if sub == "":
        return await show(cb, "<b>Новости</b>", kb([[("Создать новость", "adm:news:new")],
                                                    [("Опубликованные", "adm:news:arch")], [("Админ-панель", "adm")]]))
    if sub == "new":
        await state.set_state(S.news_title)
        return await show(cb, "Введите заголовок новости.", kb([[(t("cancel"), "adm:news")]]))
    if sub == "arch":
        lst = await fetch("SELECT id,title,created_at FROM news ORDER BY id DESC LIMIT 15")
        rows = [[(f"{n['created_at'][:10]} · {n['title']}"[:60], f"adm:news:view:{n['id']}")] for n in lst]
        return await show(cb, "Опубликованные новости:" if lst else "Новостей нет.", kb(rows + [[(t("back"), "adm:news")]]))
    if sub == "view":
        n = await fetch1("SELECT * FROM news WHERE id=?", int(parts[3]))
        if n:
            await send_news(cb.bot, cb.from_user.id, n, kb([[(t("back"), "adm:news:arch")]]))


@rt.message(S.news_title, F.text)
async def msg_news_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()[:120])
    await state.set_state(S.news_text)
    await m.answer("Введите текст новости.")


@rt.message(S.news_text, F.text)
async def msg_news_text(m: Message, state: FSMContext):
    await state.update_data(text=m.text.strip()[:3000])
    await state.set_state(S.news_media)
    await m.answer("Отправьте изображение или файл либо пропустите этот шаг.",
                   reply_markup=kb([[("Пропустить", "nw:skip")]]))


async def news_preview(ev, state):
    d = await state.get_data()
    await state.set_state(None)
    text = f"<b>Предпросмотр</b>\n\n<b>{e(d['title'])}</b>\n\n{e(d['text'])}\n\nВложение: {'есть' if d.get('fid') else 'нет'}"
    markup = kb([[("Опубликовать и уведомить", "np:1")], [("Опубликовать без уведомления", "np:0")],
                 [(t("cancel"), "cf:no")]])
    await show(ev, text, markup)


@rt.callback_query(F.data == "nw:skip")
async def cb_news_skip(cb: CallbackQuery, state: FSMContext):
    await news_preview(cb, state)


@rt.message(S.news_media, F.photo | F.document)
async def msg_news_media(m: Message, state: FSMContext):
    fid, ft = extract_file(m)
    await state.update_data(fid=fid, ft=ft)
    await news_preview(m, state)


@rt.callback_query(F.data.startswith("np:"))
async def cb_news_publish(cb: CallbackQuery, state: FSMContext):
    a = await guard(cb, "news")
    if not a:
        return
    d = await state.get_data()
    await state.clear()
    if "title" not in d:
        return await show(cb, "Черновик не найден.", ADM_BACK)
    nid = await run("INSERT INTO news(title,text,file_id,ftype,author_id,created_at) VALUES(?,?,?,?,?,?)",
                    d["title"], d["text"], d.get("fid"), d.get("ft"), a["tg_id"], now())
    await log(a["tg_id"], "news_publish", f"Новость №{nid}: {d['title']}")
    notify = cb.data == "np:1"
    if notify:
        n = await fetch1("SELECT * FROM news WHERE id=?", nid)
        task = asyncio.create_task(do_broadcast(cb.bot, a["tg_id"], news=n))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
    await show(cb, f"Новость опубликована{'; рассылка уведомлений запущена' if notify else ''}.", ADM_BACK)


# ── рассылка ──
async def do_broadcast(bot, adm, text=None, news=None):
    ok = fail = 0
    for u in await fetch("SELECT tg_id FROM users"):
        for _ in range(2):
            try:
                if news:
                    await send_news(bot, u["tg_id"], news)
                else:
                    await bot.send_message(u["tg_id"], text)
                ok += 1
                break
            except TelegramRetryAfter as ex:
                await asyncio.sleep(ex.retry_after + 1)
            except Exception:
                fail += 1
                break
        await asyncio.sleep(0.05)
    await log(adm, "broadcast", f"{'Новость №' + str(news['id']) if news else 'Рассылка'}: доставлено {ok}, ошибок {fail}")
    await safe_send(bot, adm, f"Рассылка завершена. Доставлено: {ok}, ошибок: {fail}.")


@rt.callback_query(F.data == "adm:bc")
async def adm_bc(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "broadcast"):
        return
    await state.set_state(S.bc)
    await show(cb, "Отправьте текст сообщения для всех зарегистрированных игроков.", kb([[(t("cancel"), "adm")]]))


@rt.message(S.bc, F.text)
async def msg_bc(m: Message, state: FSMContext):
    if not await guard(m, "broadcast"):
        return await state.clear()
    n = (await fetch1("SELECT COUNT(*) c FROM users"))["c"]
    await ask_confirm(m, state, {"type": "bc", "text": m.text[:4000], "perm": "broadcast"},
                      f"<b>Массовая рассылка</b> ({n} получателей)\n\n{e(m.text[:1500])}")


# ── поиск ──
@rt.callback_query(F.data == "adm:search")
async def adm_search(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "search"):
        return
    await state.set_state(S.search)
    await show(cb, "Введите ник, @username, Telegram ID или номер обращения (З12 — заявка, Ж7 — жалоба).",
               kb([[(t("cancel"), "adm")]]))


@rt.message(S.search, F.text)
async def msg_search(m: Message, state: FSMContext):
    a = await guard(m, "search")
    await state.clear()
    if not a:
        return
    q = m.text.strip()
    rows = []
    mm = re.fullmatch(r"([ЖжЗзCcAa])\s*-?\s*(\d+)", q)
    num = None
    if mm:
        kind = "cmp" if mm.group(1) in "ЖжCc" else "app"
        num = [(kind, int(mm.group(2)))]
    elif q.isdigit() and len(q) <= 6:
        num = [("app", int(q)), ("cmp", int(q))]
    for kind, i in num or []:
        if kind == "app" and "apps" in a["perms"] and await fetch1("SELECT 1 FROM applications WHERE id=?", i):
            rows.append([(f"Заявка {anum(i)}", f"adm:app:{i}")])
        if kind == "cmp" and "complaints" in a["perms"] and await fetch1("SELECT 1 FROM complaints WHERE id=?", i):
            rows.append([(f"Жалоба {cnum(i)}", f"adm:cmp:{i}")])
    like = f"%{q.lstrip('@')}%"
    users = await fetch("SELECT * FROM users WHERE mc_nick LIKE ? OR username LIKE ? OR CAST(tg_id AS TEXT)=? LIMIT 10",
                        like, like, q)
    rows += [[(f"{u['mc_nick'] or '—'} · @{u['username'] or '—'} · {u['tg_id']}", f"adm:pl:{u['tg_id']}")] for u in users]
    rows.append([("Админ-панель", "adm")])
    await m.answer("Результаты поиска:" if len(rows) > 1 else "Ничего не найдено.", reply_markup=kb(rows))


@rt.callback_query(F.data.startswith("adm:pl:"))
async def adm_player(cb: CallbackQuery):
    a = await guard(cb, "search")
    if not a:
        return
    uid = int(cb.data.split(":")[2])
    u = await fetch1("SELECT * FROM users WHERE tg_id=?", uid)
    if not u:
        return await show(cb, "Игрок не найден.", ADM_BACK)
    text = (f"<b>Игрок</b>\nTelegram ID: <code>{uid}</code>\nUsername: {('@' + e(u['username'])) if u['username'] else '—'}\n"
            f"Ник: {e(u['mc_nick']) or '—'}\nРегистрация: {u['registered_at']}")
    rows = []
    if "apps" in a["perms"]:
        rows += [[(f"Заявка {anum(x['id'])} · {x['position']} · {APP_ST[x['status']]}", f"adm:app:{x['id']}")]
                 for x in await fetch("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 8", uid)]
    if "complaints" in a["perms"]:
        rows += [[(f"Жалоба {cnum(x['id'])} · {CMP_ST[x['status']]}", f"adm:cmp:{x['id']}")]
                 for x in await fetch("SELECT * FROM complaints WHERE user_id=? ORDER BY id DESC LIMIT 8", uid)]
    await show(cb, text, kb(rows + [[("Админ-панель", "adm")]]))


# ── журнал ──
@rt.callback_query(F.data.startswith("adm:logs:"))
async def adm_logs(cb: CallbackQuery):
    if not await guard(cb, "logs"):
        return
    _, _, flt, off = cb.data.split(":")
    off = int(off)
    where = "WHERE action='unauthorized'" if flt == "unauth" else ""
    rows = await fetch(f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT 11 OFFSET ?", off)
    lines = [f"{x['ts'][:16]} · <code>{x['admin_id']}</code> · {e(x['action'])}\n{e(x['details'])[:150]}" for x in rows[:10]]
    nav = []
    if off > 0:
        nav.append(("◀", f"adm:logs:{flt}:{max(0, off - 10)}"))
    if len(rows) > 10:
        nav.append(("▶", f"adm:logs:{flt}:{off + 10}"))
    sw = ("Только попытки доступа", "adm:logs:unauth:0") if flt == "all" else ("Все записи", "adm:logs:all:0")
    await show(cb, "<b>Журнал действий</b>\n\n" + ("\n\n".join(lines) or "Записей нет."),
               kb([nav, [sw], [("Админ-панель", "adm")]] if nav else [[sw], [("Админ-панель", "adm")]]))


# ── очистка ──
@rt.callback_query(F.data.startswith("adm:clean"))
async def adm_clean(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "cleanup"):
        return
    parts = cb.data.split(":")
    if len(parts) == 2:
        return await show(cb, "<b>Очистка данных</b>\nУдаляются закрытые заявки и жалобы (вместе с файлами и комментариями), "
                              "не обновлявшиеся указанный срок.",
                          kb([[("Старше 90 дней", "adm:clean:90"), ("Старше 180 дней", "adm:clean:180")],
                              [("Старше 365 дней", "adm:clean:365")], [("Админ-панель", "adm")]]))
    d = int(parts[2])
    await ask_confirm(cb, state, {"type": "clean", "days": d, "perm": "cleanup"}, f"Удалить закрытые обращения старше {d} дней? Это необратимо.")


# ── администраторы (только владелец) ──
@rt.callback_query(F.data.startswith("adm:admins"))
async def adm_admins(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await guard(cb, "admins"):
        return
    lst = await fetch("SELECT * FROM admins ORDER BY added_at")
    rows = [[(f"{x['tg_id']} · {role_name(x['role'])}", f"adm:ad:{x['tg_id']}")] for x in lst]
    rows += [[("Добавить администратора", "adm:ad:add")], [("Админ-панель", "adm")]]
    await show(cb, "<b>Администраторы</b>\nВладелец задаётся в настройках бота (OWNER_ID).", kb(rows))


@rt.callback_query(F.data == "adm:ad:add")
async def adm_ad_add(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "admins"):
        return
    await state.set_state(S.ad_id)
    await show(cb, "Введите Telegram ID нового администратора (число). Узнать ID можно через @userinfobot.",
               kb([[(t("cancel"), "adm:admins")]]))


@rt.message(S.ad_id, F.text)
async def msg_ad_id(m: Message, state: FSMContext):
    if not await guard(m, "admins"):
        return await state.clear()
    if not m.text.strip().isdigit():
        return await m.answer("Нужен числовой Telegram ID.")
    uid = int(m.text.strip())
    await state.clear()
    rows = [[(v[0], f"adm:ad:new:{uid}:{k}")] for k, v in ROLES.items()] + [[(t("cancel"), "adm:admins")]]
    await m.answer(f"Выберите роль для <code>{uid}</code>:", reply_markup=kb(rows))


@rt.callback_query(F.data.startswith("adm:ad:new:"))
async def adm_ad_new(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "admins"):
        return
    _, _, _, uid, role = cb.data.split(":")
    await ask_confirm(cb, state, {"type": "admin_add", "uid": int(uid), "role": role, "perm": "admins",
                                  "back": [[("Администраторы", "adm:admins")]]},
                      f"Добавить <code>{uid}</code> с ролью «{ROLES[role][0]}»?")


@rt.callback_query(F.data.regexp(r"^adm:ad:\d+$"))
async def adm_ad_view(cb: CallbackQuery):
    if not await guard(cb, "admins"):
        return
    await render_admin(cb, int(cb.data.split(":")[2]))


async def render_admin(cb, uid):
    x = await fetch1("SELECT * FROM admins WHERE tg_id=?", uid)
    if not x:
        return await show(cb, "Администратор не найден.", kb([[(t("back"), "adm:admins")]]))
    perms = set(filter(None, (x["perms"] or "").split(",")))
    text = f"<b>Администратор</b> <code>{uid}</code>\nРоль: {role_name(x['role'])}\nДобавлен: {x['added_at'][:16]}\n\nНажмите на право, чтобы изменить."
    rows = [[(("[вкл] " if k in perms else "[выкл] ") + n, f"adm:ad:p:{uid}:{k}")] for k, n in ALL_PERMS.items()]
    rows += [[("Сменить роль", f"adm:ad:rl:{uid}"), ("Удалить", f"adm:ad:del:{uid}")], [(t("back"), "adm:admins")]]
    await show(cb, text, kb(rows))


@rt.callback_query(F.data.startswith("adm:ad:p:"))
async def adm_ad_perm(cb: CallbackQuery):
    a = await guard(cb, "admins")
    if not a:
        return
    _, _, _, uid, perm = cb.data.split(":")
    uid = int(uid)
    x = await fetch1("SELECT * FROM admins WHERE tg_id=?", uid)
    if not x or perm not in ALL_PERMS:
        return
    perms = set(filter(None, (x["perms"] or "").split(",")))
    on = perm not in perms
    perms = perms | {perm} if on else perms - {perm}
    await run("UPDATE admins SET perms=? WHERE tg_id=?", ",".join(sorted(perms)), uid)
    await log(a["tg_id"], "perm_change", f"{uid}: {ALL_PERMS[perm]} — {'выдано' if on else 'отозвано'}")
    await render_admin(cb, uid)


@rt.callback_query(F.data.startswith("adm:ad:rl:"))
async def adm_ad_rl(cb: CallbackQuery):
    if not await guard(cb, "admins"):
        return
    uid = cb.data.split(":")[3]
    rows = [[(v[0], f"adm:ad:role:{uid}:{k}")] for k, v in ROLES.items()] + [[(t("back"), f"adm:ad:{uid}")]]
    await show(cb, "Новая роль (права будут сброшены до стандартных для роли):", kb(rows))


@rt.callback_query(F.data.startswith("adm:ad:role:"))
async def adm_ad_role(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "admins"):
        return
    _, _, _, uid, role = cb.data.split(":")
    await ask_confirm(cb, state, {"type": "admin_role", "uid": int(uid), "role": role, "perm": "admins",
                                  "back": [[("Администраторы", "adm:admins")]]},
                      f"Изменить роль <code>{uid}</code> на «{ROLES[role][0]}»?")


@rt.callback_query(F.data.startswith("adm:ad:del:"))
async def adm_ad_del(cb: CallbackQuery, state: FSMContext):
    if not await guard(cb, "admins"):
        return
    uid = int(cb.data.split(":")[3])
    await ask_confirm(cb, state, {"type": "admin_del", "uid": uid, "perm": "admins",
                                  "back": [[("Администраторы", "adm:admins")]]},
                      f"Удалить администратора <code>{uid}</code>?")


# ── перехват нераспознанных команд внутри состояний ──
@rt.message(F.text.startswith("/"))
async def unknown_cmd(m: Message):
    await m.answer("Неизвестная команда. Откройте меню: /start")


# ───────────────────────── Запуск ─────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not OWNER_IDS:
        raise SystemExit("Укажите BOT_TOKEN и OWNER_ID в файле .env")
    await init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(Base())
    dp.callback_query.outer_middleware(Base())
    dp.include_router(rt)
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="profile", description="Профиль игрока"),
        BotCommand(command="application", description="Подать заявку"),
        BotCommand(command="complaint", description="Отправить жалобу"),
        BotCommand(command="news", description="Последние новости"),
        BotCommand(command="status", description="Статус заявки или жалобы"),
        BotCommand(command="help", description="Помощь"),
    ])
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
