# Changelog

All notable changes to Sherwood Toolbox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-30

### Added
- Native desktop window via pywebview (`run/desktop.py`). The `.deb` now opens
  the app in its own window instead of a browser tab.
- Native Save As dialog for generated PDFs/ZIPs in the desktop build, using the
  pywebview JS bridge.
- Sidebar shortcuts: **Code Docs** opens the Estimate Enhancer attachments
  folder; **Archive** opens the local uploads cache folder.
- Company color theming: Documents and Photo Report panels and generate buttons
  tint faintly with the selected company's brand color.
- `CRM Job/ID` custom field lookup in the CRM scraper, with fallback to the
  previous state-ZIP search.
- Configurable CRM base URL via the `TOOLBOX_CRM_BASE_URL` environment variable.
- Separate toolbox icon for the OS dock/app grid; the Sherwood brand logo
  remains in the sidebar.
- `.deb` packaging with `run/build-deb.sh` and metadata under `debian/`.
- `AGENTS.md` and this `CHANGELOG.md`.

### Changed
- CRM login URL default changed from `https://office.vanguardadj.com` to
  `https://office.publicadjustermidwest.com`.
- Highlight color dropdowns in Estimate Enhancer now use a readable white
  background with colored borders and a color swatch indicator.
- `.desktop` file uses absolute paths so the app appears correctly in GNOME and
  other freedesktop menus.
- `STRUCTURE.md` and `SHARING.md` updated for the new desktop build and
  packaging flow.

### Removed
- Spell checker from Estimate Enhancer (`pyspellchecker`, `spell_utils.py`,
  `spell_vocab.py`) and related UI text.

### Fixed
- GNOME app grid not showing Sherwood Toolbox due to placeholder paths in the
  `.desktop` file.
- PDF downloads in the desktop build turning the window into an inline PDF
  viewer.
- Highlight dropdown text being unreadable for green, blue, purple, and coral
  options.

## [0.1.0] - 2026-06-17

### Added
- Initial browser-based Sherwood Toolbox: Flask hub with Estimate Enhancer,
  Ice & Water Shield Calculator, Photo Report, and Documents tools.
- Portable tarball installer (`run/install-standalone.sh`).
- Vendored `restoration_common` for PDF generation and CRM helpers.
- Local-only operation with uploads written to `~/.local/share/sherwood-toolbox/`.

[Unreleased]: https://github.com/kachapman/sherwood-toolbox/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kachapman/sherwood-toolbox/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kachapman/sherwood-toolbox/releases/tag/v0.1.0
