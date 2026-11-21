"""codehygiene v0.7.0 — Zero-vocabulary target byte-grid projection and slice renderer.

Projects a flagged `(L x 128)` byte grid `G` onto a `_forward`-verified green grid `G*`
via pure 2D byte-grid operations (zero keywords, ASTs, or English rule templates).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# Block 0 per-cell channel indices synchronized with compile.py (N_HC = 68)
_HC_COMM = 1
_HC_STR = 46
_WINDOW_PAD = 36
_MAX_DIRECT_LINES = 140
_SCOPE_REACH = 200
_SLICE_ROWS = 40

# Byte classes for clone factoring, which reads row bytes outside `_forward`
_WORD = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
_DIGITS = frozenset(b"0123456789")
_OPEN = frozenset(b"([{")
_CLOSE = frozenset(b")]}")
_QUOTES = frozenset(b"\"'`")
_PREFIX = _WORD | frozenset(b" *&(")
_WORD_BYTES = {bytes([byte]) for byte in _WORD}
_HELPER = b"f"
_RETURN = b"return"



@dataclass(frozen=True)
class TargetProjection:
    """Projected green byte-grid state and compact changed-slice rendering."""

    target_score: float
    target_squiggles: int
    target_slice: str


def _row_indent(row: np.ndarray) -> int:
    nonspace = np.flatnonzero((row > 32) & (row < 128))
    return int(nonspace[0]) if len(nonspace) else 0


def _row_len(row: np.ndarray) -> int:
    nonspace = np.flatnonzero(row > 32)
    return int(nonspace[-1]) + 1 if len(nonspace) else 0


def _blank_row(width: int = 128) -> np.ndarray:
    return np.zeros((width,), dtype=np.uint8)


def _synth_row(indent: int, pattern: bytes, width: int = 128) -> np.ndarray:
    out = np.zeros((width,), dtype=np.uint8)
    ind = min(max(0, indent), width - 4)
    out[:ind] = 32
    raw = np.frombuffer(pattern, dtype=np.uint8)
    take = min(len(raw), width - ind)
    out[ind : ind + take] = raw[:take]
    return out


def _shift_indent(row: np.ndarray, new_indent: int) -> np.ndarray:
    rlen = _row_len(row)
    if rlen == 0:
        return _blank_row(len(row))
    cur = _row_indent(row)
    return _synth_row(new_indent, row[cur:rlen].tobytes(), len(row))


def _kind_spans(
    lprobs: np.ndarray, kind_idx: int, thresh: float, max_gap: int = 1
) -> list[tuple[int, int]]:
    if len(lprobs) == 0:
        return []
    best_k = np.argmax(lprobs, axis=1)
    best_p = lprobs[np.arange(len(lprobs)), best_k]
    rows = [int(r) for r in np.flatnonzero((best_k == kind_idx) & (best_p >= thresh))]
    if not rows:
        return []
    spans: list[tuple[int, int]] = []
    s0 = p0 = rows[0]
    for row in rows[1:]:
        if row - p0 <= max_gap:
            p0 = row
        else:
            spans.append((s0, p0))
            s0 = p0 = row
    spans.append((s0, p0))
    return spans


def _extract_grid_features(
    hygiene: Any, params: dict[str, np.ndarray], grid: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    num_lines, width = grid.shape
    hc_dim = params["col_fuse_w"].shape[1]
    cells = np.zeros((num_lines, width, hc_dim), dtype=np.float32)
    spans = hygiene._extract_active_spans(grid)
    if spans is not None:
        act = hygiene._fuse_block0_cells(params, grid, spans)
        for idx, row in enumerate(spans.nonblank):
            start = int(spans.line_starts[idx])
            take = min(int(spans.span_lens[idx]), width)
            cells[row, :take] = act[start : start + take]
    _, _, _, raw_sk = hygiene._block01_active_lines(params, grid)
    sk = hygiene._relu(raw_sk) - hygiene._relu(raw_sk - 1.0)
    return cells, hygiene._l2_norm(sk[:, :40]), hygiene._l2_norm(sk[:, 80:120])


def _row_pair_sim(
    grid: np.ndarray, sk_code: np.ndarray, sk_skel: np.ndarray, r_a: int, r_b: int
) -> float:
    la, lb = _row_len(grid[r_a]), _row_len(grid[r_b])
    if la == 0 or lb == 0:
        return 0.0

    ia, ib = _row_indent(grid[r_a]), _row_indent(grid[r_b])
    if ia == 0 or ib == 0:
        return 0.0

    sa = bytes(grid[r_a, ia:la]).strip()
    sb = bytes(grid[r_b, ib:lb]).strip()
    if sa == sb and sa in (b"{", b"}"):
        return 1.0

    c_sim = float(np.dot(sk_code[r_a], sk_code[r_b]))
    k_sim = float(np.dot(sk_skel[r_a], sk_skel[r_b]))
    return max(c_sim, 0.45 * c_sim + 0.55 * k_sim)


def _best_clone_offset(
    grid: np.ndarray, sk_code: np.ndarray, sk_skel: np.ndarray, seed_row: int
) -> int:
    best_off, best_sim = 0, 0.0
    for cand in range(len(grid)):
        if abs(cand - seed_row) >= 4:
            sim = _row_pair_sim(grid, sk_code, sk_skel, seed_row, cand)
            if sim > best_sim:
                best_sim, best_off = sim, cand - seed_row
    return best_off if best_sim >= 0.52 else 0


def _expand_clone_bounds(
    grid: np.ndarray,
    sk_code: np.ndarray,
    sk_skel: np.ndarray,
    seed_row: int,
    offset: int,
) -> tuple[int, int]:
    n_rows = len(grid)
    max_span = abs(offset) - 1
    lo, hi = seed_row, seed_row
    for step in (-1, 1):
        while (hi - lo + 1) < max_span:
            nxt = (lo - 1) if step < 0 else (hi + 1)
            if not (0 <= nxt < n_rows and 0 <= nxt + offset < n_rows):
                break
            if _row_pair_sim(grid, sk_code, sk_skel, nxt, nxt + offset) < 0.50:
                break
            if step < 0:
                lo = nxt
            else:
                hi = nxt
    return lo, hi


def _span_sim(
    grid: np.ndarray, sk_code: np.ndarray, sk_skel: np.ndarray, lo: int, cur: int, slen: int
) -> float:
    total = 0.0
    for k in range(slen):
        total += _row_pair_sim(grid, sk_code, sk_skel, lo + k, cur + k)
    return total / slen


def _grow_clone_peers(
    grid: np.ndarray, sk_code: np.ndarray, sk_skel: np.ndarray, seed_row: int
) -> tuple[int, list[tuple[int, int]]]:
    """Return the reference clone's first row and every peer span, sorted (one length for all)."""
    best_off = _best_clone_offset(grid, sk_code, sk_skel, seed_row)
    if best_off == 0:
        return 0, []

    lo, hi = _expand_clone_bounds(grid, sk_code, sk_skel, seed_row, best_off)
    slen = hi - lo + 1
    if slen < 3:
        return 0, []

    peers = [(lo, hi)]
    cur = 0
    while cur <= len(grid) - slen:
        overlaps = any(abs(cur - pa) < slen for pa, _ in peers)
        if not overlaps and _span_sim(grid, sk_code, sk_skel, lo, cur, slen) >= 0.55:
            # Take the best-aligned start among the next few rows, not the first one past the bar.
            nearby = [
                c for c in range(cur, min(cur + 3, len(grid) - slen + 1))
                if not any(abs(c - pa) < slen for pa, _ in peers)
            ]
            cur = max(nearby, key=lambda c: _span_sim(grid, sk_code, sk_skel, lo, c, slen))
            peers.append((cur, cur + slen - 1))
            cur += slen
        else:
            cur += 1
    return (lo, sorted(peers)) if len(peers) >= 2 else (0, [])


