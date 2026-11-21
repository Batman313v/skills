/** codehygiene v0.7.0 — Pure-JavaScript Float32Array zero-vocabulary geometric scorer (exact parity with __init__.py). */

const MAX_COLS = 128;
const MAX_REL_DIST = 32;
const SKETCH_DIM = 40;
const PRIOR_LINES = 16.0;
const PEAK_WINDOW = 32;
const PEAK_MIN_GATE = 10.0;
const PEAK_PRIOR = 6.0;
const SPAN_CONT_WEIGHT = 0.25;
const RIBBON_GUARD = 6;

const HC_ACT = 0;
const HC_COMM = 1;
const HC_CODE = 2;
const HC_IND = 5;
const HC_STR = 46;

const POW3 = [1, 3, 9, 27, 81];
const CONV_BANKS = ["conv3", "conv7", "sketch"];
const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;
const f32 = Math.fround;

const SQUIGGLE_SPEC = [
  ["deep_nesting", "Deep nesting pyramid; flatten with guard clauses or helpers"],
  ["dense_line", "Wide line, packed statements, call train, or nested ternary"],
  ["cramped_layout", "Unbroken statement wall or ragged off-grid indentation"],
  ["duplicate_block", "Duplicated or structurally cloned multi-line block"],
  ["repetitive_structure", "Repetitive parallel equality or branch ladder"],
  ["comment_echo", "Comment echoes adjacent code or leaves commented-out code"],
];

export const SQUIGGLE_KINDS = SQUIGGLE_SPEC.map((entry) => entry[0]);
export const SQUIGGLE_MESSAGES = Object.fromEntries(SQUIGGLE_SPEC);
const RL_KIND_SEVERITY = {
  deep_nesting: 0.42,
  dense_line: 0.10,
  cramped_layout: 0.08,
  duplicate_block: 0.58,
  repetitive_structure: 0.34,
  comment_echo: 0.18,
};

function findZipEndOffset(bytes, view) {
  const minOffset = Math.max(0, bytes.length - 65557);
  for (let pos = bytes.length - 22; pos >= minOffset; pos -= 1) {
    if (view.getUint32(pos, true) === 0x06054b50) {
      return pos;
    }
  }
  throw new Error("Invalid weights.npz archive: missing central directory");
}

async function inflateRawBytes(compressed) {
  const stream = new Blob([compressed])
    .stream()
    .pipeThrough(new DecompressionStream("deflate-raw"));
  const buffer = await new Response(stream).arrayBuffer();
  return new Uint8Array(buffer);
}

function parseNpyArray(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const major = bytes[6];
  const headerLen = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true);
  const dataOffset = (major === 1 ? 10 : 12) + headerLen;
  const headerText = new TextDecoder("latin1").decode(bytes.subarray(8, dataOffset));
  const descrMatch = headerText.match(/'descr':\s*'([^']+)'/);
  const descr = descrMatch ? descrMatch[1] : "";
  const body = bytes.slice(dataOffset);

  if (descr.endsWith("u1")) {
    return body;
  }
  if (descr === "<f4") {
    return new Float32Array(body.buffer);
  }
  if (descr.startsWith("<U")) {
    const codePoints = new Uint32Array(body.buffer);
    return Array.from(codePoints, (cp) => String.fromCodePoint(cp)).join("");
  }
  throw new Error(`Unsupported .npy descriptor: ${descr}`);
}

export async function unpackNpzBuffer(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const view = new DataView(arrayBuffer);
  const eocdPos = findZipEndOffset(bytes, view);
  const entryCount = view.getUint16(eocdPos + 10, true);
  let dirPos = view.getUint32(eocdPos + 16, true);
  const entries = {};
  const decoder = new TextDecoder("utf-8");

  for (let idx = 0; idx < entryCount; idx += 1) {
    const method = view.getUint16(dirPos + 10, true);
    const compSize = view.getUint32(dirPos + 20, true);
    const nameLen = view.getUint16(dirPos + 28, true);
    const extraLen = view.getUint16(dirPos + 30, true);
    const commentLen = view.getUint16(dirPos + 32, true);
    const localPos = view.getUint32(dirPos + 42, true);
    const entryName = decoder.decode(bytes.subarray(dirPos + 46, dirPos + 46 + nameLen));

    const localNameLen = view.getUint16(localPos + 26, true);
    const localExtraLen = view.getUint16(localPos + 28, true);
    const dataStart = localPos + 30 + localNameLen + localExtraLen;
    const rawSlice = bytes.subarray(dataStart, dataStart + compSize);
    const unzipped = method === 8 ? await inflateRawBytes(rawSlice) : rawSlice;
    entries[entryName.replace(/\.npy$/, "")] = parseNpyArray(unzipped);
    dirPos += 46 + nameLen + extraLen + commentLen;
  }

  const params = unpackTernaryArchive(entries.trits, entries.vecs, entries.meta);
  params.stats.archiveBytes = arrayBuffer.byteLength;
  return params;
}

function buildSparse2D(dense, rows, cols) {
  const rowOffsets = new Int32Array(rows + 1);
  const colList = [];
  const valList = [];
  const activeList = [];

  for (let row = 0; row < rows; row += 1) {
    rowOffsets[row] = colList.length;
    for (let col = 0; col < cols; col += 1) {
      const val = dense[row * cols + col];
      if (val !== 0) {
        colList.push(col);
        valList.push(val);
      }
    }
    if (colList.length > rowOffsets[row]) {
      activeList.push(row);
    }
  }
  rowOffsets[rows] = colList.length;
  return {
    data: dense,
    rows,
    cols,
    rowOffsets,
    colIndices: Int32Array.from(colList),
    values: Float32Array.from(valList),
    activeRows: Int32Array.from(activeList),
    nonZeroCount: colList.length,
  };
}

