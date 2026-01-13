from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import posixpath
from urllib.parse import urlsplit
from typing import Dict, Iterable, List, Optional
from datetime import datetime

BASE_URL = "https://cppreference.com"
SYMBOL_INDEX_URL = f"{BASE_URL}/w/cpp/symbol_index.html"
SYMBOL_INDEX_PATH = "w/cpp/symbol_index.html"
INDEX_VERSION = 1

@dataclass(frozen=True)
class IndexOption:
    label: str
    url: str


@dataclass(frozen=True)
class IndexEntry:
    symbol: str
    options: List[IndexOption]


class _SymbolIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: Optional[str] = None
        self._current_text_parts: List[str] = []
        self._pending_symbol: Optional[str] = None
        self._pending_label_base: Optional[str] = None
        self._pending_url: Optional[str] = None
        self._pending_tail_parts: List[str] = []
        self.entries: Dict[str, List[IndexOption]] = {}

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        if tag == "br":
            self._finalize_pending()
            return
        if tag != "a":
            return
        self._finalize_pending()
        href = None
        for key, value in attrs:
            if key == "href":
                href = value
                break
        if href:
            self._current_href = href
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href is None:
            if self._pending_url is None:
                return
            text = data.strip()
            if text:
                self._pending_tail_parts.append(text)
            return
        text = data.strip()
        if text:
            self._current_text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current_href is None:
            return
        raw_text = "".join(self._current_text_parts).strip()
        cleaned = _clean_symbol(raw_text)
        href = self._current_href
        self._current_href = None
        self._current_text_parts = []

        normalized = _normalize_href(href)
        if not cleaned or not normalized or normalized.startswith("w/cpp/symbol_index"):
            self._pending_symbol = None
            self._pending_label_base = None
            self._pending_url = None
            self._pending_tail_parts = []
            return

        self._pending_symbol = cleaned
        self._pending_label_base = raw_text or cleaned
        self._pending_url = normalized
        self._pending_tail_parts = []

    def close(self) -> None:
        self._finalize_pending()
        super().close()

    def _finalize_pending(self) -> None:
        if not self._pending_symbol or not self._pending_url:
            self._pending_symbol = None
            self._pending_label_base = None
            self._pending_url = None
            self._pending_tail_parts = []
            return

        tail = _normalize_tail(self._pending_tail_parts)
        label = self._pending_label_base or self._pending_symbol
        if tail:
            label = f"{label} {tail}"

        options = self.entries.setdefault(self._pending_symbol, [])
        option = IndexOption(label=label, url=self._pending_url)
        if all(existing.url != option.url or existing.label != option.label for existing in options):
            options.append(option)

        self._pending_symbol = None
        self._pending_label_base = None
        self._pending_url = None
        self._pending_tail_parts = []


def _normalize_href(href: str) -> Optional[str]:
    href = href.split("#", 1)[0].strip()
    if not href:
        return None
    if href.startswith("mailto:"):
        return None

    if href.startswith("http://") or href.startswith("https://"):
        path = urlsplit(href).path
    else:
        path = href

    if path.startswith("mwiki/"):
        return None

    if path.startswith("/"):
        normalized = posixpath.normpath(path)
    else:
        normalized = posixpath.normpath(posixpath.join("/w/cpp/", path))

    if not normalized.startswith("/w/cpp/"):
        return None

    return normalized.lstrip("/")


def _clean_symbol(symbol: str) -> str:
    if not symbol:
        return symbol
    return symbol.translate({ord("("): None, ord(")"): None, ord("<"): None, ord(">"): None}).strip()


def _normalize_tail(parts: List[str]) -> str:
    if not parts:
        return ""
    tail = " ".join(parts).strip()
    tail = tail.replace("( ", "(").replace(" )", ")")
    while "  " in tail:
        tail = tail.replace("  ", " ")
    return tail


def parse_symbol_index(html: str) -> List[IndexEntry]:
    parser = _SymbolIndexParser()
    parser.feed(html)
    parser.close()
    return [IndexEntry(symbol=k, options=v) for k, v in sorted(parser.entries.items())]


def write_index(entries: Iterable[IndexEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged: Dict[str, List[IndexOption]] = {}
    for entry in entries:
        options = merged.setdefault(entry.symbol, [])
        for option in entry.options:
            if all(existing.url != option.url or existing.label != option.label for existing in options):
                options.append(option)
    payload = {
        "index_version": INDEX_VERSION,
        "index_time": datetime.utcnow().isoformat() + "Z",
        "base_url": BASE_URL,
        "entries": [
            {
                "symbol": symbol,
                "options": [
                    {"label": option.label, "url": option.url} for option in options
                ],
            }
            for symbol, options in sorted(merged.items())
        ]
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_index(path: Path) -> List[IndexEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: List[IndexEntry] = []
    for item in data.get("entries", []):
        symbol = item.get("symbol")
        if not symbol:
            continue
        options: List[IndexOption] = []
        if "options" in item:
            for option in item.get("options") or []:
                label = option.get("label")
                url = option.get("url")
                if label and url:
                    options.append(IndexOption(label=label, url=url))
        elif "urls" in item:
            for url in item.get("urls") or []:
                if url:
                    options.append(IndexOption(label=url, url=url))
        else:
            url = item.get("url")
            if url:
                options.append(IndexOption(label=url, url=url))
        if options:
            entries.append(IndexEntry(symbol=symbol, options=options))
    return entries


def build_lookup(entries: Iterable[IndexEntry]) -> Dict[str, List[IndexOption]]:
    lookup: Dict[str, List[IndexOption]] = {}
    for entry in entries:
        lookup.setdefault(entry.symbol, entry.options)
        if entry.symbol.startswith("std::"):
            alias = entry.symbol[len("std::") :]
            lookup.setdefault(alias, entry.options)
    return lookup


def show_index_info(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Index not found at {path}. Run `cppref index` first.")
    data = path.read_text(encoding="utf-8")
    obj = json.loads(data)
    version = obj.get("index_version", "unknown")
    time = obj.get("index_time", "unknown")
    entries = obj.get("entries", [])
    print(f"Index path: {path}")
    print(f"Index version: {version}")
    print(f"Index time: {time}")
    print(f"Number of entries: {len(entries)}")