def _shared_run(
    grid: np.ndarray, sk_code: np.ndarray, sk_skel: np.ndarray, ref: int, start: int, slen: int
) -> int:
    """Count the leading rows of the peer at `start` that match the reference clone row for row."""
    ref_ind, peer_ind = _row_indent(grid[ref]), _row_indent(grid[start])
    for k in range(slen):
        r_a, r_b = ref + k, start + k
        if _row_len(grid[r_a]) == 0 or _row_len(grid[r_b]) == 0:
            return k
        if _row_indent(grid[r_a]) - ref_ind != _row_indent(grid[r_b]) - peer_ind:
            return k
        if r_a != r_b and _row_pair_sim(grid, sk_code, sk_skel, r_a, r_b) < 0.50:
            return k
    return slen


# Clone factoring from the file's own bytes: rows, brackets, names and helper plans.


@dataclass(frozen=True)
class _Lines:
    """Row bytes (trailing blanks cut) and code-cell masks (False in strings and comments)."""

    text: list[bytes]
    code: np.ndarray

    @classmethod
    def of(cls, grid: np.ndarray, cells: np.ndarray) -> _Lines:
        code = (cells[:, :, _HC_STR] <= 0.5) & (cells[:, :, _HC_COMM] <= 0.5)
        return cls([bytes(row[: _row_len(row)]) for row in grid], code)

    def indent(self, row: int) -> int:
        return next((col for col, byte in enumerate(self.text[row]) if 32 < byte < 128), 0)

    def blank(self, row: int) -> bool:
        return not self.text[row].strip()

    def opens_block(self, row: int) -> bool:
        return not self.blank(row) and bool(self.code[row][self.indent(row)])

    def next_nonblank(self, row: int) -> int:
        return next((cur for cur in range(row + 1, len(self.text)) if not self.blank(cur)), len(self.text))

    def starts_with(self, row: int, chars: frozenset[int]) -> bool:
        text = self.text[row]
        return bool(text.strip()) and text[self.indent(row)] in chars

    def is_opener(self, row: int) -> bool:
        """True when the next nonblank row is deeper, or is a `{` at the same indent."""
        nxt = self.next_nonblank(row)
        if nxt >= len(self.text) or self.blank(row):
            return False
        return self.indent(nxt) > self.indent(row) or (
            self.indent(nxt) == self.indent(row) and self.text[nxt].strip()[:1] == b"{"
        )


@dataclass(frozen=True)
class _HelperPlan:
    """One shared helper: its rows, where it goes, and the call that replaces each clone."""

    insert_at: int
    rows: list[bytes]
    blank_after: int
    calls: dict[int, tuple[int, list[int], bytes]]


def _depths(text: bytes, code: np.ndarray, depth: int = 0) -> tuple[list[int], int]:
    """Bracket depth before each byte, ignoring strings and comments, and the depth after the row."""
    before: list[int] = []
    for col, byte in enumerate(text):
        before.append(depth)
        if code[col] and byte in _OPEN:
            depth += 1
        elif code[col] and byte in _CLOSE:
            depth -= 1
    return before, depth


def _words(text: bytes, code: np.ndarray, lo: int = 0, hi: int | None = None) -> list[tuple[int, int]]:
    """Name spans in the code cells of text[lo:hi]; numbers and attributes after `.` are skipped."""
    hi = len(text) if hi is None else hi
    spans: list[tuple[int, int]] = []
    col = lo
    while col < hi:
        if text[col] not in _WORD or not code[col]:
            col += 1
            continue
        end = col + 1
        while end < hi and text[end] in _WORD and code[end]:
            end += 1
        if text[col] not in _DIGITS and (col == 0 or text[col - 1] != 46):
            spans.append((col, end))
        col = end
    return spans


def _bound_names(text: bytes, code: np.ndarray, depth_at: list[int], eq_col: int) -> list[tuple[int, int]]:
    """Last name of each depth-0, comma-split target left of `=`, before any `:` annotation."""
    commas = [c for c in range(eq_col) if text[c] == 44 and code[c] and depth_at[c] == 0]
    names: list[tuple[int, int]] = []
    lo = 0
    for cut in (*commas, eq_col):
        colons = (c for c in range(lo, cut) if text[c] == 58 and code[c] and depth_at[c] == 0)
        hi = next(colons, cut)
        spans = [span for span in _words(text, code, lo, hi) if depth_at[span[0]] == 0]
        if spans and not text[spans[-1][1] : hi].strip():
            names.append(spans[-1])
        lo = cut + 1
    return names


