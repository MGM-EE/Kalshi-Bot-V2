"""Native Windows dialog; no private key is entered into a webpage or command line."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.credentials import CredentialError, LocalCredentialStore, read_credentials


def main():
    if os.name != "nt":
        raise SystemExit("Run this helper on the Windows PC where you will run the terminal.")
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        raise SystemExit(
            "Install Python's Tcl/Tk component, then rerun this setup helper."
        ) from None

    store = LocalCredentialStore()
    root = tk.Tk()
    root.title("Kalshi Terminal — local credentials")
    root.resizable(False, False)
    key_id, path = tk.StringVar(), tk.StringVar()
    tk.Label(
        root,
        text="Import the Kalshi API key ID and downloaded private key.\n"
        "The copy is encrypted for your Windows account.\n"
        "The browser and cloud environment do not receive this key.",
        justify="left",
    ).grid(row=0, column=0, columnspan=2, padx=16, pady=12)
    tk.Label(root, text="API key ID").grid(row=1, column=0, sticky="w", padx=16)
    tk.Entry(root, textvariable=key_id, width=50).grid(
        row=2, column=0, columnspan=2, padx=16, pady=6
    )
    tk.Label(root, text="Downloaded private key").grid(row=3, column=0, sticky="w", padx=16)
    tk.Entry(root, textvariable=path, width=50, state="readonly").grid(
        row=4, column=0, columnspan=2, padx=16
    )

    def browse():
        selected = filedialog.askopenfilename(
            title="Select Kalshi private key",
            filetypes=[("Private key files", "*.pem *.key *.txt"), ("All files", "*.*")],
        )
        if selected:
            path.set(selected)

    def save():
        try:
            if not path.get():
                raise CredentialError("Select your downloaded private key file")
            credentials = read_credentials(key_id.get().strip(), path.get())
            replace = store.path.exists()
            if replace and not messagebox.askyesno(
                "Replace?", "Replace the credentials already stored for this Windows user?"
            ):
                return
            store.save(credentials, replace=replace)
        except (CredentialError, OSError) as exc:
            messagebox.showerror(
                "Import failed",
                str(exc)
                if isinstance(exc, CredentialError)
                else "Could not read or save the selected file.",
            )
            return
        messagebox.showinfo(
            "Saved locally",
            "Credentials are encrypted for this Windows account.\n"
            "Restart the terminal to connect.\n"
            "This import validates the key format, not Kalshi account access.",
        )
        root.destroy()

    def remove():
        if messagebox.askyesno(
            "Remove?",
            "Remove this app's encrypted credential copy?\n"
            "This does not revoke your key at Kalshi or delete the original download.",
        ):
            try:
                store.remove()
            except OSError:
                messagebox.showerror("Remove failed", "Windows could not remove the local store.")
                return
            root.destroy()

    tk.Button(root, text="Choose key file…", command=browse).grid(
        row=5, column=0, padx=16, pady=10, sticky="w"
    )
    tk.Button(root, text="Save encrypted copy", command=save).grid(
        row=6, column=0, padx=16, pady=12, sticky="w"
    )
    tk.Button(root, text="Remove stored copy", command=remove).grid(
        row=6, column=1, padx=16, pady=12
    )
    root.mainloop()


if __name__ == "__main__":
    main()
