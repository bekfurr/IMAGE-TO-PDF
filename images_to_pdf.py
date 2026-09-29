"""
Rasm → PDF Studio
=================
Sodda va zamonaviy dastur:
  1) Rasmlarni (bir xil formatdagi) bitta PDF ga jamlaydi
  2) Bir nechta PDF faylni bittaga birlashtiradi (sahifalarni burish mumkin)

Ishga tushirish:
    python images_to_pdf.py

Kerakli kutubxonalar:
    pip install Pillow pypdf        (pypdf faqat PDF birlashtirish uchun)
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

try:
    from pypdf import PdfReader, PdfWriter

    HAS_PYPDF = True
except ImportError:  # pypdf o'rnatilmagan bo'lsa ham dastur ishlayveradi
    HAS_PYPDF = False


# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

DPI = 150  # PDF sahifa sifati (piksel / dyuym)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
SUPPORTED_FORMATS = {"PNG", "JPEG", "BMP", "TIFF", "WEBP"}
FORMAT_ALIASES = {"MPO": "JPEG"}  # telefon JPEG lari ba'zan MPO deb aniqlanadi

FIT = "Rasm o'lchamida"
PAGES = {  # millimetrda (eni, bo'yi) — vertikal holatda
    FIT: None,
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
}
ORIENTATIONS = {"Avto": "auto", "Vertikal": "portrait", "Gorizontal": "landscape"}
ROTATIONS = {"0° (o'zgarishsiz)": 0, "90° o'ngga": 90, "180°": 180, "90° chapga": 270}

# Ranglar (yorug' zamonaviy mavzu)
BG = "#F3F4F8"
CARD = "#FFFFFF"
ACCENT = "#4F46E5"
ACCENT_HOVER = "#4338CA"
ACCENT_SOFT = "#E0E7FF"
TEXT = "#1F2937"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
PREVIEW_BG = "#ECEEF5"

PREVIEW_W, PREVIEW_H = 250, 196
FAMILY = "TkDefaultFont"  # setup_style() da haqiqiy shrift bilan almashtiriladi


# ---------------------------------------------------------------------------
# Yordamchi funksiyalar
# ---------------------------------------------------------------------------

def natural_key(path: str) -> list:
    """Tabiiy saralash: 1, 2, 10 (1, 10, 2 emas) va a, b, c."""
    name = os.path.basename(path)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def unique_path(folder: str, stem: str, ext: str = ".pdf") -> str:
    """Mavjud faylni ustidan yozmaslik uchun bo'sh nom topadi."""
    candidate = Path(folder) / f"{stem}{ext}"
    n = 1
    while candidate.exists():
        candidate = Path(folder) / f"{stem}_{n}{ext}"
        n += 1
    return str(candidate)


