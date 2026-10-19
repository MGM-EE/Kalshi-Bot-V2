# Get the Windows app running

The app uses paper money only. Downloading a new copy restores the program, not
paper trades or recordings deleted from your PC.

## Updating an existing installation

1. Stop the old backend with **Ctrl+C** in its terminal and wait for shutdown.
2. Make a backup copy of its entire `data` folder. Keep the old program folder
   with old recordings; replay checks the matching backend version.
3. Download the latest ZIP from the build link below and extract it to a new,
   permanent folder outside OneDrive.
4. Copy the old `data` folder into the new program folder **before first launch**.
   Copy your custom configuration too if you use one. Keep any configured external
   account/configuration paths pointing to their existing files.
5. Run **START_HERE.bat** in the new folder to install/build the updated files and
   recreate the desktop shortcut. Then run **OPEN_TERMINAL.bat**.

The Windows-encrypted API key is stored outside the installation folder and can
be reused by the same Windows user. Re-import it only if the status panel says it
is missing or cannot be unlocked. Use only one running backend for an account.
The new recovery code takes effect only after restarting the old backend and
rebuilding the dashboard; refreshing a browser connected to the old app is insufficient.

See [what changed in connectivity](connectivity_update.md) for recovery behavior
and remaining Windows/authenticated-feed acceptance checks.

## 1. Download and extract

Open [the recovery build on GitHub](https://github.com/MGM-EE/Kalshi-Bot-V2/tree/build/kalshi-terminal-v2),
then choose **Code → Download ZIP**. Right-click the ZIP in Downloads and choose
**Extract All**. Open the inner folder that contains `START_HERE.bat`.

Move the whole project folder somewhere permanent outside OneDrive, such as
`C:\Users\mikey\KalshiTerminal`. Do this **before** setup/creating a shortcut.
Do not run the app inside the ZIP viewer. If you have an old `data` folder in a
backup or Recycle Bin, restore it before starting; otherwise a new paper account
will be created. Do not copy over an existing account with a different one.

## 2. Set up once

Install **Python 3.12** with its Python launcher and **Node.js LTS (22+)** if they
are missing. After installing, close old terminal windows so Windows sees them.
Double-click **START_HERE.bat**. It installs the dependencies, builds the dashboard
and creates a **Kalshi Terminal** desktop shortcut. Wait for **Setup complete**.
If Windows hides filename extensions, START_HERE appears as a Windows Batch File.

Setup does not reset paper account data or remove stored API credentials.

## 3. Connect your Kalshi API key locally

In Kalshi account settings, find **API Keys** and create a key if you do not
already have its **API key ID** and **downloaded private-key file**. Use the
narrowest permissions offered for market data. These are not your login password.
If the private-key download has been lost, create a replacement key at Kalshi.

Double-click **CONNECT_KALSHI.bat**. Enter the API key ID, click **Choose key
file**, select the downloaded `.pem`, `.key` or text file, and click **Save
encrypted copy**. You do not need to type a private-key path. Keep the original
file outside this project and do not paste the file or its contents into chat.

The helper stores an encrypted copy for the current Windows user at
`%LOCALAPPDATA%\KalshiTerminalV2\credentials.dpapi`. This is outside the project,
so deleting the project alone usually does not delete the saved copy. If the
app already finds the key, you can reuse it; importing the same downloaded key
is also supported. Restart the app after importing/replacing credentials.

The key signs read-only Kalshi data requests, including authenticated streaming
and the settlement-reference feed. The browser never receives it. Public Kraken
prices and public Kalshi REST data need no key. Saving a key checks its format;
it does not prove account access or that every feed is available.

## 4. Open the app

Double-click **OPEN_TERMINAL.bat** or the **Kalshi Terminal** desktop icon. The
browser opens **http://127.0.0.1:8000**. Keep the app's terminal window open;
**Ctrl+C in that window** stops the backend. Closing the browser alone does not.
If the app is already running, opening the icon again opens its dashboard.

At the top of the page, **What's happening?** explains the app connection, API
access, required prices, paper-bot activity, saved data and recording. A fresh
account starts with new automatic buys paused. Pick a strategy and choose
**Enable paper entries** only when ready; all rules and data checks still apply.
A browser outage does not pause a running strategy. Use **Pause new entries**
when you want to pause it. Exits for existing positions can continue.

## 5. Save research data

The normal account automatically saves paper trades, balances and settings in
`data\paper.sqlite3`. Full market-data recording is off by default. To record,
stop any running app, then double-click **RECORD_PAPER_SESSION.bat**. It creates a
new uniquely named paper account and recording without changing your normal
account. The status panel confirms whether recording is active.

Recorded files are in `data\recordings`; recorded accounts are in `data\sessions`.
Stop with **Ctrl+C** and let shutdown finish so the recording can be replayed.
This records observations; it does not automatically train a prediction model.
Stop the app before copying the `data` folder for a backup. Keep a copy of the
matching program version with recordings; replay checks the backend version.

## If something goes wrong

- **WinError 64 / Accept failed on 127.0.0.1:8000:** Windows launches now select
  the Selector networking loop explicitly. This avoids the Proactor/IOCP accept
  path shown in that error. The launcher prints **Windows networking
  compatibility mode is enabled** when starting a new backend. Stop the old app
  with Ctrl+C before updating; opening another icon reuses an existing backend.
  To update an older installation, download the current build ZIP and replace
  only `C:\KalshiTerminal\scripts\launch.py` with `scripts\launch.py` from the
  extracted download, then reopen the desktop icon. No dependency installation,
  account reset, or credential import is required. Both normal and recorded
  launchers use this fix. Actual Windows verification remains a PC-side check.
- **App does not open:** the launcher now keeps startup errors visible. Copy the
  error text; do not delete `data` or reinstall over a saved paper account.
- **Backup updates:** the app is reachable through ordinary local requests while
  it restores the live connection. The dashboard still checks source freshness.
- **No fresh app update:** keep the terminal open and check its errors. Use the
  page's **Copy troubleshooting summary** button when asking for help.
- **Key found; waiting for Kalshi:** key presence is not authenticated access.
  There may be no current contract, a permission problem, or a network problem.
  Expand **Source health and diagnostics** for the actual source state.
- **Saved key ignored:** previously set `KALSHI_API_KEY_ID` and
  `KALSHI_PRIVATE_KEY_PATH` environment variables take precedence. Remove both
  if you intend to use the Windows encrypted store, then restart the app.
- **Shortcut stopped working after moving the folder:** open
  `scripts\create_desktop_shortcut.bat` from the new location to recreate it.
