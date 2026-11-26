---
name: deslop-please
description: Runs the deslop-please architectural linter on Rust/Cargo workspaces to find and fix sloppy code structure — module cycles, tangles, field leaks, shallow modules, wide directories, long files (deep modules, narrow interfaces). Use when reviewing or refactoring Rust code architecture, cleaning up module boundaries, removing slop, or when the user asks to deslop a codebase.
---

# deslop-please

An opinionated linter that removes sloppy code structure from Rust/Cargo workspaces. It follows *A Philosophy of Software Design* (John Ousterhout): **deep** modules, **narrow** interfaces. Deep modules hide the attention for you and your agents, so you only need to care about the narrow interface.

Source: https://github.com/deterministic-machines/deslop-please (MIT)

## Installation

`deslop-please` shells out to `cargo` and indexes with `rust-analyzer`, so the Rust toolchain is a hard requirement on every platform.

### 1. Rust toolchain (if `cargo` is missing)

```sh
# macOS / Linux
curl https://sh.rustup.rs | sh -s -- -y --profile minimal

# Windows (PowerShell)
powershell -c "irm https://rustup.rs | iex"
# — or: winget install Rustlang.Rustup
```

Then, on every platform (the tool runs `rust-analyzer` for its index):

```sh
rustup component add rust-analyzer
```

### 2. The linter

Prebuilt binaries are published for `aarch64-apple-darwin` (Apple silicon) and `x86_64`/`aarch64` Linux (musl). Otherwise build from crates.io — the universal fallback, which works everywhere Rust does:

```sh
cargo install deslop-please
```

Or download a prebuilt binary (adjust the triple; if a URL 404s, the asset name changed with a new release — check the releases page; verify the `.sha256` sidecar before running):

```sh
# Apple silicon macOS
curl -fsSL -o deslop.tar.gz https://github.com/deterministic-machines/deslop-please/releases/latest/download/deslop-please-aarch64-apple-darwin-v0.1.6.tar.gz
# Linux x86_64
curl -fsSL -o deslop.tar.gz https://github.com/deterministic-machines/deslop-please/releases/latest/download/deslop-please-x86_64-unknown-linux-musl-v0.1.6.tar.gz
# Linux arm64
curl -fsSL -o deslop.tar.gz https://github.com/deterministic-machines/deslop-please/releases/latest/download/deslop-please-aarch64-unknown-linux-musl-v0.1.6.tar.gz
mkdir -p ~/.local/bin && tar -xzf deslop.tar.gz -C ~/.local/bin
```

Note: even when using a prebuilt binary, `cargo` must be on PATH — the tool invokes it to read the workspace's configuration.

Verify: `deslop-please --version`

## Usage

Run from the Cargo workspace root:

```sh
deslop-please                       # lint the whole workspace
deslop-please my-crate              # only one crate or module
deslop-please --allow .deslop-please.allow
deslop-please --update-allow .deslop-please.allow
deslop-please --json                # JSON diagnostics on stdout
deslop-please --sarif               # SARIF 2.1.0 on stdout
```

It takes the same configuration as `cargo check --workspace`: `--target <triple>`, `-F/--features`, `--all-features`, `--no-default-features`. A run checks **one** configuration — the host target with default features, unless these flags (or cargo's own `build.target`, `RUSTFLAGS`) choose another. Code behind a cfg it leaves off, such as a `#[cfg(windows)]` module on Linux, is not checked: run once for each configuration that matters.

Exit status is nonzero when new slop is found.

## The lints

- **`module-cycle`** — modules that use each other.
- **`tangle`** — modules that know each other without forming a cycle (e.g. a parent module and a child module both referencing each other's items through a chain).
- **`field-leak`** — code outside a struct works on one of its fields (writes, borrows, indexes, or method calls through getters/setters) instead of the struct's own methods. Plain reads are not leaks.
- **`shallow-module`** — callers reach into many of a module's functions instead of a few deep ones.
- **`wide-directory`** — a directory holding more than 15 Rust files and subdirectories; past that, a listing is searched rather than read.
- **`long-file`** — a file with more than 2,000 lines (blank and comment lines count); it holds parts that want files of their own.

Each diagnostic names the location, lists the notes (where, and what uses what), and draws a picture of the structure.

## Fixing slop

Fix flagged errors **from first principles — don't do mechanical moves to hide them**:

- `module-cycle` & `tangle`: what is the intended layer of abstraction? Which module should be at which layer?
- `field-leak` & `shallow-module`: why do these fields/functions exist at all? What are the patterns of use? Can they be hidden behind a narrow interface? Don't fix each leak one at a time — fix them holistically.
- `wide-directory` & `long-file`: group the members by what they are about; each group wants its own directory or file.
- Draw a diagram to help the user understand the before and proposed after structure.

## Allow list (existing debt)

When a codebase has pre-existing slop that shouldn't block other work:

```sh
deslop-please --update-allow .deslop-please.allow   # write current diagnostics
deslop-please --allow .deslop-please.allow          # suppress that debt; new errors still fail
```

## Example report

```
error[field-leak]: `RunQueue::items` is known outside `RunQueue`: 4 places in 2 modules
  --> src/sched/policy.rs:7:1
  = note: src/sched/policy.rs:7:1: `runqueue::sched::policy::admit`
  = note: src/sched/policy.rs:12:1: `runqueue::sched::policy::next`
  = note: src/sched/report.rs:4:1: `runqueue::sched::report::backlog`
  = note: src/sched/report.rs:8:1: `runqueue::sched::report::drained`
    runqueue::queue::ring::RunQueue
      items ◀━┳━ 2  runqueue::sched::policy
              ┗━ 2  runqueue::sched::report
      also known outside: head
      kept: limit
```