def _binding(text: bytes, code: np.ndarray) -> tuple[list[tuple[int, int]], bool]:
    """Name spans bound by the row's first depth-0 `=` or `:=`, and whether it is `:=`."""
    depth_at, _ = _depths(text, code)
    for col, byte in enumerate(text):
        if byte != 61 or not code[col] or depth_at[col] != 0:
            continue
        prev = text[col - 1] if col else 0
        nxt = text[col + 1] if col + 1 < len(text) else 0
        if nxt in b"=>" or prev in b"=!<>":
            continue
        return _bound_names(text, code, depth_at, col), prev == 58
    return [], False


def _keyword_arg(text: bytes, depth_at: list[int], end: int) -> bool:
    """True for a name inside brackets followed by a single `=` (`type=str`): a label, not a read."""
    col = end
    while col < len(text) and text[col] == 32:
        col += 1
    if col >= len(text) or text[col] != 61 or depth_at[col] == 0:
        return False
    return col + 1 >= len(text) or text[col + 1] != 61


def _row_flow(
    lines: _Lines, row: int, skip: tuple[int, int] | None = None
) -> tuple[list[bytes], list[bytes], bool]:
    """Names a row binds, names it reads (outside `skip`), and whether it binds with `:=`."""
    text, code = lines.text[row], lines.code[row]
    bound, walrus = _binding(text, code)
    starts = {start for start, _ in bound}
    lo, hi = skip or (0, 0)
    depth_at, _ = _depths(text, code)
    reads = [
        text[s:e]
        for s, e in _words(text, code)
        if s not in starts and not lo <= s < hi and not _keyword_arg(text, depth_at, e)
    ]
    return [text[s:e] for s, e in bound], reads, walrus


def _open_row(lines: _Lines, row: int) -> int:
    """Walk up from `row` to the row that opens its still-open brackets (multi-line headers)."""
    need = 0
    for cur in range(row, -1, -1):
        need -= _depths(lines.text[cur], lines.code[cur])[1]
        if need <= 0:
            return cur
    return 0


def _enclosing(lines: _Lines, start: int) -> list[tuple[int, int]]:
    """First and last row of each block header around `start`, innermost first."""
    blocks: list[tuple[int, int]] = []
    cur, row = lines.indent(start), start - 1
    while row >= 0 and cur > 0:
        if lines.opens_block(row) and lines.indent(row) < cur:
            head = row
            prev = next((r for r in range(row - 1, -1, -1) if not lines.blank(r)), -1)
            if lines.text[row].strip()[:1] == b"{" and prev >= 0 and lines.indent(prev) == lines.indent(row):
                head = prev
            first = _open_row(lines, head)
            blocks.append((first, row))
            cur, row = lines.indent(first), first - 1
        else:
            row -= 1
    return blocks


def _header_prefix(text: bytes, code: np.ndarray) -> bytes | None:
    """Bytes before `NAME(` on a definition-like header row, such as `def ` or `func (s *S) `."""
    ind = next((col for col, byte in enumerate(text) if 32 < byte < 128), 0)
    depth_at, _ = _depths(text, code)
    for col in range(ind, len(text)):
        if text[col] != 40 or not code[col] or depth_at[col] != 0:
            continue
        start = col
        while start > ind and text[start - 1] in _WORD:
            start -= 1
        if start == col or text[start] in _DIGITS:
            continue
        head = range(ind, start)
        keyword = any(text[c] in _WORD and depth_at[c] == 0 for c in head)
        plain = all(depth_at[c] > 0 or text[c] in _PREFIX for c in head)
        return text[ind:start] if keyword and plain else None
    return None


def _param_decl(text: bytes, code: np.ndarray, depth_at: list[int], span: tuple[int, int]) -> bytes:
    """The parameter item around `span` on its own row (`User user`, `drive: Drive`), else the name."""
    lo, hi = span[0] - 1, span[1]
    while lo >= 0 and not (code[lo] and (text[lo] == 44 and depth_at[lo] == 1 or text[lo] == 40 and depth_at[lo] == 0)):
        lo -= 1
    while hi < len(text) and not (code[hi] and depth_at[hi] == 1 and text[hi] in b",=)"):
        hi += 1
    if lo < 0 or hi >= len(text):
        return text[span[0] : span[1]]
    return text[lo + 1 : hi].strip()


def _header_params(lines: _Lines, first: int, last: int) -> list[tuple[bytes, bytes]]:
    """Parameters in header rows as (name, declaration): depth-1 names before any `:` or `=`."""
    params: list[tuple[bytes, bytes]] = []
    depth, typed = 0, False
    for row in range(first, last + 1):
        text, code = lines.text[row], lines.code[row]
        depth_at, _ = _depths(text, code, depth)
        ends = dict(_words(text, code))
        for col, byte in enumerate(text):
            if not code[col]:
                continue
            if byte in _OPEN:
                depth += 1
                typed = typed and depth > 1
            elif byte in _CLOSE:
                depth -= 1
            elif depth == 1 and byte in b",:=":
                typed = byte != 44
            elif depth == 1 and not typed and col in ends:
                params.append((text[col : ends[col]], _param_decl(text, code, depth_at, (col, ends[col]))))
    return params


def _header_names(lines: _Lines, first: int, last: int) -> list[bytes]:
    return [name for name, _ in _header_params(lines, first, last)]


def _uses_scope(lines: _Lines, block: tuple[int, int], outer: tuple[int, int]) -> bool:
    """True when a header's bracket names are already bound in the block around it (`if f(x):`)."""
    scope = set(_names_before(lines, block[0], (outer[0], outer[1], b""), lines.indent(block[0])))
    return bool(scope.intersection(_header_names(lines, block[0], block[1])))


def _pick_header(lines: _Lines, start: int, reads: set[bytes]) -> tuple[int, int, bytes] | None:
    """Header of the definition around `start`: the outermost whose parameters the clone reads.

    Headers that only use names already in scope are control flow, not definitions."""
    blocks = _enclosing(lines, start)
    if not blocks or lines.indent(blocks[-1][0]) != 0:
        return None
    forms = []
    for idx, (first, last) in enumerate(blocks):
        prefix = _header_prefix(lines.text[first], lines.code[first])
        if prefix is None or (idx + 1 < len(blocks) and _uses_scope(lines, (first, last), blocks[idx + 1])):
            continue
        forms.append((first, last, prefix))
    for first, last, prefix in reversed(forms):
        if reads.intersection(_header_names(lines, first, last)):
            return first, last, prefix
    return forms[0] if forms else None


