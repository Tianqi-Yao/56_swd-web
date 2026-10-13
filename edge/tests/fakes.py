"""测试替身：按图块内容"检测"的假 YOLO 模型。"""
import numpy as np


class _Boxes:
    def __init__(self, xyxy, conf):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(conf, dtype=np.float32)
        self.cls = np.zeros(len(self.conf), dtype=np.float32)

    def __len__(self):
        return len(self.conf)


class _Result:
    def __init__(self, xyxy, conf):
        self.boxes = _Boxes(xyxy, conf)


class FakeModel:
    """把图块里像素值为 255 的区域当成一只虫，返回它的外接框。

    记录每次收到的图块形状和调用参数，供测试断言。
    """

    def __init__(self, conf=0.9, fail=False):
        self.conf = conf
        self.fail = fail
        self.tile_shapes = []
        self.kwargs = []

    def __call__(self, tile, **kw):
        if self.fail:
            raise RuntimeError("model crashed")
        self.tile_shapes.append(tile.shape)
        self.kwargs.append(kw)
        ys, xs = np.nonzero(tile[:, :, 0] == 255)
        if len(xs) == 0:
            return [_Result([], [])]
        return [_Result([[xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]], [self.conf])]
