"""FOSS stage adapters for the verification pipeline (TW-649).

One adapter per pipeline stage from TW-646 Section 5.3. Every heavy
library is imported lazily, inside the stage that needs it: the metric
core and the registry stay pure stdlib, and a machine without the
model stack can still run the harness math, probe stage availability,
and say honestly which stages it cannot execute.

InsightFace is deliberately absent: its code is MIT but its widely
used pretrained model packs are licensed for non-commercial research
only (TW-646 Section 5.3). Unclear or non-commercial weights do not
enter this pipeline, even to benchmark against.

A stage that cannot run raises StageUnavailable with the reason.
Nothing here degrades silently to a weaker check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.verify.benchmark.documents import mrz_field_valid


class StageUnavailable(RuntimeError):
    """The stage's library or model is not present on this machine."""


@dataclass(frozen=True)
class StageStatus:
    name: str
    component: str
    license: str
    available: bool
    detail: str


def _import(module: str):
    try:
        return __import__(module)
    except ImportError:
        return None


def distance_to_similarity(distance: float) -> float:
    """Normalize a dlib face distance to the harness similarity shape.

    dlib distances are unbounded above in theory and cluster under
    ~1.0 in practice; similarity = 1 - distance, clamped to [0, 1].
    The harness threshold (metrics module) is expressed against this
    normalized score.
    """
    return max(0.0, min(1.0, 1.0 - distance))


class Stage:
    """Base class: name, component, license posture, availability probe."""

    name: str = "stage"
    component: str = ""
    license: str = ""
    module: str = ""  # import name probed for availability

    def probe(self) -> StageStatus:
        if self.module and _import(self.module) is None:
            return StageStatus(
                self.name, self.component, self.license, False,
                f"python module {self.module!r} not installed "
                f"(see requirements-benchmark.txt)",
            )
        return StageStatus(
            self.name, self.component, self.license, True, "module importable"
        )

    def _require(self, module: str):
        mod = _import(module)
        if mod is None:
            raise StageUnavailable(
                f"stage {self.name}: module {module!r} not installed "
                f"(see requirements-benchmark.txt)"
            )
        return mod


class OcrStage(Stage):
    """PaddleOCR over the document front: printed fields as text."""

    name = "ocr"
    component = "PaddleOCR"
    license = "Apache-2.0 (code and models)"
    module = "paddleocr"

    def extract_text(self, image_path: str) -> list[str]:
        paddleocr = self._require("paddleocr")
        engine = paddleocr.PaddleOCR(use_angle_cls=True, lang="en")
        result = engine.ocr(image_path, cls=True)
        lines: list[str] = []
        for page in result or []:
            for item in page or []:
                lines.append(item[1][0])
        return lines


# AAMVA element ids used from the PDF417 payload on US licenses/IDs.
_AAMVA_FIELDS = {
    "DCS": "surname",
    "DAC": "given_names",
    "DAQ": "doc_number",
    "DBB": "dob",
    "DBA": "expiry",
    "DAG": "address_street",
    "DAI": "address_city",
    "DAJ": "address_state",
    "DAK": "address_postal",
}


def parse_aamva(payload: str) -> dict[str, str]:
    """Parse an AAMVA PDF417 text payload into named fields.

    Pure stdlib: the payload layout is element-id + value, separated
    by newlines in the decoded text. Unknown elements are ignored.
    """
    fields: dict[str, str] = {}
    for line in payload.replace("\r", "\n").split("\n"):
        if len(line) > 3 and line[:3] in _AAMVA_FIELDS:
            fields[_AAMVA_FIELDS[line[:3]]] = line[3:].strip()
    return fields


class Pdf417Stage(Stage):
    """zxing-cpp decode of the PDF417 barcode on US licenses/IDs."""

    name = "pdf417"
    component = "zxing-cpp"
    license = "Apache-2.0"
    module = "zxingcpp"

    def decode_fields(self, image_path: str) -> dict[str, str]:
        zxingcpp = self._require("zxingcpp")
        results = zxingcpp.read_barcodes(image_path)
        for result in results:
            if result.text:
                return parse_aamva(result.text)
        return {}


