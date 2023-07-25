"""
This file is used to download the cartoon dataset from HuggingFace.
"""

import os
from PIL import Image
import io

import pandas as pd

# Dataset: https://huggingface.co/datasets/Norod78/cartoon-blip-captions
parquet_ds = pd.read_parquet('ds.parquet', engine='pyarrow')
os.makedirs("../new_cartoons", exist_ok=True)

for i, data in enumerate(parquet_ds["image"]):
    image = Image.open(io.BytesIO(data["bytes"]))
    image.save(f"new_cartoons/image_{i}.png")
