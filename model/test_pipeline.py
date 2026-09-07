import importlib
import json
from fastapi.testclient import TestClient
import compliance_engine
import extraction
import report_generator
import main

importlib.reload(compliance_engine)
importlib.reload(extraction)
importlib.reload(report_generator)
importlib.reload(main)

client = TestClient(app=main.app)

image_path = "/kaggle/input/datasets/aayushprasad11/testingimage/Gemini_Generated_Image_nf4l3gnf4l3gnf4l.png"
with open(image_path, "rb") as f:
    image_bytes = f.read()

response = client.post(
    "/analyze/image",
    files={"file": ("product.jpg", image_bytes, "image/jpeg")},
    data={
        "manual_pack_width_cm": 10.0,
        "manual_pack_height_cm": 15.0
    }
)

print("Status Code:", response.status_code)
print(json.dumps(response.json(), indent=2))