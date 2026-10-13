from pathlib import Path

import numpy as np
import pytest

from detect import detect
from fakes import FakeModel

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "data/Rpi_contain_YOLO/run_code/yolo/images/1.jpg"
MODEL = REPO / "data/Rpi_contain_YOLO/run_code/yolo/yolo11n_b8/best_ncnn_model"
# 2026-10-12 用原 yolo_ncnn.py（只改了路径）跑 1.jpg 得到："Detections after NMS : 86"
EXPECTED_N = 86


def blank(w, h):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_box_remapped_to_full_image():
    img = blank(1200, 700)
    img[10:50, 700:740] = 255  # 只落在 x=512 那一列图块里
    (box,) = detect(FakeModel(), img)
    assert box[:4] == [700.0, 10.0, 740.0, 50.0]
    assert box[4] == pytest.approx(0.9)


def test_duplicate_boxes_across_overlap_merged_by_nms():
    img = blank(1200, 700)
    img[10:50, 530:570] = 255  # 落在 x=0 和 x=512 两个图块的重叠区
    model = FakeModel()
    boxes = detect(model, img)
    assert len(boxes) == 1
    assert boxes[0][:4] == [530.0, 10.0, 570.0, 50.0]


def test_edge_tile_padded_to_640():
    model = FakeModel()
    detect(model, blank(1000, 700))
    assert model.tile_shapes and all(s == (640, 640, 3) for s in model.tile_shapes)
    assert len(model.tile_shapes) == 4  # x∈{0,512} × y∈{0,512}
    assert model.kwargs[0] == {"imgsz": 640, "conf": 0.25, "verbose": False}


def test_box_clamped_to_image_bounds():
    img = blank(1000, 700)
    img[650:700, 980:1000] = 255
    (box,) = detect(FakeModel(), img)
    assert box[2] <= 1000 and box[3] <= 700


def test_no_detections_returns_empty():
    assert detect(FakeModel(), blank(800, 600)) == []


@pytest.mark.model
def test_parity_with_original_script():
    if not SAMPLE.exists() or not MODEL.exists():
        pytest.skip("sample image or model not present under data/")
    import cv2
    from detect import load_model
    boxes = detect(load_model(str(MODEL)), cv2.imread(str(SAMPLE)))
    assert len(boxes) == EXPECTED_N