function buildSparseConv(dense, kSize, inChannels, outChannels) {
  const span = inChannels * outChannels;
  const taps = [];
  for (let tap = 0; tap < kSize; tap += 1) {
    taps.push(buildSparse2D(dense.subarray(tap * span, (tap + 1) * span), inChannels, outChannels));
  }
  const nonZeroCount = taps.reduce((acc, tap) => acc + tap.nonZeroCount, 0);
  return { data: dense, kSize, rows: inChannels, cols: outChannels, taps, nonZeroCount };
}

function parseMetaEntry(entry) {
  const [name, dimsText, countText] = entry.split(":");
  const shape = dimsText.split(",").map((dim) => Number.parseInt(dim, 10));
  return { name, shape, count: Number.parseInt(countText, 10) };
}

function decodePackedTrits(tritBuf) {
  const flatTrits = new Int8Array(tritBuf.length * 5);
  for (let byteIdx = 0; byteIdx < tritBuf.length; byteIdx += 1) {
    const byteVal = tritBuf[byteIdx];
    for (let slot = 0; slot < 5; slot += 1) {
      const digit = Math.floor(byteVal / POW3[slot]) % 3;
      flatTrits[byteIdx * 5 + slot] = ((digit + 1) % 3) - 1;
    }
  }
  return flatTrits;
}

function resolveScaleKey(name) {
  if (name === "col_sum_proj") {
    return "col_sum_s";
  }
  return name.endsWith("_w") ? `${name.slice(0, -2)}_s` : `${name}_s`;
}

function foldTritTensor(flatTrits, tritStart, numElems, scaleVec) {
  const dense = new Float32Array(numElems);
  let nonZero = 0;
  for (let idx = 0; idx < numElems; idx += 1) {
    const trit = flatTrits[tritStart + idx];
    if (trit !== 0) {
      nonZero += 1;
      dense[idx] = scaleVec ? f32(trit * scaleVec[idx % scaleVec.length]) : trit;
    }
  }
  return { dense, nonZero };
}

export function unpackTernaryArchive(tritBuf, vecBuf, metaStr) {
  const [tritPart, vecPart] = metaStr.split("|");
  const vecs = {};
  let vecOffset = 0;
  for (const entry of vecPart.split(" ; ")) {
    const { name, shape, count } = parseMetaEntry(entry);
    vecs[name] = { data: vecBuf.subarray(vecOffset, vecOffset + count), shape };
    vecOffset += count;
  }

  const flatTrits = decodePackedTrits(tritBuf);
  const params = {};
  let byteOffset = 0;
  let totalTrits = 0;
  let nonZeroTrits = 0;
  const tensorEntries = tritPart.split(" ; ");

  for (const entry of tensorEntries) {
    const { name, shape, count: packedLen } = parseMetaEntry(entry);
    const numElems = shape.reduce((acc, dim) => acc * dim, 1);
    const scaleVec = vecs[resolveScaleKey(name)]?.data ?? null;
    const { dense, nonZero } = foldTritTensor(flatTrits, byteOffset * 5, numElems, scaleVec);
    byteOffset += packedLen;
    totalTrits += numElems;
    nonZeroTrits += nonZero;
    params[name] = shape.length === 3
      ? buildSparseConv(dense, shape[0], shape[1], shape[2])
      : buildSparse2D(dense, shape[0], shape[1]);
  }

  for (const [name, vecObj] of Object.entries(vecs)) {
    if (!name.endsWith("_s")) {
      params[name] = vecObj;
    }
  }
  params.stats = {
    packedBytes: tritBuf.byteLength + vecBuf.byteLength,
    tensorCount: tensorEntries.length,
    totalTrits,
    nonZeroTrits,
    zeroRatio: 1 - nonZeroTrits / totalTrits,
    scalarCount: vecBuf.length,
  };
  return params;
}

let cachedWeightsPromise = null;

export function loadWeights(url = "./weights.npz") {
  if (!cachedWeightsPromise) {
    cachedWeightsPromise = fetch(url).then(async (resp) => {
      if (!resp.ok) {
        throw new Error(`Failed to load weights (${resp.status})`);
      }
      const buffer = await resp.arrayBuffer();
      const unpacked = await unpackNpzBuffer(buffer);
      unpacked.rawBuffer = buffer;
      return unpacked;
    });
  }
  return cachedWeightsPromise;
}

function expandTabsTwo(text) {
  if (!text.includes("\t")) {
    return text;
  }
  const chunks = [];
  let colPos = 0;
  for (const ch of text) {
    if (ch === "\t") {
      const pad = 2 - (colPos % 2);
      chunks.push(" ".repeat(pad));
      colPos += pad;
    } else {
      chunks.push(ch);
      colPos = ch === "\n" || ch === "\r" ? 0 : colPos + 1;
    }
  }
  return chunks.join("");
}

function splitPythonLines(text) {
  if (!text) {
    return [""];
  }
  const parts = text.split(LINE_BREAK);
  if (parts.length > 1 && parts[parts.length - 1] === "") {
    parts.pop();
  }
  return parts;
}

