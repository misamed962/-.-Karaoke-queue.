#!/usr/bin/env python3
"""Терминальный менеджер караоке-очереди на SQLite (с цветным выводом)."""

import argparse
import random
import shlex
import sqlite3
import sys
import time
from pathlib import Path

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

    class _Dummy:
        GREEN = YELLOW = RED = CYAN = MAGENTA = ""
        RESET_ALL = ""

    Fore = _Dummy()
    Style = _Dummy()

DB_FILE = Path("karaoke.db")

_SHARP_MAJOR = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_MAJOR  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
VALID_KEYS = set(_SHARP_MAJOR) | set(_FLAT_MAJOR)
VALID_KEYS |= {k + "m" for k in _SHARP_MAJOR}
VALID_KEYS |= {k + "m" for k in _FLAT_MAJOR}


def normalize_key(k):
    """'c' -> 'C', 'am' -> 'Am', 'bb' -> 'Bb', 'A#M' -> 'A#m'."""
    k = k.strip()
    if not k:
        return ""
    result = k[0].upper() + k[1:]
    if len(result) > 1 and result[-1] == "M":
        result = result[:-1] + "m"
    return result


def keys_display():
    majors = sorted(k for k in VALID_KEYS if not k.endswith("m"))
    minors = sorted(k for k in VALID_KEYS if k.endswith("m"))
    return "мажор: " + ", ".join(majors) + " | минор: " + ", ".join(minors)


# ---------- База данных ----------