def _names_before(
    lines: _Lines, start: int, header: tuple[int, int, bytes] | None, deepest: int = 128
) -> list[bytes]:
    """Names bound before `start` in its definition: parameters, then assignments indented <= `deepest`."""
    first, last = (header[0], header[1]) if header else (0, -1)
    names = _header_names(lines, first, last) if header else []
    depth = 0
    for row in range(last + 1, start):
        if depth == 0 and lines.indent(row) <= deepest:
            names.extend(_row_flow(lines, row)[0])
        depth = max(0, _depths(lines.text[row], lines.code[row], depth)[1])
    return names


def _reads_after(lines: _Lines, row: int, header: tuple[int, int, bytes] | None) -> set[bytes]:
    """Names read from `row` to the end of the definition that holds it."""
    stop = lines.indent(header[0]) if header else lines.indent(row) - 1
    reads: set[bytes] = set()
    for cur in range(row, len(lines.text)):
        if lines.opens_block(cur) and lines.indent(cur) <= stop:
            break
        reads.update(_row_flow(lines, cur)[1])
    return reads


def _atom(mid: bytes, code: np.ndarray) -> bool:
    """Check for one name, number, dotted name or plain string literal."""
    if len(mid) >= 2 and mid[0] in _QUOTES:
        return mid[-1] == mid[0] and mid[0] not in mid[1:-1] and 92 not in mid
    dotted = bool(mid) and all(byte in _WORD or byte == 46 for byte in mid)
    return dotted and mid[:1] != b"." and mid[-1:] != b"." and bool(code.all())


def _slot(rows: list[bytes], codes: list[np.ndarray]) -> tuple[int, int] | None:
    """Common prefix and suffix lengths when the rows differ in exactly one atom."""
    ref = rows[0]
    short = min(len(row) for row in rows)
    pre = suf = 0
    while pre < short and all(row[pre] == ref[pre] for row in rows):
        pre += 1
    while suf < short - pre and all(row[-1 - suf] == ref[-1 - suf] for row in rows):
        suf += 1
    while pre and (ref[pre - 1] in _WORD or ref[pre - 1] == 46):
        pre -= 1
    while suf and (ref[len(ref) - suf] in _WORD or ref[len(ref) - suf] == 46):
        suf -= 1
    if pre and suf and ref[pre - 1] in _QUOTES and ref[len(ref) - suf] == ref[pre - 1]:
        pre, suf = pre - 1, suf - 1
    for row, code in zip(rows, codes):
        if not _atom(row[pre : len(row) - suf], code[pre : len(row) - suf]):
            return None
    return pre, suf


def _fresh(stem: bytes, taken: set[bytes]) -> bytes:
    name, k = stem, 2
    while name in taken:
        name, k = stem + str(k).encode(), k + 1
    return name


def _last_code_byte(lines: _Lines, row: int) -> int:
    text, code = lines.text[row], lines.code[row]
    return next((text[c] for c in range(len(text) - 1, -1, -1) if code[c] and text[c] > 32), 0)


def _clone_body(lines: _Lines, members: list[int], length: int) -> tuple[list[int], int]:
    """Drop a header (and its closing row) the clones start with, so the helper takes the body."""
    ref = members[0]
    if length < 3 or not lines.is_opener(ref):
        return members, length
    skip = 2 if lines.text[ref + 1].strip()[:1] == b"{" else 1
    last = ref + length - 1
    closes = lines.indent(last) == lines.indent(ref) and lines.starts_with(last, _CLOSE)
    return [m + skip for m in members], length - skip - int(closes)


def _starts_statement(lines: _Lines, row: int) -> bool:
    """False when the row before `row` leaves an expression open (`(`, `[`, `,`, `\\`)."""
    prev = next((r for r in range(row - 1, -1, -1) if not lines.blank(r)), -1)
    return prev < 0 or _last_code_byte(lines, prev) not in b"([,\\"


def _row_kinds(lines: _Lines, members: list[int], length: int, width: int) -> list[Any] | None:
    """Per clone row: "same" text, a `(prefix, suffix)` slot, or None for a row each clone keeps.

    Rows stop at the first one above the clone's indent, and at a kept row that is nested or opens
    a block (moving it would change control flow)."""
    kinds: list[Any] = []
    for k in range(length):
        rows = [m + k for m in members]
        if any(len(lines.text[row]) >= width for row in rows):
            return None
        rel = lines.indent(rows[0]) - lines.indent(members[0])
        if rel < 0:
            break
        bodies = [lines.text[row][lines.indent(row) :] for row in rows]
        if all(body == bodies[0] for body in bodies):
            kinds.append("same")
            continue
        codes = [lines.code[row][lines.indent(row) : len(lines.text[row])] for row in rows]
        slot = _slot(bodies, codes)
        if slot is None and (rel > 0 or any(lines.is_opener(row) for row in rows)):
            break
        kinds.append(slot)
    return kinds


def _balanced_length(lines: _Lines, members: list[int], kinds: list[Any]) -> int:
    """Longest prefix of the clone rows that closes its brackets and blocks in every clone."""
    limit = len(kinds)
    for m in members:
        base, depth, good = lines.indent(m), 0, 0
        for k in range(limit):
            depth_at, after = _depths(lines.text[m + k], lines.code[m + k], depth)
            low = min(depth_at, default=depth)
            if low < 0 or after < 0 or (kinds[k] is None and (depth or after)):
                break
            depth = after
            nxt = lines.next_nonblank(m + k)
            ends_block = nxt >= len(lines.text) or (lines.indent(nxt) <= base and not lines.is_opener(m + k))
            good = k + 1 if depth == 0 and ends_block else good
        limit = min(limit, good)
    return limit


def _slot_span(lines: _Lines, row: int, slot: tuple[int, int]) -> tuple[int, int]:
    lo = lines.indent(row) + slot[0]
    return lo, len(lines.text[row]) - slot[1]


