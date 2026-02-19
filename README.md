# Ludo Titan Automation

Automation bot for Ludo running in LDPlayer, plus a small license server used to authorize users.

**Repository Layout**
- `ludo_bot.py`: Main automation bot (ADB + OCR).
- `license_server/`: Flask license server and admin UI.
- `render.yaml`: Render deployment config for the license server.
- `build_exe.ps1`: Optional PyInstaller build script.
- `prepare_easyocr_models.ps1`: Optional helper to bundle EasyOCR models.

**Prerequisites (Bot)**
- Windows machine with LDPlayer installed.
- Python 3.10+ (tested with current dependencies).
- ADB available via LDPlayer or Android SDK.

**Quick Start (Bot)**
1. `python -m pip install -r requirements.txt`
2. Set LDPlayer to `1080x1920` (portrait).
3. Update `LICENSE_CHECK_URL` in `ludo_bot.py` to your license server.
4. Run the bot (example): `python ludo_bot.py --club #bcaf9533`
5. Run the bot with a specific device (example): `python ludo_bot.py --serial 127.0.0.1:5555 --club #bcaf9533`

**License Server (Local)**
1. `cd license_server`
2. Copy `.env.example` to `.env` and fill in values.
3. `python -m pip install -r requirements.txt`
4. `python app.py`

**License Server (Render)**
- Push to GitHub, then create a Render Web Service from this repo.
- Render will pick up `render.yaml`. Set the required env vars from `.env.example`.

**Build EXE (Optional)**
- `build_exe.ps1` will install PyInstaller and build `dist/LudoTitan.exe`.
- `prepare_easyocr_models.ps1` downloads EasyOCR models into `easyocr_models/` for offline bundling.
- `easyocr_models/`, `build/`, `dist/`, and `release/` are ignored by Git.

**Notes**
- The bot reads `popup_template.png` at runtime. Keep it alongside `ludo_bot.py`.
- Do not commit `.env` or any `licenses.db` files to GitHub.
