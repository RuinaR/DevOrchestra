"""Windows Command Center visual tokens and reusable Tk control skins."""
import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import font, ttk
from xml.etree import ElementTree

BG, PANEL, TILE, EDGE = "#121212", "#1c1c1c", "#272727", "#505050"
RAIL, HERO, HOVER = "#171717", "#303030", "#383838"
INK, MUTED, CYAN = "#f5f5f5", "#bcbcbc", "#a8ceff"
TEAL, AMBER, RED = "#75e2c0", "#ffd18a", "#ff9eae"
ACCENT, ACCENT_HOVER = "#354b67", "#405a7b"
GAP, CARD_PAD, RADIUS = 12, 16, 8
ASSETS = Path(__file__).resolve().parent / "ui_assets"
ASSET_FILES = ("Pretendard-Regular.otf", "Pretendard-OFL.txt", "terminal.svg",
               "Lucide-LICENSE.txt", "Feather-LICENSE.txt")


def brand_icon(parent):
    canvas = tk.Canvas(parent, width=40, height=40, bg=BG, highlightthickness=0)
    canvas.create_rectangle(2, 2, 38, 38, outline=EDGE, fill=HERO)
    svg = ElementTree.parse(ASSETS / "terminal.svg").getroot()
    for item in svg:
        if item.tag.endswith("polyline"):
            points = [float(value) for value in item.attrib["points"].split()]
        elif item.tag.endswith("line"):
            points = [float(item.attrib[key]) for key in ("x1", "y1", "x2", "y2")]
        else:
            continue
        canvas.create_line(*(8 + value for value in points), width=2, fill=CYAN,
                           capstyle=tk.ROUND, joinstyle=tk.ROUND)
    return canvas


def _private_font(root):
    if os.name != "nt":
        return
    path = ASSETS / "Pretendard-Regular.otf"
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    for function in (gdi.AddFontResourceExW, gdi.RemoveFontResourceExW):
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p)
    if gdi.AddFontResourceExW(str(path), 0x10, None):
        def release(event):
            if event.widget is root:
                gdi.RemoveFontResourceExW(str(path), 0x10, None)
        root.bind("<Destroy>", release, add="+")


def _rounded_image(root, fill, stroke, scale, surface):
    radius = max(4, round(RADIUS * scale))
    side = radius * 2 + 5
    image = tk.PhotoImage(master=root, width=side, height=side)
    image.put(surface, to=(0, 0, side, side))

    def inside(x, y, inset):
        r = radius - inset
        px = max(radius - x, x - (side - 1 - radius), 0)
        py = max(radius - y, y - (side - 1 - radius), 0)
        return px * px + py * py <= r * r and inset <= x < side - inset and inset <= y < side - inset

    for y in range(side):
        for x in range(side):
            if inside(x, y, 0):
                image.put(fill if inside(x, y, 1) else stroke, (x, y))
    return image, radius + 1


def install(root):
    _private_font(root)
    families = {name.casefold(): name for name in font.families(root)}
    family = next((families[name] for name in ("pretendard", "맑은 고딕", "malgun gothic") if name in families),
                  "Segoe UI")
    root.ui_font = family
    root.ui_images = []
    for control in ("Text", "Listbox"):
        options = {"background": TILE, "foreground": INK, "selectBackground": EDGE, "selectForeground": INK,
                   "highlightBackground": EDGE, "highlightColor": CYAN, "highlightThickness": 1,
                   "font": f"{{{family}}} 11", "relief": "flat"}
        if control == "Text":
            options.update(insertBackground=INK, padX=10, padY=8)
        for key, value in options.items():
            root.option_add(f"*{control}.{key}", value)
    root.option_add("*TCombobox*Listbox.background", TILE)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=PANEL, foreground=INK, fieldbackground=TILE, bordercolor=EDGE,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=BG, font=(family, 10))
    scale = min(2, max(1, float(root.tk.call("tk", "scaling")) / (96 / 72)))
    for name, fill, hover, surface in (("TButton", TILE, HOVER, PANEL),
                                       ("Primary.TButton", ACCENT, ACCENT_HOVER, PANEL),
                                       ("Hero.TButton", TILE, HOVER, HERO),
                                       ("Rail.TButton", RAIL, HERO, RAIL),
                                       ("RailActive.TButton", HERO, HOVER, RAIL)):
        element = name.replace(".", "") + ".skin"
        frames = [_rounded_image(root, color, border, scale, surface) for color, border in (
            (fill, EDGE), (PANEL, EDGE), (ACCENT, CYAN), (hover, CYAN), (hover, EDGE),
        )]
        root.ui_images.extend(image for image, _ in frames)
        normal, disabled, pressed, focused, active = (image for image, _ in frames)
        style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", pressed),
                             ("focus", focused), ("active", active), border=frames[0][1], sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nswe"})]})
        ]})])
        style.configure(name, padding=(12, 8), anchor="w" if name.startswith("Rail") else "center")
        style.map(name, foreground=[("disabled", MUTED)])
    for control in ("TEntry", "TCombobox", "TCheckbutton", "TNotebook.Tab"):
        style.configure(control, padding=(6, 5))
        style.map(control, background=[("disabled", PANEL), ("selected", HERO), ("active", TILE)],
                  bordercolor=[("invalid", RED), ("focus", CYAN)])
    style.map("TCombobox", fieldbackground=[("readonly", TILE)], selectbackground=[("readonly", EDGE)],
              selectforeground=[("readonly", INK)])
    style.configure("Treeview", rowheight=round(27 * scale), background=TILE, fieldbackground=TILE)
    style.map("Treeview", background=[("selected", HERO)])
    style.configure("Treeview.Heading", padding=(8, 6), background=PANEL)
    style.configure("Vertical.TScrollbar", width=11, background=EDGE, troughcolor=BG, arrowsize=9)
    style.map("Vertical.TScrollbar", background=[("active", CYAN)])
