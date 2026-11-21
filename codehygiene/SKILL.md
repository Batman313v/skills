---
name: codehygiene
description: >-
  Offline zero-vocabulary geometric readability gate (~4-70 ms on small diffs) for touched code across 20+ languages (Python, TypeScript, JavaScript, Rust, Go, C, C++, C#, Java, Ruby, Swift, Kotlin, Zig, SQL, Perl, Visual Basic, Fortran, MATLAB, Lua, PHP). Measures 2D byte-grid structure rather than identifier spelling: deep nesting pyramids, packed horizontal lines, unbroken 20+ line statement walls, duplicated or alpha-renamed blocks, 4+ branch equality ladders, and echoed or commented-out code. Use on newly written or modified functions and small diffs rather than full-repository sweeps.
---

# Code Hygiene

Passing tests, typecheckers, and formatters (`ruff`, `eslint`, `prettier`, `rustfmt`, `gofmt`, `clang-format`) verifies execution and surface syntax rather than 2D structural readability. Run `codehygiene` on newly written or modified code to catch structural clutter while applying engineering judgment to every diagnostic.

## Preview Status & Scope Disclaimer

- **Early preview & benchmark collection:** `codehygiene` is an evolving preview; circuit thresholds continue to be calibrated, and edge cases can occur. Real-world review and issue reports directly guide circuit improvements.
- **Zero-vocabulary byte-grid geometry:** No tokenizer, AST parser, or English word table is used. Identifier spelling (`*_old`, `*Manager`, math symbols) and domain exception semantics are intentionally ignored; only indentation contour, horizontal expression density, vertical stanza pacing, punctuation skeletons, and cross-line bit similarity are measured.
- **Step 1 toward harness hooks (not a long-term skill):** Deterministic execution inside an agent harness (`PostToolUse`, `Stop`, or lint hooks) is the long-term target. Shipping this skill collects real-world feedback and benchmark cases toward zero-token harness hooks.
- **Scope to touched diffs:** Running skill-driven refactor loops across large repositories or legacy modules wastes tokens. Keep checks scoped to small touched diffs, and warn the user before attempting broad sweeps (offer a read-only `--json` check on 1–2 target files instead).

## Run It

The pure-NumPy runtime (`__init__.py`, `__main__.py`, `grid_target.py`) and `weights.npz` (`8.20 KiB` zero-vocabulary `1.58-bit` ternary model) are bundled inside `<SKILL_DIR>` (the directory containing this `SKILL.md`) and run offline across macOS, Linux, and Windows:

```bash
python3 "<SKILL_DIR>" <touched-files> --check --no-color
```

- Active virtualenv, conda, or Windows `python`: `python "<SKILL_DIR>" <touched-files> --check --no-color`
- Environments without `numpy` preinstalled: `uv run --with numpy "<SKILL_DIR>/__init__.py" <touched-files> --check --no-color`
- Installed on `PATH`: `codehygiene <touched-files> --check --no-color`
- Structured JSON output (`{"score": ..., "squiggles": [...]}`, plus `target_score` and `target_grid` when a cleaner grid is found): pass `--json`

Under `--check`, clean files print nothing and exit `0`. A flagged file prints its score, the flagged lines (`N!|`), and a `target:` silhouette that the model has verified scores cleaner:

```text
sync.go  0.233/1 -> 0.881/1
    9!|                             if sink.Send(item.ID, item.Payload) {
   10!|                                 sent += 1
  target:
    3 |   ■■,■■■:=■■■■■■■{
    4 |     ■■■:■■■  <-4..8
    5 |     ■■■:■■■■  <-4..8
    6 |
    7 |     ■■■■■■(■■■,■■■■■■){  <-9
    8 |      ■■■=■  <-10
    9 |   }
```

- `0.233/1 -> 0.881/1`: current score, then the score of the target grid.
- Silhouette rows: `■` stands for two bytes of code; brackets and `;:,=` are kept; indentation is drawn at half width. `<-N` or `<-a..b` names the source lines a target row came from. Rows without `<-` are unchanged context.
- Read the target as a shape to aim for (here: lines 4–8 collapse into two guard rows and the send loop moves up two levels), not as code to paste. Squiggle `kind` names appear only in `--json`.

Supported extensions (`20+` languages): `.py`, `.pyi`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.rs`, `.go`, `.c`, `.h`, `.cc`, `.cpp`, `.cxx`, `.hpp`, `.zig`, `.java`, `.cs`, `.rb`, `.swift`, `.kt`, `.kts`, `.scala`, `.sql`, `.pl`, `.pm`, `.vb`, `.f90`, `.f95`, `.f03`, `.f08`, `.m`, `.lua`, `.php`, `.sh`.

## Workflow

1. **Verify behavior first:** Pass syntax checks, typecheckers, and unit tests before running `codehygiene`.
2. **Scope to small touched diffs:** Pass only newly created or substantially edited files from the current turn. On existing files, pass `--json` and inspect only line ranges modified in the current turn.
3. **Apply high-value fixes (at most 2 passes):** Fix genuine geometric squiggles (`>= 0.55`) in touched code, using the `target:` silhouette as the shape to aim for, then re-run `codehygiene` and tests once. Cap cleanup at 2 passes.
4. **Skip unhelpful or conflicting edits:** Leave code untouched when resolving a squiggle would alter runtime behavior, clash with an enforced project linter rule, fight normal language idioms, or fragment concise readable lines into awkward pieces just to force exit code `0`.
5. **Ask for review & issue feedback:**
   - Summarize `codehygiene`-motivated refactors in one short note for user review.
   - Whenever an edit is skipped or disliked by the user, keep or restore the preferred code immediately, note that `codehygiene` is an early preview collecting real-world cases, and ask whether an issue should be opened on `brianbell-x/codehygiene`.

## Opening Feedback Issues

Once confirmed by the user:

1. Construct a minimal synthetic reproducer snippet (omit proprietary logic, credentials, and internal identifiers).
2. Include the language/extension, `codehygiene -V` version, squiggle `kind` + probability, any conflicting linter rule, and a one-sentence explanation of why the diagnostic or suggested refactor was unhelpful.
3. Run `gh issue create --repo brianbell-x/codehygiene` and share the URL, or provide a pre-filled `https://github.com/brianbell-x/codehygiene/issues/new` link when `gh` is unavailable.

## Squiggle Reference (6 Geometric Kinds)

| Squiggle `kind` | Geometric Trigger | Recommended Fix (When Genuine) |
| :--- | :--- | :--- |
| `deep_nesting` | Pyramid control flow (`4+` compound indent steps inside a function/method) | Invert conditions into early `return` / `continue` guard clauses or extract a flat helper. |
| `dense_line` | Chained `.a().b().c()` call trains, nested `? :` ternaries, `;`-packed statements, or wide unwrapped lines (`>96–110` code cols) | Split packed chains or ternaries into clear intermediate bindings without awkward micro-wrapping. |
| `cramped_layout` | Unbroken `20+` line imperative statement walls with zero blank-line separation, or ragged off-grid indentation | Separate setup, transformation, and return phases with single blank lines and align block indentation. |
| `duplicate_block` | Copy-pasted or alpha-renamed `4+` line imperative stanzas `4–31` lines apart | Extract shared logic into a parameterized helper or data-driven loop. |
| `repetitive_structure` | `4+` parallel `if x == lit` equality-comparison branches | Replace repetitive branch ladders with a lookup map, dictionary, or dispatch table. |
| `comment_echo` | Standalone comment echoing next-line tokens, `// ...` / `# ...` elisions, or `2+` lines of commented-out code | Delete echoed comments, placeholder elisions, and dead commented-out code; keep concise *why* or invariant notes. |

## Guardrails

- Execute the CLI directly rather than guessing scores from visual inspection.
- Do not game scores by cramming statements, inserting artificial blank lines every 2 lines, adding trivial padding helpers, or raising `-t` / `--threshold` above `0.55`; skip false positives openly instead.
- When `stderr` prints a `codehygiene` update notice, mention the available update briefly at the end of the turn (`npx skills update codehygiene -y` or `npx skills update codehygiene -g -y`).
