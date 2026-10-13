"""切块推理：整张大图切成 640×640 的块分别跑 YOLO，再在块之间做 NMS 去重。

算法和参数照搬 run_code/yolo/yolo_ncnn.py（2026-03 的 yolo11n_b8 NCNN 模型），
只去掉了画框、保存切块图和进度条。
"""
from __future__ import annotations

from typing import List

import cv2
import numpy as np


def load_model(model_path: str):
    from ultralytics import YOLO  # 延迟导入：ultralytics 很重
    return YOLO(model_path, task="detect")


def detect(model, img: np.ndarray, tile: int = 640, overlap: float = 0.2,
           conf: float = 0.25, nms: float = 0.45) -> List[List[float]]:
    """返回 [[x1, y1, x2, y2, conf], ...]，原图像素坐标。"""
    H, W = img.shape[:2]
    stride = int(tile * (1 - overlap))

    boxes: List[List[float]] = []
    scores: List[float] = []
    for y in range(0, H, stride):
        for x in range(0, W, stride):
            x2, y2 = min(x + tile, W), min(y + tile, H)
            crop = img[y:y2, x:x2]
            pad_h, pad_w = tile - crop.shape[0], tile - crop.shape[1]
            if pad_h > 0 or pad_w > 0:
                crop = cv2.copyMakeBorder(crop, 0, pad_h, 0, pad_w,
                                          borderType=cv2.BORDER_CONSTANT, value=(0, 0, 0))

            r = model(crop, imgsz=tile, conf=conf, verbose=False)[0]
            if r.boxes is None or len(r.boxes) == 0:
                continue
            xyxy = np.asarray(r.boxes.xyxy)
            confs = np.asarray(r.boxes.conf)
            for box, score in zip(xyxy, confs):
                bx1, by1, bx2, by2 = map(int, box)
                boxes.append([
                    max(0.0, min(float(bx1) + x, W)),
                    max(0.0, min(float(by1) + y, H)),
                    max(0.0, min(float(bx2) + x, W)),
                    max(0.0, min(float(by2) + y, H)),
                ])
                scores.append(float(score))

    if not boxes:
        return []
    xywh = [[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes]
    keep = cv2.dnn.NMSBoxes(xywh, scores, score_threshold=conf, nms_threshold=nms)
    if len(keep) == 0:
        return []
    return [boxes[i] + [scores[i]] for i in np.asarray(keep).flatten()]
