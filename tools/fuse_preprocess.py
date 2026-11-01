"""Development tool: put the image preparation into the pose models, so that it runs on the GPU.

  python tools/fuse_preprocess.py                 # the app's models (yolo26*-pose-<size>-b<n>.onnx)
  python tools/fuse_preprocess.py models/x.onnx   # just these

The short-named copies (yolo26n-pose-b1.onnx, ...) stay as exported: the tests compare against them.

A patched model takes the letterboxed picture as it is: uint8, (batch, size, size, 3), BGR,
padded with grey (114). Converting to float, scaling to 0..1, BGR -> RGB and the channel-first
layout the network expects then happen inside the model, on the GPU, instead of on the CPU:
a quarter of the data to copy to the GPU per frame, and no float pass over every pixel.
PoseDetector (vision/yolo_pose.py) reads the input type and handles both kinds of model.
Needs the onnx package (development environment only).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

MODELS = Path(__file__).resolve().parent.parent / "models"


def fuse(path: Path) -> bool:
    """Patch one model in place; False if it already takes uint8 pictures."""
    model = onnx.load(str(path))
    graph = model.graph
    old = graph.input[0]
    if old.type.tensor_type.elem_type == TensorProto.UINT8:
        return False
    n, c, h, w = (d.dim_value or d.dim_param for d in old.type.tensor_type.shape.dim)
    if c != 3:
        raise ValueError(f"{path.name}: expected a 3-channel image input, got shape {(n, c, h, w)}")
    pictures = helper.make_tensor_value_info("images_u8", TensorProto.UINT8, [n, h, w, 3])
    graph.initializer.extend([numpy_helper.from_array(np.array(1 / 255, np.float32), "pre_scale"),
                              numpy_helper.from_array(np.array([2, 1, 0], np.int64), "pre_rgb_order")])
    steps = [
        helper.make_node("Cast", ["images_u8"], ["pre_float"], to=TensorProto.FLOAT, name="pre_cast"),
        helper.make_node("Mul", ["pre_float", "pre_scale"], ["pre_unit"], name="pre_scale"),
        helper.make_node("Gather", ["pre_unit", "pre_rgb_order"], ["pre_rgb"], axis=3, name="pre_bgr_to_rgb"),
        helper.make_node("Transpose", ["pre_rgb"], [old.name], perm=[0, 3, 1, 2], name="pre_channels_first"),
    ]
    graph.input.remove(old)
    graph.input.insert(0, pictures)
    for i, node in enumerate(steps):
        graph.node.insert(i, node)
    onnx.checker.check_model(model)
    tmp = path.with_suffix(".tmp")
    onnx.save(model, str(tmp))
    tmp.replace(path)
    return True


def main(args) -> int:
    paths = [Path(p) for p in args.models] or sorted(MODELS.glob("yolo26*-pose-[0-9]*-b*.onnx"))
    for path in paths:
        print(f"{'patched' if fuse(path) else 'already done'}: {path.name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("models", nargs="*", help="model files (default: every pose model in models/)")
    sys.exit(main(p.parse_args()))
