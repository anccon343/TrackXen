"""The simulator's footage stage: the virtual camera's view cut out of a real video (sim/footage.py)."""

import numpy as np
import pytest


@pytest.fixture
def clip(tmp_path):
    """A 1-second 640x360 video: left half dark, right half bright."""
    av = pytest.importorskip("av")
    path = tmp_path / "stage.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=25, options={"preset": "ultrafast"})
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        image = np.full((360, 640, 3), 40, np.uint8)
        image[:, 320:] = 220
        for _ in range(25):
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="bgr24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return str(path)


def test_the_view_is_cut_out_where_the_camera_points(clip):
    from trackxen.sim.footage import FootageScene

    scene = FootageScene(clip, hfov=60.0, blur=False)
    try:
        assert (scene.width, scene.height) == (640, 360)
        left = scene.render(-15.0, 0.0, 20.0, 0.0, (320, 180))  # the dark half, zoomed in
        right = scene.render(15.0, 0.0, 20.0, 0.1, (320, 180))  # the bright half
        assert left.shape == right.shape == (180, 320, 3)
        assert left.mean() < 80 < 180 < right.mean()
        past_edge = scene.render(40.0, 0.0, 20.0, 0.2, (320, 180))  # beyond the clip's right edge: black
        assert past_edge[:, -40:].max() == 0
    finally:
        scene.stop()


def test_panning_blurs_like_a_shutter(clip):
    from trackxen.sim.footage import FootageScene

    scene = FootageScene(clip, hfov=60.0)
    try:
        scene.render(0.0, 0.0, 30.0, 0.0, (320, 180))
        still = scene.render(0.0, 0.0, 30.0, 0.04, (320, 180))
        panning = scene.render(2.0, 0.0, 30.0, 0.08, (320, 180))  # 50 degrees/s
        edge = lambda img: np.abs(np.diff(img[:, :, 0].astype(int), axis=1)).max()  # noqa: E731
        assert edge(panning) < edge(still)  # the dark/bright border is smeared while panning
    finally:
        scene.stop()
