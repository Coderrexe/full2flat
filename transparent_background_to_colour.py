# import cv2
# im = cv2.imread("/home/titanxp/Desktop/Generate Line Art from Photo/examples/train/flat_color/cs1104753354305220177.png", cv2.IMREAD_UNCHANGED)
# print(im.shape)
# cv2.imshow("image", im[:,:,1])
# cv2.waitKey(0)
#
import cv2
import numpy as np
import os

dpath = "examples/train/flat_color"
for filename in os.listdir("examples/train/flat_color"):
    image_path = f"{dpath}/{filename}"
    image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    # Ensure the image has 4 channels (B, G, R, A)
    if image.shape[2] == 4:
        # Split the image into RGB and Alpha channels
        rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        alpha = image[:,:,3]

        # Generate a single random RGB color
        random_bgr_color = np.random.randint(0, 256, 3, dtype=np.uint8)

        # Wherever the image is transparent (alpha channel is 0), replace with the random color
        rgb[alpha==0] = random_bgr_color

        cv2.imwrite(image_path, rgb)
