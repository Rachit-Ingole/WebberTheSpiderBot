"""ORB keypoint matching class to recognize custom floor equipment from reference images."""

import os
from pathlib import Path
import cv2
import numpy as np

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

class EquipmentRecognizer:
    """Recognize industrial equipment from reference photos stored under known_equipment."""

    def __init__(self, project_root=None, min_match_count=8, max_hamming_dist=42):
        root = Path(project_root or Path(__file__).resolve().parent.parent)
        self.gallery_root = root / "known_equipment"
        self.min_match_count = min_match_count
        self.max_hamming_dist = max_hamming_dist

        # Initialize ORB (Oriented FAST and Rotated BRIEF) feature extractor
        self.orb = cv2.ORB_create(nfeatures=1000, scaleFactor=1.2, nlevels=8)
        
        # BFMatcher with Hamming distance since ORB descriptors are binary
        # crossCheck=True ensures mutual best matches for high precision
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        
        self.gallery = self._load_gallery()
        if not self.gallery:
            print("[EquipmentRecognizer] No reference photos found in known_equipment/.")
        else:
            total_templates = sum(len(templates) for templates in self.gallery.values())
            print(f"[EquipmentRecognizer] Loaded {total_templates} templates for: {', '.join(sorted(self.gallery))}")

    def _load_gallery(self):
        gallery = {}
        if not self.gallery_root.exists():
            return gallery

        for equip_dir in self.gallery_root.iterdir():
            if not equip_dir.is_dir():
                continue
            
            templates = []
            for path in equip_dir.iterdir():
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    print(f"[EquipmentRecognizer] Could not read {path}")
                    continue
                
                _, des = self.orb.detectAndCompute(img, None)
                if des is not None:
                    templates.append(des)
                else:
                    print(f"[EquipmentRecognizer] Warning: No features found in reference {path}")
            
            if templates:
                gallery[equip_dir.name] = templates
        return gallery

    def match_frame(self, frame):
        """Scan the center ROI of the frame and return the recognized equipment name and match count.

        Returns:
            (name, matches_count) or (None, 0)
        """
        if not self.gallery:
            return None, 0

        # Decode if raw bytes
        if not isinstance(frame, np.ndarray):
            if isinstance(frame, (bytes, bytearray, memoryview)):
                frame = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
            else:
                return None, 0

        if frame is None:
            return None, 0

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Crop to center 60% ROI to eliminate background clutter (walls, ceiling, floor)
        h, w = gray.shape[:2]
        cy, cx = h // 2, w // 2
        dy, dx = int(h * 0.3), int(w * 0.3)
        roi = gray[cy - dy : cy + dy, cx - dx : cx + dx]

        _, des_scene = self.orb.detectAndCompute(roi, None)
        if des_scene is None:
            return None, 0

        best_name = None
        best_matches = 0

        for name, templates in self.gallery.items():
            for des_ref in templates:
                try:
                    matches = self.matcher.match(des_ref, des_scene)
                    # Filter matches by distance (lower distance = closer match)
                    good_matches = [m for m in matches if m.distance < self.max_hamming_dist]
                    match_count = len(good_matches)
                    
                    if match_count > best_matches:
                        best_matches = match_count
                        best_name = name
                except Exception as exc:
                    print(f"[EquipmentRecognizer] Error matching {name}: {exc}")

        if best_matches >= self.min_match_count:
            return best_name, best_matches
        return None, best_matches