export function encodeByteGrid(code, maxLines = null) {
  const allLines = splitPythonLines(expandTabsTwo(code));
  const lines = maxLines === null ? allLines : allLines.slice(0, maxLines);
  const numLines = lines.length;
  const grid = new Uint8Array(numLines * MAX_COLS);

  for (let row = 0; row < numLines; row += 1) {
    let col = 0;
    for (const ch of lines[row]) {
      if (col >= MAX_COLS) {
        break;
      }
      const codePoint = ch.codePointAt(0);
      grid[row * MAX_COLS + col] = codePoint > 127 ? 127 : (codePoint || 32);
      col += 1;
    }
  }
  return { grid, lines, numLines, width: MAX_COLS };
}

function gridRowLengths(grid, numLines, width) {
  const rowLens = new Int32Array(numLines);
  for (let row = 0; row < numLines; row += 1) {
    let len = width;
    while (len > 0 && grid[row * width + len - 1] === 0) {
      len -= 1;
    }
    rowLens[row] = len;
  }
  return rowLens;
}

function extractActiveSpans(rowLens, width) {
  const nonblank = [];
  const spanLens = [];
  rowLens.forEach((len, row) => {
    if (len > 0) {
      nonblank.push(row);
      spanLens.push(Math.min(width, len + RIBBON_GUARD));
    }
  });

  const lineStarts = new Int32Array(nonblank.length);
  let total = 0;
  spanLens.forEach((len, idx) => {
    lineStarts[idx] = total;
    total += len;
  });

  const lineIds = new Int32Array(total);
  const withinCol = new Int32Array(total);
  const cellRows = new Int32Array(total);
  for (let cell = 0, line = 0; cell < total; cell += 1) {
    while (line + 1 < nonblank.length && cell >= lineStarts[line + 1]) {
      line += 1;
    }
    lineIds[cell] = line;
    withinCol[cell] = cell - lineStarts[line];
    cellRows[cell] = nonblank[line];
  }
  return { nonblank, spanLens, lineStarts, lineIds, withinCol, cellRows, total };
}

/** Mirror numpy `_scatter_ribbon` offsets, where the second span abuts the first. */
function ribbonPositions(spans, guard) {
  const starts = new Int32Array(spans.nonblank.length + 1);
  starts[0] = guard;
  let running = 0;
  spans.spanLens.forEach((len, idx) => {
    running += len + guard;
    starts[idx + 1] = running;
  });

  const pos = new Int32Array(spans.total);
  for (let cell = 0; cell < spans.total; cell += 1) {
    pos[cell] = starts[spans.lineIds[cell]] + spans.withinCol[cell];
  }
  return { pos, length: starts[starts.length - 1] + guard };
}

function accumulateRow(src, srcBase, weight, acc) {
  const { activeRows, rowOffsets, colIndices, values } = weight;
  for (let actIdx = 0; actIdx < activeRows.length; actIdx += 1) {
    const inIdx = activeRows[actIdx];
    const inVal = src[srcBase + inIdx];
    if (inVal === 0) {
      continue;
    }
    const end = rowOffsets[inIdx + 1];
    for (let ptr = rowOffsets[inIdx]; ptr < end; ptr += 1) {
      acc[colIndices[ptr]] += inVal * values[ptr];
    }
  }
}

function buildPartSlices(parts, activeRows) {
  const slices = [];
  let inOff = 0;
  let cursor = 0;
  for (const [src, dim] of parts) {
    const nextOff = inOff + dim;
    const start = cursor;
    while (cursor < activeRows.length && activeRows[cursor] < nextOff) {
      cursor += 1;
    }
    if (cursor > start) {
      slices.push({ src, dim, inOff, start, end: cursor });
    }
    inOff = nextOff;
  }
  return slices;
}

function accumulateSlice(slice, row, weight, acc) {
  const { activeRows, rowOffsets, colIndices, values } = weight;
  const srcBase = row * slice.dim - slice.inOff;
  for (let actIdx = slice.start; actIdx < slice.end; actIdx += 1) {
    const inIdx = activeRows[actIdx];
    const inVal = slice.src[srcBase + inIdx];
    if (inVal === 0) {
      continue;
    }
    for (let ptr = rowOffsets[inIdx]; ptr < rowOffsets[inIdx + 1]; ptr += 1) {
      acc[colIndices[ptr]] += inVal * values[ptr];
    }
  }
}

function linearSparseParts(parts, numRows, weight, biasVec = null, applyRelu = false) {
  const out = new Float32Array(numRows * weight.cols);
  const acc = new Float32Array(weight.cols);
  const slices = buildPartSlices(parts, weight.activeRows);
  for (let row = 0; row < numRows; row += 1) {
    if (biasVec) {
      acc.set(biasVec);
    } else {
      acc.fill(0);
    }
    for (let idx = 0; idx < slices.length; idx += 1) {
      accumulateSlice(slices[idx], row, weight, acc);
    }
    if (applyRelu) {
      for (let col = 0; col < acc.length; col += 1) {
        if (acc[col] < 0) {
          acc[col] = 0;
        }
      }
    }
    out.set(acc, row * weight.cols);
  }
  return out;
}

function linearSparse(input, numRows, weight, biasVec = null, applyRelu = false) {
  return linearSparseParts([[input, weight.rows]], numRows, weight, biasVec, applyRelu);
}

function concatLastAxis(parts, numRows) {
  const totalDim = parts.reduce((acc, part) => acc + part[1], 0);
  const merged = new Float32Array(numRows * totalDim);
  let colStart = 0;
  for (const [src, partDim] of parts) {
    for (let row = 0; row < numRows; row += 1) {
      merged.set(src.subarray(row * partDim, (row + 1) * partDim), row * totalDim + colStart);
    }
    colStart += partDim;
  }
  return merged;
}

