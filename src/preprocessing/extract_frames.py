import argparse
from pathlib import Path
import cv2


def fixed_length(n):
    s = "0000000000" + str(n)
    return s[-10:]


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(f"{message}\nHint: provide --video-path FILE --output-dir DIRECTORY; use --help for details.")


parser = ArgumentParser(description="Extract every fifth frame from a 1280 x 720 video.")
parser.add_argument("--video-path", required=True, type=Path, help="Input video file.")
parser.add_argument("--output-dir", required=True, type=Path, help="Directory to save extracted frames in.")
args = parser.parse_args()
video_path = args.video_path
output_dir = args.output_dir
output_dir.mkdir(parents=True, exist_ok=True)
frame_id = 0
interval = 5
counter = 0

cap = cv2.VideoCapture(str(video_path))
if not cap.isOpened():
    raise SystemExit(f"[ERROR] Cannot open video: {video_path}")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    h, w = frame.shape[:2]
    if h != 720 or w != 1280:
        raise SystemExit(f"[ERROR] Expected 1280 x 720, Found {w}x{h}")
    
    if frame_id % interval == 0:
        counter += 1
        success = cv2.imwrite(str(output_dir / f"{fixed_length(counter)}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not success:
            raise SystemExit("[ERROR] Failed to save image")
    frame_id += 1

cap.release()
print(f"{counter} images saved.")
