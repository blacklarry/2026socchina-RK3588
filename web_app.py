"""
DLP-NIR Web 控制台 — Flask 后端
"""

import os
import json
import csv
import re
import signal
import subprocess
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file
from dlp_device import DLPDevice

app = Flask(__name__)
device = None
crop_predictor = None
crop_predictor_error = None
cached_wavelengths = None
white_reference = None
white_reference_meta = None
dark_reference = None
dark_reference_meta = None
dark_correction_enabled = False

# 历史数据默认存储目录
DEFAULT_SAVE_DIR = os.path.join(os.path.dirname(__file__), "spectra_data")
os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)

BASE_DIR = os.path.dirname(__file__)
BLE_GATT_NAME = "DLP-NIR"
BLE_GATT_SCRIPT = os.path.join(BASE_DIR, "ble_gatt_server.py")
BLE_GATT_LOG = os.path.join(BASE_DIR, "ble_gatt_server.log")
BLE_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
BLE_COMMAND_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
PREDICTION_MODEL_NAME = "SCF-mtr-c"


def get_device():
    global device
    if device is None:
        device = DLPDevice()
    return device


def get_crop_predictor():
    global crop_predictor, crop_predictor_error

    if crop_predictor is not None:
        return crop_predictor
    if crop_predictor_error:
        raise RuntimeError(crop_predictor_error)

    try:
        from crop_scf_mtr_c_predictor import CropSCFMtrCPredictor

        crop_predictor = CropSCFMtrCPredictor()
        return crop_predictor
    except Exception as exc:
        crop_predictor_error = str(exc)
        raise


def get_json_data():
    return request.get_json(silent=True) or {}


def error_response(message, code=None, status_code=200, mock=False):
    payload = {"ok": False, "error": message, "mock": mock}
    if code is not None:
        payload["code"] = code
    return jsonify(payload), status_code


def success_response(data=None, **kwargs):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)


def reset_references():
    global white_reference, white_reference_meta
    global dark_reference, dark_reference_meta, dark_correction_enabled

    white_reference = None
    white_reference_meta = None
    dark_reference = None
    dark_reference_meta = None
    dark_correction_enabled = False


def reset_device_cache():
    global cached_wavelengths
    cached_wavelengths = None


def get_cached_wavelengths(dev):
    global cached_wavelengths

    if cached_wavelengths is not None:
        return 0, cached_wavelengths

    ret, wls = dev.get_wavelengths()
    if ret < 0:
        return ret, wls
    cached_wavelengths = list(wls)
    return ret, cached_wavelengths


def white_status_payload():
    return {
        "has_reference": white_reference is not None,
        "points": len(white_reference) if white_reference else 0,
        "timestamp": white_reference_meta.get("timestamp") if white_reference_meta else None,
    }


def dark_status_payload():
    return {
        "enabled": dark_correction_enabled,
        "has_reference": dark_reference is not None,
        "points": len(dark_reference) if dark_reference else 0,
        "timestamp": dark_reference_meta.get("timestamp") if dark_reference_meta else None,
    }


def apply_dark_correction(intensities):
    if not dark_correction_enabled or dark_reference is None:
        return intensities, False
    if len(intensities) != len(dark_reference):
        return intensities, False
    return [i - d for i, d in zip(intensities, dark_reference)], True


def matching_reference(values, length):
    if values is None:
        return None
    if len(values) != length:
        return None
    return values


def next_numbered_name(save_dir, name):
    """Return the next name when the requested name ends with digits."""
    match = re.search(r"(\d+)$", name)
    if not match:
        return name

    prefix = name[:match.start(1)]
    current_digits = match.group(1)
    current_num = int(current_digits)
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.(?:json|csv)$")

    numbers = []
    for fname in os.listdir(save_dir):
        file_match = pattern.match(fname)
        if file_match:
            numbers.append(int(file_match.group(1)))

    if not numbers:
        return name

    next_num = max(max(numbers), current_num) + 1
    return f"{prefix}{next_num:0{len(current_digits)}d}"


def ble_gatt_processes():
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    processes = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if "ble_gatt_server.py" not in line:
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        processes.append({"pid": pid, "cmd": parts[1] if len(parts) > 1 else ""})
    return processes