def open_in_viewer(path: str) -> None:
    """PDF ni standart PDF dasturida, bo'lmasa brauzerda ochadi."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        webbrowser.open(Path(path).resolve().as_uri())


def inspect_image(path: str) -> tuple[str, tuple[int, int]]:
    """
    Rasmni haqiqiy mazmuni bo'yicha tekshiradi (kengaytmaga ishonmaydi).
    (format, o'lcham) qaytaradi yoki ValueError ko'taradi.
    """
    try:
        with Image.open(path) as im:
            fmt = FORMAT_ALIASES.get(im.format, im.format)
            size = im.size
            im.verify()
    except Exception as exc:
        raise ValueError("rasm sifatida ochilmadi (buzilgan yoki rasm emas)") from exc
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"qo'llab-quvvatlanmaydigan format ({fmt})")
    return fmt, size


def load_rgb(path: str, thumb: int | None = None) -> Image.Image:
    """
    Rasmni RGB ko'rinishda yuklaydi:
    EXIF burilishini to'g'rilaydi, shaffof joylarni OQ rangga aylantiradi
    (oddiy convert('RGB') shaffoflikni qora qilib yuborardi).
    """
    with Image.open(path) as im:
        if thumb:
            im.draft("RGB", (thumb, thumb))  # JPEG uchun tezlashtiradi
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            out = Image.new("RGB", rgba.size, "white")
            out.paste(rgba, mask=rgba.getchannel("A"))
        else:
            out = im.convert("RGB")
    if thumb:
        out.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
    return out


def page_pixels(img_size: tuple[int, int], page_key: str, orientation: str,
                dpi: int = DPI) -> tuple[int, int]:
    """Sahifaning piksel o'lchamini hisoblaydi (yo'nalishni hisobga olib)."""
    mm = PAGES[page_key]
    if mm is None:
        w, h = img_size
    else:
        w, h = (round(v / 25.4 * dpi) for v in mm)
        if orientation == "auto" and img_size[0] > img_size[1]:
            w, h = h, w  # keng rasm — gorizontal sahifa
    if orientation == "portrait" and w > h:
        w, h = h, w
    elif orientation == "landscape" and h > w:
        w, h = h, w
    return w, h


def compose_page(img: Image.Image, page_key: str, orientation: str,
                 dpi: int = DPI) -> Image.Image:
    """Rasmni sahifaga proporsiyasini buzmasdan, markazga sig'diradi."""
    pw, ph = page_pixels(img.size, page_key, orientation, dpi)
    if (pw, ph) == img.size:
        return img
    scale = min(pw / img.width, ph / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    page = Image.new("RGB", (pw, ph), "white")
    page.paste(resized, ((pw - nw) // 2, (ph - nh) // 2))
    return page


def inspect_pdf(path: str) -> int:
    """PDF ni tekshiradi va sahifalar sonini qaytaradi (yoki ValueError)."""
    try:
        with open(path, "rb") as f:
            if b"%PDF" not in f.read(1024):
                raise ValueError("PDF fayl emas")
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("parol bilan himoyalangan")
        pages = len(reader.pages)
        if pages == 0:
            raise ValueError("sahifalar yo'q")
        return pages
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("buzilgan yoki o'qib bo'lmaydi") from exc


def report_skipped(skipped: list[tuple[str, str]]) -> None:
    if not skipped:
        return
    lines = [f"•  {name} — {why}" for name, why in skipped[:8]]
    if len(skipped) > 8:
        lines.append(f"…va yana {len(skipped) - 8} ta fayl")
    messagebox.showwarning("Ba'zi fayllar qo'shilmadi", "\n".join(lines))


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return ("Faylni saqlab bo'lmadi. U boshqa dasturda ochiq bo'lishi yoki "
                "papkaga yozish huquqi bo'lmasligi mumkin.")
    if isinstance(exc, MemoryError):
        return "Xotira yetmadi. Rasmlar sonini kamaytirib ko'ring."
    return str(exc)


# ---------------------------------------------------------------------------
# Asosiy ishlar (alohida oqimda bajariladi)
# ---------------------------------------------------------------------------

def images_to_pdf(paths: list[str], output: str, page_key: str,
                  orientation: str, progress) -> None:
    pages: list[Image.Image] = []
    total = len(paths)
    for i, path in enumerate(paths, 1):
        try:
            pages.append(compose_page(load_rgb(path), page_key, orientation))
        except Exception as exc:
            raise RuntimeError(f"{os.path.basename(path)}: {_friendly_error(exc)}") from exc
        progress(int(i / total * 90), f"Rasm tayyorlanmoqda: {i} / {total}")

    tmp = output + ".tmp"
    progress(93, "PDF saqlanmoqda…")
    try:
        pages[0].save(tmp, "PDF", save_all=True, append_images=pages[1:],
                      resolution=float(DPI))
        os.replace(tmp, output)  # yarim yozilgan fayl qolib ketmasligi uchun
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc)) from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    progress(100, f"Tayyor: {total} ta rasm → {os.path.basename(output)}")


def merge_pdfs(paths: list[str], output: str, rotation: int, progress) -> None:
    writer = PdfWriter()
    total = len(paths)
    pages_count = 0
    for i, path in enumerate(paths, 1):
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                reader.decrypt("")
            for page in reader.pages:
                if rotation:
                    page.rotate(rotation)
                writer.add_page(page)
                pages_count += 1
        except Exception as exc:
            raise RuntimeError(f"{os.path.basename(path)}: {_friendly_error(exc)}") from exc
        progress(int(i / total * 90), f"PDF qo'shilmoqda: {i} / {total}")

    tmp = output + ".tmp"
    progress(93, "PDF saqlanmoqda…")
    try:
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, output)
    except Exception as exc:
        raise RuntimeError(_friendly_error(exc)) from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    progress(100, f"Tayyor: {total} ta PDF, {pages_count} bet → {os.path.basename(output)}")


