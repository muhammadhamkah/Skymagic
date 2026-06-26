"""Minimal, dependency-free protobuf wire-format decoder.

MEXC's public spot websocket pushes protobuf frames (channels ending in
`.pb`). Rather than require `protoc`/generated classes, this walks the raw
wire format into a nested dict so we can pull out the bid/ask fields — and,
importantly, so `ws_collect.py --raw` can PRINT the decoded tree and let you
confirm the field numbers against live data (the schema can shift, and this
was written without the ability to test against the live endpoint).

Wire format reference: https://protobuf.dev/programming-guides/encoding/
  tag = (field_number << 3) | wire_type
  wire types: 0 varint | 1 64-bit | 2 length-delimited | 5 32-bit
"""

from __future__ import annotations


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def decode(buf: bytes, max_depth: int = 6) -> dict:
    """Decode protobuf bytes into {field_number: [values...]}.

    Length-delimited fields are returned as raw bytes AND, when they parse
    cleanly as a sub-message, as a nested dict under the same field number's
    list. Strings are surfaced via the helper as_str().
    """
    out: dict[int, list] = {}
    i = 0
    n = len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except IndexError:
            break
        field = tag >> 3
        wire = tag & 0x07
        if wire == 0:  # varint
            val, i = _read_varint(buf, i)
        elif wire == 1:  # 64-bit
            val = buf[i : i + 8]
            i += 8
        elif wire == 2:  # length-delimited
            length, i = _read_varint(buf, i)
            raw = buf[i : i + length]
            i += length
            val = raw  # keep raw; caller decides string vs sub-message
        elif wire == 5:  # 32-bit
            val = buf[i : i + 4]
            i += 4
        else:
            break  # unknown wire type; stop to avoid garbage
        out.setdefault(field, []).append(val)
    return out


def as_str(v) -> str | None:
    """Best-effort UTF-8 decode of a length-delimited value."""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def tree(buf: bytes, depth: int = 0, max_depth: int = 6) -> dict:
    """Recursive, human-readable view for --raw inspection.

    Each length-delimited field is shown both as a decoded string (if it looks
    like text) and as a nested sub-message (if it parses). You use this to find
    which field holds symbol / bid / ask on the live feed.
    """
    d = decode(buf)
    view: dict = {}
    for field, vals in d.items():
        rendered = []
        for v in vals:
            if isinstance(v, bytes):
                s = as_str(v)
                # Printable UTF-8 text is almost always a real string field
                # (symbol, price). Only try to recurse when it does NOT look
                # like text, so we don't render bogus sub-messages of "BTCUSDT".
                if s is not None and s.isprintable():
                    rendered.append({"str": s})
                elif depth < max_depth and v:
                    rendered.append({"msg": tree(v, depth + 1, max_depth)})
                else:
                    rendered.append({"bytes": v.hex()})
            else:
                rendered.append(v)
        view[field] = rendered if len(rendered) > 1 else rendered[0]
    return view
