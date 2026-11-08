"""Inert Windows Sandbox baseline. No files are exported and nothing is launched."""
from xml.etree.ElementTree import Element, SubElement, tostring


def restricted_profile() -> str:
    """Prepare a capability-denial profile for future isolated negative tests."""
    root = Element("Configuration")
    for feature in ("Networking", "vGPU", "AudioInput", "VideoInput",
                    "ProtectedClient", "ClipboardRedirection", "PrinterRedirection"):
        SubElement(root, feature).text = "Enable" if feature == "ProtectedClient" else "Disable"
    # The baseline has no mapped folders or logon command.
    return tostring(root, encoding="unicode")
