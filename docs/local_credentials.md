# Connect Kalshi on your Windows PC

The app needs Kalshi API authentication for its WebSocket connection, including
public books/trades, lifecycle updates, and CF Benchmarks settlement-reference
data. Public Kalshi REST and Kraken Spot feeds require no credentials. This app
has no real-money order transport; it uses the key for signed data requests only.
The credential may have broader Kalshi permissions than this app uses: use the
narrowest available permission scope and revoke it at Kalshi if compromised.

1. In Kalshi's account settings, create an API key. Keep the **API key ID** and
   download its **private key file**. These are different from your login/password.
2. Install the terminal locally with `scripts/setup_windows.ps1`.
3. Double-click `scripts/configure_kalshi.bat`. Enter the API key ID in the native
   Windows dialog and choose the downloaded `.pem`/`.key` file. Click **Save
   encrypted copy**. No key is entered into the browser or sent to this chat.
4. Run/restart `scripts/start_windows.bat` under the same Windows account.
   The backend unlocks the local store in memory, signs requests, and connects
   directly to Kalshi. Check `kalshi:socket` and per-asset reference/book/trade
   source health. Importing validates key format, not Kalshi account permissions.
5. Run `scripts/self_test.ps1` locally; the real DPAPI test runs only on Windows.
   For actual feed acceptance, run `.venv\Scripts\python.exe scripts\live_check.py`.

The encrypted app copy lives at
`%LOCALAPPDATA%\KalshiTerminalV2\credentials.dpapi`, outside the repository.
Windows DPAPI protects it for the current Windows user. There is no plaintext
fallback. The original downloaded file remains yours; keep it outside Git with
restricted access. The app does not delete it. Software running as your logged-in
Windows user can potentially access the same store, so account security matters.

To replace/remove the app copy, reopen the setup helper. Replacement requires
confirmation. Removing a local copy does not revoke the key at Kalshi; restart
the terminal after any credential change. Existing `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH` environment bindings take precedence. Clear both to use
the Windows store. A partial environment binding is an error, not a silent fallback.

`GET /api/credentials/status` exposes only presence/source/scope, never key IDs,
private key contents, or file paths. Presence does not prove successful access.

## Cloud versus PC

This cloud development environment cannot read files or the Windows credential
store on your PC. Run the terminal on your PC to keep the private key there.
The cloud instance would need separately supplied secure signing credentials to
connect itself. A PC-to-cloud market-data bridge is a different deployment option
and is not implemented; no private-key forwarding or public PC listener is added.

Windows DPAPI and the native dialog cannot be executed in this Linux environment.
Cross-platform store/authentication tests pass using an injected encryption
provider; they do not establish real Windows behavior. The Windows-only test and
live connectivity check must run on your PC before claiming that connection works.