def _flow_cut(lines: _Lines, members: list[int], kinds: list[Any]) -> tuple[int, list[bytes], list[bytes], bool]:
    """Keep rows while data flow allows: kept rows must not read or rebind helper names."""
    bound: list[bytes] = []
    free: list[bytes] = []
    walrus = False
    for k, kind in enumerate(kinds):
        if kind is None:
            for m in members:
                names, reads, _ = _row_flow(lines, m + k)
                if set(reads) & set(bound) or set(names) & set(bound + free):
                    return k, bound, free, walrus
            continue
        skip = _slot_span(lines, members[0] + k, kind) if isinstance(kind, tuple) else None
        if skip and any(lines.text[m + k][slice(*_slot_span(lines, m + k, kind))] in bound for m in members):
            return k, bound, free, walrus
        spans, _ = _binding(lines.text[members[0] + k], lines.code[members[0] + k])
        if skip and any(s < skip[1] and skip[0] < e for s, e in spans):
            return k, bound, free, walrus
        names, reads, binds_walrus = _row_flow(lines, members[0] + k, skip)
        free.extend(name for name in reads if name not in bound and name not in free)
        bound.extend(name for name in names if name not in bound)
        walrus = walrus or binds_walrus
    return len(kinds), bound, free, walrus


def _slot_names(lines: _Lines, first: int, kinds: list[Any], params: list[bytes], bound: list[bytes]) -> dict[int, bytes]:
    """Name each slot after the reference text when that name is free, else `n`, `s` or `x`."""
    taken = {text[s:e] for text, code in zip(lines.text, lines.code) for s, e in _words(text, code)}
    names: dict[int, bytes] = {}
    for k, kind in enumerate(kinds):
        if not isinstance(kind, tuple):
            continue
        row = first + k
        lo, hi = _slot_span(lines, row, kind)
        mid = lines.text[row][lo:hi]
        others = {
            lines.text[first + j][s:e]
            for j, other in enumerate(kinds)
            if other is not None
            for s, e in _words(lines.text[first + j], lines.code[first + j])
            if j != k or not lo <= s < hi
        }
        plain = mid[0] not in _DIGITS and mid[0] not in _QUOTES and 46 not in mid
        if plain and mid not in others and mid not in params and mid not in bound:
            names[k] = mid
        else:
            stem = b"n" if mid[0] in _DIGITS else b"s" if mid[0] in _QUOTES else b"x"
            names[k] = _fresh(stem, taken | set(params) | set(names.values()))
        params.append(names[k])
    return names


def _named_block(lines: _Lines, row: int) -> bool:
    """A `:` header made of two names and an optional bracket group, like `class Name(Base):`."""
    text, code = lines.text[row], lines.code[row]
    if _last_code_byte(lines, row) != 58:
        return False
    depth_at, _ = _depths(text, code)
    top = [c for c in range(lines.indent(row), len(text)) if code[c] and depth_at[c] == 0]
    if any(text[c] not in _WORD and text[c] not in b" (:" for c in top):
        return False
    return len([s for s in _words(text, code) if depth_at[s[0]] == 0]) == 2


def _helper_site(lines: _Lines, header: tuple[int, int, bytes], first: int) -> tuple[int, int, bytes, bytes, int]:
    """Placement and syntax copied from the enclosing header: (row, indent, prefix, tail, body indent).

    A `:`-block helper leaves named blocks such as a class body, so it needs no receiver."""
    top, last, prefix = header
    ind = lines.indent(top)
    end = _last_code_byte(lines, last)
    tail = b":" if end == 58 else b" {" if end == 123 else b""
    body = next((lines.indent(r) for r in range(last + 1, first + 1) if not lines.blank(r)), ind + 4)
    body = body - ind if body > ind else 4
    blocks = _enclosing(lines, top) if tail == b":" else []
    while blocks and _named_block(lines, blocks[0][0]):
        top = blocks[0][0]
        blocks = _enclosing(lines, top)
    ind = lines.indent(top)
    while top > 0 and not lines.blank(top - 1) and lines.indent(top - 1) == ind:
        if lines.text[top - 1][ind] in _CLOSE:
            break
        top -= 1
    return top, ind, prefix, tail, ind + body


def _prose(lines: _Lines, row: int) -> bool:
    """Six or more names in a row joined by single spaces: comment text the masks missed."""
    text, run, best = lines.text[row], 0, 0
    spans = _words(text, lines.code[row])
    for (_, end), (start, _) in zip(spans, spans[1:]):
        run = run + 1 if text[end:start] == b" " else 0
        best = max(best, run)
    return best + 1 >= 6


def _movable_body(lines: _Lines, rows: list[int]) -> bool:
    """At least two rows start with code (not comments), and none is a `return` the helper would capture."""
    starts = [lines.text[row][lines.indent(row) :] for row in rows if lines.opens_block(row) and not _prose(lines, row)]
    returns = any(text[: len(_RETURN)] == _RETURN and text[len(_RETURN) : len(_RETURN) + 1] not in _WORD_BYTES for text in starts)
    return len(starts) >= 2 and not returns