def parse_mrz_lines(lines: list[str]) -> dict[str, object]:
    """Parse TD3 (passport) MRZ lines and validate check digits.

    Pure stdlib, using documents.mrz_field_valid. Returns the fields
    plus a per-field validity map; a failed check digit is reported,
    never repaired. Passports only: US licenses carry no MRZ.
    """
    cleaned = [ln.replace(" ", "").upper() for ln in lines if ln.strip()]
    if len(cleaned) < 2 or len(cleaned[1]) < 44:
        return {"parsed": False, "fields": {}, "valid": {}}
    line = cleaned[1]
    fields = {
        "doc_number": line[0:9],
        "nationality": line[10:13],
        "dob": line[13:19],
        "sex": line[20],
        "expiry": line[21:27],
        "personal_number": line[28:42],
    }
    valid = {
        "doc_number": mrz_field_valid(line[0:10]),
        "dob": mrz_field_valid(line[13:20]),
        "expiry": mrz_field_valid(line[21:28]),
        "personal_number": mrz_field_valid(line[28:43]),
        "composite": mrz_field_valid(
            line[0:10] + line[13:20] + line[21:28] + line[28:43] + line[43]
        ),
    }
    return {"parsed": True, "fields": fields, "valid": valid}


class MrzStage(Stage):
    """passporteye MRZ read from the image, validated by check digits."""

    name = "mrz"
    component = "passporteye + ICAO 9303 check digits"
    license = "MIT (passporteye); check-digit math is ICAO 9303"
    module = "passporteye"

    def read(self, image_path: str) -> Optional[dict[str, object]]:
        passporteye = self._require("passporteye")
        mrz = passporteye.read_mrz(image_path)
        if mrz is None:
            return None
        return parse_mrz_lines(mrz.lines())


class FaceDetectStage(Stage):
    """OpenCV YuNet face detection: capture guidance and cropping."""

    name = "face_detect"
    component = "OpenCV YuNet (opencv_zoo)"
    license = "MIT (model and code)"
    module = "cv2"

    def detect(self, image_path: str, model_path: str) -> list[tuple]:
        cv2 = self._require("cv2")
        detector = cv2.FaceDetectorYN.create(model_path, "", (0, 0))
        image = cv2.imread(image_path)
        if image is None:
            raise StageUnavailable(f"stage {self.name}: cannot read {image_path}")
        detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = detector.detect(image)
        return [tuple(face) for face in (faces if faces is not None else [])]


class LandmarkStage(Stage):
    """MediaPipe Face Mesh landmarks: liveness challenge + guidance."""

    name = "landmarks"
    component = "MediaPipe Face Mesh"
    license = "Apache-2.0"
    module = "mediapipe"

    def landmarks(self, image_path: str) -> list[tuple[float, float, float]]:
        mp = self._require("mediapipe")
        cv2 = self._require("cv2")
        image = cv2.imread(image_path)
        if image is None:
            raise StageUnavailable(f"stage {self.name}: cannot read {image_path}")
        with mp.solutions.face_mesh.FaceMesh(static_image_mode=True) as mesh:
            result = mesh.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        if not result.multi_face_landmarks:
            return []
        return [
            (lm.x, lm.y, lm.z) for lm in result.multi_face_landmarks[0].landmark
        ]


class FaceMatchStage(Stage):
    """dlib face recognition (via face_recognition): 1:1 embedding match."""

    name = "face_match"
    component = "dlib face recognition via face_recognition"
    license = "dlib Boost Software License; model freely usable"
    module = "face_recognition"

    def similarity(self, image_a: str, image_b: str) -> float:
        fr = self._require("face_recognition")
        enc_a = fr.face_encodings(fr.load_image_file(image_a))
        enc_b = fr.face_encodings(fr.load_image_file(image_b))
        if not enc_a or not enc_b:
            raise StageUnavailable(
                f"stage {self.name}: no face found in one of the images"
            )
        distance = fr.face_distance([enc_a[0]], enc_b[0])[0]
        return distance_to_similarity(float(distance))


class LivenessStage(Stage):
    """MiniFASNet passive anti-spoof signal (Silent-Face-Anti-Spoofing).

    One signal, never a gate by itself (TW-646 Section 5.3): returns
    the bona-fide probability from the ONNX model.
    """

    name = "liveness"
    component = "MiniFASNet (Silent-Face-Anti-Spoofing) via ONNX Runtime"
    license = "Apache-2.0"
    module = "onnxruntime"

    def bona_fide_probability(self, model_path: str, tensor) -> float:
        ort = self._require("onnxruntime")
        session = ort.InferenceSession(model_path)
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: tensor})[0]
        scores = output[0]
        # MiniFASNet class layout: index 1 is the bona fide (real) class.
        return float(scores[1]) / float(sum(scores))


STAGES: tuple[Stage, ...] = (
    OcrStage(),
    MrzStage(),
    Pdf417Stage(),
    FaceDetectStage(),
    LandmarkStage(),
    FaceMatchStage(),
    LivenessStage(),
)


def probe_stages() -> list[StageStatus]:
    """Availability of every stage on this machine, honestly reported."""
    return [stage.probe() for stage in STAGES]
