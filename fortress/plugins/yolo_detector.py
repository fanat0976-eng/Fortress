"""YOLO detector — fast object detection for camera frames."""

import logging
from typing import Any

logger = logging.getLogger("fortress.yolo")

# COCO classes we care about for security
ALERT_CLASSES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    # Add more as needed
}

# Classes that trigger AI deep analysis
SUSPICIOUS_CLASSES = {0, 1, 2, 3, 5, 7}  # person, vehicles


class YOLODetector:
    """Fast object detection using YOLOv8 nano."""

    def __init__(self, model_name: str = "yolov8n.pt", confidence: float = 0.5):
        self.model_name = model_name
        self.confidence = confidence
        self._model = None

    def load(self):
        """Load YOLO model (called once at startup)."""
        try:
            from ultralytics import YOLO
            logger.info(f"Loading YOLO model: {self.model_name}")
            self._model = YOLO(self.model_name)
            logger.info("YOLO model loaded")
        except Exception as e:
            logger.error(f"Failed to load YOLO: {e}")
            self._model = None

    def detect(self, frame) -> list[dict]:
        """Detect objects in frame. Returns list of {class, name, confidence, bbox}."""
        if self._model is None:
            return []

        try:
            results = self._model(frame, verbose=False, conf=self.confidence)
            detections = []

            for r in results:
                boxes = r.boxes
                if boxes is not None:
                    for box in boxes:
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        x1, y1, x2, y2 = box.xyxy[0].tolist()

                        class_name = ALERT_CLASSES.get(cls_id, f"class_{cls_id}")
                        detections.append({
                            "class_id": cls_id,
                            "class_name": class_name,
                            "confidence": round(conf, 3),
                            "bbox": {
                                "x1": round(x1), "y1": round(y1),
                                "x2": round(x2), "y2": round(y2),
                            },
                            "center_x": round((x1 + x2) / 2),
                            "center_y": round((y1 + y2) / 2),
                        })

            return detections

        except Exception as e:
            logger.error(f"YOLO detection error: {e}")
            return []

    def has_suspicious(self, detections: list[dict]) -> bool:
        """Check if any detections are suspicious (person, vehicle)."""
        return any(d["class_id"] in SUSPICIOUS_CLASSES for d in detections)

    @property
    def available(self) -> bool:
        return self._model is not None