/** Emulate float32 numpy `_line_cumsum`: one global running sum minus each line's prior offset. */
function lineCumsum(vals, dim, spans) {
  const csum = new Float32Array(vals.length);
  csum.set(vals.subarray(0, dim), 0);
  for (let idx = dim; idx < vals.length; idx += 1) {
    csum[idx] = csum[idx - dim] + vals[idx];
  }

  const out = new Float32Array(vals.length);
  for (let cell = 0; cell < spans.total; cell += 1) {
    const start = spans.lineStarts[spans.lineIds[cell]];
    for (let chan = 0; chan < dim; chan += 1) {
      const base = start > 0 ? csum[(start - 1) * dim + chan] : 0;
      out[cell * dim + chan] = csum[cell * dim + chan] - base;
    }
  }
  return out;
}

function embedActiveCells(params, grid, spans, width) {
  const embDim = params.tok_emb.cols;
  const emb = new Float32Array(spans.total * embDim);
  const tokData = params.tok_emb.data;
  const colData = params.col_emb.data;
  for (let cell = 0; cell < spans.total; cell += 1) {
    const col = spans.withinCol[cell];
    const code = grid[spans.cellRows[cell] * width + col];
    for (let chan = 0; chan < embDim; chan += 1) {
      emb[cell * embDim + chan] = tokData[code * embDim + chan] + colData[col * embDim + chan];
    }
  }
  return emb;
}

function lexicalPreConv(params, emb, spans) {
  const embDim = params.tok_emb.cols;
  const preW = params.pre_conv3_w;
  const ribbon = ribbonPositions(spans, 1);
  const rib = new Float32Array(ribbon.length * embDim);
  for (let cell = 0; cell < spans.total; cell += 1) {
    rib.set(emb.subarray(cell * embDim, (cell + 1) * embDim), ribbon.pos[cell] * embDim);
  }

  const lex = new Float32Array(spans.total * preW.cols);
  const acc = new Float32Array(preW.cols);
  const biasVec = params.pre_conv3_b.data;
  for (let cell = 0; cell < spans.total; cell += 1) {
    acc.set(biasVec);
    for (let tap = 0; tap < preW.kSize; tap += 1) {
      accumulateRow(rib, (ribbon.pos[cell] - 1 + tap) * embDim, preW.taps[tap], acc);
    }
    for (let chan = 0; chan < acc.length; chan += 1) {
      if (acc[chan] < 0) {
        acc[chan] = 0;
      }
    }
    lex.set(acc, cell * preW.cols);
  }
  return lex;
}

function linePrefixProjection(parts, nCells, proj, spans, exclusive) {
  const projected = linearSparseParts(parts, nCells, proj);
  const prefix = lineCumsum(projected, proj.cols, spans);
  if (exclusive) {
    for (let idx = 0; idx < prefix.length; idx += 1) {
      prefix[idx] -= projected[idx];
    }
  }
  return prefix;
}

function fuseBlock0Cells(params, grid, spans, width) {
  const nCells = spans.total;
  const emb = embedActiveCells(params, grid, spans, width);
  const lex = lexicalPreConv(params, emb, spans);
  const embPart = [emb, params.tok_emb.cols];
  const lexPart = [lex, params.pre_conv3_w.cols];

  const prefix1 = linePrefixProjection([embPart, lexPart], nCells, params.pref1_proj, spans, true);
  const gateParts = [embPart, lexPart, [prefix1, params.pref1_proj.cols]];
  const gates = linearSparseParts(gateParts, nCells, params.gate1_w, params.gate1_b.data, true);
  const gatePart = [gates, params.gate1_w.cols];
  const prefix2 = linePrefixProjection([gatePart], nCells, params.pref2_proj, spans, false);

  const fuseParts = [embPart, lexPart, gatePart, [prefix2, params.pref2_proj.cols]];
  return linearSparseParts(fuseParts, nCells, params.col_fuse_w, params.col_fuse_b.data, true);
}

function compactCells(cells, totalCells, dim) {
  const offsets = new Int32Array(totalCells + 1);
  const chans = [];
  const vals = [];
  for (let cell = 0; cell < totalCells; cell += 1) {
    offsets[cell] = chans.length;
    for (let chan = 0; chan < dim; chan += 1) {
      const val = cells[cell * dim + chan];
      if (val !== 0) {
        chans.push(chan);
        vals.push(val);
      }
    }
  }
  offsets[totalCells] = chans.length;
  return { offsets, chans: Int32Array.from(chans), vals: Float32Array.from(vals) };
}

function accumulateCompactCell(sparseCells, cellIdx, tapMat, acc) {
  const { offsets, chans, vals } = sparseCells;
  const { rowOffsets, colIndices, values } = tapMat;
  for (let idx = offsets[cellIdx]; idx < offsets[cellIdx + 1]; idx += 1) {
    const chan = chans[idx];
    const inVal = vals[idx];
    for (let ptr = rowOffsets[chan]; ptr < rowOffsets[chan + 1]; ptr += 1) {
      acc[colIndices[ptr]] += inVal * values[ptr];
    }
  }
}

function buildConvRibbon(params, cells, spans) {
  const hcDim = params.col_fuse_w.cols;
  const ribbon = ribbonPositions(spans, RIBBON_GUARD);
  const cellAt = new Int32Array(ribbon.length).fill(-1);
  ribbon.pos.forEach((ribPos, cell) => {
    cellAt[ribPos] = cell;
  });
  const padFill = Float32Array.from(params.col_fuse_b.data, (val) => (val > 0 ? val : 0));
  return { pos: ribbon.pos, cellAt, padFill, sparseCells: compactCells(cells, spans.total, hcDim) };
}

