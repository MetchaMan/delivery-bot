import os
import re
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_FILE = "employees.json"

# Conversation states
ASK_FLOOR, ASK_ROOM = range(2)


# ─────────────────────────────────────────────────────────
# БАЗА СОТРУДНИКОВ
# { "батталова": {"full_name": "Батталова Лейла", "floor": 12, "room": "12.43"} }
# ─────────────────────────────────────────────────────────

def load_db() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_db(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize(name: str) -> str:
    return name.strip().lower()


# ─────────────────────────────────────────────────────────
# ПАРСИНГ ОТБИВКИ
# батталова 0835
# Батталова Лейла 0835
# батталова - 0835
# ─────────────────────────────────────────────────────────

def parse_delivery(text: str) -> list:
    results = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(
            r"^([а-яёА-ЯЁa-zA-Z][а-яёА-ЯЁa-zA-Z\s\-]+?)\s*[-–—]?\s*(\d{3,6})\s*$",
            line
        )
        if match:
            raw_name = match.group(1).strip()
            order = match.group(2).strip()
            surname = raw_name.split()[0].lower()
            results.append({
                "name": raw_name,
                "surname": surname,
                "order": order,
            })
    return results


# ─────────────────────────────────────────────────────────
# МАРШРУТ: сортировка по этажу, затем по комнате
# ─────────────────────────────────────────────────────────

def optimize_route(deliveries: list) -> list:
    def key(d):
        floor = d.get("floor", 99)
        room = d.get("room", "99.99")
        try:
            room_num = int(room.split(".")[1])
        except:
            room_num = 99
        return (floor, room_num)
    return sorted(deliveries, key=key)


def format_route(deliveries: list) -> str:
    if not deliveries:
        return "Список пуст."
    lines = ["🗺 *МАРШРУТ*\n"]
    current_floor = None
    for i, d in enumerate(deliveries, 1):
        floor = d.get("floor", "?")
        if floor != current_floor:
            if current_floor is not None:
                lines.append("")
            lines.append(f"🔼 *Этаж {floor}*")
            current_floor = floor
        lines.append(f"  {i}\\. {d['name']} — ком\\. {d['room']} \\| заказ \\#{d['order']}")
    floors_count = len(set(d.get("floor") for d in deliveries))
    lines.append(f"\n📦 Итого: {len(deliveries)} доставок, {floors_count} этажей")
    return "\n".join(lines)


def build_route_keyboard(deliveries: list) -> InlineKeyboardMarkup:
    keyboard = []
    for i, d in enumerate(deliveries):
        keyboard.append([InlineKeyboardButton(
            f"✅ {i+1}. {d['name']} · ком. {d['room']}",
            callback_data=f"done:{i}"
        )])
    keyboard.append([InlineKeyboardButton("🗑 Очистить маршрут", callback_data="done:clear")])
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────────────────────
# СЕССИЯ (хранится в user_data)
# ─────────────────────────────────────────────────────────

def get_session(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "session" not in context.user_data:
        context.user_data["session"] = {"deliveries": [], "pending": [], "current": None}
    return context.user_data["session"]

def clear_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["session"] = {"deliveries": [], "pending": [], "current": None}


# ─────────────────────────────────────────────────────────
# КОМАНДЫ
# ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет\\! Я помогаю строить маршрут доставки\\.*\n\n"
        "Просто пришли отбивку:\n"
        "`батталова 0835`\n"
        "`погудин 2397`\n"
        "`евстратов 5851`\n\n"
        "Если не знаю сотрудника — спрошу этаж и комнату, запомню навсегда\\.\n\n"
        "⚙️ *Команды:*\n"
        "/list — все сотрудники в базе\n"
        "/add — добавить сотрудника вручную\n"
        "/delete — удалить сотрудника\n"
        "/clear — очистить маршрут",
        parse_mode="MarkdownV2"
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    if not db:
        await update.message.reply_text(
            "📭 База пустая\\. Пришли отбивку — заполнится автоматически\\.",
            parse_mode="MarkdownV2"
        )
        return

    by_floor = {}
    for surname, info in db.items():
        floor = info.get("floor", "?")
        by_floor.setdefault(floor, []).append((surname, info))

    lines = ["📋 *СОТРУДНИКИ В БАЗЕ*\n"]
    for floor in sorted(by_floor.keys(), key=lambda x: (str(x) == "?", x)):
        lines.append(f"🔼 *Этаж {floor}*")
        for surname, info in sorted(by_floor[floor], key=lambda x: x[1].get("room", "")):
            full_name = info.get("full_name", surname.capitalize())
            room = info.get("room", "?")
            lines.append(f"  • {full_name} — ком\\. {room}")
        lines.append("")
    lines.append(f"_Всего: {len(db)} чел\\._")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_session(context)
    await update.message.reply_text("🗑 Маршрут очищен\\. Пришли новую отбивку\\.", parse_mode="MarkdownV2")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    if not db:
        await update.message.reply_text("База пустая.")
        return
    keyboard = []
    for surname in sorted(db.keys()):
        full_name = db[surname].get("full_name", surname.capitalize())
        room = db[surname].get("room", "?")
        keyboard.append([InlineKeyboardButton(
            f"❌ {full_name} (ком. {room})",
            callback_data=f"del:{surname}"
        )])
    keyboard.append([InlineKeyboardButton("↩️ Отмена", callback_data="del:cancel")])
    await update.message.reply_text("Кого удалить?", reply_markup=InlineKeyboardMarkup(keyboard))


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "del:cancel":
        await query.edit_message_text("Отменено.")
        return
    surname = query.data[4:]
    db = load_db()
    if surname in db:
        name = db[surname].get("full_name", surname)
        del db[surname]
        save_db(db)
        await query.edit_message_text(f"✅ {name} удалён.")
    else:
        await query.edit_message_text("Не найден.")


# ─────────────────────────────────────────────────────────
# CALLBACK: отметить доставку
# ─────────────────────────────────────────────────────────

async def cb_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "done:clear":
        clear_session(context)
        await query.edit_message_text("🗑 Маршрут очищен.")
        return

    idx = int(query.data[5:])
    session = get_session(context)
    deliveries = session.get("deliveries", [])

    if idx >= len(deliveries):
        return

    done = deliveries.pop(idx)

    if not deliveries:
        await query.edit_message_text(
            f"✅ *{done['name']}* — доставлено\\!\n\n🎉 *Все заказы выполнены\\!*",
            parse_mode="MarkdownV2"
        )
        clear_session(context)
        return

    await query.edit_message_text(
        f"✅ _{done['name']}_ доставлено\\!\n\n" + format_route(deliveries),
        parse_mode="MarkdownV2",
        reply_markup=build_route_keyboard(deliveries)
    )


# ─────────────────────────────────────────────────────────
# CONVERSATION: получение отбивки + опрос неизвестных
# ─────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parsed = parse_delivery(text)

    if not parsed:
        await update.message.reply_text(
            "Не понял формат\\. Пришли отбивку:\n\n"
            "`батталова 0835`\n`погудин 2397`",
            parse_mode="MarkdownV2"
        )
        return ConversationHandler.END

    db = load_db()
    session = get_session(context)
    session["deliveries"] = []
    session["pending"] = []
    session["current"] = None

    known, unknown = [], []
    for item in parsed:
        if item["surname"] in db:
            emp = db[item["surname"]]
            known.append({**item,
                "name": emp.get("full_name", item["name"].capitalize()),
                "floor": emp["floor"],
                "room": emp["room"],
            })
        else:
            unknown.append(item)

    session["deliveries"] = known

    if not unknown:
        route = optimize_route(known)
        session["deliveries"] = route
        await update.message.reply_text(
            format_route(route),
            parse_mode="MarkdownV2",
            reply_markup=build_route_keyboard(route)
        )
        return ConversationHandler.END

    session["pending"] = unknown[1:]
    session["current"] = unknown[0]
    item = unknown[0]
    await update.message.reply_text(
        f"❓ Не знаю *{item['name'].capitalize()}*\n\nНа каком этаже сидит? \\(введи число\\)",
        parse_mode="MarkdownV2"
    )
    return ASK_FLOOR


async def got_floor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Введи число, например: `7`", parse_mode="MarkdownV2")
        return ASK_FLOOR

    session = get_session(context)
    session["current"]["floor"] = int(text)

    await update.message.reply_text(
        f"Этаж {text} ✅\n\nТеперь комната, например: `7\\.47`",
        parse_mode="MarkdownV2"
    )
    return ASK_ROOM


async def got_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = update.message.text.strip()
    session = get_session(context)
    item = session["current"]
    item["room"] = room

    # Сохраняем в базу
    db = load_db()
    db[item["surname"]] = {
        "full_name": item["name"].capitalize(),
        "floor": item["floor"],
        "room": room,
    }
    save_db(db)

    session["deliveries"].append({
        "name": item["name"].capitalize(),
        "surname": item["surname"],
        "order": item["order"],
        "floor": item["floor"],
        "room": room,
    })

    await update.message.reply_text(
        f"✅ *{item['name'].capitalize()}* сохранён — этаж {item['floor']}, ком\\. {room}",
        parse_mode="MarkdownV2"
    )

    if session["pending"]:
        next_item = session["pending"].pop(0)
        session["current"] = next_item
        await update.message.reply_text(
            f"❓ Ещё один: *{next_item['name'].capitalize()}*\n\nЭтаж?",
            parse_mode="MarkdownV2"
        )
        return ASK_FLOOR
    else:
        route = optimize_route(session["deliveries"])
        session["deliveries"] = route
        await update.message.reply_text(
            format_route(route),
            parse_mode="MarkdownV2",
            reply_markup=build_route_keyboard(route)
        )
        return ConversationHandler.END


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введи фамилию сотрудника \\(строчными\\):\n\n`батталова`",
        parse_mode="MarkdownV2"
    )
    context.user_data["manual_add"] = True
    return ASK_FLOOR


async def got_floor_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("manual_add") and "manual_name" not in context.user_data:
        # Это ответ на имя
        name = update.message.text.strip()
        context.user_data["manual_name"] = name
        await update.message.reply_text(
            f"*{name.capitalize()}* — этаж?",
            parse_mode="MarkdownV2"
        )
        return ASK_FLOOR

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Введи число этажа:", parse_mode="MarkdownV2")
        return ASK_FLOOR
    context.user_data["manual_floor"] = int(text)
    await update.message.reply_text(f"Этаж {text} ✅\n\nКомната? \\(напр\\. `7\\.47`\\)", parse_mode="MarkdownV2")
    return ASK_ROOM


# ─────────────────────────────────────────────────────────
# СБОРКА И ЗАПУСК
# ─────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise ValueError("Установи BOT_TOKEN в переменных окружения!")

    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            CommandHandler("add", cmd_add),
        ],
        states={
            ASK_FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_floor)],
            ASK_ROOM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_room)],
        },
        fallbacks=[CommandHandler("clear", cmd_clear)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CallbackQueryHandler(cb_delete, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(cb_done, pattern=r"^done:"))
    app.add_handler(conv)

    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
