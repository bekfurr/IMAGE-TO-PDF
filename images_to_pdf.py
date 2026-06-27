"""
Images to PDF Combiner
======================
A simple desktop app that combines sequentially-named images (1, 2, 3... or a, b, c...)
into a single multi-page PDF.

Usage:
    python images_to_pdf.py

Requirements:
    pip install Pillow
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "bmp", "tiff", "webp"]

PAGE_SIZES = {
    "Fit to image": None,
    "Fit to image (centered on A4)": (595.28, 841.89),       # A4 @ 72 dpi
    "Fit to image (centered on Letter)": (612, 792),          # Letter @ 72 dpi
    "A4": (595.28, 841.89),
    "Letter": (612, 792),
}

DEFAULT_PNG = "png"
DPI = 150


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_filename(index: int, naming_mode: str, ext: str) -> str:
    """Return a filename like '1.png' or 'a.jpg' depending on naming_mode."""
    if naming_mode == "letters":
        return f"{chr(96 + index)}.{ext}"
    return f"{index}.{ext}"


def collect_images(folder: str, naming_mode: str, start: int, end: int, ext: str) -> list[str]:
    """
    Build the list of image file paths matching the naming scheme
    in the given folder and index range.  Returns paths that exist.
    """
    paths = []
    for i in range(start, end + 1):
        name = build_filename(i, naming_mode, ext)
        full = os.path.join(folder, name)
        if os.path.isfile(full):
            paths.append(full)
    return paths


def fit_image_to_page(img: Image.Image, page_size: tuple[int, int]) -> Image.Image:
    """Scale *img* to fit inside *page_size* (preserving aspect ratio) on a white canvas."""
    page_w, page_h = page_size
    img = img.copy()
    img.thumbnail((page_w, page_h), Image.LANCZOS)

    canvas = Image.new("RGB", page_w), (255, 255, 255)
    offset_x = (page_w - img.width) // 2
    offset_y = (page_h - img.height) // 2
    canvas.paste(img, (offset_x, offset_y))
    return canvas


def convert_images_to_pdf(
    image_paths: list[str],
    output_path: str,
    page_size_key: str,
    status_callback,
    progress_callback,
) -> None:
    """Convert a list of image files into a multi-page PDF."""
    if not image_paths:
        status_callback("No images found!")
        return

    page_size = PAGE_SIZES.get(page_size_key)
    centering = "centered" in (page_size_key.lower())
    images: list[Image.Image] = []

    for i, path in enumerate(image_paths):
        img = Image.open(path).convert("RGB")

        if page_size and centering:
            img = fit_image_to_page(img, page_size)
        elif page_size:
            # Just create a white canvas of the page size and paste the image at top-left
            canvas = Image.new("RGB", page_size, (255, 255, 255))
            scaled = img.copy()
            scaled.thumbnail((int(page_size[0]), int(page_size[1])), Image.LANCZOS)
            canvas.paste(scaled, (0, 0))
            img = canvas

        images.append(img)
        progress_callback(int((i + 1) / len(image_paths) * 100))
        status_callback(f"Processing image {i + 1} of {len(image_paths)}")

    first = images[0]
    rest = images[1:] if len(images) > 1 else []
    first.save(output_path, "PDF", save_all=True, append_images=rest, resolution=DPI)
    status_callback(f"Done! Saved to {output_path}")
    progress_callback(100)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ImageToPdfApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Images to PDF Combiner")
        self.root.resizable(False, False)

        # Variables
        self.folder_var = tk.StringVar()
        self.naming_var = tk.StringVar(value="numbers")
        self.start_var = tk.StringVar(value="1")
        self.end_var = tk.StringVar(value="20")
        self.ext_var = tk.StringVar(value=DEFAULT_PNG)
        self.page_size_var = tk.StringVar(value="Fit to image")
        self.output_var = tk.StringVar()

        self._build_ui()

    # -- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        frame = tk.Frame(self.root)
        frame.pack(padx=15, pady=15)

        row = 0

        # --- Title ---
        tk.Label(frame, text="Images to PDF Combiner", font=("Segoe UI", 14, "bold")).grid(
            column=0, columnspan=3, row=row, sticky="w", **pad
        )
        row += 1

        # --- Folder ---
        tk.Label(frame, text="Image folder:").grid(column=0, row=row, sticky="w", **pad)
        tk.Entry(frame, textvariable=self.folder_var, width=40, state="readonly").grid(
            column=1, row=row, sticky="ew", **pad
        )
        tk.Button(frame, text="Browse…", command=self._browse_folder).grid(
            column=2, row=row, sticky="e", **pad
        )
        row += 1

        # --- Naming mode ---
        tk.Label(frame, text="Naming mode:").grid(column=0, row=row, sticky="w", **pad)
        naming_frame = tk.Frame(frame)
        naming_frame.grid(column=1, columnspan=2, row=row, sticky="w", **pad)
        tk.Radiobutton(naming_frame, text="Numbers (1, 2, 3…)", variable=self.naming_var,
                       value="numbers", command=self._update_range_labels).pack(side="left")
        tk.Radiobutton(naming_frame, text="Letters (a, b, c…)", variable=self.naming_var,
                       value="letters", command=self._update_range_labels).pack(side="left")
        row += 1

        # --- Range ---
        tk.Label(frame, text="Start index:").grid(column=0, row=row, sticky="w", **pad)
        range_frame = tk.Frame(frame)
        range_frame.grid(column=1, columnspan=2, row=row, sticky="w", **pad)
        self.start_entry = tk.Entry(range_frame, textvariable=self.start_var, width=6)
        self.start_entry.pack(side="left")
        tk.Label(range_frame, text="  End index:  ").pack(side="left")
        self.end_entry = tk.Entry(range_frame, textvariable=self.end_var, width=6)
        self.end_entry.pack(side="left")
        row += 1

        # --- Image format ---
        tk.Label(frame, text="Image format:").grid(column=0, row=row, sticky="w", **pad)
        ext_frame = tk.Frame(frame)
        ext_frame.grid(column=1, columnspan=2, row=row, sticky="w", **pad)
        tk.OptionMenu(ext_frame, self.ext_var, *IMAGE_EXTENSIONS).pack(side="left")
        row += 1

        # --- Page size ---
        tk.Label(frame, text="Page size:").grid(column=0, row=row, sticky="w", **pad)
        page_frame = tk.Frame(frame)
        page_frame.grid(column=1, columnspan=2, row=row, sticky="w", **pad)
        tk.OptionMenu(page_frame, self.page_size_var, *PAGE_SIZES.keys()).pack(side="left")
        row += 1

        # --- Output PDF ---
        tk.Label(frame, text="Output PDF:").grid(column=0, row=row, sticky="w", **pad)
        tk.Entry(frame, textvariable=self.output_var, width=40, state="readonly").grid(
            column=1, row=row, sticky="ew", **pad
        )
        tk.Button(frame, text="Browse…", command=self._browse_output).grid(
            column=2, row=row, sticky="e", **pad
        )
        row += 1

        # --- Convert button ---
        self.convert_btn = tk.Button(
            frame, text="Combine Images to PDF", command=self._start_conversion,
            bg="#4CAF50", fg="white", font=("Segoe UI", 10, "bold"), padx=20, pady=6,
        )
        self.convert_btn.grid(column=0, columnspan=3, row=row, pady=(12, 4))
        row += 1

        # --- Progress bar ---
        self.progress = tk.Canvas(frame, width=400, height=18, bg="#e0e0e0", highlightthickness=0)
        self.progress.grid(column=0, columnspan=3, row=row, pady=(4, 2))
        self.progress_rect = self.progress.create_rectangle(0, 0, 0, 18, fill="#4CAF50", width=0)
        row += 1

        # --- Status label ---
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(frame, textvariable=self.status_var, fg="gray").grid(
            column=0, columnspan=3, row=row, sticky="w", padx=10
        )

    # -- Helpers -------------------------------------------------------------

    def _update_range_labels(self) -> None:
        """Switch placeholder text between numbers and letters."""
        mode = self.naming_var.get()
        if mode == "letters":
            self.start_entry.delete(0, tk.END)
            self.start_entry.insert(0, "a")
            self.end_entry.delete(0, tk.END)
            self.end_entry.insert(0, "z")
        else:
            self.start_entry.delete(0, tk.END)
            self.start_entry.insert(0, "1")
            self.end_entry.delete(0, tk.END)
            self.end_entry.insert(0, "20")

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select folder with images")
        if folder:
            self.folder_var.set(folder)
            # Auto-set output path
            if not self.output_var.get():
                self.output_var.set(os.path.join(folder, "output.pdf"))

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save PDF as",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if path:
            self.output_var.set(path)

    def _set_progress(self, value: int) -> None:
        self.progress.coords(self.progress_rect, 0, 0, int(400 * value / 100), 18)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # -- Conversion ----------------------------------------------------------

    def _start_conversion(self) -> None:
        folder = self.folder_var.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid image folder.")
            return

        output = self.output_var.get()
        if not output:
            messagebox.showerror("Error", "Please choose an output PDF path.")
            return

        # Parse range
        naming_mode = self.naming_var.get()
        if naming_mode == "numbers":
            try:
                start = int(self.start_var.get())
                end = int(self.end_var.get())
            except ValueError:
                messagebox.showerror("Error", "Start and end index must be whole numbers.")
                return
        else:
            start_s = self.start_var.get().strip().lower()
            end_s = self.end_var.get().strip().lower()
            if len(start_s) != 1 or len(end_s) != 1 or not start_s.isalpha() or not end_s.isalpha():
                messagebox.showerror("Error", "Start and end index must be single letters (a–z).")
                return
            start = ord(start_s) - 96   # 'a' → 1
            end = ord(end_s) - 96

        if start < 1 or end < start:
            messagebox.showerror("Error", "Invalid range. Start must be >= 1 and end >= start.")
            return

        ext = self.ext_var.get()
        image_paths = collect_images(folder, naming_mode, start, end, ext)

        if not image_paths:
            messagebox.showwarning(
                "No Images Found",
                f"No images matching the naming pattern were found in:\n{folder}\n\n"
                f"Expected files like: {build_filename(start, naming_mode, ext)} … "
                f"{build_filename(end, naming_mode, ext)}",
            )
            return

        # Disable button during conversion
        self.convert_btn.config(state="disabled")
        self._set_progress(0)
        self._set_status("Starting…")

        page_size_key = self.page_size_var.get()

        def _worker():
            try:
                convert_images_to_pdf(
                    image_paths, output, page_size_key,
                    status_callback=lambda msg: self.root.after(0, self._set_status, msg),
                    progress_callback=lambda val: self.root.after(0, self._set_progress, val),
                )
            except Exception as exc:
                self.root.after(0, messagebox.showerror, "Conversion Error", str(exc))
            finally:
                self.root.after(0, lambda: self.convert_btn.config(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    ImageToPdfApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
