# cppref

Search cppreference.com for standard library symbols.

## Installation

**Suggested:** `pipx install .`

## Usage

Generate/update the index (requires network access):

```bash
$ cppref index
```

Search by symbol:

```bash
$ cppref search vector
```

Interactive fuzzy search:

```bash
$ cppref search
```

## Browser selection

Set `CPPREF_BROWSER` to override the opener command (for example `firefox`, `chromium`, or `open`).
Otherwise the tool falls back to `xdg-open`.
