import argparse
import os
import shutil
import uuid


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        super().error(f"{message}\nHint: provide --source-dir DIRECTORY --target-dir DIRECTORY; use --help for details.")


parser = ArgumentParser(description="Copy images to a target directory with unique filenames.")
parser.add_argument("--source-dir", required=True, help="Directory containing images to copy.")
parser.add_argument("--target-dir", required=True, help="Directory to copy images into.")
args = parser.parse_args()
source_dir = args.source_dir
target_dir = args.target_dir

os.makedirs(target_dir, exist_ok=True)

for filename in os.listdir(source_dir):
    if filename.lower().endswith(".jpg") or filename.lower().endswith(".png"):
        source_path = os.path.join(source_dir, filename)
        new_filename = f"{uuid.uuid4()}.jpg"
        target_path = os.path.join(target_dir, new_filename)
        shutil.copy2(source_path, target_path)
        print(f"Copied: {filename} -> {new_filename}")

print("Done!")
