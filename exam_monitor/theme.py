THEMES = {
    "light": {
        "bg": "#F4F6F8",
        "surface": "#FFFFFF",
        "surface_alt": "#F0F3F6",
        "surface_hover": "#E3E8EE",
        "border": "#D8DEE6",
        "sidebar": "#FBFCFE",
        "video_bg": "#0A0B0D",
        "text": "#161A20",
        "text_muted": "#596575",
        "text_dim": "#7A8696",
        "white": "#FFFEFE",
        "black": "#010101",
        "primary": "#111827",
        "primary_text": "#F8FAFC",
        "primary_hover": "#273244",
        "good": "#16845B",
        "good_dim": "#E7F5EF",
        "warning": "#A96500",
        "warning_dim": "#FFF2D8",
        "danger": "#D63F4B",
        "danger_dim": "#FCECEF",
        "danger_hover": "#F7DDE2",
        "info": "#3F6F9F",
    },
    "dark": {
        "bg": "#090A0C",
        "surface": "#111318",
        "surface_alt": "#171A20",
        "surface_hover": "#20242B",
        "border": "#2A2E36",
        "sidebar": "#0D0F12",
        "video_bg": "#050506",
        "text": "#F5F7FA",
        "text_muted": "#9AA2AF",
        "text_dim": "#68717E",
        "white": "#FFFFFF",
        "black": "#000000",
        "primary": "#F7F8FA",
        "primary_text": "#08090B",
        "primary_hover": "#E9EDF2",
        "good": "#D7FF5F",
        "good_dim": "#273018",
        "warning": "#F4C95D",
        "warning_dim": "#332B18",
        "danger": "#FF6B6B",
        "danger_dim": "#351C20",
        "danger_hover": "#46242A",
        "info": "#A9B7C9",
    },
}


# Updated in place so modules importing COLORS keep the same live palette.
COLORS = dict(THEMES["light"])


def set_theme(name: str) -> dict[str, str]:
    selected = name if name in THEMES else "light"
    COLORS.clear()
    COLORS.update(THEMES[selected])
    return COLORS

FONTS = {
    "display": ("Segoe UI Semibold", 24),
    "h1": ("Segoe UI Semibold", 20),
    "h2": ("Segoe UI Semibold", 14),
    "body": ("Segoe UI", 10),
    "body_medium": ("Segoe UI Semibold", 10),
    "small": ("Segoe UI", 9),
    "tiny": ("Segoe UI Semibold", 8),
    "mono": ("Cascadia Mono", 9),
}
