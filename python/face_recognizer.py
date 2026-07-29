"""YuNet + SFace gallery recognizer used by the person follower."""

from pathlib import Path

import cv2
import numpy as np


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class FaceRecognizer:
    """Recognize faces against images stored under ``known_people``."""

    def __init__(self, project_root=None, match_threshold=0.40):
        root = Path(project_root or Path(__file__).resolve().parent.parent)
        models = root / "models"
        self.gallery_root = root / "known_people"
        self.match_threshold = match_threshold

        detector_model = models / "face_detection_yunet_2023mar.onnx"
        recognizer_model = models / "face_recognition_sface_2021dec.onnx"
        if not detector_model.exists() or not recognizer_model.exists():
            raise FileNotFoundError(
                "Face models are missing from the models/ directory."
            )

        self.detector = cv2.FaceDetectorYN.create(
            str(detector_model), "", (320, 320), 0.75, 0.3, 5000
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(recognizer_model), ""
        )
        self.gallery = self._load_gallery()

        if not self.gallery:
            print("[FaceRecognizer] No usable photos found in known_people/.")
        else:
            print(
                f"[FaceRecognizer] Loaded {sum(map(len, self.gallery.values()))} "
                f"face embeddings for: {', '.join(sorted(self.gallery))}"
            )

    @staticmethod
    def _decode(frame):
        if isinstance(frame, np.ndarray):
            return frame
        if isinstance(frame, (bytes, bytearray)):
            return cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
        return None

    def frame_width(self, frame):
        image = self._decode(frame)
        return None if image is None else image.shape[1]

    def _detect(self, image):
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _, faces = self.detector.detect(image)
        return [] if faces is None else faces

    def _embedding(self, image, face):
        aligned = self.recognizer.alignCrop(image, face)
        return self.recognizer.feature(aligned)

    def _load_gallery(self):
        gallery = {}
        if not self.gallery_root.exists():
            return gallery

        for person_dir in sorted(self.gallery_root.iterdir()):
            if not person_dir.is_dir():
                continue
            embeddings = []
            for path in sorted(person_dir.iterdir()):
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                image = cv2.imread(str(path))
                if image is None:
                    print(f"[FaceRecognizer] Could not read {path}")
                    continue
                faces = self._detect(image)
                if len(faces) == 0:
                    print(f"[FaceRecognizer] No face found in {path}")
                    continue
                face = max(faces, key=lambda item: float(item[14]))
                embeddings.append(self._embedding(image, face))
            if embeddings:
                gallery[person_dir.name] = embeddings
        return gallery

    @staticmethod
    def _person_box(box, width, height):
        values = [float(v) for v in box]
        if max(abs(v) for v in values) <= 1.5:
            x1, y1, x2, y2 = values
            return x1 * width, y1 * height, x2 * width, y2 * height
        return tuple(values)

    def identify(self, frame, person_box):
        """Return ``(face_visible, name, confidence)`` for one person box."""
        image = self._decode(frame)
        if image is None:
            return False, None, 0.0

        height, width = image.shape[:2]
        px1, py1, px2, py2 = self._person_box(person_box, width, height)
        faces = self._detect(image)
        candidates = []
        for face in faces:
            fx, fy, fw, fh = [float(v) for v in face[:4]]
            center_x, center_y = fx + fw / 2, fy + fh / 2
            if px1 <= center_x <= px2 and py1 <= center_y <= py2:
                candidates.append(face)
        if not candidates:
            return False, None, 0.0

        face = max(candidates, key=lambda item: float(item[14]))
        query = self._embedding(image, face)
        best_name = None
        best_score = -1.0
        for name, embeddings in self.gallery.items():
            for reference in embeddings:
                score = float(self.recognizer.match(
                    query, reference, cv2.FaceRecognizerSF_FR_COSINE
                ))
                if score > best_score:
                    best_name, best_score = name, score

        if best_score >= self.match_threshold:
            return True, best_name, best_score
        return True, None, max(best_score, 0.0)