function buildPadTap(padFill, tapMat, outDim) {
  const prod = new Float32Array(outDim);
  accumulateRow(padFill, 0, tapMat, prod);
  const cols = [];
  const vals = [];
  for (let chan = 0; chan < outDim; chan += 1) {
    if (prod[chan] !== 0) {
      cols.push(chan);
      vals.push(prod[chan]);
    }
  }
  return { cols: Int32Array.from(cols), vals: Float32Array.from(vals) };
}

function convAtCell(ribbonState, convWeight, padTaps, ribPos, acc) {
  const pad = (convWeight.kSize - 1) >> 1;
  for (let tap = 0; tap < convWeight.kSize; tap += 1) {
    if (convWeight.taps[tap].nonZeroCount === 0) {
      continue;
    }
    const srcCell = ribbonState.cellAt[ribPos - pad + tap];
    if (srcCell >= 0) {
      accumulateCompactCell(ribbonState.sparseCells, srcCell, convWeight.taps[tap], acc);
      continue;
    }
    const { cols, vals } = padTaps[tap];
    for (let idx = 0; idx < cols.length; idx += 1) {
      acc[cols[idx]] += vals[idx];
    }
  }
}

function ribbonConvSum(ribbonState, spans, convWeight, biasVec, numLines) {
  const outDim = convWeight.cols;
  const out = new Float32Array(numLines * outDim);
  if (convWeight.nonZeroCount === 0) {
    return out;
  }
  const padTaps = convWeight.taps.map((tap) => buildPadTap(ribbonState.padFill, tap, outDim));
  const acc = new Float32Array(outDim);
  for (let cell = 0; cell < spans.total; cell += 1) {
    acc.set(biasVec);
    convAtCell(ribbonState, convWeight, padTaps, ribbonState.pos[cell], acc);
    const dstBase = spans.cellRows[cell] * outDim;
    for (let chan = 0; chan < outDim; chan += 1) {
      if (acc[chan] > 0) {
        out[dstBase + chan] += acc[chan];
      }
    }
  }
  return out;
}

function sumLineColumns(params, cells, spans, numLines) {
  const hcDim = params.col_fuse_w.cols;
  const cellSums = new Float32Array(numLines * hcDim);
  for (let cell = 0; cell < spans.total; cell += 1) {
    const dstBase = spans.cellRows[cell] * hcDim;
    for (let chan = 0; chan < hcDim; chan += 1) {
      cellSums[dstBase + chan] += cells[cell * hcDim + chan];
    }
  }
  return linearSparse(cellSums, numLines, params.col_sum_proj);
}

function block01ActiveLines(params, grid, spans, numLines, width) {
  const banks = {};
  for (const name of CONV_BANKS) {
    banks[name] = new Float32Array(numLines * params[`${name}_w`].cols);
  }
  if (spans.total === 0) {
    const colSums = new Float32Array(numLines * params.col_sum_proj.cols);
    return { colSums, banks, cells: null };
  }

  const cells = fuseBlock0Cells(params, grid, spans, width);
  const colSums = sumLineColumns(params, cells, spans, numLines);
  const ribbonState = buildConvRibbon(params, cells, spans);
  for (const name of CONV_BANKS) {
    banks[name] = ribbonConvSum(
      ribbonState, spans, params[`${name}_w`], params[`${name}_b`].data, numLines
    );
  }
  return { colSums, banks, cells };
}

function exclusiveLinePrefix(deltas, dim) {
  const running = new Float32Array(deltas.length);
  const out = new Float32Array(deltas.length);
  for (let idx = 0; idx < deltas.length; idx += 1) {
    running[idx] = (idx >= dim ? running[idx - dim] : 0) + deltas[idx];
    out[idx] = running[idx] - deltas[idx];
  }
  return out;
}

function lineAxisConv(deltas, numLines, convWeight, biasVec) {
  const inDim = convWeight.rows;
  const pad = (convWeight.kSize - 1) >> 1;
  const out = new Float32Array(numLines * convWeight.cols);
  const acc = new Float32Array(convWeight.cols);
  for (let row = 0; row < numLines; row += 1) {
    acc.set(biasVec);
    const lo = Math.max(0, pad - row);
    const hi = Math.min(convWeight.kSize, numLines - row + pad);
    for (let tap = lo; tap < hi; tap += 1) {
      accumulateRow(deltas, (row + tap - pad) * inDim, convWeight.taps[tap], acc);
    }
    for (let chan = 0; chan < acc.length; chan += 1) {
      if (acc[chan] < 0) {
        acc[chan] = 0;
      }
    }
    out.set(acc, row * convWeight.cols);
  }
  return out;
}

function appendMultilineState(params, featParts, numLines) {
  if (!params.ml_proj) {
    return featParts;
  }
  const deltas = linearSparseParts(featParts, numLines, params.ml_proj);
  const mlSaw = linearSparse(
    exclusiveLinePrefix(deltas, params.ml_proj.cols),
    numLines,
    params.ml_gate_w,
    params.ml_gate_b.data,
    true,
  );
  const parenSuf = lineAxisConv(deltas, numLines, params.ml_paren_w, params.ml_paren_b.data);
  return [...featParts, [mlSaw, params.ml_gate_w.cols], [parenSuf, params.ml_paren_w.cols]];
}

