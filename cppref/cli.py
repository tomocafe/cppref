from __future__ import annotations

import argparse
import difflib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, TypeVar

from .index import BASE_URL, IndexEntry, IndexOption, SYMBOL_INDEX_URL, build_lookup, load_index, parse_symbol_index, write_index, show_index_info


def default_index_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "index.json"


def open_url(url: str) -> None:
    if url.startswith("w/"):
        url = f"{BASE_URL}/{url}"
    elif url.startswith("/w/"):
        url = f"{BASE_URL}{url}"
    browser = os.environ.get("CPPREF_BROWSER")
    if browser:
        cmd = shlex.split(browser) + [url]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise SystemExit("xdg-open not found. Set CPPREF_BROWSER to your browser/opener command.")
        cmd = [opener, url]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


def print_url(url: str) -> None:
    if url.startswith("w/"):
        url = f"{BASE_URL}/{url}"
    elif url.startswith("/w/"):
        url = f"{BASE_URL}{url}"
    print(url)


def fetch_symbol_index() -> str:
    from urllib.request import urlopen

    with urlopen(SYMBOL_INDEX_URL) as response:
        return response.read().decode("utf-8")


def run_index(args: argparse.Namespace) -> None:
    if args.status:
        show_index_info(default_index_path())
        return
    html = fetch_symbol_index()
    entries = parse_symbol_index(html)
    output_path = default_index_path()
    write_index(entries, output_path)
    print(f"Wrote {len(entries)} entries to {output_path}")


def load_entries() -> List[IndexEntry]:
    path = default_index_path()
    if not path.exists():
        raise SystemExit(f"Index not found at {path}. Run `cppref index` first.")
    return load_index(path)


def find_exact(lookup: dict, symbol: str) -> Optional[List[IndexOption]]:
    return lookup.get(symbol)


def choose_url(symbol: str, options: List[IndexOption]) -> Optional[str]:
    if len(options) == 1:
        return options[0].url
    require_tty('Multiple matches found. Run interactively to select or refine your query.')
    selected = _select_option_curses(symbol, options)
    if selected is None:
        return None
    return selected.url


def require_tty(msg="Interactive selection requires a terminal (TTY).") -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit(msg)


