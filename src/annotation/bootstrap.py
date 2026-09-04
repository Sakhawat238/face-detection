from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any
import torch
from tqdm import tqdm
from retinaface.pre_trained_models import get_model


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(f"{message}\nHint: provide --input-dir DIRECTORY --output-file FILE; use --help for details.")


DEFAULT_CONFIDENCE = 0.50
DEFAULT_MAX_SIZE = 2048
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def find_images(input_dir: Path) -> list[Path]:
    images = []
    for path in input_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(path)
    return sorted(images)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def convert_landmarks(retinaface_landmarks: dict[str, Any],) -> dict[str, dict[str, Any]]:
    landmarks = ["right_eye", "left_eye", "nose", "mouth_right", "mouth_left"]
    output = {}
    for i, lm in enumerate(landmarks):
        point = retinaface_landmarks[i]
        if point is None or len(point) != 2:
            output[lm] = {
                "x": None,
                "y": None,
                "visible": False,
            }
            continue

        x = float(point[0])
        y = float(point[1])

        output[lm] = {
            "x": x,
            "y": y,
            "visible": True,
        }

    return output


def convert_bbox(bbox: list[Any], width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = [float(v) for v in bbox]
    x1 = clamp(x1, 0, width - 1)
    y1 = clamp(y1, 0, height - 1)
    x2 = clamp(x2, 0, width - 1)
    y2 = clamp(y2, 0, height - 1)
    return [x1, y1, x2, y2]


def create_face_annotation(prediction: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    bbox = convert_bbox(prediction["bbox"], width, height)
    landmarks = convert_landmarks(prediction.get("landmarks", {}))
    return {
        "bbox": bbox,
        "landmarks": landmarks,
        "attributes": {
            # These are NOT inferred by RetinaFace.
            # They are intentionally left as defaults for manual review.
            "occluded": False,
            "blurred": False,
            "small": False,
            "pose": "unknown",
        }
    }


def process_image(image_path: Path, model: Any, confidence_threshold: float) -> dict[str, Any] | None:
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        print(f"[WARNING] Could not read image: {image_path}")
        return None

    height, width = image.shape[:2]
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    predictions = model.predict_jsons(
        rgb_image,
        confidence_threshold=confidence_threshold,
    )

    faces = []
    for prediction in predictions:
        score = float(prediction.get("score", 0.0))
        if score < confidence_threshold:
            continue

        face = create_face_annotation(prediction, width, height)
        faces.append(face)

    annotation = {
        "image": {
            "id": image_path.stem,
            "file": image_path.as_posix(),
            "width": width,
            "height": height,
        },
        "faces": faces,
    }

    return annotation


def main() -> None:
    parser = ArgumentParser(description="Generate face annotations for images in a directory.")
    parser.add_argument("--input-dir", required=True, type=str, help="Directory containing input images.")
    parser.add_argument("--output-file", required=True, type=str, help="JSON file to write annotations to.")
    args = parser.parse_args()
    input_dir = args.input_dir
    output_file = args.output_file

    image_paths = find_images(Path(input_dir))
    if not image_paths:
        raise RuntimeError(
            f"No images found in {input_dir}"
        )
    print(f"Found {len(image_paths)} images.")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading RetinaFace ResNet-50 on {device}...")
    model = get_model(
        "resnet50_2020-07-20",
        max_size=DEFAULT_MAX_SIZE,
        device=device,
    )
    model.eval()
    print("RetinaFace loaded.")

    annotations = []
    failed_images = []
    total_faces = 0

    for image_path in tqdm(image_paths, desc="Generating annotations",):
        try:
            annotation = process_image(
                image_path=image_path,
                model=model,
                confidence_threshold=DEFAULT_CONFIDENCE
            )

            if annotation is None:
                failed_images.append(str(image_path))
                continue

            annotations.append(annotation)
            total_faces += len(annotation["faces"])

        except Exception as exc:
            print(f"\n[ERROR] Failed to process {image_path}: {exc}")
            failed_images.append(str(image_path))

    output = {
        "images": annotations,
    }

    Path(output_file).parent.mkdir(exist_ok=True, parents=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False,)
        f.write("\n")

    print()
    print("=" * 60)
    print("Annotation generation complete")
    print("=" * 60)

    print(f"Images found:       {len(image_paths)}")
    print(f"Images processed:   {len(annotations)}")
    print(f"Images failed:      {len(failed_images)}")
    print(f"Faces detected:     {total_faces}")
    print(f"Output:             {output_file}")

    if failed_images:
        print()
        print("Failed images:")
        for path in failed_images:
            print(f"  - {path}")


if __name__ == "__main__":
    main()
