"""
Define the dataset class
"""

import os

import cv2
import numpy as np
from torch.utils.data import Dataset
from torchvision import transforms
from PIL import Image

from data.base_dataset import get_params, get_transform

IMG_EXTENSIONS = [".jpg", ".JPG", ".jpeg", ".JPEG", ".png", ".PNG"]


def unpickle(file):
    import pickle
    with open(file, "rb") as f:
        file_dict = pickle.load(f, encoding="bytes")
    return file_dict


def is_image_file(filename):
    return any(filename.endswith(extension) for extension in IMG_EXTENSIONS)


def make_dataset(dir, stop=10000):
    images = []
    count = 0
    assert os.path.isdir(dir), f"{dir} is not a valid directory"
    for root, _, files in sorted(os.walk(dir)):
        for file in files:
            if is_image_file(file):
                path = os.path.join(root, file)
                images.append(path)
                count += 1
            if count >= stop:
                return images
    return images


class UnpairedDepthDataset(Dataset):
    def __init__(self, root, root2, opt, transform=None, mode="train", midas=False, depthroot="", sketchroot=""):
        self.root = root
        self.mode = mode
        self.midas = midas

        all_img = make_dataset(self.root)

        self.depth_maps = 0
        self.sketchroot = sketchroot
        depthroot = sketchroot

        if depthroot != "":
            print(depthroot)
            if os.path.exists(depthroot):
                depth = make_dataset(depthroot)
            else:
                print("could not find %s" % depthroot)
                import sys
                sys.exit(0)

            new_images = []
            self.depth_maps = []

            for dmap in depth:
                lastname = os.path.basename(dmap)
                trainName1 = os.path.join(self.root, lastname)
                trainName2 = os.path.join(self.root, lastname.split(".")[0] + ".jpg")
                if (os.path.exists(trainName1)):
                    new_images += [trainName1]
                elif (os.path.exists(trainName2)):
                    new_images += [trainName2]
            print(f"Found {len(new_images)} paired images.")

            self.depth_maps = depth
            all_img = new_images

        self.data = all_img
        self.mode = mode

        self.transform = transforms.Compose(transform)

        self.opt = opt

        if mode == "train":
            self.img2 = make_dataset(root2)

            # Ensure that directories trainA and trainB have the same number of images.
            if len(self.data) > len(self.img2):
                howmanyrepeat = (len(self.data) // len(self.img2)) + 1
                self.img2 = self.img2 * howmanyrepeat
            elif len(self.img2) > len(self.data):
                howmanyrepeat = (len(self.img2) // len(self.data)) + 1
                self.data = self.data * howmanyrepeat
                self.depth_maps = self.depth_maps * howmanyrepeat

            cutoff = min(len(self.data), len(self.img2))

            self.data = self.data[:cutoff]
            self.img2 = self.img2[:cutoff]

            self.min_length = cutoff
        else:
            self.min_length = len(self.data)

    def __getitem__(self, index):
        img_path = self.data[index]

        basename = os.path.basename(img_path)
        base = basename.split(".")[0]

        img_r = Image.open(img_path).convert("RGB")
        transform_params = get_params(self.opt, img_r.size)
        A_transform = get_transform(self.opt, transform_params, grayscale=(self.opt.input_nc == 1), norm=False)
        B_transform = get_transform(self.opt, transform_params, grayscale=(self.opt.output_nc == 1), norm=False)

        if self.mode != "train":
            A_transform = self.transform

        img_r = A_transform(img_r)

        B_mode = "L"
        if self.opt.output_nc == 3:
            B_mode = "RGB"

        img_depth = 0
        if self.midas:
            img_depth = cv2.imread(self.depth_maps[index])
            img_depth = A_transform(Image.fromarray(img_depth.astype(np.uint8)).convert("RGB"))

        if self.sketchroot != "":
            img_depth = cv2.imread(self.depth_maps[index])
            img_depth = A_transform(Image.fromarray(img_depth.astype(np.uint8)).convert("L"))

        img_normals = 0
        label = 0

        input_dict = {"r": img_r, "depth": img_depth, "path": img_path, "index": index, "name": base, "label": label}

        if self.mode == "train":
            cur_path = self.img2[index]
            cur_img = B_transform(Image.open(cur_path).convert(B_mode))
            input_dict["line"] = cur_img

        return input_dict

    def __len__(self):
        return self.min_length
