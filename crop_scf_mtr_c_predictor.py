from pathlib import Path

import joblib
import numpy as np
from scipy.signal import savgol_filter


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
CROP_MODEL_PATHS = {
    "玉米": MODEL_DIR / "corn_scf_mtr_c_model.pkl",
    "豌豆": MODEL_DIR / "pea_scf_mtr_c_model.pkl",
}
SAVGOL_WINDOW_LENGTH = 15
SAVGOL_POLYORDER = 2
MODEL_DISPLAY_NAME = "SCF-mtr-c"


DEFAULT_LABEL_MAPS = {
    "玉米": {
        "0": ("玉米", "脆甜89"),
        "1": ("玉米", "金甜13"),
        "2": ("玉米", "金糯606"),
        "3": ("玉米", "科泰甜8"),
        "4": ("玉米", "苏科甜1506"),
    },
    "豌豆": {
        "0": ("豌豆", "彩虹甜口口脆"),
        "1": ("豌豆", "长耕口口脆"),
        "2": ("豌豆", "脆甜豌豆"),
        "3": ("豌豆", "矮生双荚豌豆"),
        "4": ("豌豆", "食荚豌豆"),
        "5": ("豌豆", "双荚豌豆"),
    },
}


def trim_spectrum_bands(X, trim_front=0, trim_back=0):
    total_bands = X.shape[1]
    if trim_front < 0 or trim_back < 0:
        raise ValueError("trim_front and trim_back must be non-negative.")
    if trim_front + trim_back >= total_bands:
        raise ValueError("No bands remain after trimming.")
    end_idx = total_bands - trim_back if trim_back > 0 else total_bands
    return X[:, trim_front:end_idx]


def preprocess_1std(x):
    return np.diff(x, axis=1, prepend=x[:, :1])


def snv(input_data):
    mean = np.mean(input_data, axis=1, keepdims=True)
    std = np.std(input_data, axis=1, keepdims=True)
    return (input_data - mean) / (std + 1e-8)


def apply_training_preprocess(X, trim_front=0, trim_back=0):
    X = np.asarray(X, dtype=np.float32)
    if X.ndim == 1:
        X = X.reshape(1, -1)

    X = trim_spectrum_bands(X, trim_front=trim_front, trim_back=trim_back)
    if X.shape[1] < SAVGOL_WINDOW_LENGTH:
        raise ValueError(
            f"Band count after trimming is {X.shape[1]}, smaller than "
            f"Savitzky-Golay window_length={SAVGOL_WINDOW_LENGTH}."
        )

    X_smooth = savgol_filter(
        X,
        window_length=SAVGOL_WINDOW_LENGTH,
        polyorder=SAVGOL_POLYORDER,
        axis=1,
    )
    return snv(preprocess_1std(X_smooth)).astype(np.float32)


class SingleCropSCFMtrCPredictor:
    def __init__(self, crop_name, model_path, label_map=None):
        self.crop_name = crop_name
        self.model_path = Path(model_path)
        self.label_map = label_map or DEFAULT_LABEL_MAPS[crop_name]
        self.package = None
        self.model = None
        self.idx_to_class = {}
        self.trim_front = 0
        self.trim_back = 0
        self.load()

    def load(self):
        if not self.model_path.exists():
            raise FileNotFoundError(f"{self.crop_name}模型文件不存在: {self.model_path}")

        package = joblib.load(self.model_path)
        if not isinstance(package, dict) or "model" not in package:
            raise ValueError(f"{self.crop_name}模型文件不是支持的 {MODEL_DISPLAY_NAME} 模型包。")

        self.package = package
        self.model = package["model"]
        self.idx_to_class = package.get("idx_to_class", {})
        self.trim_front = int(package.get("trim_front_bands", 0))
        self.trim_back = int(package.get("trim_back_bands", 0))

    def predict(self, intensities):
        X = apply_training_preprocess(
            intensities,
            trim_front=self.trim_front,
            trim_back=self.trim_back,
        )

        pred = self.model.predict(X)
        pred_idx = int(np.asarray(pred)[0])
        label = str(self.idx_to_class.get(pred_idx, pred_idx))
        crop, variety = self.label_map.get(label, (self.crop_name, f"{self.crop_name}类别{label}"))

        candidates = self._candidate_results(X)
        if candidates:
            best = candidates[0]
            return {
                "crop": best["crop"],
                "variety": best["variety"],
                "confidence": best["confidence"],
                "raw_label": best["raw_label"],
                "candidates": candidates,
            }

        return {
            "crop": crop,
            "variety": variety,
            "confidence": 0.0,
            "raw_label": label,
            "candidates": [
                {
                    "crop": crop,
                    "variety": variety,
                    "confidence": 0.0,
                    "raw_label": label,
                }
            ],
        }

    def _candidate_results(self, X):
        if not hasattr(self.model, "predict_proba"):
            return []

        proba = self.model.predict_proba(X)[0]
        classes = list(getattr(self.model, "classes_", []))
        ranked_indices = np.argsort(proba)[::-1][:3]
        candidates = []
        for index in ranked_indices:
            class_idx = int(classes[index])
            raw_label = str(self.idx_to_class.get(class_idx, class_idx))
            crop, variety = self.label_map.get(raw_label, (self.crop_name, f"{self.crop_name}类别{raw_label}"))
            candidates.append(
                {
                    "crop": crop,
                    "variety": variety,
                    "confidence": float(proba[index] * 100.0),
                    "raw_label": raw_label,
                }
            )
        return candidates


class CropSCFMtrCPredictor:
    def __init__(self, model_paths=None, label_maps=None):
        self.model_paths = model_paths or CROP_MODEL_PATHS
        self.label_maps = label_maps or DEFAULT_LABEL_MAPS
        self.predictors = {}

    def configured_crops(self):
        return [crop for crop, path in self.model_paths.items() if Path(path).exists()]

    def _get_predictor(self, crop_name):
        if crop_name not in self.model_paths:
            raise ValueError(f"{crop_name}模型未配置。")
        if crop_name not in self.predictors:
            self.predictors[crop_name] = SingleCropSCFMtrCPredictor(
                crop_name=crop_name,
                model_path=self.model_paths[crop_name],
                label_map=self.label_maps.get(crop_name),
            )
        return self.predictors[crop_name]

    def predict(self, intensities, target="自动识别"):
        if target != "自动识别":
            return self._get_predictor(target).predict(intensities)

        configured = self.configured_crops()
        if not configured:
            raise FileNotFoundError("未找到玉米或豌豆识别模型。")

        results = [self._get_predictor(crop).predict(intensities) for crop in configured]
        results.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
        best = results[0]
        best["candidates"] = [
            {
                "crop": result["crop"],
                "variety": result["variety"],
                "confidence": result["confidence"],
                "raw_label": result.get("raw_label"),
            }
            for result in results[:3]
        ]
        return best