def _plan_helper(lines: _Lines, members: list[int], length: int, width: int) -> _HelperPlan | None:
    """Plan a helper for `length` matching rows at each clone start in `members`, from their text."""
    members, length = _clone_body(lines, members, length)
    if not all(_starts_statement(lines, m) for m in members):
        return None
    kinds = _row_kinds(lines, members, length, width)
    if kinds is None:
        return None
    kinds = kinds[: _balanced_length(lines, members, kinds)]
    cut, bound, free, walrus = _flow_cut(lines, members, kinds)
    kinds = kinds[:cut]
    while kinds and kinds[-1] is None:
        kinds.pop()
    body_rows = [k for k, kind in enumerate(kinds) if kind is not None]
    if not _movable_body(lines, [members[0] + k for k in body_rows]):
        return None

    first = members[0]
    headers = [_pick_header(lines, m, set(free)) for m in members]
    if any(header is None for header in headers):
        return None
    avail: list[bytes] = []
    for m, header in zip(members, headers):
        kept = [n for k, kind in enumerate(kinds) if kind is None for n in _row_flow(lines, m + k)[0]]
        avail.extend(n for n in _names_before(lines, m, header) + kept if n not in avail)
    inputs = sorted((n for n in free if n in avail), key=avail.index)
    after: set[bytes] = set()
    for m, header in zip(members, headers):
        after |= _reads_after(lines, m + len(kinds), header)
    outputs = [n for n in bound if n in after]
    params = list(inputs)
    slots = _slot_names(lines, first, kinds, params, bound)

    taken = {text[s:e] for text, code in zip(lines.text, lines.code) for s, e in _words(text, code)}
    name = _fresh(_HELPER, taken)
    at, ind, prefix, tail, body_ind = _helper_site(lines, headers[0], first)
    semi = b";" if all(_last_code_byte(lines, first + k) == 59 for k in body_rows) else b""
    decls = dict(reversed(_header_params(lines, headers[0][0], headers[0][1])))
    rows = [b" " * ind + prefix + name + b"(" + b", ".join(decls.get(p, p) for p in params) + b")" + tail]
    for k in body_rows:
        row = first + k
        text = lines.text[row][lines.indent(row) :]
        if k in slots:
            text = text[: kinds[k][0]] + slots[k] + text[len(text) - kinds[k][1] :]
        rows.append(b" " * (body_ind + lines.indent(row) - lines.indent(first)) + text)
    if outputs:
        rows.append(b" " * body_ind + _RETURN + b" " + b", ".join(outputs) + semi)
    if tail == b" {":
        rows.append(b" " * ind + b"}")

    bind = (b", ".join(outputs) + (b" := " if walrus else b" = ")) if outputs else b""
    calls: dict[int, tuple[int, list[int], bytes]] = {}
    for m in members:
        args = inputs + [lines.text[m + k][slice(*_slot_span(lines, m + k, kinds[k]))] for k in slots]
        kept = [m + k for k, kind in enumerate(kinds) if kind is None]
        call = b" " * lines.indent(m) + bind + name + b"(" + b", ".join(args) + b")" + semi
        calls[m] = (m + len(kinds) - 1, kept, call)
    if max(len(text) for text in rows + [call for _, _, call in calls.values()]) > width:
        return None
    blanks = 0
    while blanks < 2 and at - blanks > 0 and lines.blank(at - blanks - 1):
        blanks += 1
    return _HelperPlan(at, rows, max(1, blanks), calls)


def _splice_helper(
    grid: np.ndarray, prov: list[tuple[bool, str]], plan: _HelperPlan
) -> tuple[np.ndarray, list[tuple[bool, str]]]:
    width = grid.shape[1]
    inner = {row for start, (last, _, _) in plan.calls.items() for row in range(start, last + 1)}
    rows_out: list[np.ndarray] = []
    prov_out: list[tuple[bool, str]] = []
    for idx in range(len(grid)):
        if idx == plan.insert_at:
            rows_out.extend(_synth_row(0, text, width) for text in plan.rows)
            prov_out.extend((True, "") for _ in plan.rows)
            rows_out.extend(_blank_row(width) for _ in range(plan.blank_after))
            prov_out.extend((True, "") for _ in range(plan.blank_after))
        if idx in plan.calls:
            last, kept, call = plan.calls[idx]
            rows_out.extend(grid[row] for row in kept)
            prov_out.extend((True, prov[row][1]) for row in kept)
            rows_out.append(_synth_row(0, call, width))
            prov_out.append((True, f"{prov[idx][1]}..{prov[last][1]}"))
        elif idx not in inner:
            rows_out.append(grid[idx])
            prov_out.append(prov[idx])
    return np.stack(rows_out, axis=0), prov_out


def _apply_clone_factor(
    hygiene: Any,
    params: dict[str, np.ndarray],
    grid: np.ndarray,
    prov: list[tuple[bool, str]],
    lprobs: np.ndarray,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]], np.ndarray]:
    dup_spans = _kind_spans(lprobs, 3, thresh, max_gap=2)
    if not dup_spans:
        return grid, prov, lprobs
    cells, sk_code, sk_skel = _extract_grid_features(hygiene, params, grid)
    ref, peers = _grow_clone_peers(grid, sk_code, sk_skel, dup_spans[0][0])
    if not peers:
        return grid, prov, lprobs

    lines = _Lines.of(grid, cells)
    slen = peers[0][1] - peers[0][0] + 1
    runs = [_shared_run(grid, sk_code, sk_skel, ref, start, slen) for start, _ in peers]
    dup_before = int(np.sum(lprobs[:, 3] >= thresh))
    best = None
    for length in sorted({n for n in runs if n >= 2}, reverse=True):
        members = [start for (start, _), n in zip(peers, runs) if n >= length]
        plan = _plan_helper(lines, members, length, grid.shape[1]) if len(members) >= 2 else None
        if plan is None:
            continue
        cand, cand_prov = _splice_helper(grid, prov, plan)
        lp_c, sc_c = hygiene._forward(params, cand)
        rank = (int(np.sum(lp_c.max(axis=1) >= thresh)), -float(sc_c))
        if int(np.sum(lp_c[:, 3] >= thresh)) < dup_before and (best is None or rank < best[0]):
            best = (rank, cand, cand_prov, lp_c)
    if best is None:
        return grid, prov, lprobs
    return best[1], best[2], best[3]


def _apply_dense_unpack(
    hygiene: Any,
    params: dict[str, np.ndarray],
    grid: np.ndarray,
    prov: list[tuple[bool, str]],
    lprobs: np.ndarray,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]], np.ndarray]:
    spans = _kind_spans(lprobs, 1, thresh)
    if not spans:
        return grid, prov, lprobs

    cells, _, _ = _extract_grid_features(hygiene, params, grid)
    flagged = {r_i for a, b in spans for r_i in range(a, b + 1)}
    rows_out: list[np.ndarray] = []
    prov_out: list[tuple[bool, str]] = []

    for idx in range(len(grid)):
        if idx not in flagged:
            rows_out.append(grid[idx])
            prov_out.append(prov[idx])
            continue

        ind = _row_indent(grid[idx])
        rlen = _row_len(grid[idx])
        semis = [
            col
            for col in range(ind, rlen - 1)
            if grid[idx, col] == 59
            and cells[idx, col, _HC_STR] < 0.5
            and cells[idx, col, _HC_COMM] < 0.5
        ]
        if semis:
            cuts = [ind, *[col + 1 for col in semis], rlen]
            for seg_i in range(len(cuts) - 1):
                seg = bytes(grid[idx, cuts[seg_i] : cuts[seg_i + 1]]).strip()
                if seg:
                    rows_out.append(_synth_row(ind, seg))
                    prov_out.append((True, prov[idx][1]))
        else:
            rows_out.extend([
                _synth_row(ind, b"v = f("),
                _synth_row(ind + 4, b"a=x,"),
                _synth_row(ind + 4, b"b=y,"),
                _synth_row(ind, b")"),
            ])
            prov_out.extend([(True, prov[idx][1]), (True, ""), (True, ""), (True, "")])

    cand = np.stack(rows_out, axis=0)
    lp_c, _ = hygiene._forward(params, cand)
    return cand, prov_out, lp_c


