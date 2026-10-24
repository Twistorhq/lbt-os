"""Real port adapters over the TW-649 harness stages (TW-658).

Each adapter delegates to the benchmark stage of the same job, so
the engine and the gate run the identical FOSS stack: PaddleOCR,
passporteye MRZ, zxing-cpp PDF417, YuNet detection, MediaPipe
landmarks, dlib face match (via face_recognition), MiniFASNet
liveness. Libraries import lazily inside the stages; a machine
without the stack gets StageUnavailable, honestly.

The one piece the harness did not need is preprocessing for the
liveness model (crop the detected face, resize to the MiniFASNet
input, scale to a float tensor). It lives here, behind the same
lazy imports, driven by the YuNet box so the crop is the detected
face and not a guess.
"""

from __future__ import annotations

import os

from app.verify.benchmark import stages

_YUNET_MODEL_ENV = "VFY_YUNET_MODEL_PATH"
_MINIFASNET_MODEL_ENV = "VFY_MINIFASNET_MODEL_PATH"
_MINIFASNET_INPUT = 80  # MiniFASNetV2 input is 80x80.


class OcrAdapter:
    def __init__(self):
        self._stage = stages.OcrStage()

    def extract_text(self, image_path: str) -> list[str]:
        return self._stage.extract_text(image_path)


class MrzAdapter:
    def __init__(self):
        self._stage = stages.MrzStage()

    def read(self, image_path: str):
        return self._stage.read(image_path)


class Pdf417Adapter:
    """PDF417 decode via zxing-cpp, AAMVA parsing via the harness.

    Integration note (found live in TW-658): the TW-649 stage calls
    zxingcpp.read_barcodes(image_path), but zxing-cpp 3.x takes an
    image, not a path, and raises TypeError on a string. The stage
    was only ever exercised against a stubbed module. This adapter
    loads the image and calls the current API, then reuses the
    harness's parse_aamva so field semantics stay single-sourced.
    The harness stage itself is flagged for a follow-up fix.
    """

    def decode_fields(self, image_path: str) -> dict[str, str]:
        zxingcpp = stages.Pdf417Stage()._require("zxingcpp")
        from PIL import Image

        results = zxingcpp.read_barcodes(Image.open(image_path))
        for result in results:
            if "PDF417" in str(getattr(result, "format", "")):
                return stages.parse_aamva(result.text)
        return {}


class FaceDetectAdapter:
    def __init__(self, model_path: str | None = None):
        self._stage = stages.FaceDetectStage()
        self._model_path = model_path or os.environ.get(_YUNET_MODEL_ENV, "")

    def detect(self, image_path: str) -> list[tuple]:
        if not self._model_path:
            raise stages.StageUnavailable(
                f"face_detect: YuNet model path not configured ({_YUNET_MODEL_ENV})"
            )
        return self._stage.detect(image_path, self._model_path)


class EmbedAdapter:
    """dlib embeddings via face_recognition: the same component the
    harness FaceMatchStage scores with, exposed as vectors because
    the engine needs the template, not just a pairwise score."""

    def embed(self, image_path: str):
        fr = stages.FaceMatchStage()._require("face_recognition")
        encodings = fr.face_encodings(fr.load_image_file(image_path))
        if not encodings:
            raise stages.StageUnavailable("face_match: no face found in the image")
        return tuple(float(v) for v in encodings[0])


class LandmarkAdapter:
    def __init__(self):
        self._stage = stages.LandmarkStage()

    def landmarks(self, image_path: str):
        return self._stage.landmarks(image_path)


class LivenessAdapter:
    """MiniFASNet passive signal with YuNet-crop preprocessing."""

    def __init__(self, model_path: str | None = None):
        self._stage = stages.LivenessStage()
        self._detect = FaceDetectAdapter()
        self._model_path = model_path or os.environ.get(_MINIFASNET_MODEL_ENV, "")

    def bona_fide_probability(self, image_path: str) -> float:
        if not self._model_path:
            raise stages.StageUnavailable(
                "liveness: MiniFASNet model path not configured "
                f"({_MINIFASNET_MODEL_ENV})"
            )
        cv2 = self._stage._require("cv2")
        self._stage._require("numpy")  # the tensor below is an ndarray
        image = cv2.imread(image_path)
        if image is None:
            raise stages.StageUnavailable(f"liveness: cannot read {image_path}")
        faces = self._detect.detect(image_path)
        if not faces:
            raise stages.StageUnavailable("liveness: no face to score")
        x, y, w, h = (int(v) for v in faces[0][:4])
        crop = image[max(y, 0) : y + h, max(x, 0) : x + w]
        resized = cv2.resize(crop, (_MINIFASNET_INPUT, _MINIFASNET_INPUT))
        tensor = resized.transpose(2, 0, 1)[None].astype("float32") / 255.0
        return self._stage.bona_fide_probability(self._model_path, tensor)


def real_ports() -> "object":
    """The production PipelinePorts over the harness stages."""
    from .ports import PipelinePorts

    return PipelinePorts(
        ocr=OcrAdapter(),
        mrz=MrzAdapter(),
        pdf417=Pdf417Adapter(),
        face_detect=FaceDetectAdapter(),
        embed=EmbedAdapter(),
        landmarks=LandmarkAdapter(),
        liveness=LivenessAdapter(),
    )


def secondary_port() -> None:
    """The production secondary identity source (TW-678): none.

    The stage exists (app/verify/engine/secondary.py) and its
    port is wired into the production pipeline here, but no
    adapter is configured, deliberately: the FOSS survey found no
    $0 records source that meets A.R.S. 4-101 condition (b).
    Knowledge based authentication and public records data are
    commercial per-use products, and AAMVA DLDV charges a setup
    fee plus a per-transaction fee, reached directly or through
    paid gateway partners. Wiring any of them is a spend decision
    for Justynn, not an engineering default, and returning the
    test fixture from this factory would be a false claim that a
    source is wired. Until that decision lands, flows that
    require the stage get an honest unavailable and fail closed.
    """
    return None
