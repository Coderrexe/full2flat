import os
from PIL import Image


# FROM 1700 ONWARDS, THE IMAGES ARE NOT HANDPICKED.
def reduce_colors(image_path, image_name, num_colors):
    # Open the image
    image = Image.open(image_path)

    # If image is not in "P" mode (palette-based), convert it
    if image.mode != "P":
        image = image.convert("RGB")

    # Reduce the number of colors
    image = image.quantize(colors=num_colors)
    image = image.convert("RGB")

    # Save the new image
    new_image_path = f"examples/train/flat_color/{image_name}"
    image.save(new_image_path)


for image_name in os.listdir("../examples/train/unprocessed_flat_color"):
    file_path = f"examples/train/unprocessed_flat_color/{image_name}"
    reduce_colors(file_path, image_name, 250)