def _apply_ladder(
    hygiene: Any,
    params: dict[str, np.ndarray],
    grid: np.ndarray,
    prov: list[tuple[bool, str]],
    lprobs: np.ndarray,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]], np.ndarray]:
    lad_spans = _kind_spans(lprobs, 4, thresh, max_gap=2)
    if not lad_spans:
        return grid, prov, lprobs

    smap = {a: (a, b) for a, b in lad_spans}
    skip = {r_i for a, b in lad_spans for r_i in range(a + 1, b + 1)}
    rows_out, prov_out = [], []

    for idx in range(len(grid)):
        if idx in skip:
            continue
        if idx in smap:
            a, b = smap[idx]
            ind = _row_indent(grid[a])
            tag = f"{prov[a][1]}..{prov[b][1]}"
            rows_out.append(_synth_row(ind, b"m = {"))
            prov_out.append((True, tag))
            for k in range(4):
                rows_out.append(_synth_row(ind + 4, f"k{k}: v{k},".encode()))
                prov_out.append((True, ""))
            rows_out.extend([_synth_row(ind, b"}"), _synth_row(ind, b"r = m[k]")])
            prov_out.extend([(True, ""), (True, tag)])
        else:
            rows_out.append(grid[idx])
            prov_out.append(prov[idx])

    cand = np.stack(rows_out, axis=0)
    lp_c, _ = hygiene._forward(params, cand)
    return cand, prov_out, lp_c


def _is_deep_nonblank(grid: np.ndarray, row_idx: int) -> bool:
    return _row_len(grid[row_idx]) > 0 and _row_indent(grid[row_idx]) >= 8


def _apply_pyramid(
    hygiene: Any,
    params: dict[str, np.ndarray],
    grid: np.ndarray,
    prov: list[tuple[bool, str]],
    lprobs: np.ndarray,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]], np.ndarray]:
    nest_spans = _kind_spans(lprobs, 0, thresh, max_gap=2)
    if not nest_spans:
        return grid, prov, lprobs

    a, b = nest_spans[0]
    top, bot = a, b
    while top > 0 and _is_deep_nonblank(grid, top - 1):
        top -= 1
    while bot + 1 < len(grid) and _is_deep_nonblank(grid, bot + 1):
        bot += 1

    base = min(8, max(4, _row_indent(grid[top])))
    guard_tag = f"{prov[top][1]}..{prov[max(top, a - 1)][1]}"
    rows_out, prov_out = [], []

    for idx in range(len(grid)):
        if idx == top:
            rows_out.extend([
                _synth_row(base, b"if !ok: return"),
                _synth_row(base, b"if !rdy: return"),
                _blank_row(),
            ])
            prov_out.extend([(True, guard_tag), (True, guard_tag), (True, "")])
            for core_r in range(a, b + 1):
                rows_out.append(_shift_indent(grid[core_r], base + (core_r - a) * 2))
                prov_out.append((True, prov[core_r][1]))
        elif not (top < idx <= bot):
            rows_out.append(grid[idx])
            prov_out.append(prov[idx])

    cand = np.stack(rows_out, axis=0)
    lp_c, _ = hygiene._forward(params, cand)
    return cand, prov_out, lp_c