def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            artist    TEXT NOT NULL,
            song      TEXT NOT NULL,
            song_key  TEXT DEFAULT '',
            note      TEXT DEFAULT '',
            performed INTEGER DEFAULT 0,
            created   INTEGER NOT NULL
        )
    """)
    return conn


def fetch_queue(conn):
    return conn.execute("SELECT * FROM queue ORDER BY id").fetchall()


# ---------- Вывод с цветом ----------

def print_color(text, color):
    if HAS_COLOR:
        print(color + text + Style.RESET_ALL)
    else:
        print(text)


# ---------- Логика очереди ----------

def call_next(conn):
    rows = fetch_queue(conn)
    if not rows:
        print_color("Очередь пуста.", Fore.YELLOW)
        return
    pending = [r for r in rows if not r["performed"]]
    if not pending:
        print_color("Все уже спели — начинаем новый круг! 🎶", Fore.CYAN)
        with conn:
            conn.execute("UPDATE queue SET performed = 0")
        pending = fetch_queue(conn)
    entry = pending[0]
    with conn:
        conn.execute("UPDATE queue SET performed = 1 WHERE id = ?", (entry["id"],))
    key = " (тональность: " + entry["song_key"] + ")" if entry["song_key"] else ""
    msg = "🎤 Микрофон переходит к: " + entry["name"] + " — " + entry["artist"] + " — " + entry["song"] + key
    print_color(msg, Fore.GREEN)
    remaining = conn.execute(
        "SELECT name FROM queue WHERE performed = 0 ORDER BY id LIMIT 1"
    ).fetchone()
    if remaining:
        print_color("Следующий: " + remaining["name"], Fore.MAGENTA)


def add_entry(conn, name, artist, song, song_key):
    with conn:
        conn.execute(
            "INSERT INTO queue (name, artist, song, song_key, created) VALUES (?, ?, ?, ?, ?)",
            (name, artist, song, song_key, time.time()),
        )
    print_color(name + " добавлен(а) в очередь!", Fore.GREEN)


def show(conn):
    rows = fetch_queue(conn)
    if not rows:
        print_color("Очередь пуста. Кто первый смелым? 🎤", Fore.YELLOW)
        return
    print_color("\nТекущая очередь:", Fore.CYAN)
    for i, r in enumerate(rows, 1):
        mark = "✔" if r["performed"] else " "
        key = " | тональность: " + r["song_key"] if r["song_key"] else ""
        note = " | заметка: " + r["note"] if r["note"] else ""
        line = str(i) + ". [" + mark + "] " + r["name"] + " — " + r["artist"] + " — " + r["song"] + key + note
        print(line)
    print()


def _reorder(conn, ids):
    """Присваивает записям id = 1..N в порядке ids. Должно вызываться внутри транзакции."""
    conn.execute("UPDATE queue SET id = -id - 100000")
    for i, entry_id in enumerate(ids):
        conn.execute("UPDATE queue SET id = ? WHERE id = ?", (i + 1, -entry_id - 100000))


def shuffle(conn):
    rows = fetch_queue(conn)
    if len(rows) < 2:
        print_color("Перемешивать нечего.", Fore.YELLOW)
        return
    ids = [r["id"] for r in rows]
    random.shuffle(ids)
    with conn:
        _reorder(conn, ids)
    print_color("Очередь перемешана!", Fore.CYAN)


def move(conn, src, dst):
    rows = fetch_queue(conn)
    if not (1 <= src <= len(rows) and 1 <= dst <= len(rows)):
        print_color("Нет такой позиции в очереди.", Fore.RED)
        return
    ids = [r["id"] for r in rows]
    entry_id = ids.pop(src - 1)
    ids.insert(dst - 1, entry_id)
    with conn:
        _reorder(conn, ids)
    print_color("Запись перемещена на позицию " + str(dst) + ".", Fore.GREEN)


def remove(conn, pos):
    rows = fetch_queue(conn)
    if not (1 <= pos <= len(rows)):
        print_color("Нет такой позиции в очереди.", Fore.RED)
        return
    entry = rows[pos - 1]
    with conn:
        conn.execute("DELETE FROM queue WHERE id = ?", (entry["id"],))
    print_color(entry["name"] + " удалён(а) из очереди.", Fore.YELLOW)


def set_note(conn, pos, text):
    rows = fetch_queue(conn)
    if not (1 <= pos <= len(rows)):
        print_color("Нет такой позиции в очереди.", Fore.RED)
        return
    with conn:
        conn.execute("UPDATE queue SET note = ? WHERE id = ?", (text, rows[pos - 1]["id"]))
    if text:
        print_color("Заметка сохранена.", Fore.GREEN)
    else:
        print_color("Заметка очищена.", Fore.YELLOW)


def set_key(conn, pos, raw_key):
    rows = fetch_queue(conn)
    if not (1 <= pos <= len(rows)):
        print_color("Нет такой позиции в очереди.", Fore.RED)
        return
    song_key = normalize_key(raw_key)
    if song_key not in VALID_KEYS:
        print_color("Не знаю такую тональность. Доступны — " + keys_display(), Fore.RED)
        return
    with conn:
        conn.execute("UPDATE queue SET song_key = ? WHERE id = ?", (song_key, rows[pos - 1]["id"]))
    print_color("Тональность для " + rows[pos - 1]["name"] + " установлена: " + song_key, Fore.GREEN)


# ---------- Команды ----------

HELP_TEXT = """
Команды:
  add Имя Исполнитель Песня [тональность] — добавить в конец очереди
  next                                    — вызвать следующего певца (честная ротация)
  show                                    — показать очередь
  move ОТКУДА КУДА                        — переместить запись (номера позиций)
  remove НОМЕР                            — удалить из очереди
  note НОМЕР текст                        — свободная заметка (note НОМЕР "" — очистить)
  key НОМЕР ТОНАЛЬНОСТЬ                   — установить тональность (C, Am, F#m, Bb...)
  shuffle                                 — перемешать очередь
  reset                                   — сбросить круг выступлений
  help                                    — список команд
  quit / exit                             — выход

Строки с пробелами заключайте в кавычки: add "John Doe" Beatles "Hey Jude"
"""


def _no_extra(args, cmd):
    if args:
        print_color("Команда '" + cmd + "' не принимает аргументов.", Fore.RED)
        return False
    return True


def handle(conn, cmd, args):
    """Возвращает False, если пора выходить."""
    if cmd in ("quit", "exit"):
        print_color("Пока! Хорошей вечеринки 🎶", Fore.CYAN)
        return False

    if cmd == "add":
        if len(args) < 3:
            print_color("Формат: add Имя Исполнитель Песня [тональность]", Fore.RED)
        else:
            name, artist = args[0], args[1]
            if len(args) > 3 and normalize_key(args[-1]) in VALID_KEYS:
                song = " ".join(args[2:-1])
                song_key = normalize_key(args[-1])
            else:
                song = " ".join(args[2:])
                song_key = ""
            if not name or not artist or not song:
                print_color("Имя, исполнитель и песня не могут быть пустыми.", Fore.RED)
            else:
                add_entry(conn, name, artist, song, song_key)

    elif cmd in ("next", "n"):
        if _no_extra(args, cmd):
            call_next(conn)

    elif cmd in ("show", "list"):
        if _no_extra(args, cmd):
            show(conn)

    elif cmd == "move":
        if len(args) == 2 and args[0].isdigit() and args[1].isdigit():
            move(conn, int(args[0]), int(args[1]))
        else:
            print_color("Формат: move ОТКУДА КУДА", Fore.RED)

    elif cmd == "remove":
        if len(args) == 1 and args[0].isdigit():
            remove(conn, int(args[0]))
        else:
            print_color("Формат: remove НОМЕР", Fore.RED)

    elif cmd == "note":
        if len(args) >= 2 and args[0].isdigit():
            set_note(conn, int(args[0]), " ".join(args[1:]))
        else:
            print_color('Формат: note НОМЕР текст (note НОМЕР "" — очистить)', Fore.RED)

    elif cmd == "key":
        if len(args) == 2 and args[0].isdigit():
            set_key(conn, int(args[0]), args[1])
        else:
            print_color("Формат: key НОМЕР ТОНАЛЬНОСТЬ", Fore.RED)

    elif cmd == "shuffle":
        if _no_extra(args, cmd):
            shuffle(conn)

    elif cmd == "reset":
        if _no_extra(args, cmd):
            with conn:
                conn.execute("UPDATE queue SET performed = 0")
            print_color("Круг выступлений сброшен.", Fore.CYAN)

    elif cmd == "help":
        if _no_extra(args, cmd):
            print(HELP_TEXT)

    else:
        print_color("Неизвестная команда. Введите help.", Fore.RED)
    return True


# ---------- Точки входа ----------

def run_interactive():
    conn = connect()
    print_color("=== Караоке-менеджер очереди ===  (help — список команд)", Fore.CYAN)
    try:
        while True:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print_color("\nПока! Хорошей вечеринки 🎶", Fore.CYAN)
                break
            if not raw:
                continue
            try:
                parts = shlex.split(raw)
            except ValueError as e:
                print_color("Ошибка разбора строки: " + str(e), Fore.RED)
                continue
            if not parts:
                continue
            if not handle(conn, parts[0].lower(), parts[1:]):
                break
    finally:
        conn.close()


def run_oneshot():
    parser = argparse.ArgumentParser(description="Менеджер караоке-очереди")
    parser.add_argument(
        "command",
        choices=["add", "next", "n", "show", "list", "move", "remove",
                 "note", "key", "shuffle", "reset", "help"],
    )
    parser.add_argument("args", nargs="*", help="аргументы команды")
    ns = parser.parse_args()
    conn = connect()
    try:
        handle(conn, ns.command, ns.args)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_oneshot()
    else:
        run_interactive()
