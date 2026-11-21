"""codehygiene v0.7.0 — Zero-vocabulary 1.58-bit ternary model for code geometry and structure."""

from __future__ import annotations

import argparse
import codecs
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

__version__ = "0.7.0"
__all__ = [
    "MAX_COLS",
    "MAX_REL_DIST",
    "SQUIGGLE_KINDS",
    "SQUIGGLE_MESSAGES",
    "Result",
    "Squiggle",
    "encode_byte_grid",
    "lint",
    "load_weights",
    "main",
    "project_target_grid",
    "render_report",
    "rl_reward",
    "score",
    "unpack_ternary_archive",
]

MAX_COLS = 128
MAX_REL_DIST = 32
_SKETCH_DIM = 40
_PRIOR_LINES = 16.0
_PEAK_WINDOW = 32
_PEAK_MIN_GATE = 10.0
_PEAK_PRIOR = 6.0
_SPAN_CONT_WEIGHT = 0.25
_RIBBON_GUARD = 6
_POW3 = 3 ** np.arange(5, dtype=np.uint8)

_CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".c", ".h",
    ".cc", ".cpp", ".cxx", ".hpp", ".rs", ".go", ".zig", ".java", ".cs", ".lua",
    ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".sh", ".sql", ".pl",
    ".pm", ".vb", ".f90", ".f95", ".f03", ".f08", ".m",
})
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "target",
    "build", "dist", "venv", ".venv", ".tox", ".mypy_cache", ".pytest_cache",
})

_SPEC = (
    ("deep_nesting", "Deep nesting pyramid; flatten with guard clauses or helpers"),
    ("dense_line", "Wide line, packed statements, call train, or nested ternary"),
    ("cramped_layout", "Unbroken statement wall or ragged off-grid indentation"),
    ("duplicate_block", "Duplicated or structurally cloned multi-line block"),
    ("repetitive_structure", "Repetitive parallel equality or branch ladder"),
    ("comment_echo", "Comment echoes adjacent code or leaves commented-out code"),
)
SQUIGGLE_KINDS = tuple(kind for kind, _ in _SPEC)
SQUIGGLE_MESSAGES = dict(_SPEC)
_RL_KIND_SEVERITY = {
    "deep_nesting": 0.42,
    "dense_line": 0.10,
    "cramped_layout": 0.08,
    "duplicate_block": 0.58,
    "repetitive_structure": 0.34,
    "comment_echo": 0.18,
}

_CONV_BANKS = ("conv3", "conv7", "sketch")
_ANSI = ("\033[1m", "\033[2m", "\033[33m", "\033[32m", "\033[31m", "\033[0m")


@dataclass
class Squiggle:
    line: int
    col_start: int
    col_end: int
    probability: float
    kind: str
    message: str


@dataclass
class Result:
    score: float
    squiggles: list[Squiggle]
    line_probs: np.ndarray

    def to_dict(self) -> dict[str, Any]:
        items = [
            {**asdict(sq), "probability": round(sq.probability, 4)}
            for sq in self.squiggles
        ]
        return {"score": round(self.score, 4), "squiggles": items}


@dataclass(frozen=True)
class _ActiveSpans:
    nonblank: np.ndarray
    span_lens: np.ndarray
    line_starts: np.ndarray
    line_ids: np.ndarray
    within_col: np.ndarray
    total_active: int


def _relu(arr: np.ndarray) -> np.ndarray:
    return np.maximum(arr, 0.0)


def _sigmoid(arr: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(arr, -30.0, 30.0)))


def _softmax(arr: np.ndarray) -> np.ndarray:
    shifted = np.exp(arr - np.max(arr, axis=-1, keepdims=True))
    return shifted / np.sum(shifted, axis=-1, keepdims=True)


