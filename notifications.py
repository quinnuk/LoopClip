"""
notifications.py
-----------------
Best-effort Windows notifications with no extra dependencies.

Toasts are shown via a short PowerShell script (Windows ships PowerShell
and the WinRT toast APIs it uses), so nothing new needs to be installed.
If that fails for any reason - wrong OS, PowerShell missing, WinRT not
available - it fails silently. A background download finishing is not
worth interrupting the user over a notification that couldn't be shown.
"""

import subprocess
import sys

_CREATE_NO_WINDOW = 0x08000000


def show_toast(title: str, message: str) -> None:
    if sys.platform != "win32":
        return
    safe_title = title.replace('"', "'").replace("`", "'")
    safe_message = message.replace('"', "'").replace("`", "'")
    ps_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$text = $template.GetElementsByTagName('text'); "
        f"$text.Item(0).AppendChild($template.CreateTextNode(\"{safe_title}\")) | Out-Null; "
        f"$text.Item(1).AppendChild($template.CreateTextNode(\"{safe_message}\")) | Out-Null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Video Downloader Pro').Show($toast)"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            creationflags=_CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def play_sound() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:  # noqa: BLE001 - a missed notification sound is never worth crashing over
        pass
