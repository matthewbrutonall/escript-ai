#!/usr/bin/env python3
"""Write django.po/.mo for Phase-1 homepage languages. No network."""
import datetime
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _homepage_catalog import STRINGS
from _chrome_catalog import CHROME
from _new_langs import EXTRA

def _merge(table):
    out = {}
    for msgid, langs in table.items():
        extra = EXTRA.get(msgid, {})
        merged = {**langs, **extra}
        out[msgid] = merged
    return out

STRINGS = _merge({**STRINGS, **CHROME})

LANGS = ("ar", "hi", "pl", "it", "es", "pt", "fr", "de", "ur", "tr", "te")
ROOT = Path(__file__).resolve().parent


def po_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_po(lang: str, path: Path) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M+0000")
    lines = [
        'msgid ""',
        'msgstr ""',
        f'"Project-Id-Version: Escript AI\\n"',
        f'"POT-Creation-Date: {now}\\n"',
        f'"Language: {lang}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for msgid, table in STRINGS.items():
        msgstr = table[lang]
        lines.append(f'msgid "{po_escape(msgid)}"')
        lines.append(f'msgstr "{po_escape(msgstr)}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_mo(po_entries: list, path: Path) -> None:
    # Unique ids; encode utf-8
    ids = []
    strs = []
    for msgid, msgstr in po_entries:
        ids.append(msgid.encode("utf-8"))
        strs.append(msgstr.encode("utf-8"))
    # header empty msgid
    keys = [b""] + ids
    vals = [
        b"Content-Type: text/plain; charset=UTF-8\n"
    ] + strs
    n = len(keys)
    header_size = 28
    k_off = header_size
    v_off = k_off + 8 * n
    extra = v_off + 8 * n
    k_index = []
    v_index = []
    blob = b""
    for k in keys:
        k_index.append((len(k), extra + len(blob)))
        blob += k + b"\0"
    for v in vals:
        v_index.append((len(v), extra + len(blob)))
        blob += v + b"\0"
    out = struct.pack("<Iiiiiii", 0x950412DE, 0, n, k_off, v_off, 0, 0)
    for length, offset in k_index:
        out += struct.pack("<II", length, offset)
    for length, offset in v_index:
        out += struct.pack("<II", length, offset)
    out += blob
    path.write_bytes(out)


def main() -> None:
    for lang in LANGS:
        d = ROOT / lang / "LC_MESSAGES"
        d.mkdir(parents=True, exist_ok=True)
        po = d / "django.po"
        mo = d / "django.mo"
        write_po(lang, po)
        entries = [(k, STRINGS[k][lang]) for k in STRINGS]
        write_mo(entries, mo)
        print(f"wrote {po.relative_to(ROOT.parent)} {mo.name}")


if __name__ == "__main__":
    main()