# ---------------------------------------------------------------------------
# Dizayn
# ---------------------------------------------------------------------------

def pick_font_family(root: tk.Tk) -> str:
    available = set(tkfont.families(root))
    for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter",
                 "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans"):
        if name in available:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def setup_style(root: tk.Tk) -> str:
    global FAMILY
    family = pick_font_family(root)
    FAMILY = family
    for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(font_name).configure(family=family, size=10)

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                focuscolor=CARD, troughcolor=BORDER)

    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD)
    s.configure("TLabel", background=BG, foreground=TEXT)
    s.configure("Card.TLabel", background=CARD, foreground=TEXT)
    s.configure("Muted.TLabel", background=BG, foreground=MUTED)
    s.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    s.configure("Section.TLabel", background=CARD, foreground=MUTED,
                font=(family, 8, "bold"))

    s.configure("TButton", background="#F1F2F7", foreground=TEXT, bordercolor="#D9DCE6",
                lightcolor="#F1F2F7", darkcolor="#F1F2F7", padding=(12, 6), relief="flat")
    s.map("TButton",
          background=[("active", ACCENT_SOFT), ("disabled", "#F7F7FA")],
          lightcolor=[("active", ACCENT_SOFT)], darkcolor=[("active", ACCENT_SOFT)],
          foreground=[("disabled", "#9CA3AF")])

    s.configure("Accent.TButton", background=ACCENT, foreground="white",
                bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                padding=(22, 10), font=(family, 10, "bold"))
    s.map("Accent.TButton",
          background=[("active", ACCENT_HOVER), ("disabled", "#A5A8E8")],
          foreground=[("disabled", "white")],
          bordercolor=[("active", ACCENT_HOVER)])

    s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                padding=(20, 9), borderwidth=0, font=(family, 10, "bold"))
    s.map("TNotebook.Tab",
          background=[("selected", CARD)], foreground=[("selected", ACCENT)])

    s.configure("TCombobox", fieldbackground=CARD, background=CARD,
                bordercolor=BORDER, arrowcolor=MUTED, padding=5)
    s.map("TCombobox", fieldbackground=[("readonly", CARD)],
          bordercolor=[("focus", ACCENT)])
    s.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER, padding=6)
    s.map("TEntry", fieldbackground=[("readonly", "#F9FAFB")])

    s.configure("Card.TCheckbutton", background=CARD, foreground=TEXT,
                indicatorbackground=CARD, indicatorforeground=ACCENT,
                upperbordercolor="#9CA3AF", lowerbordercolor="#9CA3AF")
    s.map("Card.TCheckbutton", background=[("active", CARD)],
          indicatorbackground=[("selected", CARD), ("active", CARD)],
          indicatorforeground=[("selected", ACCENT)])

    s.configure("Horizontal.TProgressbar", troughcolor="#E5E7EB", background=ACCENT,
                bordercolor="#E5E7EB", lightcolor=ACCENT, darkcolor=ACCENT,
                thickness=8)
    s.configure("Vertical.TScrollbar", background="#D1D5DB", troughcolor=CARD,
                bordercolor=CARD, arrowcolor=MUTED)
    return family