function normalizeSketchSlices(sketch, numLines, totalSketch) {
  for (let idx = 0; idx < sketch.length; idx += 1) {
    sketch[idx] = Math.min(1, Math.max(0, sketch[idx]));
  }
  for (let base = 0; base < numLines * totalSketch; base += SKETCH_DIM) {
    let normSq = 0;
    for (let idx = base; idx < base + SKETCH_DIM; idx += 1) {
      normSq += sketch[idx] * sketch[idx];
    }
    const norm = f32(Math.sqrt(f32(normSq + 1e-6)));
    for (let idx = base; idx < base + SKETCH_DIM; idx += 1) {
      sketch[idx] = f32(sketch[idx] / norm);
    }
  }
  return sketch;
}

function headHasQuery(wq, colStart, colEnd) {
  return Boolean(wq && wq.colIndices.some((col) => col >= colStart && col < colEnd));
}

function attendUniformGlobal(ctx, headOff, sinkLogit, merged) {
  const { numLines, valTotal, valDim, vProj } = ctx;
  const weight = f32(1.0 / (numLines + Math.exp(sinkLogit)));
  const sumVec = new Float64Array(valDim);
  for (let row = 0; row < numLines; row += 1) {
    for (let dim = 0; dim < valDim; dim += 1) {
      sumVec[dim] += vProj[row * valTotal + headOff + dim];
    }
  }
  for (let row = 0; row < numLines; row += 1) {
    for (let dim = 0; dim < valDim; dim += 1) {
      merged[row * valTotal + headOff + dim] = sumVec[dim] * weight;
    }
  }
}

function queryKeyDot(ctx, head, qLine, kLine) {
  const { qProj, kProj, qkTotal, headDim } = ctx;
  const qBase = qLine * qkTotal + head.qkOff;
  const kBase = kLine * qkTotal + head.qkOff;
  let dot = 0.0;
  for (let dim = 0; dim < headDim; dim += 1) {
    dot += qProj[qBase + dim] * kProj[kBase + dim];
  }
  return dot * ctx.invScale;
}

function gatherLogits(ctx, head, qLine, scratch) {
  const kStart = head.banded ? Math.max(0, qLine - MAX_REL_DIST + 1) : 0;
  const kEnd = head.banded ? Math.min(ctx.numLines, qLine + MAX_REL_DIST) : ctx.numLines;
  let count = 0;
  let maxLogit = head.sink;
  for (let kLine = kStart; kLine < kEnd; kLine += 1) {
    const rel = Math.max(-MAX_REL_DIST, Math.min(MAX_REL_DIST, kLine - qLine));
    const relBias = ctx.bRel[head.relBase + rel + MAX_REL_DIST];
    if (head.banded && relBias <= -1e3) {
      continue;
    }
    const logit = relBias + (head.hasQuery ? queryKeyDot(ctx, head, qLine, kLine) : 0);
    scratch.keys[count] = kLine;
    scratch.logits[count] = logit;
    maxLogit = Math.max(maxLogit, logit);
    count += 1;
  }
  return { count, maxLogit };
}

function attendHeadQuery(ctx, head, qLine, scratch, merged) {
  const { count, maxLogit } = gatherLogits(ctx, head, qLine, scratch);
  let expSum = Math.exp(head.sink - maxLogit);
  for (let idx = 0; idx < count; idx += 1) {
    scratch.logits[idx] = Math.exp(scratch.logits[idx] - maxLogit);
    expSum += scratch.logits[idx];
  }

  const { valTotal, valDim, vProj } = ctx;
  const dstBase = qLine * valTotal + head.valOff;
  for (let idx = 0; idx < count; idx += 1) {
    const attnWeight = scratch.logits[idx] / expSum;
    const vBase = scratch.keys[idx] * valTotal + head.valOff;
    for (let dim = 0; dim < valDim; dim += 1) {
      merged[dstBase + dim] += attnWeight * vProj[vBase + dim];
    }
  }
}

function describeHead(ctx, headIdx, wq, bSink) {
  const relSpan = 2 * MAX_REL_DIST + 1;
  const relBase = headIdx * relSpan;
  const qkOff = headIdx * ctx.headDim;
  const hasQuery = ctx.layerHasQuery && headHasQuery(wq, qkOff, qkOff + ctx.headDim);
  const banded = ctx.bRel[relBase] <= -1e3 && ctx.bRel[relBase + relSpan - 1] <= -1e3;
  const relSlice = ctx.bRel.subarray(relBase, relBase + relSpan);
  const uniform = !banded && !hasQuery && relSlice.every((val) => val === 0);
  return {
    relBase, qkOff, valOff: headIdx * ctx.valDim, hasQuery, banded, uniform, sink: bSink[headIdx],
  };
}

function sinkAttention(state, numLines, wq, wk, wv, wo, bRel, bSink) {
  const numHeads = bSink.length;
  const layerHasQuery = Boolean(wq && wk && wq.nonZeroCount > 0 && wk.nonZeroCount > 0);
  const headDim = wq ? wq.cols / numHeads : 1;
  const ctx = {
    numLines,
    bRel,
    layerHasQuery,
    qkTotal: wq ? wq.cols : 0,
    valTotal: wv.cols,
    headDim,
    valDim: wv.cols / numHeads,
    invScale: f32(1.0 / Math.sqrt(headDim)),
    qProj: layerHasQuery ? linearSparse(state, numLines, wq) : null,
    kProj: layerHasQuery ? linearSparse(state, numLines, wk) : null,
    vProj: linearSparse(state, numLines, wv),
  };
  const merged = new Float32Array(numLines * wv.cols);
  const scratch = { logits: new Float64Array(numLines), keys: new Int32Array(numLines) };

  for (let headIdx = 0; headIdx < numHeads; headIdx += 1) {
    const head = describeHead(ctx, headIdx, wq, bSink);
    if (head.uniform) {
      attendUniformGlobal(ctx, head.valOff, head.sink, merged);
      continue;
    }
    for (let qLine = 0; qLine < numLines; qLine += 1) {
      attendHeadQuery(ctx, head, qLine, scratch, merged);
    }
  }
  return linearSparse(merged, numLines, wo);
}