def ble_gatt_status_payload():
    processes = ble_gatt_processes()
    return {
        "running": bool(processes),
        "processes": processes,
        "name": BLE_GATT_NAME,
        "script": BLE_GATT_SCRIPT,
        "log": BLE_GATT_LOG,
        "service_uuid": BLE_SERVICE_UUID,
        "command_uuid": BLE_COMMAND_UUID,
        "notify_uuid": BLE_NOTIFY_UUID,
    }


# ==================== 页面 ====================

@app.route("/")
def index():
    return render_template("index.html")


# ==================== 设备连接 ====================

@app.route("/api/connect", methods=["POST"])
def connect():
    dev = get_device()
    ret = dev.init_usb()
    if ret < 0:
        return error_response(f"USB 初始化失败: {ret}", code=ret, mock=dev._mock_mode)
    ret = dev.open_usb(0)
    if ret < 0:
        return error_response(f"打开设备失败: {ret}", code=ret, mock=dev._mock_mode)
    ret2, config = dev.get_config_info()
    if ret2 < 0:
        return error_response(f"获取配置失败: {ret2}", code=ret2, mock=dev._mock_mode)
    reset_device_cache()
    reset_references()
    return success_response(
        config=config or {},
        connected=dev.connected,
        white_status=white_status_payload(),
        dark_status=dark_status_payload(),
        mock=dev._mock_mode,
    )


@app.route("/api/disconnect", methods=["POST"])
def disconnect():
    dev = get_device()
    dev.close(0)
    reset_device_cache()
    reset_references()
    return success_response(connected=dev.connected, mock=dev._mock_mode)


@app.route("/api/status")
def status():
    dev = get_device()
    return success_response(connected=dev.connected, mock=dev._mock_mode)


# ==================== 光谱采集 ====================

@app.route("/api/acquire", methods=["POST"])
def acquire():
    started = time.perf_counter()
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    if white_reference is None:
        return error_response("请先采集白参考", mock=dev._mock_mode)
    ret, wls = get_cached_wavelengths(dev)
    if ret < 0:
        return error_response(f"获取波长失败: {ret}", code=ret, mock=dev._mock_mode)
    ret, intensities = dev.get_intensities(0)
    if ret < 0:
        return error_response(f"获取强度失败: {ret}", code=ret, mock=dev._mock_mode)
    intensities, corrected = apply_dark_correction(intensities)
    return success_response(
        wavelengths=wls,
        intensities=intensities,
        dark_corrected=corrected,
        white_reference=white_reference,
        dark_reference=dark_reference,
        white_status=white_status_payload(),
        dark_status=dark_status_payload(),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        mock=dev._mock_mode,
    )


@app.route("/api/white/reference", methods=["POST"])
def white_reference_capture():
    """读取设备内置参考强度作为白参考"""
    global white_reference, white_reference_meta

    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)

    ret, wls = get_cached_wavelengths(dev)
    if ret < 0:
        return error_response(f"获取波长失败: {ret}", code=ret, mock=dev._mock_mode)
    ret, intensities = dev.get_ref_intensity()
    if ret < 0:
        return error_response(f"获取白参考失败: {ret}", code=ret, mock=dev._mock_mode)

    white_reference = list(intensities)
    white_reference_meta = {
        "timestamp": datetime.now().isoformat(),
        "points": len(white_reference),
        "wl_min": min(wls) if wls else None,
        "wl_max": max(wls) if wls else None,
    }

    return success_response(
        wavelengths=wls,
        intensities=white_reference,
        status=white_status_payload(),
        mock=dev._mock_mode,
    )


@app.route("/api/white/status")
def white_status():
    return success_response(status=white_status_payload())


@app.route("/api/dark/reference", methods=["POST"])
def dark_reference_capture():
    """采集当前强度作为软件黑参考"""
    global dark_reference, dark_reference_meta, dark_correction_enabled

    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)

    ret, wls = get_cached_wavelengths(dev)
    if ret < 0:
        return error_response(f"获取波长失败: {ret}", code=ret, mock=dev._mock_mode)
    ret, intensities = dev.get_intensities(0)
    if ret < 0:
        return error_response(f"获取黑参考失败: {ret}", code=ret, mock=dev._mock_mode)

    dark_reference = list(intensities)
    dark_reference_meta = {
        "timestamp": datetime.now().isoformat(),
        "points": len(dark_reference),
        "wl_min": min(wls) if wls else None,
        "wl_max": max(wls) if wls else None,
    }
    dark_correction_enabled = True

    return success_response(
        wavelengths=wls,
        intensities=dark_reference,
        status=dark_status_payload(),
        mock=dev._mock_mode,
    )