# ---------------------------------------------------------------------------
# Umumiy ro'yxat vidjeti
# ---------------------------------------------------------------------------

class ItemList(ttk.Frame):
    """Tartiblanadigan fayllar ro'yxati (yuqoriga / pastga / o'chirish)."""

    def __init__(self, master, label_fn, on_select, on_change):
        super().__init__(master, style="Card.TFrame")
        self.items: list[dict] = []
        self.label_fn = label_fn
        self.on_select = on_select
        self.on_change = on_change

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(
            self, activestyle="none", selectmode="browse", exportselection=False,
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT, bg=CARD, fg=TEXT, selectbackground=ACCENT_SOFT,
            selectforeground=TEXT, borderwidth=0, height=8,
        )
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self.on_select())
        self.listbox.bind("<Delete>", lambda _e: self.remove())

        bar = ttk.Frame(self, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="↑", width=3, command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(bar, text="↓", width=3, command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Button(bar, text="O'chirish", command=self.remove).pack(side="left")
        ttk.Button(bar, text="Tozalash", command=self.clear).pack(side="left", padx=4)

    def selected(self) -> int | None:
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def refresh(self, select: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for n, item in enumerate(self.items, 1):
            self.listbox.insert("end", f"  {n:>2}.   {self.label_fn(item)}")
        if self.items and select is not None:
            select = max(0, min(select, len(self.items) - 1))
            self.listbox.selection_set(select)
            self.listbox.activate(select)
            self.listbox.see(select)
        self.on_change()
        self.on_select()

    def move(self, delta: int) -> None:
        i = self.selected()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self.refresh(j)

    def remove(self) -> None:
        i = self.selected()
        if i is not None:
            del self.items[i]
            self.refresh(i)

    def clear(self) -> None:
        self.items.clear()
        self.refresh()


# ---------------------------------------------------------------------------
# 1-varaq: Rasm → PDF
# ---------------------------------------------------------------------------

class ImageTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, style="Card.TFrame", padding=16)
        self.app = app
        self.page_var = tk.StringVar(value=FIT)
        self.orient_var = tk.StringVar(value="Avto")
        self.output_var = tk.StringVar()
        self.open_var = tk.BooleanVar(value=True)
        self.info_var = tk.StringVar(value="Ro'yxat bo'sh")
        self.preview_info = tk.StringVar(value="")
        self._preview_photo = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Yuqori panel
        top = ttk.Frame(self, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(top, text="+  Rasm qo'shish", command=self.add_files).pack(side="left")
        ttk.Button(top, text="+  Papka", command=self.add_folder).pack(side="left", padx=6)
        ttk.Label(top, textvariable=self.info_var, style="CardMuted.TLabel").pack(side="right")

        # Ro'yxat
        self.list = ItemList(
            self,
            label_fn=lambda it: f"{os.path.basename(it['path'])}    ·    "
                                f"{it['size'][0]}×{it['size'][1]}",
            on_select=self.update_preview,
            on_change=self.on_list_change,
        )
        self.list.grid(row=1, column=0, sticky="nsew")

        # O'ng panel: preview + sozlamalar
        side = ttk.Frame(self, style="Card.TFrame")
        side.grid(row=0, column=1, rowspan=2, sticky="ns", padx=(18, 0))
        ttk.Label(side, text="KO'RINISH", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.preview = tk.Canvas(side, width=PREVIEW_W, height=PREVIEW_H, bg=PREVIEW_BG,
                                 highlightthickness=0)
        self.preview.pack()
        ttk.Label(side, textvariable=self.preview_info, style="CardMuted.TLabel",
                  font=(FAMILY, 9)).pack(anchor="w", pady=(4, 10))

        ttk.Label(side, text="SAHIFA SOZLAMALARI", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        grid = ttk.Frame(side, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="O'lcham", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        page_cb = ttk.Combobox(grid, textvariable=self.page_var, values=list(PAGES),
                               state="readonly", width=16)
        page_cb.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=3)
        ttk.Label(grid, text="Yo'nalish", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        orient_cb = ttk.Combobox(grid, textvariable=self.orient_var, values=list(ORIENTATIONS),
                                 state="readonly", width=16)
        orient_cb.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=3)
        for cb in (page_cb, orient_cb):
            cb.bind("<<ComboboxSelected>>", lambda _e: self.update_preview())

        # Pastki panel: saqlash
        bottom = ttk.Frame(self, style="Card.TFrame")
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Saqlash:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(bottom, textvariable=self.output_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=10)
        ttk.Button(bottom, text="Tanlash…", command=self.browse_output).grid(row=0, column=2)

        action = ttk.Frame(bottom, style="Card.TFrame")
        action.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(action, text="Tayyor bo'lgach PDF ni ochish",
                        variable=self.open_var, style="Card.TCheckbutton").pack(side="left")
        self.action_btn = ttk.Button(action, text="PDF yaratish", style="Accent.TButton",
                                     command=self.start)
        self.action_btn.pack(side="right")

        self.update_preview()

    # -- Fayl qo'shish -------------------------------------------------------

    def add_files(self) -> None:
        patterns = " ".join(f"*{e} *{e.upper()}" for e in IMAGE_EXTS)
        paths = filedialog.askopenfilenames(
            title="Rasmlarni tanlang", filetypes=[("Rasmlar", patterns), ("Hammasi", "*.*")])
        if paths:
            self.add_paths(sorted(paths, key=natural_key))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Rasmlar papkasini tanlang")
        if not folder:
            return
        files = [str(p) for p in Path(folder).iterdir()
                 if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        if not files:
            messagebox.showinfo("Rasm topilmadi", "Bu papkada qo'llab-quvvatlanadigan rasm yo'q.")
            return
        self.add_paths(sorted(files, key=natural_key))

    def add_paths(self, paths: list[str]) -> None:
        items = self.list.items
        known = {os.path.normcase(os.path.abspath(i["path"])) for i in items}
        current_fmt = items[0]["fmt"] if items else None
        skipped: list[tuple[str, str]] = []

        for path in paths:
            name = os.path.basename(path)
            key = os.path.normcase(os.path.abspath(path))
            if key in known:
                skipped.append((name, "allaqachon ro'yxatda"))
                continue
            try:
                fmt, size = inspect_image(path)
            except ValueError as exc:
                skipped.append((name, str(exc)))
                continue
            if current_fmt is None:
                current_fmt = fmt  # birinchi rasm formatni belgilaydi
            elif fmt != current_fmt:
                skipped.append((name, f"boshqa format ({fmt}); ro'yxatda faqat {current_fmt}"))
                continue
            items.append({"path": path, "fmt": fmt, "size": size})
            known.add(key)

        sel = self.list.selected()
        self.list.refresh(select=sel if sel is not None else 0)
        report_skipped(skipped)

    # -- Holat yangilanishi ----------------------------------------------------

    def on_list_change(self) -> None:
        items = self.list.items
        if items:
            self.info_var.set(f"Format: {items[0]['fmt']}   ·   {len(items)} ta rasm")
            if not self.output_var.get():
                folder = os.path.dirname(os.path.abspath(items[0]["path"]))
                self.output_var.set(unique_path(folder, "output"))
        else:
            self.info_var.set("Ro'yxat bo'sh")

    def update_preview(self) -> None:
        c = self.preview
        c.delete("all")
        idx = self.list.selected()
        if idx is None or idx >= len(self.list.items):
            c.create_text(PREVIEW_W // 2, PREVIEW_H // 2, text="Rasm tanlang",
                          fill=MUTED, justify="center")
            self.preview_info.set("")
            return
        item = self.list.items[idx]
        try:
            img = load_rgb(item["path"], thumb=700)
            page = compose_page(img, self.page_var.get(),
                                ORIENTATIONS[self.orient_var.get()], dpi=40)
            page.thumbnail((PREVIEW_W - 20, PREVIEW_H - 20), Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(page)
            cx, cy = PREVIEW_W // 2, PREVIEW_H // 2
            hw, hh = page.width // 2, page.height // 2
            c.create_rectangle(cx - hw + 3, cy - hh + 3, cx + hw + 3, cy + hh + 3,
                               fill="#D5D8E3", width=0)  # soya
            c.create_image(cx, cy, image=self._preview_photo)
            c.create_rectangle(cx - hw, cy - hh, cx + hw, cy + hh, outline=BORDER)
            self.preview_info.set(f"{idx + 1}-bet  ·  {os.path.basename(item['path'])}")
        except Exception:
            c.create_text(PREVIEW_W // 2, PREVIEW_H // 2, text="Ko'rib bo'lmadi", fill=MUTED)
            self.preview_info.set("")

    def browse_output(self) -> None:
        initial = self.output_var.get()
        path = filedialog.asksaveasfilename(
            title="PDF ni saqlash", defaultextension=".pdf",
            filetypes=[("PDF fayllar", "*.pdf")], confirmoverwrite=False,
            initialdir=os.path.dirname(initial) or None,
            initialfile=os.path.basename(initial) or "output.pdf")
        if path:
            self.output_var.set(path)

    # -- Ishga tushirish -------------------------------------------------------

    def start(self) -> None:
        items = self.list.items
        if not items:
            messagebox.showinfo("Ro'yxat bo'sh", "Avval rasmlarni qo'shing.")
            return

        missing = [i for i in items if not os.path.isfile(i["path"])]
        if missing:
            names = "\n".join(f"• {os.path.basename(i['path'])}" for i in missing[:8])
            self.list.items = [i for i in items if i not in missing]
            self.list.refresh(0)
            messagebox.showwarning("Fayllar topilmadi",
                                   f"Quyidagi fayllar o'chirilgan yoki ko'chirilgan va ro'yxatdan olib tashlandi:\n{names}")
            return

        out = self.output_var.get().strip()
        if not out:
            self.browse_output()
            out = self.output_var.get().strip()
            if not out:
                return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        if os.path.exists(out) and not messagebox.askyesno(
                "Fayl mavjud", f"“{os.path.basename(out)}” allaqachon mavjud.\nUstiga yozilsinmi?"):
            return

        paths = [i["path"] for i in items]
        page_key = self.page_var.get()
        orientation = ORIENTATIONS[self.orient_var.get()]
        self.app.run_task(
            lambda progress: images_to_pdf(paths, out, page_key, orientation, progress),
            out, self.open_var.get())


# ---------------------------------------------------------------------------
# 2-varaq: PDF birlashtirish
# ---------------------------------------------------------------------------

class PdfTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, style="Card.TFrame", padding=16)
        self.app = app
        self.rotation_var = tk.StringVar(value=list(ROTATIONS)[0])
        self.output_var = tk.StringVar()
        self.open_var = tk.BooleanVar(value=True)
        self.info_var = tk.StringVar(value="Ro'yxat bo'sh")
        self.summary_var = tk.StringVar(value="—")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.add_btn = ttk.Button(top, text="+  PDF qo'shish", command=self.add_files)
        self.add_btn.pack(side="left")
        ttk.Label(top, textvariable=self.info_var, style="CardMuted.TLabel").pack(side="right")

        self.list = ItemList(
            self,
            label_fn=lambda it: f"{os.path.basename(it['path'])}    ·    {it['pages']} bet",
            on_select=lambda: None,
            on_change=self.on_list_change,
        )
        self.list.grid(row=1, column=0, sticky="nsew")

        side = ttk.Frame(self, style="Card.TFrame")
        side.grid(row=0, column=1, rowspan=2, sticky="ns", padx=(18, 0))
        ttk.Label(side, text="XULOSA", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        box = tk.Label(side, textvariable=self.summary_var, bg=PREVIEW_BG, fg=TEXT,
                       width=30, height=6, justify="center", font=(FAMILY, 11))
        box.pack()
        ttk.Label(side, text="SAHIFA SOZLAMALARI", style="Section.TLabel").pack(anchor="w", pady=(16, 6))
        grid = ttk.Frame(side, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="Burish", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(grid, textvariable=self.rotation_var, values=list(ROTATIONS),
                     state="readonly", width=16).grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ttk.Label(side, text="Burish barcha sahifalarga qo'llanadi\n(vertikal ↔ gorizontal).",
                  style="CardMuted.TLabel", font=(FAMILY, 9), justify="left").pack(anchor="w", pady=(8, 0))

        bottom = ttk.Frame(self, style="Card.TFrame")
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Saqlash:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(bottom, textvariable=self.output_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=10)
        ttk.Button(bottom, text="Tanlash…", command=self.browse_output).grid(row=0, column=2)

        action = ttk.Frame(bottom, style="Card.TFrame")
        action.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(action, text="Tayyor bo'lgach PDF ni ochish",
                        variable=self.open_var, style="Card.TCheckbutton").pack(side="left")
        self.action_btn = ttk.Button(action, text="PDF larni birlashtirish",
                                     style="Accent.TButton", command=self.start)
        self.action_btn.pack(side="right")

        if not HAS_PYPDF:
            self.info_var.set("pypdf o'rnatilmagan:  pip install pypdf")
            self.add_btn.state(["disabled"])
            self.action_btn.state(["disabled"])

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="PDF fayllarni tanlang",
            filetypes=[("PDF fayllar", "*.pdf *.PDF"), ("Hammasi", "*.*")])
        if not paths:
            return
        items = self.list.items
        known = {os.path.normcase(os.path.abspath(i["path"])) for i in items}
        skipped: list[tuple[str, str]] = []
        for path in sorted(paths, key=natural_key):
            name = os.path.basename(path)
            key = os.path.normcase(os.path.abspath(path))
            if key in known:
                skipped.append((name, "allaqachon ro'yxatda"))
                continue
            try:
                pages = inspect_pdf(path)
            except ValueError as exc:
                skipped.append((name, str(exc)))
                continue
            items.append({"path": path, "pages": pages})
            known.add(key)
        sel = self.list.selected()
        self.list.refresh(select=sel if sel is not None else 0)
        report_skipped(skipped)

    def on_list_change(self) -> None:
        items = self.list.items
        if items:
            total = sum(i["pages"] for i in items)
            self.info_var.set(f"{len(items)} ta PDF   ·   {total} bet")
            self.summary_var.set(f"{len(items)} ta fayl\n\n{total} bet")
            if not self.output_var.get():
                folder = os.path.dirname(os.path.abspath(items[0]["path"]))
                self.output_var.set(unique_path(folder, "birlashtirilgan"))
        else:
            self.info_var.set("Ro'yxat bo'sh" if HAS_PYPDF else self.info_var.get())
            self.summary_var.set("—")

    def browse_output(self) -> None:
        initial = self.output_var.get()
        path = filedialog.asksaveasfilename(
            title="PDF ni saqlash", defaultextension=".pdf",
            filetypes=[("PDF fayllar", "*.pdf")], confirmoverwrite=False,
            initialdir=os.path.dirname(initial) or None,
            initialfile=os.path.basename(initial) or "birlashtirilgan.pdf")
        if path:
            self.output_var.set(path)

    def start(self) -> None:
        items = self.list.items
        if len(items) < 2:
            messagebox.showinfo("Kamida 2 ta PDF kerak", "Birlashtirish uchun kamida ikkita PDF qo'shing.")
            return
        missing = [i for i in items if not os.path.isfile(i["path"])]
        if missing:
            names = "\n".join(f"• {os.path.basename(i['path'])}" for i in missing[:8])
            self.list.items = [i for i in items if i not in missing]
            self.list.refresh(0)
            messagebox.showwarning("Fayllar topilmadi",
                                   f"Quyidagi fayllar ro'yxatdan olib tashlandi:\n{names}")
            return

        out = self.output_var.get().strip()
        if not out:
            self.browse_output()
            out = self.output_var.get().strip()
            if not out:
                return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"

        # Natija fayli manba fayllardan biri bo'lib qolmasin (u buzilib ketadi)
        out_key = os.path.normcase(os.path.abspath(out))
        if any(os.path.normcase(os.path.abspath(i["path"])) == out_key for i in items):
            messagebox.showerror("Xato", "Saqlash fayli ro'yxatdagi PDF lardan biri bilan bir xil.\n"
                                         "Boshqa nom tanlang.")
            return
        if os.path.exists(out) and not messagebox.askyesno(
                "Fayl mavjud", f"“{os.path.basename(out)}” allaqachon mavjud.\nUstiga yozilsinmi?"):
            return

        paths = [i["path"] for i in items]
        rotation = ROTATIONS[self.rotation_var.get()]
        self.app.run_task(
            lambda progress: merge_pdfs(paths, out, rotation, progress),
            out, self.open_var.get())


# ---------------------------------------------------------------------------
# Asosiy oyna
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.busy = False
        self.queue: queue.Queue = queue.Queue()

        root.title("Rasm → PDF Studio")
        w, h = 900, 700
        x = max(0, (root.winfo_screenwidth() - w) // 2)
        y = max(0, (root.winfo_screenheight() - h) // 3)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(860, 680)

        self.family = setup_style(root)

        # Sarlavha
        header = tk.Frame(root, bg=ACCENT)
        header.pack(fill="x")
        tk.Label(header, text="Rasm → PDF Studio", bg=ACCENT, fg="white",
                 font=(self.family, 16, "bold")).pack(anchor="w", padx=22, pady=(10, 0))
        tk.Label(header, text="Rasmlarni PDF ga aylantiring va PDF fayllarni birlashtiring",
                 bg=ACCENT, fg="#C7D2FE", font=(self.family, 10)).pack(anchor="w", padx=22, pady=(0, 10))

        # Pastki holat paneli (varaqlardan oldin joylanadi — doim ko'rinib turadi)
        footer = ttk.Frame(root, padding=(20, 10, 20, 12))
        footer.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Tayyor")
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # Varaqlar
        body = ttk.Frame(root, padding=(16, 14, 16, 0))
        body.pack(side="top", fill="both", expand=True)
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)
        self.image_tab = ImageTab(notebook, self)
        self.pdf_tab = PdfTab(notebook, self)
        notebook.add(self.image_tab, text="Rasm → PDF")
        notebook.add(self.pdf_tab, text="PDF birlashtirish")

        root.after(80, self._poll)

    # -- Fon oqimida ishlash -----------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for tab in (self.image_tab, self.pdf_tab):
            if busy:
                tab.action_btn.state(["disabled"])
            elif tab is self.image_tab or HAS_PYPDF:
                tab.action_btn.state(["!disabled"])

    def run_task(self, job, output: str, open_after: bool) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.progress["value"] = 0
        self.status_var.set("Boshlanmoqda…")

        def worker() -> None:
            try:
                job(lambda pct, msg: self.queue.put(("progress", pct, msg)))
                self.queue.put(("done", output, open_after))
            except Exception as exc:
                self.queue.put(("error", str(exc) or exc.__class__.__name__))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        """Fon oqimidan kelgan xabarlarni asosiy oqimda xavfsiz qayta ishlaydi."""
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress["value"] = msg[1]
                    self.status_var.set(msg[2])
                elif kind == "done":
                    self._set_busy(False)
                    self.progress["value"] = 100
                    if msg[2]:
                        open_in_viewer(msg[1])
                elif kind == "error":
                    self._set_busy(False)
                    self.progress["value"] = 0
                    self.status_var.set("Xatolik yuz berdi")
                    messagebox.showerror("Xatolik", msg[1])
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


def main() -> None:
    if sys.platform.startswith("win"):
        try:  # Windows da aniq (xira bo'lmagan) matn uchun
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
