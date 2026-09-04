from __future__ import annotations
import json
import math
import copy
import os
import shutil
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageOps, ImageTk


EXTENSIONS = {".jpg", ".jpeg", ".png"}
COLORS = {
    "right_eye": "#ff6868", 
    "left_eye": "#65dfff", 
    "nose": "#ffe36e",
    "mouth_right": "#df8fff", 
    "mouth_left": "#ffab65"
}


def load_annotations(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return {}
    
    data = json.loads(text)
    if data == {}:
        return {}
    
    if isinstance(data, dict) and "images" in data:
        records = data["images"]
    else:
        raise ValueError("Invalid annotation file format.")

    if not isinstance(records, list):
        raise ValueError("Expected {'images': [...]}, a template object, or an empty list/file.")
    
    result = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("image"), dict):
            raise ValueError(f"Record {index + 1} is missing image metadata.")
        
        image_id = record["image"].get("id")
        if not isinstance(image_id, str) or not image_id:
            raise ValueError(f"Record {index + 1} needs a nonempty string image.id.")
        
        if image_id in result:
            raise ValueError(f"Duplicate image.id: {image_id}")
        
        if not isinstance(record.get("faces", []), list):
            raise ValueError(f"Faces must be a list for {image_id}.")
        
        result[image_id] = record

    return result


def find_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    
    paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
    seen = set()
    for path in paths:
        if path.stem in seen:
            raise ValueError(f"Duplicate image filename stem: {path.stem}. IDs must be unique within the source folder.")
        seen.add(path.stem)
    return paths


def coordinates(values, length):
    if not isinstance(values, (list, tuple)) or len(values) != length:
        raise ValueError("Invalid coordinates")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError("Coordinates must be finite numbers")
    return values


class AnnotationViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Face annotation tool")
        self.geometry("1280x800")
        self.minsize(760, 480)
        self.paths = []
        self.annotations = {}
        self.annotation_path = None
        self.dirty = False
        self.selected = None
        self.selected_landmark = None
        self.drag = None
        self.transform = None
        self.image_cache_key = None
        self.index = 0
        self.source_image = None
        self.photo = None
        self.resize_job = None
        self.folder_text = tk.StringVar(value="No source folder selected")
        self.json_text = tk.StringVar(value="No annotations loaded")
        self.info = tk.StringVar(value="Choose a source folder to begin.")
        self.status = tk.StringVar()
        self.selection_info = tk.StringVar(value="No face selected")
        self.show_boxes = tk.BooleanVar(value=True)
        self.show_landmarks = tk.BooleanVar(value=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self, padding=16, width=270)
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="Face annotation tool", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 16))
        ttk.Button(sidebar, text="Open Folder", command=self.choose_folder).pack(fill="x")
        ttk.Label(sidebar, textvariable=self.folder_text, wraplength=245).pack(anchor="w", pady=(8, 18))
        ttk.Button(sidebar, text="Choose Annotation File", command=self.choose_annotations).pack(fill="x")
        ttk.Label(sidebar, textvariable=self.json_text, wraplength=245).pack(anchor="w", pady=(8, 8))
        ttk.Button(sidebar, text="Clear annotations from view", command=self.clear_annotations).pack(fill="x")
        ttk.Button(sidebar, text="Save (Space)", command=self.save).pack(fill="x", pady=8)
        ttk.Separator(sidebar).pack(fill="x", pady=16)
        navigation = ttk.Frame(sidebar)
        navigation.pack(fill="x")
        self.previous = ttk.Button(navigation, text="← Previous", command=lambda: self.navigate(-1))
        self.previous.pack(side="left")
        self.next = ttk.Button(navigation, text="Next →", command=lambda: self.navigate(1))
        self.next.pack(side="right")
        ttk.Label(sidebar, textvariable=self.info, wraplength=245).pack(anchor="w", pady=12)
        ttk.Label(sidebar, textvariable=self.selection_info, wraplength=245).pack(anchor="w", pady=(0, 8))
        ttk.Checkbutton(sidebar, text="Bounding boxes", variable=self.show_boxes, command=self.render).pack(anchor="w")
        ttk.Checkbutton(sidebar, text="Landmarks", variable=self.show_landmarks, command=self.render).pack(anchor="w")
        for name, color in COLORS.items():
            ttk.Label(sidebar, text=f"● {name}", foreground=color).pack(anchor="w", pady=2)
        ttk.Label(sidebar, text="← / →: browse · Space: save\nDrag boxes, corners or landmarks.\nDelete: hide landmark / remove face\n1/2/3: occluded/blurred/small\n4/5/6: frontal/side/downward\n0: unknown pose\nPlace at pointer on selected face:\nQ/E: right/left eye · W: nose\nA/D: mouth_right/mouth_left", wraplength=245).pack(anchor="w", pady=8)
        self.canvas = tk.Canvas(self, background="#20242b", highlightthickness=0)
        self.canvas.grid(row=0, column=1, sticky="nsew")
        ttk.Label(self, textvariable=self.status, padding=6).grid(row=1, column=0, columnspan=2, sticky="ew")
        self.canvas.bind("<Configure>", self.queue_render)
        self.bind("<Left>", lambda event: self.navigate(-1))
        self.bind("<Right>", lambda event: self.navigate(1))
        self.bind("<space>", self.save)
        self.bind("<Delete>", self.delete_selection)
        for key in "0123456":
            self.bind(f"<KeyPress-{key}>", lambda event, key=key: self.set_attribute(key))
        for key, name in {"q": "right_eye", "e": "left_eye", "w": "nose",
                          "a": "mouth_right", "d": "mouth_left"}.items():
            self.bind(f"<KeyPress-{key}>", lambda event, name=name: self.place_landmark(name))
        self.canvas.bind("<Button-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.move_drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.bind("<Escape>", self.cancel_drag)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.update_navigation()

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("Unsaved edits", "Save your edits before continuing?", parent=self)
        if answer is None:
            return False
        return self.save() if answer else True

    def close(self):
        if self.confirm_discard():
            self.destroy()

    def save(self, event=None):
        if self.annotation_path is None:
            filename = filedialog.asksaveasfilename(parent=self, title="Save annotations", defaultextension=".json", filetypes=[("JSON", "*.json")])
            if not filename:
                return "break" if event else False
            destination = Path(filename)
        else:
            destination = self.annotation_path
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=".annotations-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"images": list(self.annotations.values())}, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            backup = destination.with_suffix(destination.suffix + ".bak")
            if destination.exists() and not backup.exists():
                shutil.copy2(destination, backup)
            os.replace(temporary, destination)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return "break" if event else False
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.annotation_path = destination
        self.dirty = False
        self.json_text.set(f"{destination}\n{len(self.annotations)} image records")
        self.render()
        self.status.set(f"Saved: {destination}")
        return "break" if event else True

    def current_faces(self):
        if not self.paths:
            return []
        return self.annotations.get(self.paths[self.index].stem, {}).get("faces", [])

    def selected_face(self):
        faces = self.current_faces()
        if self.selected is None or not 0 <= self.selected < len(faces):
            return None
        return faces[self.selected]

    def place_landmark(self, name):
        """Place at the current pointer, converting canvas to image coordinates."""
        face = self.selected_face()
        if face is None or self.source_image is None or self.transform is None:
            return "break"
        # Read the actual pointer instead of retaining a potentially stale motion event.
        px, py = self.canvas.winfo_pointerxy()
        cx, cy = px - self.canvas.winfo_rootx(), py - self.canvas.winfo_rooty()
        ox, oy, sx, sy = self.transform
        width, height = self.source_image.size
        if not (ox <= cx < ox + width*sx and oy <= cy < oy + height*sy):
            self.status.set("Place the mouse over the image to position a landmark.")
            return "break"
        x, y = min(width-1, (cx-ox)/sx), min(height-1, (cy-oy)/sy)
        point = face.setdefault("landmarks", {}).setdefault(name, {})
        if (point.get("x"), point.get("y"), point.get("visible")) != (x, y, True):
            point.update(x=x, y=y, visible=True)
            self.dirty = True
        self.selected_landmark = name
        self.drag = None
        self.show_landmarks.set(True)
        self.render()
        return "break"

    def delete_selection(self, event=None):
        face = self.selected_face()
        if face is None:
            return "break"
        if self.selected_landmark is not None:
            point = face.get("landmarks", {}).get(self.selected_landmark)
            if not isinstance(point, dict):
                return "break"
            point.update(x=None, y=None, visible=False)
        else:
            del self.current_faces()[self.selected]
        self.dirty = True
        self.selected = self.selected_landmark = self.drag = None
        self.render()
        return "break"

    def set_attribute(self, key):
        face = self.selected_face()
        if face is None:
            return "break"
        attributes = face.setdefault("attributes", {})
        toggles = {"1": "occluded", "2": "blurred", "3": "small"}
        poses = {"0": "unknown", "4": "frontal", "5": "side", "6": "downward"}
        if key in toggles:
            name = toggles[key]
            attributes[name] = not attributes.get(name, False)
        elif key in poses:
            if attributes.get("pose") == poses[key]:
                return "break"
            attributes["pose"] = poses[key]
        else:
            return "break"
        self.drag = None
        self.dirty = True
        self.render()
        return "break"

    def update_selection_info(self):
        face = self.selected_face()
        if face is None:
            self.selection_info.set("No face selected")
            return
        attributes = face.get("attributes", {})
        label = f"Selected face: {self.selected + 1}"
        if self.selected_landmark:
            label += f"\nLandmark: {self.selected_landmark}"
        label += "\n" + " · ".join(f"{name}: {'yes' if attributes.get(name, False) else 'no'}" for name in ("occluded", "blurred", "small"))
        label += f"\nPose: {attributes.get('pose', 'unknown')}"
        self.selection_info.set(label)

    def start_drag(self, event):
        self.canvas.focus_set()
        self.drag = None
        self.selected_landmark = None
        if self.source_image is None or self.transform is None:
            return
        ox, oy, sx, sy = self.transform
        faces = self.current_faces()
        candidates = []
        for i, face in enumerate(faces):
            try:
                if self.show_landmarks.get():
                    for name, point in face.get("landmarks", {}).items():
                        if name not in COLORS or not point.get("visible", False):
                            continue
                        x, y = coordinates([point.get("x"), point.get("y")], 2)
                        distance = math.hypot(event.x - ox - x*sx, event.y - oy - y*sy)
                        if distance <= 9:
                            candidates.append((0, distance, i, "landmark", name))
                if self.show_boxes.get():
                    x1, y1, x2, y2 = coordinates(face["bbox"], 4)
                    if self.selected == i:
                        for corner, (x, y) in enumerate(((x1,y1), (x2,y1), (x2,y2), (x1,y2))):
                            distance = math.hypot(event.x-ox-x*sx, event.y-oy-y*sy)
                            if distance <= 9:
                                candidates.append((-1, distance, i, "corner", corner))
                    if x1 <= (event.x-ox)/sx <= x2 and y1 <= (event.y-oy)/sy <= y2:
                        candidates.append((1, (x2-x1)*(y2-y1), i, "box", None))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        if candidates:
            _, _, i, kind, detail = min(candidates)
            self.selected = i
            self.selected_landmark = detail if kind == "landmark" else None
            self.drag = (i, kind, detail, (event.x-ox)/sx, (event.y-oy)/sy, copy.deepcopy(faces[i]))
        else:
            self.selected = None
            width, height = self.source_image.size
            if ox <= event.x < ox+width*sx and oy <= event.y < oy+height*sy:
                x, y = min(width-1, (event.x-ox)/sx), min(height-1, (event.y-oy)/sy)
                self.drag = (None, "new", None, x, y, {"bbox": [x, y, x, y]})
        self.render()

    def move_drag(self, event):
        if self.drag is None:
            return
        i, kind, detail, start_x, start_y, original = self.drag
        ox, oy, sx, sy = self.transform
        width, height = self.source_image.size
        x = min(width-1, max(0, (event.x-ox)/sx))
        y = min(height-1, max(0, (event.y-oy)/sy))
        if kind == "new":
            original["bbox"] = [min(start_x, x), min(start_y, y), max(start_x, x), max(start_y, y)]
            self.render()
            return
        updated = copy.deepcopy(original)
        if kind == "landmark":
            updated["landmarks"][detail].update(x=x, y=y)
        elif kind == "corner":
            x1, y1, x2, y2 = original["bbox"]
            if detail in (0, 3):
                x1 = min(x, x2-1)
            else:
                x2 = max(x, x1+1)
            if detail in (0, 1):
                y1 = min(y, y2-1)
            else:
                y2 = max(y, y1+1)
            updated["bbox"] = [x1,y1,x2,y2]
        else:
            x1,y1,x2,y2 = original["bbox"]
            dx = min(width-1-x2, max(-x1, x-start_x))
            dy = min(height-1-y2, max(-y1, y-start_y))
            updated["bbox"] = [x1+dx,y1+dy,x2+dx,y2+dy]
            for point in updated.get("landmarks", {}).values():
                try:
                    px, py = coordinates([point.get("x"), point.get("y")], 2)
                    point.update(x=px+dx, y=py+dy)
                except (ValueError, AttributeError):
                    pass
        faces = self.current_faces()
        if updated != faces[i]:
            faces[i] = updated
            self.dirty = True
        self.render()

    def cancel_drag(self, event=None):
        # New boxes are only committed on release. Existing edits remain intact.
        self.drag = None
        self.render()
        return "break"

    def end_drag(self, event):
        if self.drag is None:
            return
        self.move_drag(event)
        _, kind, _, _, _, draft = self.drag
        self.drag = None
        if kind == "new":
            x1, y1, x2, y2 = draft["bbox"]
            # Ignore clicks and accidental tiny drags.
            if x2-x1 < 1 or y2-y1 < 1:
                self.render()
                return
            path = self.paths[self.index]
            width, height = self.source_image.size
            record = self.annotations.setdefault(path.stem, {
                "image": {"id": path.stem, "file": path.as_posix(), "width": width, "height": height},
                "faces": [],
            })
            faces = record.setdefault("faces", [])
            faces.append({
                "bbox": [x1, y1, x2, y2],
                "landmarks": {name: {"x": None, "y": None, "visible": False} for name in COLORS},
                "attributes": {"occluded": False, "blurred": False, "small": False, "pose": "unknown"},
            })
            self.selected = len(faces)-1
            self.selected_landmark = None
            self.show_boxes.set(True)
            self.dirty = True
        self.render()


    def choose_folder(self):
        directory = filedialog.askdirectory(parent=self, title="Select image source folder")
        if directory:
            self.open_folder(Path(directory))


    def open_folder(self, directory):
        try:
            paths = find_images(directory)
            if not paths:
                raise ValueError("No supported images found in this folder or its subfolders.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot open folder", str(exc), parent=self)
            return
        self.paths, self.index = paths, 0
        self.folder_text.set(str(directory))
        self.load_current()


    def choose_annotations(self):
        filename = filedialog.askopenfilename(parent=self, title="Select annotation JSON", filetypes=[("JSON", "*.json"), ("All files", "*")])
        if filename:
            self.open_annotations(Path(filename))


    def open_annotations(self, path):
        try:
            annotations = load_annotations(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot load annotations", str(exc), parent=self)
            return
        if not self.confirm_discard():
            return
        # A save prompt may have just changed this same file on disk.
        try:
            annotations = load_annotations(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot load annotations", str(exc), parent=self)
            return
        self.annotations = annotations
        self.annotation_path = path
        self.dirty = False
        self.selected = self.drag = None
        self.selected_landmark = None
        self.json_text.set(f"{path}\n{len(annotations)} image records")
        self.render()


    def clear_annotations(self):
        if not self.confirm_discard():
            return
        self.annotations = {}
        self.annotation_path = None
        self.dirty = False
        self.selected = self.drag = None
        self.selected_landmark = None
        self.json_text.set("No annotations loaded")
        self.render()


    def update_navigation(self):
        self.previous.configure(state="normal" if self.paths and self.index > 0 else "disabled")
        self.next.configure(state="normal" if self.index + 1 < len(self.paths) else "disabled")


    def navigate(self, direction):
        target = self.index + direction
        if 0 <= target < len(self.paths):
            self.index = target
            self.load_current()
        return "break"


    def load_current(self):
        self.selected = self.drag = None
        self.selected_landmark = None
        self.image_cache_key = None
        self.source_image = None
        try:
            with Image.open(self.paths[self.index]) as image:
                self.source_image = ImageOps.exif_transpose(image).convert("RGB")
        except (OSError, ValueError) as exc:
            self.status.set(f"Cannot read {self.paths[self.index].name}: {exc}")
        self.update_navigation()
        self.render()


    def queue_render(self, event=None):
        if self.resize_job is not None:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(80, self.render)


    def render(self):
        self.resize_job = None
        self.canvas.delete("all")
        self.update_selection_info()

        if not self.paths:
            return
        path = self.paths[self.index]
        record = self.annotations.get(path.stem)
        faces = record.get("faces", []) if record is not None else []

        self.info.set(f"Image {self.index + 1} / {len(self.paths)}\n{path.name}\n" +
                      (f"Annotation found · {len(faces)} faces" if record is not None else "No annotation for this image"))

        if self.source_image is None:
            self.canvas.create_text(20, 30, text="Image could not be read. Use Next to continue.", anchor="nw", fill="white")
            return
        
        width, height = self.source_image.size
        cw, ch = min(max(1, self.canvas.winfo_width()), 1380), min(max(1, self.canvas.winfo_height()), 720)
        scale = min(cw / width, ch / height)
        dw, dh = max(1, round(width * scale)), max(1, round(height * scale))
        ox, oy = (cw - dw) / 2, (ch - dh) / 2
        self.transform = (ox, oy, dw/width, dh/height)
        cache_key = (str(path), dw, dh)
        if self.image_cache_key != cache_key:
            self.photo = ImageTk.PhotoImage(self.source_image.resize((dw, dh), Image.Resampling.LANCZOS))
            self.image_cache_key = cache_key
        self.canvas.create_image(ox, oy, image=self.photo, anchor="nw")

        warnings = []
        if record and (record["image"].get("width"), record["image"].get("height")) != (width, height):
            warnings.append("Annotation dimensions differ from image; overlays use original pixel coordinates")

        for index, face in enumerate(faces, 1):
            try:
                if self.show_boxes.get():
                    x1, y1, x2, y2 = coordinates(face["bbox"], 4)
                    if x2 <= x1 or y2 <= y1:
                        raise ValueError("Invalid box extent")
                    self.canvas.create_rectangle(ox+x1*dw/width, oy+y1*dh/height, ox+x2*dw/width, oy+y2*dh/height, outline="#63ed9c", width=2)
                    if index-1 == self.selected:
                        for x,y in ((x1,y1),(x2,y1),(x2,y2),(x1,y2)):
                            cx,cy = ox+x*dw/width, oy+y*dh/height
                            self.canvas.create_rectangle(cx-5,cy-5,cx+5,cy+5, fill="white", outline="#111111")
                if self.show_landmarks.get():
                    for name, color in COLORS.items():
                        point = face.get("landmarks", {}).get(name)
                        if not point or not point.get("visible", False):
                            continue
                        x, y = coordinates([point.get("x"), point.get("y")], 2)
                        x, y = ox+x*dw/width, oy+y*dh/height
                        self.canvas.create_oval(x-3, y-3, x+3, y+3, fill=color, outline="#111111")
                        if index-1 == self.selected and name == self.selected_landmark:
                            self.canvas.create_oval(x-7, y-7, x+7, y+7, outline="white", width=2)
            except (KeyError, TypeError, ValueError, AttributeError):
                warnings.append(f"Face {index}: malformed overlay data")

        if self.drag is not None and self.drag[1] == "new":
            x1, y1, x2, y2 = self.drag[5]["bbox"]
            self.canvas.create_rectangle(ox+x1*dw/width, oy+y1*dh/height,
                                         ox+x2*dw/width, oy+y2*dh/height,
                                         outline="#ffe36e", width=2, dash=(5, 3))
        self.title("Face annotation tool" + (" — Unsaved edits" if self.dirty else ""))
        self.status.set(f"{width} x {height} | {scale:.0%} | " + ("Unsaved edits | " if self.dirty else "") + "; ".join(warnings))


def main():
    app = AnnotationViewer()
    app.mainloop()


if __name__ == "__main__":
    main()