def _l2_norm(arr: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    norms = np.sqrt(np.sum(arr * arr, axis=-1, keepdims=True) + eps)
    return arr / norms


def _conv1d_grid(grid_x: np.ndarray, kernel: np.ndarray, bias: np.ndarray) -> np.ndarray:
    """Apply symmetric 1D convolution `(k, in_ch, out_ch)` along the column axis."""
    num_lines, num_cols, _ = grid_x.shape
    active_in = np.flatnonzero(np.any(kernel != 0, axis=(0, 2)))
    if len(active_in) == 0:
        return np.broadcast_to(bias, (num_lines, num_cols, kernel.shape[2])).copy()

    k_size, _, out_channels = kernel.shape
    pad = (k_size - 1) // 2
    sub_x = grid_x[:, :, active_in]
    sub_kern = np.ascontiguousarray(kernel[:, active_in, :])
    padded = np.pad(sub_x, ((0, 0), (pad, pad), (0, 0)))
    stride_cols = num_cols + 2 * pad
    flat_in = padded.reshape(num_lines * stride_cols, len(active_in))
    span = num_lines * stride_cols - (k_size - 1)

    flat_out = np.empty((num_lines * stride_cols, out_channels), dtype=np.float32)
    flat_out[:] = bias
    for offset in range(k_size):
        k_slice = sub_kern[offset]
        if k_slice.any():
            flat_out[:span] += flat_in[offset : offset + span] @ k_slice
    return flat_out.reshape(num_lines, stride_cols, out_channels)[:, :num_cols, :]


def _ribbon_conv_sum(
    rib_cells: np.ndarray,
    rib_starts: np.ndarray,
    rib_pos: np.ndarray,
    spans: _ActiveSpans,
    kernel: np.ndarray,
    bias: np.ndarray,
) -> np.ndarray:
    active_in = np.flatnonzero(np.any(kernel != 0, axis=(0, 2)))
    if len(active_in) == 0:
        return np.zeros((len(spans.line_starts), kernel.shape[2]), dtype=np.float32)

    k_size, n_in, out_channels = kernel.shape[0], len(active_in), kernel.shape[2]
    pad = (k_size - 1) // 2
    sub_cells = np.ascontiguousarray(rib_cells[:, active_in])
    flat_kern = np.ascontiguousarray(
        kernel[:, active_in, :].reshape(k_size * n_in, out_channels)
    )
    line_starts = spans.line_starts
    num_nb = len(line_starts)
    out = np.empty((num_nb, out_channels), dtype=np.float32)

    for s_ln in range(0, num_nb, 128):
        e_ln = min(s_ln + 128, num_nb)
        cell_lo = int(line_starts[s_ln])
        cell_hi = int(line_starts[e_ln]) if e_ln < num_nb else spans.total_active
        rib_lo = int(rib_starts[s_ln]) - pad
        rib_hi = int(rib_starts[e_ln]) + pad
        valid_len = rib_hi - rib_lo - (k_size - 1)

        windows = np.lib.stride_tricks.sliding_window_view(
            sub_cells[rib_lo:rib_hi], k_size, axis=0
        )
        col_mat = np.ascontiguousarray(windows.transpose(0, 2, 1)).reshape(
            valid_len, k_size * n_in
        )
        conv_out = col_mat @ flat_kern + bias
        local_pos = rib_pos[cell_lo:cell_hi] - (rib_lo + pad)
        active_out = np.maximum(conv_out[local_pos], 0.0)
        out[s_ln:e_ln] = np.add.reduceat(
            active_out, line_starts[s_ln:e_ln] - cell_lo, axis=0
        )
    return out


def _extract_active_spans(grid: np.ndarray) -> _ActiveSpans | None:
    _, max_width = grid.shape
    col_pos = np.arange(1, max_width + 1, dtype=np.int32)[None, :]
    row_lens = np.max(np.where(grid != 0, col_pos, 0), axis=1)
    nonblank = np.flatnonzero(row_lens > 0)
    if len(nonblank) == 0:
        return None

    span_lens = np.minimum(max_width, row_lens[nonblank] + _RIBBON_GUARD)
    line_starts = np.cumsum(span_lens) - span_lens
    total_active = int(np.sum(span_lens))
    line_ids = np.repeat(np.arange(len(nonblank), dtype=np.int32), span_lens)
    within_col = np.arange(total_active, dtype=np.int32) - line_starts[line_ids]
    return _ActiveSpans(
        nonblank, span_lens, line_starts, line_ids, within_col, total_active
    )


def _scatter_ribbon(
    cells: np.ndarray,
    spans: _ActiveSpans,
    guard: int,
    fill: np.ndarray | float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts = np.empty(len(spans.nonblank) + 1, dtype=np.int32)
    starts[0] = guard
    np.cumsum(spans.span_lens + guard, out=starts[1:])

    pos = starts[:-1][spans.line_ids] + spans.within_col
    ribbon = np.empty((int(starts[-1]) + guard, cells.shape[1]), dtype=np.float32)
    ribbon[:] = fill
    ribbon[pos] = cells
    return ribbon, starts, pos


def _line_cumsum(vals: np.ndarray, starts: np.ndarray, ids: np.ndarray) -> np.ndarray:
    csum = np.cumsum(vals, axis=0)
    prior = csum[np.maximum(starts - 1, 0)]
    base = np.where((starts > 0)[:, None], prior, 0.0)
    return csum - base[ids]


def _fuse_block0_cells(
    params: dict[str, np.ndarray], grid: np.ndarray, spans: _ActiveSpans
) -> np.ndarray:
    codes = grid[spans.nonblank[spans.line_ids], spans.within_col]
    emb_act = params["tok_emb"][codes] + params["col_emb"][spans.within_col]
    rib_emb, _, pre_pos = _scatter_ribbon(emb_act, spans, guard=1)

    pre_w = params["pre_conv3_w"]
    lex_len = len(rib_emb) - 2
    lex_rib = np.broadcast_to(params["pre_conv3_b"], (lex_len, pre_w.shape[2])).copy()
    for offset in range(3):
        lex_rib += rib_emb[offset : offset + lex_len] @ pre_w[offset]
    lex_act = np.maximum(lex_rib[pre_pos - 1], 0.0)

    pref1_in = np.concatenate([emb_act, lex_act], axis=-1) @ params["pref1_proj"]
    prefix1 = _line_cumsum(pref1_in, spans.line_starts, spans.line_ids) - pref1_in

    gate_in = np.concatenate([emb_act, lex_act, prefix1], axis=-1)
    gates = np.maximum(gate_in @ params["gate1_w"] + params["gate1_b"], 0.0)
    prefix2 = _line_cumsum(gates @ params["pref2_proj"], spans.line_starts, spans.line_ids)

    fuse_in = np.concatenate([emb_act, lex_act, gates, prefix2], axis=-1)
    return np.maximum(fuse_in @ params["col_fuse_w"] + params["col_fuse_b"], 0.0)


def _block01_active_lines(
    params: dict[str, np.ndarray],
    grid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Run Block 0 masking and Block 1 k=3/7/sketch convolutions on occupied spans."""
    num_lines = grid.shape[0]
    col_sums = np.zeros((num_lines, params["col_sum_proj"].shape[1]), dtype=np.float32)
    banks = {
        name: np.zeros((num_lines, params[f"{name}_w"].shape[2]), dtype=np.float32)
        for name in _CONV_BANKS
    }
    spans = _extract_active_spans(grid)
    if spans is None:
        return col_sums, banks["conv3"], banks["conv7"], banks["sketch"]

    cells_act = _fuse_block0_cells(params, grid, spans)
    col_sums[spans.nonblank] = (
        np.add.reduceat(cells_act, spans.line_starts, axis=0) @ params["col_sum_proj"]
    )

    pad_fill = np.maximum(params["col_fuse_b"], 0.0)
    rib_cells, rib_starts, rib_pos = _scatter_ribbon(
        cells_act, spans, guard=_RIBBON_GUARD, fill=pad_fill
    )
    for name in _CONV_BANKS:
        banks[name][spans.nonblank] = _ribbon_conv_sum(
            rib_cells, rib_starts, rib_pos, spans, params[f"{name}_w"], params[f"{name}_b"]
        )
    return col_sums, banks["conv3"], banks["conv7"], banks["sketch"]


def _attend_banded_heads(
    q_proj: np.ndarray | None,
    k_proj: np.ndarray | None,
    v_proj: np.ndarray,
    b_rel: np.ndarray,
    b_sink: np.ndarray,
    banded: list[int],
    inv_scale: np.float32,
) -> np.ndarray:
    num_lines, val_dim = v_proj.shape[1], v_proj.shape[2]
    rad = MAX_REL_DIST
    win = 2 * rad + 1
    num_b = len(banded)

    q_sub = q_proj[banded] * inv_scale if q_proj is not None else None
    k_sub = k_proj[banded] if k_proj is not None else None
    v_sub = v_proj[banded]
    rel_sub = b_rel[banded]
    has_q = (
        np.any(q_sub != 0, axis=(1, 2))
        if q_sub is not None
        else np.zeros((num_b,), dtype=bool)
    )
    has_v = np.any(v_sub != 0, axis=(1, 2))

    logits = np.full((num_b, num_lines, win + 1), -1e9, dtype=np.float32)
    logits[:, :, win] = b_sink[banded, None]
    active_offsets = [
        off for off in range(-rad + 1, rad) if np.any(rel_sub[:, off + rad] > -1e3)
    ]

    for off in active_offsets:
        q_start, q_end = max(0, -off), min(num_lines, num_lines - off)
        if q_start >= q_end:
            continue

        slot = off + rad
        act_mask = rel_sub[:, slot] > -1e3
        h_act = np.flatnonzero(act_mask)
        logits[h_act, q_start:q_end, slot] = rel_sub[h_act, slot : slot + 1]

        if q_sub is not None and k_sub is not None:
            h_qk = np.flatnonzero(act_mask & has_q)
            if len(h_qk):
                k_win = k_sub[h_qk, q_start + off : q_end + off, :]
                logits[h_qk, q_start:q_end, slot] += np.sum(
                    q_sub[h_qk, q_start:q_end, :] * k_win, axis=-1
                )

    weights = _softmax(logits)
    out = np.zeros((num_b, num_lines, val_dim), dtype=np.float32)
    for off in active_offsets:
        q_start, q_end = max(0, -off), min(num_lines, num_lines - off)
        slot = off + rad
        h_val = np.flatnonzero((rel_sub[:, slot] > -1e3) & has_v)
        if q_start < q_end and len(h_val):
            out[h_val, q_start:q_end, :] += (
                weights[h_val, q_start:q_end, slot : slot + 1]
                * v_sub[h_val, q_start + off : q_end + off, :]
            )
    return out


def _attend_global_head(
    q_head: np.ndarray | None,
    k_head: np.ndarray | None,
    v_head: np.ndarray,
    b_rel_h: np.ndarray,
    sink_h: np.float32,
    qh_zero: bool,
    inv_scale: np.float32,
) -> np.ndarray:
    num_lines = v_head.shape[0]
    if qh_zero and np.all(b_rel_h == 0.0):
        weight = np.float32(1.0 / (float(num_lines) + float(np.exp(sink_h))))
        return v_head.sum(axis=0, keepdims=True) * weight

    line_idx = np.arange(num_lines)
    raw_diff = line_idx[None, :] - line_idx[:, None]
    rel_idx = np.clip(raw_diff, -MAX_REL_DIST, MAX_REL_DIST) + MAX_REL_DIST
    logits_h = b_rel_h[rel_idx].copy()
    if not qh_zero and q_head is not None and k_head is not None:
        logits_h += (q_head @ k_head.T) * inv_scale
    sink_col = np.full((num_lines, 1), sink_h, dtype=np.float32)
    weights_h = _softmax(np.concatenate([logits_h, sink_col], axis=-1))[:, :num_lines]
    return weights_h @ v_head


def _project_qk(
    state: np.ndarray, wq: np.ndarray | None, wk: np.ndarray | None, num_heads: int
) -> tuple[np.ndarray | None, np.ndarray | None, int, bool]:
    if wq is None or wk is None or not (np.any(wq != 0) and np.any(wk != 0)):
        return None, None, 1, False
    num_lines = state.shape[0]
    head_dim = wq.shape[1] // num_heads
    q_proj = (state @ wq).reshape(num_lines, num_heads, head_dim).transpose(1, 0, 2)
    k_proj = (state @ wk).reshape(num_lines, num_heads, head_dim).transpose(1, 0, 2)
    return q_proj, k_proj, head_dim, True


def _sink_attn(
    state: np.ndarray,
    wq: np.ndarray | None,
    wk: np.ndarray | None,
    wv: np.ndarray,
    wo: np.ndarray,
    b_rel: np.ndarray,
    b_sink: np.ndarray,
) -> np.ndarray:
    """Multi-head relative-offset attention across lines with O(L) banded execution."""
    num_lines, num_heads = state.shape[0], b_sink.shape[0]
    val_dim = wv.shape[1] // num_heads
    v_proj = (state @ wv).reshape(num_lines, num_heads, val_dim).transpose(1, 0, 2)
    q_proj, k_proj, head_dim, has_qk = _project_qk(state, wq, wk, num_heads)
    inv_scale = np.float32(1.0 / np.sqrt(float(head_dim)))

    banded = [h for h in range(num_heads) if b_rel[h, 0] <= -1e3 and b_rel[h, -1] <= -1e3]
    merged = np.zeros((num_heads, num_lines, val_dim), dtype=np.float32)
    if banded:
        merged[banded] = _attend_banded_heads(
            q_proj, k_proj, v_proj, b_rel, b_sink, banded, inv_scale
        )

    banded_set = set(banded)
    for head in range(num_heads):
        if head in banded_set:
            continue
        h_slice = slice(head * head_dim, (head + 1) * head_dim)
        qh_zero = (not has_qk) or wq is None or (not np.any(wq[:, h_slice] != 0))
        q_h = q_proj[head] if q_proj is not None else None
        k_h = k_proj[head] if k_proj is not None else None
        merged[head] = _attend_global_head(
            q_h, k_h, v_proj[head], b_rel[head], b_sink[head], qh_zero, inv_scale
        )
    return merged.transpose(1, 0, 2).reshape(num_lines, -1) @ wo


def encode_byte_grid(code: str, max_lines: int | None = None) -> tuple[np.ndarray, list[str]]:
    """Convert raw source text into a `(lines, 128)` uint8 grid of 7-bit ASCII codes."""
    all_lines = code.expandtabs(2).splitlines() or [""]
    lines = all_lines[:max_lines] if max_lines is not None else all_lines
    grid = np.zeros((len(lines), MAX_COLS), dtype=np.uint8)
    for row, text in enumerate(lines):
        if not text:
            continue
        clipped = text[:MAX_COLS]
        if clipped.isascii():
            codes = np.frombuffer(clipped.encode("ascii"), dtype=np.uint8)
        else:
            codes = np.fromiter(
                (127 if ord(ch) > 127 else ord(ch) for ch in clipped), dtype=np.uint8
            )
        grid[row, : len(codes)] = np.where(codes == 0, 32, codes)
    return grid, lines


def _peak_window_density(state: np.ndarray, active_gate: np.ndarray) -> np.ndarray:
    """Compute maximum span-damped issue density across sliding 32-line windows."""
    raw_pos = _relu(state) * active_gate
    num_lines = raw_pos.shape[0]
    if num_lines > 1:
        prev1 = np.vstack([np.zeros((1, raw_pos.shape[1]), dtype=raw_pos.dtype), raw_pos[:-1]])
        prev2 = np.vstack([np.zeros((2, raw_pos.shape[1]), dtype=raw_pos.dtype), raw_pos[:-2]])
        overlap = np.minimum(raw_pos, np.maximum(prev1, prev2))
        pos = raw_pos - (1.0 - _SPAN_CONT_WEIGHT) * overlap
    else:
        pos = raw_pos

    pad_w = np.vstack([np.zeros((1, pos.shape[1]), dtype=pos.dtype), np.cumsum(pos, axis=0)])
    pad_g = np.vstack([
        np.zeros((1, 1), dtype=active_gate.dtype),
        np.cumsum(active_gate, axis=0),
    ])
    hi_idx = np.arange(1, num_lines + 1)
    lo_idx = np.maximum(0, hi_idx - _PEAK_WINDOW)
    denom = np.maximum(pad_g[hi_idx] - pad_g[lo_idx], _PEAK_MIN_GATE) + _PEAK_PRIOR
    return ((pad_w[hi_idx] - pad_w[lo_idx]) / denom).max(axis=0)


def _append_multiline_state(params: dict[str, np.ndarray], raw_feats: np.ndarray) -> np.ndarray:
    if "ml_proj" not in params:
        return raw_feats
    ml_deltas = raw_feats @ params["ml_proj"]
    ml_excl = np.cumsum(ml_deltas, axis=0) - ml_deltas
    ml_saw = _relu(ml_excl @ params["ml_gate_w"] + params["ml_gate_b"])
    paren_suf = _relu(
        _conv1d_grid(ml_deltas[None, :, :], params["ml_paren_w"], params["ml_paren_b"])[0]
    )
    return np.concatenate([raw_feats, ml_saw, paren_suf], axis=-1)


def _pool_document_vector(
    params: dict[str, np.ndarray], state: np.ndarray, active_gate: np.ndarray
) -> np.ndarray:
    has_ml = "ml_proj" in params
    prior_lines = _PRIOR_LINES if has_ml else 1e-5
    doc_mean = (state * active_gate).sum(axis=0) / (active_gate.sum(axis=0) + prior_lines)
    doc_max = (
        _peak_window_density(state, active_gate)
        if has_ml
        else (_relu(state) * active_gate).max(axis=0)
    )
    return np.concatenate([doc_mean, doc_max], axis=-1)


def _forward(params: dict[str, np.ndarray], grid: np.ndarray) -> tuple[np.ndarray, float]:
    """Execute the 4-block zero-vocabulary ternary forward pass on an ASCII byte grid."""
    col_sums, conv3, conv7, raw_sketch = _block01_active_lines(params, grid)
    sketch = _relu(raw_sketch) - _relu(raw_sketch - 1.0)
    line_feats = _append_multiline_state(
        params, np.concatenate([col_sums, conv3, conv7], axis=-1)
    )

    line_hidden = _relu(line_feats @ params["lmlp1_w"] + params["lmlp1_b"])
    line_stats = line_hidden @ params["lmlp2_w"] + params["lmlp2_b"]
    sketch_slices = [
        _l2_norm(sketch[:, idx : idx + _SKETCH_DIM])
        for idx in range(0, sketch.shape[1], _SKETCH_DIM)
    ]
    state = np.concatenate([line_stats, *sketch_slices], axis=-1)

    for layer in ("1", "2"):
        state = state + _sink_attn(
            state,
            params.get(f"attn{layer}_wq"),
            params.get(f"attn{layer}_wk"),
            params[f"attn{layer}_wv"],
            params[f"attn{layer}_wo"],
            params[f"attn{layer}_brel"],
            params[f"attn{layer}_bsink"],
        )
        ctx_hidden = _relu(state @ params[f"cmlp{layer}_w"] + params[f"cmlp{layer}_b"])
        state = state + ctx_hidden @ params[f"cmlp{layer}_2_w"] + params[f"cmlp{layer}_2_b"]

    line_probs = _sigmoid(state @ params["sq_w"] + params["sq_b"])
    active_gate = _sigmoid(state @ params["pool_gate_w"] + params["pool_gate_b"])
    doc_vec = _pool_document_vector(params, state, active_gate)
    doc_hidden = _relu(doc_vec @ params["doc1_w"] + params["doc1_b"])
    logits = doc_hidden @ params["doc2_w"] + params["doc2_b"]
    return line_probs, float(_sigmoid(logits)[0])


def _parse_archive_entry(entry: str) -> tuple[str, tuple[int, ...], int]:
    name, dims, count_str = entry.split(":")
    return name, tuple(int(dim) for dim in dims.split(",")), int(count_str)


def unpack_ternary_archive(
    trit_buf: np.ndarray,
    vec_buf: np.ndarray,
    meta_str: str,
) -> dict[str, np.ndarray]:
    """Unpack 5-trit-per-byte `{-1, 0, +1}` matrices and fold 1D per-channel scales."""
    trit_part, vec_part = meta_str.split("|")
    vecs: dict[str, np.ndarray] = {}
    vec_offset = 0
    for entry in vec_part.split(" ; "):
        name, shape, count = _parse_archive_entry(entry)
        vecs[name] = vec_buf[vec_offset : vec_offset + count].reshape(shape).astype(np.float32)
        vec_offset += count

    unpacked_digits = (((trit_buf[:, None] // _POW3) % 3 + 1) % 3).astype(np.int8) - 1
    flat_trits = unpacked_digits.ravel()
    params: dict[str, np.ndarray] = {
        name: val for name, val in vecs.items() if not name.endswith("_s")
    }
    byte_offset = 0
    for entry in trit_part.split(" ; "):
        name, shape, packed_len = _parse_archive_entry(entry)
        trit_start = byte_offset * 5
        num_elems = math.prod(shape)
        byte_offset += packed_len
        mat = flat_trits[trit_start : trit_start + num_elems].reshape(shape).astype(np.float32)
        scale_key = "col_sum_s" if name == "col_sum_proj" else (
            f"{name[:-2]}_s" if name.endswith("_w") else f"{name}_s"
        )
        params[name] = mat * vecs[scale_key] if scale_key in vecs else mat
    return params


@lru_cache(maxsize=2)
def load_weights(path: str | Path | None = None) -> dict[str, np.ndarray]:
    """Load and cache unpacked float32 tensors from `weights.npz`."""
    target = Path(path) if path else Path(__file__).with_name("weights.npz")
    with np.load(target) as archive:
        if "trits" in archive.files:
            return unpack_ternary_archive(
                archive["trits"], archive["vecs"], str(archive["meta"].item())
            )
        return {name: archive[name].astype(np.float32) for name in archive.files}


def _collect_squiggles(
    lines: list[str], line_probs: np.ndarray, threshold: float
) -> list[Squiggle]:
    squiggles: list[Squiggle] = []
    for idx, raw_line in enumerate(lines):
        best_kind = int(np.argmax(line_probs[idx]))
        prob = float(line_probs[idx, best_kind])
        if prob < threshold:
            continue
        stripped = raw_line.lstrip(" ")
        col_start = len(raw_line) - len(stripped) + 1 if stripped else 1
        kind = SQUIGGLE_KINDS[best_kind]
        squiggles.append(
            Squiggle(
                line=idx + 1,
                col_start=col_start,
                col_end=max(col_start, len(raw_line)),
                probability=prob,
                kind=kind,
                message=SQUIGGLE_MESSAGES[kind],
            )
        )
    return squiggles


def score(
    code: str,
    *,
    threshold: float = 0.55,
    weights: dict[str, np.ndarray] | None = None,
) -> Result:
    """Score `code` and return overall geometric cleanliness and line squiggles."""
    grid, lines = encode_byte_grid(code)
    line_probs, doc_score = _forward(weights or load_weights(), grid)
    return Result(
        score=doc_score,
        squiggles=_collect_squiggles(lines, line_probs, threshold),
        line_probs=line_probs,
    )


def lint(code: str, *, threshold: float = 0.55) -> list[Squiggle]:
    """Return active squiggles at or above `threshold`."""
    return score(code, threshold=threshold).squiggles


def _squiggle_event_load(squiggles: list[Squiggle], threshold: float = 0.55) -> float:
    if not squiggles:
        return 0.0
    load = 0.0
    prev_line: dict[str, int] = {}
    span_len: dict[str, int] = {}
    prob_span = max(1e-6, 1.0 - threshold)

    for sq in squiggles:
        excess = max(0.0, min(1.0, (sq.probability - threshold) / prob_span))
        conf_weight = 0.35 + 0.65 * excess
        if sq.line - prev_line.get(sq.kind, -10) <= 2:
            cur_span = span_len[sq.kind] + 1
            span_len[sq.kind] = cur_span
            span_mult = 0.25 * math.log(cur_span / (cur_span - 1.0))
        else:
            span_len[sq.kind] = 1
            span_mult = 1.0
        prev_line[sq.kind] = sq.line
        load += _RL_KIND_SEVERITY.get(sq.kind, 0.25) * conf_weight * span_mult
    return load


def _rl_from_result(res: Result, *, weight: float = 0.25) -> float:
    load = _squiggle_event_load(res.squiggles)
    squiggle_damp = 1.0 / (1.0 + 1.5 * (load**0.85)) if load > 0.0 else 1.0
    return 1.0 + weight * res.score * squiggle_damp


def rl_reward(
    code: str,
    tests_passed: bool | float = True,
    *,
    weight: float = 0.25,
) -> float:
    """Compute test-gated RL coding reward with telescopic squiggle damping."""
    gate = float(tests_passed)
    if gate <= 0.0:
        return 0.0
    return gate * _rl_from_result(score(code), weight=weight)


def _load_submodule(mod_name: str) -> Any:
    if __package__:
        return __import__(f"{__package__}.{mod_name}", fromlist=[mod_name])
    pkg_dir = str(Path(__file__).resolve().parent)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    return __import__(mod_name)


def project_target_grid(
    code: str,
    *,
    threshold: float = 0.55,
    weights: dict[str, np.ndarray] | None = None,
) -> Any:
    """Project `code`'s flagged byte-grid regions onto a verified green target slice."""
    return _load_submodule("grid_target").project_target_grid(
        code, threshold=threshold, weights=weights, hygiene=sys.modules[__name__]
    )


def render_report(
    label: str,
    src: str,
    thresh: float = 0.55,
    color: bool = True,
    res: Result | None = None,
) -> str:
    """Format a minimalist report with flagged lines and projected green target grid."""
    _, lines = encode_byte_grid(src)
    result = res or score(src, threshold=thresh)
    bold, dim, yel, grn, red, rst = _ANSI if color else ("",) * 6
    tone = (red, yel, grn)[int(result.score >= 0.40) + int(result.score >= 0.70)]
    if not result.squiggles:
        return f"{bold}{label}{rst}  {tone}{result.score:.3f}/1{rst}"

    proj = project_target_grid(src, threshold=thresh)
    header = f"{bold}{label}{rst}  {tone}{result.score:.3f}/1{rst}"
    if proj.target_slice:
        header += f" -> {grn}{proj.target_score:.3f}/1{rst}"
    out = [header]
    for sq in result.squiggles:
        snippet = lines[sq.line - 1][:88]
        out.append(f"  {yel}{sq.line:3d}!|{rst} {snippet}")
    if proj.target_slice:
        out.append(f"  {dim}target:{rst}")
        out.append(proj.target_slice)
    return "\n".join(out)


def _walk_dir(root: Path) -> list[Path]:
    found: list[Path] = []
    for cur_dir, subdirs, filenames in os.walk(root):
        subdirs[:] = sorted(
            name
            for name in subdirs
            if not (name.startswith(".") or name in _SKIP_DIRS or name.endswith(".egg-info"))
        )
        base = Path(cur_dir)
        found.extend(
            base / fname for fname in sorted(filenames) if Path(fname).suffix.lower() in _CODE_EXTS
        )
    return found


def _expand_paths(specs: list[str]) -> list[Path]:
    expanded: list[Path] = []
    for spec in specs:
        matches = sorted(Path().glob(spec)) if any(ch in spec for ch in "*?[") else [Path(spec)]
        for path in matches:
            expanded.extend(_walk_dir(path) if path.is_dir() else [path])
    return expanded


def _load_update_module() -> Any:
    return _load_submodule("update_check")


def _json_payload(src: str, thresh: float, res: Result) -> dict[str, Any]:
    data = res.to_dict()
    proj = project_target_grid(src, threshold=thresh) if res.squiggles else None
    if proj is not None and proj.target_slice:
        data["target_score"] = round(proj.target_score, 4)
        data["target_grid"] = proj.target_slice
    return data


def _build_parser() -> argparse.ArgumentParser:
    desc = "Zero-vocabulary 1.58-bit ternary model for code geometry and structural hygiene."
    parser = argparse.ArgumentParser(prog="codehygiene", description=desc)
    parser.add_argument("files", nargs="*", help="Files, directories, or globs (stdin if piped)")
    parser.add_argument("-t", "--threshold", type=float, default=0.55, help="Squiggle threshold")
    parser.add_argument("--check", action="store_true", help="Exit 1 when squiggles are found")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--no-update-check", action="store_true", help="Skip 12-hour release check")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _ensure_utf8_stdout() -> None:
    """Switch non-UTF-8 stdout (Windows pipes default to cp1252) so target grids can print."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    if codecs.lookup(encoding).name != "utf-8" and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for `codehygiene`."""
    _ensure_utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.files and sys.stdin.isatty():
        parser.print_help()
        return 0

    if args.files:
        targets = [
            (path.as_posix(), path.read_text("utf-8", "replace"))
            for path in _expand_paths(args.files)
        ]
    else:
        targets = [("<stdin>", sys.stdin.read())]
    if not targets:
        return 0

    updater = _load_update_module()
    update_handle = updater.start_update_check(__version__, disabled=args.no_update_check)
    runs = [(label, src, score(src, threshold=args.threshold)) for label, src in targets]
    exit_code = 1 if (args.check and any(res.squiggles for _, _, res in runs)) else 0

    if args.json:
        payloads = [_json_payload(src, args.threshold, res) for _, src, res in runs]
        is_single = len(payloads) == 1 and (not args.files or not Path(args.files[0]).is_dir())
        labels = (lbl for lbl, _, _ in runs)
        body = payloads[0] if is_single else dict(zip(labels, payloads, strict=True))
        print(json.dumps(body, indent=2))
    else:
        shown = [item for item in runs if item[2].squiggles] if args.check else runs
        if shown:
            reports = [
                render_report(lbl, src, args.threshold, not args.no_color, res)
                for lbl, src, res in shown
            ]
            print("\n\n".join(reports))

    updater.emit_update_notice(update_handle)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