@app.route("/api/dark/status")
def dark_status():
    return success_response(status=dark_status_payload())


@app.route("/api/dark/enable", methods=["POST"])
def dark_enable():
    global dark_correction_enabled

    data = get_json_data()
    enabled = bool(data.get("enabled", True))
    if enabled and dark_reference is None:
        return error_response("请先采集黑参考")

    dark_correction_enabled = enabled
    return success_response(status=dark_status_payload())


@app.route("/api/scan_results", methods=["POST"])
def scan_results():
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    ret, data = dev.get_scan_results()
    if ret < 0:
        return error_response(f"获取扫描结果失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(data=data, mock=dev._mock_mode)


@app.route("/api/reference", methods=["POST"])
def reference():
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    ret, wls = get_cached_wavelengths(dev)
    if ret < 0:
        return error_response(f"获取波长失败: {ret}", code=ret, mock=dev._mock_mode)
    ret, ref = dev.get_ref_intensity()
    if ret < 0:
        return error_response(f"获取参考光谱失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(wavelengths=wls, intensities=ref, mock=dev._mock_mode)


# ==================== 作物品种预测 ====================

@app.route("/api/crop/predict", methods=["POST"])
def crop_predict():
    data = get_json_data()
    intensities = data.get("intensities") or []
    target = data.get("target", "自动识别") or "自动识别"

    if not isinstance(intensities, list) or not intensities:
        return error_response("缺少当前光谱强度数据")

    try:
        values = [float(value) for value in intensities]
    except (TypeError, ValueError):
        return error_response("光谱强度数据必须是数值数组")

    try:
        result = get_crop_predictor().predict(values, target=target)
    except Exception as exc:
        return error_response(f"{PREDICTION_MODEL_NAME} 预测失败: {exc}")

    result["model_name"] = PREDICTION_MODEL_NAME
    return success_response(result=result)


# ==================== 设备控制 ====================

@app.route("/api/lamp", methods=["POST"])
def lamp():
    dev = get_device()
    data = get_json_data()
    on = data.get("on", True)
    ret = dev.set_lamp(on)
    if ret < 0:
        return error_response(f"设置灯状态失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(lamp_on=on, mock=dev._mock_mode)


@app.route("/api/gain", methods=["POST"])
def gain():
    dev = get_device()
    data = get_json_data()
    try:
        val = int(data.get("value", 1))
    except (TypeError, ValueError):
        return error_response("value 必须是整数", mock=dev._mock_mode)
    ret = dev.set_pga_gain(val)
    if ret < 0:
        return error_response(f"设置增益失败: {ret}", code=ret, mock=dev._mock_mode)
    ret2, current = dev.get_pga_gain()
    return success_response(gain=current, mock=dev._mock_mode)


@app.route("/api/avg", methods=["POST"])
def avg():
    dev = get_device()
    data = get_json_data()
    try:
        val = int(data.get("value", 1))
    except (TypeError, ValueError):
        return error_response("value 必须是整数", mock=dev._mock_mode)
    ret = dev.set_avg_times(val)
    if ret < 0:
        return error_response(f"设置平均次数失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(avg_times=val, mock=dev._mock_mode)


@app.route("/api/bluetooth", methods=["POST"])
def bluetooth():
    dev = get_device()
    data = get_json_data()
    on = data.get("on", True)
    ret = dev.set_bluetooth(on)
    if ret < 0:
        return error_response(f"设置蓝牙状态失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(bluetooth_on=on, mock=dev._mock_mode)


# ==================== 微信小程序 BLE GATT ====================

@app.route("/api/ble_gatt/status")
def ble_gatt_status():
    return success_response(status=ble_gatt_status_payload())


@app.route("/api/ble_gatt/start", methods=["POST"])
def ble_gatt_start():
    current = ble_gatt_status_payload()
    if current["running"]:
        return success_response(status=current)

    data = get_json_data()
    name = (data.get("name") or BLE_GATT_NAME).strip() or BLE_GATT_NAME

    try:
        log_file = open(BLE_GATT_LOG, "a", encoding="utf-8")
        log_file.write(f"\n[{datetime.now().isoformat()}] starting BLE GATT server\n")
        log_file.flush()
        subprocess.Popen(
            [
                "/usr/bin/python3",
                BLE_GATT_SCRIPT,
                "--name",
                name,
                "--chunk-size",
                "20",
                "--notify-interval-ms",
                "3",
            ],
            cwd=BASE_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        time.sleep(0.5)
    except Exception as e:
        return error_response(f"启动 BLE GATT 服务失败: {e}")

    return success_response(status=ble_gatt_status_payload())


@app.route("/api/ble_gatt/stop", methods=["POST"])
def ble_gatt_stop():
    processes = ble_gatt_processes()
    for proc in processes:
        try:
            os.kill(proc["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            return error_response(f"停止 BLE GATT 服务失败: {e}")
    time.sleep(0.3)
    return success_response(status=ble_gatt_status_payload())


# ==================== 设备信息 ====================

@app.route("/api/uuid")
def uuid():
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    ret, val = dev.get_uuid()
    if ret < 0:
        return error_response(f"获取 UUID 失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(uuid=val, mock=dev._mock_mode)


@app.route("/api/env")
def env():
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    ret, data = dev.get_humidity_temperature()
    if ret < 0:
        return error_response(f"获取环境信息失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(data=data, mock=dev._mock_mode)


@app.route("/api/battery")
def battery():
    dev = get_device()
    if not dev.connected:
        return error_response("未连接", mock=dev._mock_mode)
    ret, data = dev.get_battery_info()
    if ret < 0:
        return error_response(f"获取电池信息失败: {ret}", code=ret, mock=dev._mock_mode)
    return success_response(data=data, mock=dev._mock_mode)


# ==================== 历史数据管理 ====================

@app.route("/api/history/save", methods=["POST"])
def history_save():
    """保存当前光谱到本地文件"""
    data = get_json_data()
    wavelengths = data.get("wavelengths")
    intensities = data.get("intensities")
    save_white_reference = data.get("white_reference")
    save_dark_reference = data.get("dark_reference")
    name = data.get("name", "").strip()
    save_dir = data.get("save_dir", "").strip() or DEFAULT_SAVE_DIR
    fmt = data.get("format", "csv")

    if not wavelengths or not intensities:
        return error_response("缺少光谱数据")
    if len(wavelengths) != len(intensities):
        return error_response("波长与强度数据长度不一致")

    save_white_reference = matching_reference(
        save_white_reference if save_white_reference is not None else white_reference,
        len(wavelengths),
    )
    save_dark_reference = matching_reference(
        save_dark_reference if save_dark_reference is not None else dark_reference,
        len(wavelengths),
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not name:
        name = f"spectrum_{ts}"
    else:
        name = "".join(c for c in name if c.isalnum() or c in "-_()（）[]【】. ").strip()
        if not name:
            name = f"spectrum_{ts}"

    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        return error_response(f"创建目录失败: {e}")

    try:
        name = next_numbered_name(save_dir, name)
    except Exception as e:
        return error_response(f"读取目录失败: {e}")

    meta = {
        "name": name,
        "timestamp": datetime.now().isoformat(),
        "points": len(wavelengths),
        "wl_min": min(wavelengths),
        "wl_max": max(wavelengths),
        "has_white_reference": save_white_reference is not None,
        "has_dark_reference": save_dark_reference is not None,
    }

    try:
        if fmt == "csv":
            filepath = os.path.join(save_dir, f"{name}.csv")
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["# name", name])
                writer.writerow(["# timestamp", meta["timestamp"]])
                writer.writerow(["Wavelength(nm)", "Intensity", "WhiteReference", "DarkReference"])
                for idx, (w, i) in enumerate(zip(wavelengths, intensities)):
                    writer.writerow([
                        w,
                        i,
                        save_white_reference[idx] if save_white_reference is not None else "",
                        save_dark_reference[idx] if save_dark_reference is not None else "",
                    ])
        else:
            filepath = os.path.join(save_dir, f"{name}.json")
            payload = {
                "meta": meta,
                "wavelengths": wavelengths,
                "intensities": intensities,
                "white_reference": save_white_reference,
                "dark_reference": save_dark_reference,
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return error_response(f"保存失败: {e}")

    return success_response(
        filepath=filepath,
        name=name,
        meta=meta,
        save_dir=save_dir,
    )


@app.route("/api/history/list", methods=["GET", "POST"])
def history_list():
    """列出指定目录下的历史光谱文件"""
    data = get_json_data() if request.method == "POST" else {}
    scan_dir = data.get("save_dir", "").strip() or DEFAULT_SAVE_DIR

    if not os.path.isdir(scan_dir):
        return success_response(files=[], directory=scan_dir, default_dir=DEFAULT_SAVE_DIR)

    files = []
    for fname in sorted(os.listdir(scan_dir), reverse=True):
        if not (fname.endswith(".json") or fname.endswith(".csv")):
            continue
        fpath = os.path.join(scan_dir, fname)
        try:
            stat = os.stat(fpath)
            meta = {
                "filename": fname,
                "filepath": fpath,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "format": "json" if fname.endswith(".json") else "csv",
                "name": fname.rsplit(".", 1)[0],
            }
            if fname.endswith(".json"):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    if "meta" in obj:
                        meta.update(obj["meta"])
                except Exception:
                    pass
            files.append(meta)
        except Exception:
            continue

    return success_response(files=files, directory=scan_dir, default_dir=DEFAULT_SAVE_DIR)


@app.route("/api/history/load", methods=["POST"])
def history_load():
    """加载指定文件的光谱数据"""
    data = get_json_data()
    filepath = data.get("filepath", "").strip()

    if not filepath:
        return error_response("缺少 filepath 参数")
    if not os.path.isfile(filepath):
        return error_response(f"文件不存在: {filepath}")

    try:
        if filepath.endswith(".json"):
            with open(filepath, "r", encoding="utf-8") as f:
                obj = json.load(f)
            wavelengths = obj.get("wavelengths", [])
            intensities = obj.get("intensities", [])
            loaded_white_reference = obj.get("white_reference")
            loaded_dark_reference = obj.get("dark_reference")
            meta = obj.get("meta", {"name": os.path.basename(filepath)})
        elif filepath.endswith(".csv"):
            wavelengths, intensities = [], []
            loaded_white_reference, loaded_dark_reference = [], []
            meta = {"name": os.path.basename(filepath).rsplit(".", 1)[0]}
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith("#"):
                        if len(row) >= 2 and row[0] == "# name":
                            meta["name"] = row[1]
                        continue
                    if row[0] == "Wavelength(nm)":
                        continue
                    try:
                        wavelengths.append(float(row[0]))
                        intensities.append(float(row[1]))
                        loaded_white_reference.append(float(row[2]) if len(row) > 2 and row[2] != "" else None)
                        loaded_dark_reference.append(float(row[3]) if len(row) > 3 and row[3] != "" else None)
                    except (ValueError, IndexError):
                        continue
            if not any(v is not None for v in loaded_white_reference):
                loaded_white_reference = None
            if not any(v is not None for v in loaded_dark_reference):
                loaded_dark_reference = None
        else:
            return error_response("不支持的文件格式")
    except Exception as e:
        return error_response(f"读取失败: {e}")

    return success_response(
        wavelengths=wavelengths,
        intensities=intensities,
        meta=meta,
        filepath=filepath,
        save_dir=os.path.dirname(filepath),
    )


@app.route("/api/history/delete", methods=["POST"])
def history_delete():
    """删除指定历史文件"""
    data = get_json_data()
    filepath = data.get("filepath", "").strip()

    if not filepath:
        return error_response("缺少 filepath 参数")
    if not os.path.isfile(filepath):
        return error_response(f"文件不存在: {filepath}")

    try:
        os.remove(filepath)
    except Exception as e:
        return error_response(f"删除失败: {e}")

    return success_response(deleted=filepath)


@app.route("/api/history/default_dir")
def history_default_dir():
    """返回默认保存目录"""
    return success_response(directory=DEFAULT_SAVE_DIR)


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
