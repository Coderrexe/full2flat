import os
import shutil
import random

# Define the source directory and destination directory
src_dir = "../examples/train/flat_color"
dst_dir = "../examples/train/flat_color"

# Get a list of all file names in the source directory
all_files = os.listdir(src_dir)

# Randomly select 3000 files
selected_files = random.sample(all_files, 3000)

# Copy each selected file to the destination directory
for file_name in selected_files:
    # Create full file paths for source and destination
    src_file_path = os.path.join(src_dir, file_name)
    dst_file_path = os.path.join(dst_dir, file_name)

    # Copy the file
    shutil.copy(src_file_path, dst_file_path)
