"""Small, model-agnostic identity state machine for person following.

Face recognition is deliberately kept outside this module.  A recognizer can
call ``observe`` with the result of its face comparison, while this class
handles lost faces, track loss, and temporal confirmation.
"""

import time
from collections import Counter, deque


class PersonIdentity:
    """Track one selected person and smooth face-recognition decisions."""

    def __init__(self, face_lost_timeout=8.0, confirm_frames=3):
        self.face_lost_timeout = face_lost_timeout
        self.confirm_frames = confirm_frames
        self._state = "NO_PERSON"
        self._name = None
        self._last_seen = 0.0
        self._last_face = 0.0
        self._votes = deque(maxlen=confirm_frames)

    @property
    def state(self):
        return self._state

    @property
    def name(self):
        return self._name

    @property
    def face_visible(self):
        return time.monotonic() - self._last_face < 1.0

    def observe(self, *, track_present, face_visible=False,
                matched_name=None, match_confidence=0.0):
        """Update identity from the latest frame.

        ``matched_name`` must be a name from the enrolled gallery.  A visible
        but unmatched face becomes UNKNOWN; a hidden/occluded face is kept as
        TEMPORARILY_UNVERIFIED for a short time after a known match.
        """
        now = time.monotonic()
        if not track_present:
            self.reset()
            return self._state

        self._last_seen = now

        if face_visible:
            self._last_face = now
            if matched_name and match_confidence > 0.0:
                self._votes.append(matched_name)
                winner, count = Counter(self._votes).most_common(1)[0]
                if count >= self.confirm_frames:
                    self._name = winner
                    self._state = "KNOWN"
                else:
                    self._state = "UNKNOWN"
            else:
                self._votes.clear()
                self._name = None
                self._state = "UNKNOWN"
            return self._state

        # No face: retain a verified identity briefly, then become suspicious.
        if self._state in ("KNOWN", "TEMPORARILY_UNVERIFIED"):
            if now - self._last_face <= self.face_lost_timeout:
                self._state = "TEMPORARILY_UNVERIFIED"
            else:
                self._state = "SUSPICIOUS"
        elif self._state in ("NO_PERSON", "UNKNOWN"):
            self._state = "SUSPICIOUS"
        return self._state

    def reset(self):
        self._state = "NO_PERSON"
        self._name = None
        self._last_seen = 0.0
        self._last_face = 0.0
        self._votes.clear()

    def telemetry(self):
        return {
            "identity": self._state,
            "identity_name": self._name or "",
            "face_visible": self.face_visible,
        }
