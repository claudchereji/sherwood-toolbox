# Sherwood Toolbox

A local, offline-first desktop application that bundles several construction-estimating tools under one simple hub. It runs as a native desktop window on Linux (via pywebview) or in a browser for development.

Originally created by **Meat Claud & his Clanker**. This fork is maintained by the current owner, who is continuing to improve and package the application.

## What it does

Sherwood Toolbox is a modular Flask application that helps with common estimating workflows:

- **Estimate Enhancer** — Upload an Xactimate PDF, detect zero-quantity line items and duplicate photo names, add intra-document image links, highlight custom terms, and append IRC reference documents.
- **Ice & Water Shield Calculator** — Client-side coverage math for ice-and-water-shield installations.
- **Photo Report** — Build a branded photo-report PDF from a job's images, with company logo and header/footer.
- **Documents** — Generate invoices and certificates of completion, with CRM auto-fill, line items, signatures, and company branding.

All processing happens locally. Uploaded files are written to a per-user data directory and cleaned up after use. No data leaves the machine unless you explicitly use the optional CRM fetch feature.

## Installation

### Recommended: `.deb` package (Debian / Ubuntu / Zorin)

```bash
sudo dpkg -i sherwood-toolbox_0.2.0_amd64.deb
```

If dependency errors appear, run:

```bash
sudo apt-get install -f
```

Then launch from the app grid or run `sherwood-toolbox` in a terminal.

### Portable tarball

For distributions without `dpkg`, or for a browser-based launch:

```bash
tar -xzf sherwood-toolbox.tar.gz
cd sherwood-toolbox
./run/install-standalone.sh
sherwood-toolbox
```

See `SHARING.md` for detailed distribution instructions.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install --no-deps -e vendor/restoration-common
python3 run/standalone.py
```

Then open the URL printed in the terminal.

## Project structure

- `toolbox/` — Flask app, blueprints, shared templates, and static assets.
- `toolbox/tools/<tool>/` — One tool per package (Estimate Enhancer, IWS, Photo Report, Documents).
- `vendor/restoration-common/` — Vendored headless PDF generators and CRM helpers.
- `run/` — Launchers and packaging scripts.
- `debian/` — Debian package metadata.
- `STRUCTURE.md` — Full path-by-path reference.
- `AGENTS.md` — Guidance for coding agents working on the project.
- `CHANGELOG.md` — Release history.

## CRM integration

Photo Report and Documents can auto-fill customer and job fields from the CRM.
The CRM base URL defaults to `https://office.publicadjustermidwest.com` and can
be overridden with the `TOOLBOX_CRM_BASE_URL` environment variable. The scraper
first looks for a custom field labeled **CRM Job/ID**; if that field is empty or
missing, it falls back to searching the page text for a state-ZIP pattern.

## License

See `LICENSE` (to be added).
