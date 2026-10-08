"""
Five-line task frame.

Rol and Çerçeve define who the model is and which strategy mold it uses,
so they ride on the system message. Brief, Kısıt, and Çıktı are the task
itself, so they ride on the user message. Blank lines are omitted.
"""

from __future__ import annotations

from dataclasses import dataclass


# (field, label, hint) — order is the on-screen order from the template.
FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("rol", "01 Rol", "hangi kimlikten konuşuyorsun"),
    ("cerceve", "02 Çerçeve", "hangi iş çerçevesini referans alıyorsun"),
    ("brief", "03 Brief", "tasarlanacak şey, tek cümle"),
    ("kisit", "04 Kısıt", "tipografi, ölçek, design system"),
    ("cikti", "05 Çıktı", "format ve teslim yüzeyi"),
)

FORM_INTRO = (
    "Beş satır. Boş bırakılan alan önceki turun değerini korur (Enter ile kabul). "
    "Rol ve Çerçeve system mesajına, Brief / Kısıt / Çıktı user mesajına gider. "
    "Tümü boşsa serbest isteğe düşersin. Slash komutu ilk satıra yazılır."
)

_ROLE_NOTE = "Rol, hangi kimlikten yazacağını belirler ve genel asistan sesinin önüne geçer."
_FRAME_NOTE = "Çerçeve bu işin kalıbıdır; strateji yalnızca bu satıra yüklenir."
_BRIEF_NOTE = "Brief tek cümlelik niyettir; fazlasını ekleme."
_CONSTRAINT_NOTE = "Kısıt, çerçevedeki stratejiyi görünür tutan ölçektir."
_OUTPUT_NOTE = "Çıktı, format ve teslim yüzeyidir."


def field_prompt(label: str, hint: str, default: str = "") -> str:
    if default:
        shown = default if len(default) <= 40 else default[:37] + "..."
        return f"  {label:<12} [{hint}] (Enter=önceki: {shown}) > "
    return f"  {label:<12} [{hint}] > "


@dataclass
class PromptFrame:
    rol: str = ""
    cerceve: str = ""
    brief: str = ""
    kisit: str = ""
    cikti: str = ""

    def __post_init__(self) -> None:
        self.rol = (self.rol or "").strip()
        self.cerceve = (self.cerceve or "").strip()
        self.brief = (self.brief or "").strip()
        self.kisit = (self.kisit or "").strip()
        self.cikti = (self.cikti or "").strip()

    def is_empty(self) -> bool:
        return not any(
            (self.rol, self.cerceve, self.brief, self.kisit, self.cikti)
        )

    @classmethod
    def from_namespace(cls, args) -> "PromptFrame":
        def pick(name: str) -> str:
            return (getattr(args, name, None) or "").strip()

        return cls(
            rol=pick("rol"),
            cerceve=pick("cerceve"),
            brief=pick("brief"),
            kisit=pick("kisit"),
            cikti=pick("cikti"),
        )


def collect_prompt_frame(read_line, previous: "PromptFrame | None" = None) -> PromptFrame | str:
    """Reads the five lines.

    If the first line is a slash command, it is returned as-is and the
    remaining lines are not asked.

    When `previous` is given, each field shows the prior turn's value and a
    blank answer keeps it (Enter accepts the default).
    """
    values: dict[str, str] = {}
    for index, (key, label, hint) in enumerate(FIELD_SPECS):
        prev_val = getattr(previous, key, "") if previous else ""
        raw = (read_line(field_prompt(label, hint, prev_val)) or "").strip()
        if index == 0 and raw.startswith("/"):
            return raw
        values[key] = raw if raw else prev_val
    return PromptFrame(**values)


def identity_block(frame: PromptFrame) -> str:
    """System-side block. Empty when both Rol and Çerçeve are blank."""
    notes: list[str] = []
    lines: list[str] = []
    if frame.rol:
        notes.append(_ROLE_NOTE)
        lines.append(f"Rol: {frame.rol}")
    if frame.cerceve:
        notes.append(_FRAME_NOTE)
        lines.append(f"Çerçeve: {frame.cerceve}")
    if not lines:
        return ""
    return "GÖREV KALIBI\n" + "\n".join([*notes, *lines])


def compose_system(base: str, frame: PromptFrame) -> str:
    block = identity_block(frame)
    if not block:
        return base
    return base.rstrip() + "\n\n" + block


def build_user_content(frame: PromptFrame, extra: str = "") -> str:
    """User-side task. Blank fields are dropped. `extra` is a free-text add-on."""
    notes: list[str] = []
    lines: list[str] = []
    if frame.brief:
        notes.append(_BRIEF_NOTE)
        lines.append(f"Brief: {frame.brief}")
    if frame.kisit:
        notes.append(_CONSTRAINT_NOTE)
        lines.append(f"Kısıt: {frame.kisit}")
    if frame.cikti:
        notes.append(_OUTPUT_NOTE)
        lines.append(f"Çıktı: {frame.cikti}")

    chunks: list[str] = []
    if lines:
        chunks.append("GÖREV\n" + "\n".join([*notes, *lines]))
    extra = (extra or "").strip()
    if extra:
        chunks.append(extra if not chunks else f"Ek istek:\n{extra}")
    if not chunks:
        return "Verilen rol ve çerçeveye göre ilerle."
    return "\n\n".join(chunks)


def delivery_summary(frame: PromptFrame) -> str:
    """Short routing description of the fields that will actually be sent."""
    parts: list[str] = []
    if frame.rol:
        parts.append("Rol→system")
    if frame.cerceve:
        parts.append("Çerçeve→system")
    if frame.brief:
        parts.append("Brief→user")
    if frame.kisit:
        parts.append("Kısıt→user")
    if frame.cikti:
        parts.append("Çıktı→user")
    return ", ".join(parts)