function sigmoidScalar(val) {
  const clipped = Math.max(-30.0, Math.min(30.0, val));
  return 1.0 / (1.0 + Math.exp(-clipped));
}

function classifyCellMask(cells, cellOff) {
  if (cells[cellOff + HC_COMM] > 0.5) {
    return 4;
  }
  if (cells[cellOff + HC_STR] > 0.5) {
    return 3;
  }
  if (cells[cellOff + HC_IND] > 0.5) {
    return 1;
  }
  if (cells[cellOff + HC_CODE] > 0.5 && cells[cellOff + HC_ACT] > 0.5) {
    return 2;
  }
  return 5;
}

function extractCellMasks(cells, spans, rowLens, numLines, width, hcDim) {
  const masks = new Uint8Array(numLines * width);
  if (!cells) {
    return masks;
  }
  for (let cell = 0; cell < spans.total; cell += 1) {
    const row = spans.cellRows[cell];
    if (spans.withinCol[cell] < rowLens[row]) {
      masks[row * width + spans.withinCol[cell]] = classifyCellMask(cells, cell * hcDim);
    }
  }
  return masks;
}

function spanDampedPositives(state, activeGate, numLines, stateDim) {
  const pos = new Float32Array(numLines * stateDim);
  const rawAt = (row, chan) => f32(Math.max(state[row * stateDim + chan], 0) * activeGate[row]);
  for (let row = 0; row < numLines; row += 1) {
    for (let chan = 0; chan < stateDim; chan += 1) {
      const raw = rawAt(row, chan);
      const prev1 = row >= 1 ? rawAt(row - 1, chan) : 0;
      const prev2 = row >= 2 ? rawAt(row - 2, chan) : 0;
      const overlap = numLines > 1 ? Math.min(raw, Math.max(prev1, prev2)) : 0;
      pos[row * stateDim + chan] = f32(raw - f32((1.0 - SPAN_CONT_WEIGHT) * overlap));
    }
  }
  return pos;
}

function peakWindowDensity(state, activeGate, numLines, stateDim) {
  const pos = spanDampedPositives(state, activeGate, numLines, stateDim);
  const prefixW = new Float32Array((numLines + 1) * stateDim);
  const prefixG = new Float32Array(numLines + 1);
  for (let row = 0; row < numLines; row += 1) {
    prefixG[row + 1] = prefixG[row] + activeGate[row];
    for (let chan = 0; chan < stateDim; chan += 1) {
      prefixW[(row + 1) * stateDim + chan] = prefixW[row * stateDim + chan] + pos[row * stateDim + chan];
    }
  }

  const peak = new Float32Array(stateDim).fill(-Infinity);
  for (let hiIdx = 1; hiIdx <= numLines; hiIdx += 1) {
    const loIdx = Math.max(0, hiIdx - PEAK_WINDOW);
    const denom = f32(Math.max(f32(prefixG[hiIdx] - prefixG[loIdx]), PEAK_MIN_GATE) + PEAK_PRIOR);
    for (let chan = 0; chan < stateDim; chan += 1) {
      const span = f32(prefixW[hiIdx * stateDim + chan] - prefixW[loIdx * stateDim + chan]);
      peak[chan] = Math.max(peak[chan], f32(span / denom));
    }
  }
  return peak;
}

function poolDocumentVector(params, state, activeGate, numLines, stateDim) {
  const hasMl = Boolean(params.ml_proj);
  const docVec = new Float32Array(stateDim * 2);
  const meanAcc = new Float64Array(stateDim);
  let gateSum = hasMl ? PRIOR_LINES : 1e-5;
  for (let row = 0; row < numLines; row += 1) {
    gateSum += activeGate[row];
    for (let chan = 0; chan < stateDim; chan += 1) {
      meanAcc[chan] += state[row * stateDim + chan] * activeGate[row];
      if (!hasMl) {
        const posVal = Math.max(state[row * stateDim + chan], 0) * activeGate[row];
        docVec[stateDim + chan] = Math.max(docVec[stateDim + chan], posVal);
      }
    }
  }
  for (let chan = 0; chan < stateDim; chan += 1) {
    docVec[chan] = meanAcc[chan] / gateSum;
  }
  if (hasMl) {
    docVec.set(peakWindowDensity(state, activeGate, numLines, stateDim), stateDim);
  }
  return docVec;
}

function addInPlace(dst, delta) {
  for (let idx = 0; idx < dst.length; idx += 1) {
    dst[idx] += delta[idx];
  }
}

function buildLineState(params, block01, numLines) {
  const { colSums, banks } = block01;
  const featParts = [
    [colSums, params.col_sum_proj.cols],
    [banks.conv3, params.conv3_w.cols],
    [banks.conv7, params.conv7_w.cols],
  ];
  const lineParts = appendMultilineState(params, featParts, numLines);
  const lineHidden = linearSparseParts(
    lineParts, numLines, params.lmlp1_w, params.lmlp1_b.data, true
  );
  const lineStats = linearSparse(lineHidden, numLines, params.lmlp2_w, params.lmlp2_b.data);
  const sketchDim = params.sketch_w.cols;
  const sketch = normalizeSketchSlices(banks.sketch, numLines, sketchDim);
  const state = concatLastAxis([[lineStats, params.lmlp2_w.cols], [sketch, sketchDim]], numLines);
  return { state, sketch, sketchDim };
}