def prompt_for_choice(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def run_search_non_interactive(symbol: str, *, print_only: bool = False) -> None:
    entries = load_entries()
    lookup = build_lookup(entries)

    symbol = symbol.strip()
    if not symbol:
        raise SystemExit("Symbol required. Example: cppref search vector")

    while True:
        options = find_exact(lookup, symbol)
        if options:
            url = choose_url(symbol, options)
            if url:
                if print_only:
                    print_url(url)
                else:
                    open_url(url)
            return

        suggestions = difflib.get_close_matches(symbol, lookup.keys(), n=1, cutoff=0.6)
        if suggestions:
            suggestion = suggestions[0]
            require_tty('No exact match found. Run interactively to see suggested close matches.')
            choice = prompt_for_choice(
                f"No exact match for '{symbol}'. Did you mean '{suggestion}'? "
                "[o]pen suggested, [s]earch again, [q]uit: "
            )
            if choice in ("", "o", "y", "yes"):
                url = choose_url(suggestion, lookup[suggestion])
                if url:
                    open_url(url)
                return
            if choice in ("s", "search"):
                symbol = input("Symbol: ").strip()
                continue
            return

        require_tty('No match found.')
        choice = prompt_for_choice(
            f"No match for '{symbol}'. [s]earch again, [q]uit: "
        )
        if choice in ("s", "search", ""):
            symbol = input("Symbol: ").strip()
            continue
        return


def _match_score(symbol: str, query: str) -> Optional[int]:
    if not query:
        return 0
    symbol_l = symbol.lower()
    query_l = query.lower()
    idx = 0
    score = 0
    for ch in query_l:
        pos = symbol_l.find(ch, idx)
        if pos == -1:
            return None
        score += pos
        idx = pos + 1
    if query_l in symbol_l:
        score -= 10
    return score


T = TypeVar("T")


def _filter_entries(entries: Iterable[Tuple[str, T]], query: str) -> List[Tuple[str, T]]:
    scored = []
    for symbol, entry in entries:
        score = _match_score(symbol, query)
        if score is not None:
            scored.append((score, symbol, entry))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [(symbol, entry) for _, symbol, entry in scored]


def _interactive_select(entries: List[Tuple[str, IndexEntry]]) -> Optional[Tuple[str, IndexEntry]]:
    import curses

    def _inner(stdscr: "curses._CursesWindow") -> Optional[Tuple[str, IndexEntry]]:
        _setup_curses(curses)
        query = ""
        selected = 0

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            title = "cppref interactive search (type to filter, Enter to open, Esc to quit)"
            stdscr.addnstr(0, 0, title, width - 1)
            stdscr.addnstr(1, 0, f"Query: {query}", width - 1)

            matches = _filter_entries(entries, query)
            visible = matches[: max(1, height - 3)]
            if selected >= len(visible):
                selected = max(0, len(visible) - 1)

            for idx, (symbol, entry) in enumerate(visible):
                prefix = "> " if idx == selected else "  "
                suffix = f" ({len(entry.options)})" if len(entry.options) > 1 else ""
                stdscr.addnstr(2 + idx, 0, f"{prefix}{symbol}{suffix}", width - 1)

            stdscr.refresh()
            key = stdscr.getch()

            if key in (27, ):
                return None
            if key in (curses.KEY_ENTER, 10, 13):
                if visible:
                    return visible[selected]
                continue
            if key in (curses.KEY_UP,):
                selected = max(0, selected - 1)
                continue
            if key in (curses.KEY_DOWN,):
                selected = min(len(visible) - 1, selected + 1)
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                selected = 0
                continue
            if 32 <= key <= 126:
                query += chr(key)
                selected = 0

    return curses.wrapper(_inner)


def _select_option_curses(symbol: str, options: List[IndexOption]) -> Optional[IndexOption]:
    import curses

    def _inner(stdscr: "curses._CursesWindow") -> Optional[IndexOption]:
        _setup_curses(curses)
        query = ""
        selected = 0

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            title = f"Select match for {symbol} (type to filter, Enter to open, Esc to quit)"
            stdscr.addnstr(0, 0, title, width - 1)
            stdscr.addnstr(1, 0, f"Query: {query}", width - 1)

            filtered = [(option.label, option) for option in options]
            matches = _filter_entries(filtered, query)
            visible = matches[: max(1, height - 3)]
            if selected >= len(visible):
                selected = max(0, len(visible) - 1)

            for idx, (_, option) in enumerate(visible):
                prefix = "> " if idx == selected else "  "
                stdscr.addnstr(2 + idx, 0, f"{prefix}{option.label}", width - 1)

            stdscr.refresh()
            key = stdscr.getch()

            if key in (27, ):
                return None
            if key in (curses.KEY_ENTER, 10, 13):
                return visible[selected][1] if visible else None
            if key in (curses.KEY_UP,):
                selected = max(0, selected - 1)
                continue
            if key in (curses.KEY_DOWN,):
                selected = min(len(visible) - 1, selected + 1)
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                selected = 0
                continue
            if 32 <= key <= 126:
                query += chr(key)
                selected = 0

    return curses.wrapper(_inner)


def _setup_curses(curses_module) -> None:
    curses_module.curs_set(0)
    if hasattr(curses_module, "set_escdelay"):
        curses_module.set_escdelay(25)


def run_search_interactive(*, print_only: bool = False) -> None:
    require_tty('Interactive mode must be run in a terminal.')
    entries = load_entries()
    selection = _interactive_select([(entry.symbol, entry) for entry in entries])
    if not selection:
        return
    symbol, entry = selection
    url = choose_url(symbol, entry.options)
    if url:
        if print_only:
            print_url(url)
        else:
            open_url(url)


def run_search(args: argparse.Namespace) -> None:
    if args.symbol:
        run_search_non_interactive(args.symbol[0], print_only=args.print)
    else:
        run_search_interactive(print_only=args.print)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cppref", description="Search cppreference.com std symbols")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Generate the cppreference symbol index")
    index_parser.add_argument("--status", action="store_true", help="Show index status")
    index_parser.set_defaults(func=run_index)

    search_parser = subparsers.add_parser("search", help="Search for a std symbol")
    search_parser.add_argument("--print", action="store_true", help="Print the URL instead of opening it in a browser")
    search_parser.add_argument("symbol", nargs="*", help="Symbol to search (e.g., vector)")
    search_parser.set_defaults(func=run_search)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
