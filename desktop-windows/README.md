# Escript AI Desktop for Windows

This standalone Tauri application is being developed in its own directory. It uses eScript AI's visual identity and familiar editor workflow but does not depend on the parent Django/eScriptorium application. Kraken training and model training are outside this app's scope.

## Current implementation

The frontend includes Gemini reading, selectable Gemini model comparison, page-scoped chat, visible region/line segmentation, line-level text editing and ordering, text history, TXT/PAGE XML export, and project backup/restore. Each paid request requires user confirmation; the app does not silently retry or switch providers.

A project can be saved into a selected folder as a versioned manifest and managed image assets. Tauri's persisted filesystem scope is configured to retain access grants, and the most recent folder manifest is loaded at startup to restore the local cache. Session edits still pass through IndexedDB before they are mirrored. This folder recovery path and native shell have not yet been exercised on Windows.

`npm run build` succeeds for the frontend. The Tauri Rust shell has not compiled on this Linux host because WebKitGTK development libraries are missing. No Windows installer has been produced yet. A GitHub Actions workflow is configured to build an unsigned NSIS installer artifact on a pull request touching this subproject or by manual dispatch. Release workflows still need interactive acceptance. See [implementation status](IMPLEMENTATION_STATUS.md) for details.

## Planning documents

- [Product specification](PRODUCT_SPEC.md): users, goals, scope, workflows, and acceptance requirements.
- [Technical plan](TECHNICAL_PLAN.md): architecture and data boundaries.
- [UX specification](UX_SPEC.md): screen structure and interaction rules.
- [Decision register](DECISIONS.md): resolved and open decisions.
- [Windows release roadmap](ROADMAP.md): stages and exit criteria.
- [Test and release plan](TEST_RELEASE_PLAN.md): verification gates.
- [Implementation status](IMPLEMENTATION_STATUS.md): current code, verification, and remaining blockers.
- [Localization plan](LOCALIZATION_PLAN.md): twelve-language scope, RTL requirements, and acceptance gate.
- [AGENTS.md](AGENTS.md): project-specific agent instructions.

## Development

From `app/`:

```sh
npm install
npm run dev
```

To run the Tauri shell, install the platform prerequisites documented by Tauri and run:

```sh
npm run tauri dev
```

Build the Windows NSIS installer on a Windows machine with the Tauri prerequisites installed:

```sh
npm run tauri build
```