export function forwardModel(params, grid, lines, numLines, width = MAX_COLS) {
  const rowLens = gridRowLengths(grid, numLines, width);
  const spans = extractActiveSpans(rowLens, width);
  const block01 = block01ActiveLines(params, grid, spans, numLines, width);
  const hcDim = params.col_fuse_w.cols;
  const cellMasks = extractCellMasks(block01.cells, spans, rowLens, numLines, width, hcDim);
  const { state, sketch, sketchDim } = buildLineState(params, block01, numLines);

  for (const layer of ["1", "2"]) {
    addInPlace(state, sinkAttention(
      state, numLines,
      params[`attn${layer}_wq`], params[`attn${layer}_wk`],
      params[`attn${layer}_wv`], params[`attn${layer}_wo`],
      params[`attn${layer}_brel`].data, params[`attn${layer}_bsink`].data,
    ));
    const ctxHidden = linearSparse(
      state, numLines, params[`cmlp${layer}_w`], params[`cmlp${layer}_b`].data, true
    );
    addInPlace(state, linearSparse(
      ctxHidden, numLines, params[`cmlp${layer}_2_w`], params[`cmlp${layer}_2_b`].data
    ));
  }

  const stateDim = params.sq_w.rows;
  const sqLogits = linearSparse(state, numLines, params.sq_w, params.sq_b.data);
  const lineProbs = Float32Array.from(sqLogits, sigmoidScalar);
  const gateLogits = linearSparse(state, numLines, params.pool_gate_w, params.pool_gate_b.data);
  const activeGate = Float32Array.from(gateLogits, sigmoidScalar);

  const docVec = poolDocumentVector(params, state, activeGate, numLines, stateDim);
  const docHidden = linearSparse(docVec, 1, params.doc1_w, params.doc1_b.data, true);
  const logits = linearSparse(docHidden, 1, params.doc2_w, params.doc2_b.data);
  return {
    score: sigmoidScalar(logits[0]),
    lineProbs,
    activeGate,
    cellMasks,
    sketch,
    sketchDim,
    rowLens,
    lines,
    numLines,
    width,
  };
}

export function collectSquiggles(forwardOut, threshold = 0.55) {
  const { lineProbs, lines } = forwardOut;
  const numKinds = SQUIGGLE_KINDS.length;
  const squiggles = [];

  for (let row = 0; row < lines.length; row += 1) {
    const rawLine = lines[row];
    const probBase = row * numKinds;
    let bestKind = 0;
    for (let kindIdx = 1; kindIdx < numKinds; kindIdx += 1) {
      if (lineProbs[probBase + kindIdx] > lineProbs[probBase + bestKind]) {
        bestKind = kindIdx;
      }
    }
    const probability = lineProbs[probBase + bestKind];
    if (probability < threshold) {
      continue;
    }
    const stripped = rawLine.replace(/^ +/, "");
    const colStart = stripped ? rawLine.length - stripped.length + 1 : 1;
    const kind = SQUIGGLE_KINDS[bestKind];
    squiggles.push({
      line: row + 1,
      col_start: colStart,
      col_end: Math.max(colStart, rawLine.length),
      probability,
      kind,
      message: SQUIGGLE_MESSAGES[kind],
    });
  }
  return squiggles;
}

export function scoreCode(code, weights, threshold = 0.55) {
  const startMs = performance.now();
  const { grid, lines, numLines, width } = encodeByteGrid(code);
  const raw = forwardModel(weights, grid, lines, numLines, width);
  const squiggles = collectSquiggles(raw, threshold);
  return { ...raw, squiggles, elapsedMs: performance.now() - startMs };
}

function computeSquiggleEventLoad(squiggles, threshold = 0.55) {
  if (!squiggles || squiggles.length === 0) {
    return 0.0;
  }
  let load = 0.0;
  const prevLine = {};
  const spanLen = {};
  const probSpan = Math.max(1e-6, 1.0 - threshold);

  for (const sq of squiggles) {
    const excess = Math.max(0.0, Math.min(1.0, (sq.probability - threshold) / probSpan));
    const confWeight = 0.35 + 0.65 * excess;
    let spanMult = 1.0;
    if (sq.line - (prevLine[sq.kind] ?? -10) <= 2) {
      const curSpan = (spanLen[sq.kind] ?? 1) + 1;
      spanLen[sq.kind] = curSpan;
      spanMult = 0.25 * Math.log(curSpan / (curSpan - 1.0));
    } else {
      spanLen[sq.kind] = 1;
    }
    prevLine[sq.kind] = sq.line;
    load += (RL_KIND_SEVERITY[sq.kind] ?? 0.25) * confWeight * spanMult;
  }
  return load;
}

export function computeRlReward(result, testsPassed = true, weight = 0.25) {
  const gate = Number(testsPassed);
  if (gate <= 0) {
    return 0.0;
  }
  const load = computeSquiggleEventLoad(result.squiggles);
  const damp = load > 0.0 ? 1.0 / (1.0 + 1.5 * Math.pow(load, 0.85)) : 1.0;
  return gate * (1.0 + weight * result.score * damp);
}

export function formatJsonResult(result) {
  const round4 = (val) => Math.round(val * 10000) / 10000;
  return {
    score: round4(result.score),
    squiggles: result.squiggles.map((sq) => ({
      ...sq,
      probability: round4(sq.probability),
    })),
  };
}
