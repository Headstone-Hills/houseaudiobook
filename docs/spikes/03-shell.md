# Spike 03: macOS shell (Tauri vs PyInstaller + pywebview)

- **Date:** 2026-10-20 · **Time box:** 45 minutes · **Status:** decided provisionally; must be proven on a Mac in M9
- **Question:** how do we wrap the localhost web app as a native macOS `.app`?

## What could and could not be tested here

The container has Rust 1.97 / cargo, but it is Linux. A macOS `.app` can't be built
or signed here: Tauri's signing guide says you need an Apple device to code-sign [3].
This spike is documentation only.

## Findings

**Tauri 2** (crate `tauri` 2.12.4, updated 2026-10-14 [4])
- Ships external binaries through `bundle.externalBin`. Each binary needs a
  `-$TARGET_TRIPLE` suffix (e.g. `-aarch64-apple-darwin`), and is started with
  `shell().sidecar(name)` in Rust or `Command.sidecar(name)` in JS, given the
  `shell:allow-spawn` permission [1].
- The sidecar docs name our exact case: "Common use cases are Python CLI
  applications or API servers bundled using `pyinstaller`" [1].
- Distribution outside the App Store needs a *Developer ID Application*
  certificate, and that requires notarization (Apple ID + app-specific password +
  team ID, or an App Store Connect API key) [3].

**Fallback: PyInstaller + pywebview**
- pywebview 6.2.1, BSD-3-Clause, uses the system WebKit through pyobjc on macOS [5].
  It is a single Python process with no Rust, but it gives less control over
  native menus and updates.
- PyInstaller 6.22.3 is "GPLv2-or-later with a special exception" (the bootloader
  exception), which allows bundling non-GPL apps [6].

## Decision (provisional, D-009)

**Tauri 2 shell + PyInstaller-frozen engine as a sidecar**, with pywebview as the
fallback. The web app stays the product; the shell only starts the engine on
`127.0.0.1`, waits for `/api/health`, and opens a window on the UI. So the web
mode costs nothing extra (SPEC §3 delivery order).

## Risks for M9
- **Signing the sidecar:** every executable in the bundle, including the frozen
  Python engine and its `.dylib`s, must be signed for notarization to pass. The
  Tauri page doesn't cover sidecars [3], so this needs a test.
- **PyInstaller layout:** `externalBin` expects a single binary per name. A
  `--onefile` engine unpacks on every launch (slow start); `--onedir` needs the
  directory shipped as a resource with a small launcher. Measure both.
- **Model weights:** downloaded on first run (SPEC §9 M9), never bundled; size
  depends on the M3 model choice.

## Sources
1. Tauri docs, "Embedding External Binaries" (source): https://github.com/tauri-apps/tauri-docs/blob/v2/src/content/docs/develop/sidecar.mdx (published at https://v2.tauri.app/develop/sidecar/)
2. Tauri: https://tauri.app
3. Tauri docs, "macOS Code Signing" (source): https://github.com/tauri-apps/tauri-docs/blob/v2/src/content/docs/distribute/Sign/macos.mdx
4. crates.io, tauri: https://crates.io/crates/tauri
5. PyPI, pywebview: https://pypi.org/project/pywebview/
6. PyPI, pyinstaller: https://pypi.org/project/pyinstaller/