def _apply_layout_fixes(
    hygiene: Any,
    params: dict[str, np.ndarray],
    grid: np.ndarray,
    prov: list[tuple[bool, str]],
    lprobs: np.ndarray,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]], np.ndarray]:
    for a, b in _kind_spans(lprobs, 2, thresh, max_gap=1):
        cur = _row_indent(grid[a])
        if a == b and cur % 2 == 1:
            best_g, best_sc = grid, -1.0
            for cand_i in sorted({max(0, (cur // 4) * 4), ((cur + 3) // 4) * 4, (cur // 2) * 2}):
                if cand_i == cur:
                    continue
                cand = grid.copy()
                cand[a] = _shift_indent(grid[a], cand_i)
                _, sc_c = hygiene._forward(params, cand)
                if sc_c > best_sc:
                    best_g, best_sc = cand, sc_c
            grid = best_g
            prov[a] = (True, prov[a][1])

    for _ in range(3):
        lprobs, _ = hygiene._forward(params, grid)
        walls = _kind_spans(lprobs, 2, thresh, max_gap=2)
        if not walls:
            break
        wa, wb = walls[0]
        _, sk_code, sk_skel = _extract_grid_features(hygiene, params, grid)
        best_cut, min_cos = (wa + wb) // 2 + 1, 99.0
        for row in range(max(1, wa - 3), min(len(grid) - 2, wb + 3) + 1):
            if _row_len(grid[row]) == 0 or _row_len(grid[row + 1]) == 0:
                continue
            cos = float(
                np.dot(sk_code[row], sk_code[row + 1])
                + np.dot(sk_skel[row], sk_skel[row + 1])
            )
            if cos < min_cos:
                min_cos, best_cut = cos, row + 1
        grid = np.insert(grid, best_cut, _blank_row(), axis=0)
        prov.insert(best_cut, (True, ""))
    lprobs, _ = hygiene._forward(params, grid)
    return grid, prov, lprobs


def _silhouette_row(row: np.ndarray, line_num: int, arrow_src: str) -> str:
    rlen = _row_len(row)
    if rlen == 0:
        return f"  {line_num:3d} |"
    ind = _row_indent(row)
    pad = " " * (ind // 2) + ("·" if ind % 2 == 1 else "")
    glyphs: list[str] = []
    for col in range(ind, min(rlen, 100), 2):
        b0 = int(row[col])
        b1 = int(row[col + 1]) if col + 1 < rlen else 32
        pair = (b0, b1)
        if b0 == 32 and b1 == 32:
            glyphs.append(" ")
        elif any(32 < x < 127 and chr(x) in "{}()[]" for x in pair):
            glyphs.append(next(chr(x) for x in pair if 32 < x < 127 and chr(x) in "{}()[]"))
        elif any(32 < x < 127 and chr(x) in ";:,=" for x in pair):
            glyphs.append(next(chr(x) for x in pair if 32 < x < 127 and chr(x) in ";:,="))
        else:
            glyphs.append("■")
    suffix = f"  <-{arrow_src}" if arrow_src else ""
    return f"  {line_num:3d} | {pad}{''.join(glyphs)}{suffix}"


_STEPS = (
    _apply_clone_factor,
    _apply_dense_unpack,
    _apply_ladder,
    _apply_pyramid,
    _apply_layout_fixes,
)


def _rank(
    hygiene: Any, params: dict[str, np.ndarray], grid: np.ndarray, thresh: float
) -> tuple[int, float]:
    """Order grids by flagged-row count, then by higher document score."""
    lprobs, sc = hygiene._forward(params, grid)
    return int(np.sum(lprobs.max(axis=1) >= thresh)), -float(sc)


def _project_window(
    hygiene: Any,
    params: dict[str, np.ndarray],
    sub_grid: np.ndarray,
    line_offset: int,
    thresh: float,
) -> tuple[np.ndarray, list[tuple[bool, str]]]:
    grid = sub_grid.copy()
    prov: list[tuple[bool, str]] = [(False, str(line_offset + i + 1)) for i in range(len(grid))]
    lprobs, sc0 = hygiene._forward(params, grid)

    comm_spans = _kind_spans(lprobs, 5, thresh)
    if comm_spans:
        drops = {r_i for a, b in comm_spans for r_i in range(a, b + 1)}
        if len(drops) < len(grid):
            cand = np.delete(grid, sorted(drops), axis=0)
            lp_c, sc_c = hygiene._forward(params, cand)
            sq_before = int(np.sum(lprobs.max(axis=1) >= thresh))
            sq_after = int(np.sum(lp_c.max(axis=1) >= thresh))
            if sq_after < sq_before or sc_c > sc0:
                keep_nb = max(0, min(drops) - 1)
                prov = [p for i, p in enumerate(prov) if i not in drops]
                if keep_nb < len(prov):
                    prov[keep_nb] = (True, prov[keep_nb][1])
                grid, lprobs = cand, lp_c

    best_rank = _rank(hygiene, params, grid, thresh)
    for step in _STEPS:
        cand, cand_prov, cand_lp = step(hygiene, params, grid, list(prov), lprobs, thresh)
        cand_rank = _rank(hygiene, params, cand, thresh)
        if cand_rank < best_rank:
            grid, prov, lprobs, best_rank = cand, cand_prov, cand_lp, cand_rank
    return grid, prov


def _window(grid: np.ndarray, flagged: np.ndarray) -> tuple[int, int]:
    """Rows to project: around the flags, reaching back to the top-level statement they sit in."""
    if len(grid) <= _MAX_DIRECT_LINES:
        return 0, len(grid)
    win_lo = max(0, int(flagged[0]) - _WINDOW_PAD)
    win_hi = min(len(grid), int(flagged[-1]) + _WINDOW_PAD + 1)
    if win_hi - win_lo > _MAX_DIRECT_LINES:
        win_hi = min(len(grid), win_lo + _MAX_DIRECT_LINES)
    for row in range(win_lo, max(-1, win_lo - _SCOPE_REACH - 1), -1):
        if _row_len(grid[row]) and grid[row, 0] > 32:
            while row > 0 and _row_len(grid[row - 1]) and grid[row - 1, 0] > 32:
                row -= 1  # decorators and comments that belong to the statement
            return row, win_hi
    return win_lo, win_hi


def project_target_grid(
    code: str,
    *,
    threshold: float = 0.55,
    weights: dict[str, np.ndarray] | None = None,
    hygiene: Any = None,
) -> TargetProjection:
    """Project `code`'s flagged byte-grid regions onto a verified green target slice."""
    if hygiene is None:
        import codehygiene as hygiene

    params = weights or hygiene.load_weights()
    full_grid, _ = hygiene.encode_byte_grid(code)
    if len(full_grid) == 0:
        return TargetProjection(target_score=1.0, target_squiggles=0, target_slice="")

    lprobs0, sc0 = hygiene._forward(params, full_grid)
    flagged = np.flatnonzero(lprobs0.max(axis=1) >= threshold)
    if len(flagged) == 0:
        return TargetProjection(target_score=sc0, target_squiggles=0, target_slice="")

    win_lo, win_hi = _window(full_grid, flagged)
    opt_g, opt_prov = _project_window(
        hygiene, params, full_grid[win_lo:win_hi], win_lo, threshold
    )
    spliced = np.concatenate([full_grid[:win_lo], opt_g, full_grid[win_hi:]], axis=0)
    t_sq, neg_score = _rank(hygiene, params, spliced, threshold)
    t_score = -neg_score

    unchanged = TargetProjection(target_score=sc0, target_squiggles=len(flagged), target_slice="")
    if t_score < sc0 or (t_sq >= len(flagged) and t_score <= sc0 + 1e-4):
        return unchanged

    changed = [i for i, (is_mod, _) in enumerate(opt_prov) if is_mod]
    keep: set[int] = set()
    for idx in changed:
        keep.update(range(max(0, idx - 1), min(len(opt_g), idx + 2)))
    if not keep:
        return unchanged
    selected = sorted(keep)

    lines_out: list[str] = []
    prev_i = -2
    for idx in selected[:_SLICE_ROWS]:
        if prev_i >= 0 and idx > prev_i + 1:
            lines_out.append("    ...")
        is_mod, tag = opt_prov[idx]
        lines_out.append(_silhouette_row(opt_g[idx], win_lo + idx + 1, tag if is_mod else ""))
        prev_i = idx
    return TargetProjection(
        target_score=t_score,
        target_squiggles=t_sq,
        target_slice="\n".join(lines_out),
    )
