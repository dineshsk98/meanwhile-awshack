"""Face ID with Amazon Rekognition: index a person's photo under their person id, find the faces in a frame, name a
person from an upper-body crop of a frame, and forget a person's faces.
"""

from __future__ import annotations

import time
from functools import cache
from typing import Any

import cv2
import numpy as np

from meanwhile import config

NO_FACE = "InvalidParameterException"
MAX_IDS = 4096


class FaceError(Exception):
    """Rekognition could not be reached or refused the call (other than finding no face)."""


@cache
def _client():
    """The Rekognition client, created on first use from MEANWHILE_AWS_PROFILE (empty: the default credential
    chain, such as an instance role) and MEANWHILE_AWS_REGION."""
    import boto3

    aws = boto3.Session(profile_name=config.AWS_PROFILE or None, region_name=config.AWS_REGION)
    return aws.client("rekognition")


def upper_body(image: np.ndarray, box: list[float], margin: float, upper: float) -> np.ndarray:
    """The top `upper` share of a person box ([x0, y0, x1, y1] fractions), widened by `margin` of its size on
    every side and clipped to the frame; Rekognition rejects tight face crops."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = box[0] * w, box[1] * h, box[2] * w, (box[1] + (box[3] - box[1]) * upper) * h
    dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
    left, top = max(0, int(x0 - dx)), max(0, int(y0 - dy))
    right, bottom = min(w, int(x1 + dx)), min(h, int(y1 + dy))
    return image[top:bottom, left:right]


def jpeg(image: np.ndarray) -> bytes:
    """A BGR image as JPEG bytes."""
    return cv2.imencode(".jpg", image)[1].tobytes()


def code_of(exc: Exception) -> str:
    """The AWS error code of a botocore ClientError, or ''."""
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


class Faces:
    """Rekognition calls on one collection. `client` defaults to the configured one (tests pass a stub); `last`
    holds the latest search's similarity and seconds taken."""

    def __init__(self, client: Any = None, collection: str | None = None, threshold: float | None = None,
                 margin: float | None = None, upper: float | None = None):
        """Use the given client and settings, or the configured ones."""
        self._given = client
        self.collection = collection or config.FACE_COLLECTION
        self.threshold = config.FACE_MATCH_THRESHOLD if threshold is None else threshold
        self.margin = config.FACE_CROP_MARGIN if margin is None else margin
        self.upper = config.FACE_UPPER_BODY if upper is None else upper
        self.last: dict[str, Any] = {}

    @property
    def client(self) -> Any:
        """The Rekognition client."""
        return self._given or _client()

    def index(self, person_id: str, image: bytes) -> list[str]:
        """Index the largest face in a person's JPEG photo under their person id; returns the new face ids, []
        when the photo shows no face (or one too poor to index)."""
        try:
            answer = self.client.index_faces(CollectionId=self.collection, Image={"Bytes": image},
                                             ExternalImageId=person_id, MaxFaces=1, QualityFilter="AUTO",
                                             DetectionAttributes=[])
        except Exception as exc:
            if code_of(exc) == NO_FACE:
                return []
            raise FaceError(f"IndexFaces: {exc}") from exc
        return [r["Face"]["FaceId"] for r in answer.get("FaceRecords", [])]

    def search(self, image: bytes) -> str | None:
        """The person whose face best matches the largest face in a JPEG, or None when nobody matches or no face
        is found."""
        start = time.perf_counter()
        try:
            answer = self.client.search_faces_by_image(CollectionId=self.collection, Image={"Bytes": image},
                                                       MaxFaces=1, FaceMatchThreshold=self.threshold)
        except Exception as exc:
            if code_of(exc) == NO_FACE:
                self.last = {"face": False, "similarity": None, "took_s": round(time.perf_counter() - start, 3)}
                return None
            raise FaceError(f"SearchFacesByImage: {exc}") from exc
        match = (answer.get("FaceMatches") or [None])[0]
        self.last = {"face": True, "similarity": match and round(match["Similarity"], 1),
                     "took_s": round(time.perf_counter() - start, 3)}
        return match["Face"].get("ExternalImageId") if match else None

    def find(self, image: bytes) -> list[list[float]]:
        """The faces in a JPEG as [x0, y0, x1, y1] fractions of the image, largest first ([] when there are none)."""
        start = time.perf_counter()
        try:
            answer = self.client.detect_faces(Image={"Bytes": image}, Attributes=["DEFAULT"])
        except Exception as exc:
            raise FaceError(f"DetectFaces: {exc}") from exc
        boxes = []
        for face in answer.get("FaceDetails", []):
            b = face["BoundingBox"]
            x0, y0 = max(0.0, b["Left"]), max(0.0, b["Top"])
            boxes.append([x0, y0, min(1.0, b["Left"] + b["Width"]), min(1.0, b["Top"] + b["Height"])])
        self.last = {"faces": len(boxes), "took_s": round(time.perf_counter() - start, 3)}
        return sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)

    def identify(self, image: np.ndarray, box: list[float]) -> str | None:
        """The person in a person box of a BGR frame, searched on an upper-body crop with margin; None for someone
        unknown or a face that isn't visible."""
        crop = upper_body(image, box, self.margin, self.upper)
        if crop.size == 0:
            self.last = {"face": False, "similarity": None, "took_s": 0.0}
            return None
        return self.search(jpeg(crop))

    def delete(self, face_ids: list[str]) -> None:
        """Delete these indexed faces (a refused registration's, or photos that were replaced)."""
        try:
            for i in range(0, len(face_ids), MAX_IDS):
                self.client.delete_faces(CollectionId=self.collection, FaceIds=face_ids[i:i + MAX_IDS])
        except Exception as exc:
            raise FaceError(f"DeleteFaces: {exc}") from exc

    def forget(self, person_id: str) -> int:
        """Delete every face indexed under a person id; returns how many were deleted."""
        try:
            ids, token = [], None
            while True:
                page = self.client.list_faces(CollectionId=self.collection, MaxResults=MAX_IDS,
                                              **({"NextToken": token} if token else {}))
                ids += [f["FaceId"] for f in page.get("Faces", []) if f.get("ExternalImageId") == person_id]
                token = page.get("NextToken")
                if not token:
                    break
        except Exception as exc:
            raise FaceError(f"ListFaces: {exc}") from exc
        self.delete(ids)
        return len(ids)


_default: Faces | None = None


def get_faces() -> Faces | None:
    """The process-wide face client, or None when face ID is off."""
    global _default
    if not config.FACES:
        return None
    if _default is None:
        _default = Faces()
    return _default
