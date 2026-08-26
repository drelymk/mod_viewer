"""Evergreen WebView2 Runtime detection.

The runtime is a shared system component that cannot be bundled into the exe
(only its managed wrappers can). It ships with Windows 11 and reaches Windows 10
via Windows Update, but on an older or freshly-imaged machine it may be missing
— in which case pywebview dies with an opaque .NET stack trace. Detect it up
front and show something actionable instead.
"""

import sys

DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

# Registry client ID published by Microsoft for the Evergreen Runtime.
_CLIENT_KEY = r"Software\Microsoft\EdgeUpdate\Clients" \
              r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def is_installed():
    """True if the Evergreen WebView2 Runtime is registered on this machine.

    Checked per-machine (both registry views — a 64-bit process sees the runtime
    under WOW6432Node, not the native view) and per-user. Anything other than
    Windows is reported as fine so this never blocks other platforms.
    """
    if sys.platform != "win32":
        return True
    import winreg

    roots = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_CURRENT_USER,  0)]
    for root, view in roots:
        try:
            with winreg.OpenKey(root, _CLIENT_KEY, 0, winreg.KEY_READ | view) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            if version and version != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def report_missing():
    """Tell the user what's missing, and offer to open the download page."""
    title = "3DMigoto Mod Viewer - Missing component"
    message = (
        "This app needs the Microsoft Edge WebView2 Runtime, which is not "
        "installed on this PC.\n\n"
        "It is a free Microsoft component (already included in Windows 11).\n\n"
        "Open the download page now?\n\n"
        f"{DOWNLOAD_URL}"
    )
    print(f"{title}\n\n{message}", file=sys.stderr)
    if sys.platform != "win32":
        return
    try:
        import ctypes
        MB_YESNO, MB_ICONERROR, IDYES = 0x4, 0x10, 6
        if ctypes.windll.user32.MessageBoxW(None, message, title,
                                            MB_YESNO | MB_ICONERROR) == IDYES:
            import webbrowser
            webbrowser.open(DOWNLOAD_URL)
    except Exception:
        pass   # no GUI available — the stderr message above still stands
