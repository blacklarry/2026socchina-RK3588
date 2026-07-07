"""
DLP-NIR 光谱仪 ctypes 封装
"""

import os
from ctypes import CDLL, c_int, c_uint8, c_uint16, c_uint32, c_double, c_bool, POINTER, Structure, byref

# 常量
WLS_NUM = 228
ADC_DATA_LEN = 864
SLEW_SCAN_MAX_SECTIONS = 5


# 错误码
class RetError:
    SUCCESS = 0
    GET_SPEC_NUM_ERROR = -1
    INIT_ERROR = -2
    OPEN_SPEC_ERROR = -3
    USB_CONNECT_ERROR = -12
    GET_PGA_GAIN_ERROR = -22
    SET_PGA_GAIN_ERROR = -230
    SET_AVG_TIMES_ERROR = -7
    BLE_ENABLE_ERROR = -31
    BLE_DISABLE_ERROR = -32

    INVALID_SCAN_LENGTH = -1001
    INVALID_ADC_LENGTH = -1002
    INVALID_PARAMETER = -1003


# 数据结构
class PynScanConfigHead(Structure):
    _fields_ = [
        ("scan_type", c_uint8),
        ("scanConfigIndex", c_uint16),
        ("scanConfig_serial_number", c_uint8 * 8),
        ("config_name", c_uint8 * 40),
        ("num_repeats", c_uint16),
        ("num_sections", c_uint8),
    ]


class PynScanSection(Structure):
    _fields_ = [
        ("section_scan_type", c_uint8),
        ("width_px", c_uint8),
        ("wavelength_start_nm", c_uint16),
        ("wavelength_end_nm", c_uint16),
        ("num_patterns", c_uint16),
        ("exposure_time", c_uint16),
    ]


class PynScanConfig(Structure):
    _fields_ = [
        ("head", PynScanConfigHead),
        ("section", PynScanSection * SLEW_SCAN_MAX_SECTIONS),
    ]


class PynScanResults(Structure):
    _fields_ = [
        ("header_version", c_uint32),
        ("scan_name", c_uint8 * 20),
        ("year", c_uint8),
        ("month", c_uint8),
        ("day", c_uint8),
        ("day_of_week", c_uint8),
        ("hour", c_uint8),
        ("minute", c_uint8),
        ("second", c_uint8),
        ("system_temp_hundredths", c_int),
        ("detector_temp_hundredths", c_int),
        ("humidity_hundredths", c_uint16),
        ("lamp_pd", c_uint16),
        ("scanDataIndex", c_uint32),
        ("ShiftVectorCoeffs", c_double * 3),
        ("PixelToWavelengthCoeffs", c_double * 3),
        ("serial_number", c_uint8 * 8),
        ("adc_data_length", c_uint16),
        ("black_pattern_first", c_uint8),
        ("black_pattern_period", c_uint8),
        ("pga", c_uint8),
        ("cfg", PynScanConfig),
        ("wavelength", c_double * ADC_DATA_LEN),
        ("intensity", c_int * ADC_DATA_LEN),
        ("length", c_int),
    ]


class DLPDevice:
    """DLP-NIR 光谱仪设备封装"""

    def __init__(self, lib_path=None):
        self._connected = False
        self._mock_mode = False
        self.lib = None

        if lib_path is None:
            base_dir = os.path.dirname(__file__)
            candidates = [
                os.path.join(base_dir, "wrapper.so"),
                os.path.join(base_dir, "wrapper.dll"),
                os.path.join(base_dir, "wrapper.dylib"),
            ]
            lib_path = next((p for p in candidates if os.path.exists(p)), candidates[0])

        if os.path.exists(lib_path):
            try:
                self.lib = CDLL(lib_path)
                self._setup_functions()
            except Exception as e:
                print(f"[警告] 无法加载设备库: {e}")
                print("[提示] 将以模拟模式运行，设备功能不可用")
                self._mock_mode = True
        else:
            print(f"[警告] 未找到设备库文件: {lib_path}")
            print("[提示] 将以模拟模式运行，设备功能不可用")
            self._mock_mode = True

    def _setup_functions(self):
        if self.lib is None:
            return
        lib = self.lib

        # dlpInitUsb() -> int
        lib.dlpInitUsb.restype = c_int
        lib.dlpInitUsb.argtypes = []  # type: ignore

        # dlpOpenByUsb(deviceIndex) -> int
        lib.dlpOpenByUsb.restype = c_int
        lib.dlpOpenByUsb.argtypes = [c_int]

        # dlpClose(deviceIndex) -> void
        lib.dlpClose.restype = None
        lib.dlpClose.argtypes = [c_int]

        # dlpGetConfigInfo(config*) -> int
        lib.dlpGetConfigInfo.restype = c_int
        lib.dlpGetConfigInfo.argtypes = [POINTER(PynScanConfig)]

        # dlpGetScanResults(results*) -> int
        lib.dlpGetScanResults.restype = c_int
        lib.dlpGetScanResults.argtypes = [POINTER(PynScanResults)]

        # dlpGetWavelengths(wls*, length) -> int
        lib.dlpGetWavelengths.restype = c_int
        lib.dlpGetWavelengths.argtypes = [POINTER(c_double), c_int]

        # dlpGetIntensities(activeIndex, intensity*, length) -> int
        lib.dlpGetIntensities.restype = c_int
        lib.dlpGetIntensities.argtypes = [c_int, POINTER(c_int), c_int]

        # dlpGetRefIntensityFromDevice(pRefIntensity*, length) -> int
        lib.dlpGetRefIntensityFromDevice.restype = c_int
        lib.dlpGetRefIntensityFromDevice.argtypes = [POINTER(c_int), c_int]

        # dlpSetLampStatus(status) -> int
        lib.dlpSetLampStatus.restype = c_int
        lib.dlpSetLampStatus.argtypes = [c_bool]

        # dlpSetBluetoothStatus(status) -> int
        lib.dlpSetBluetoothStatus.restype = c_int
        lib.dlpSetBluetoothStatus.argtypes = [c_bool]

        # dlpSetPgaGain(gain) -> int
        lib.dlpSetPgaGain.restype = c_int
        lib.dlpSetPgaGain.argtypes = [c_int]

        # dlpGetPgaGain(gain*) -> int
        lib.dlpGetPgaGain.restype = c_int
        lib.dlpGetPgaGain.argtypes = [POINTER(c_int)]

        # dlpSetAvgTimes(avgTimes) -> int
        lib.dlpSetAvgTimes.restype = c_int
        lib.dlpSetAvgTimes.argtypes = [c_uint16]  # type: ignore

        # dlpEnableButtonPress(isEnable) -> int
        lib.dlpEnableButtonPress.restype = c_int
        lib.dlpEnableButtonPress.argtypes = [c_bool]

        # dlpGetButtonPressStatus(pStatus*) -> int
        lib.dlpGetButtonPressStatus.restype = c_int
        lib.dlpGetButtonPressStatus.argtypes = [POINTER(c_bool)]

        # dlpGetDeviceUuid(pUid*) -> int
        lib.dlpGetDeviceUuid.restype = c_int
        lib.dlpGetDeviceUuid.argtypes = [POINTER(c_uint8)]

        # dlpGetHumTemp(pHumidity*, pTemperature*) -> int
        lib.dlpGetHumTemp.restype = c_int
        lib.dlpGetHumTemp.argtypes = [POINTER(c_uint32), POINTER(c_int)]

        # dlpGetBatteryInfo(pVolt*, pPercent*) -> int
        lib.dlpGetBatteryInfo.restype = c_int
        lib.dlpGetBatteryInfo.argtypes = [POINTER(c_uint32), POINTER(c_uint32)]

    @property
    def connected(self):
        return self._connected

    @property
    def mock_mode(self):
        return self._mock_mode

    def _require_lib(self):
        """返回可用的 C 库对象，不可用时返回 None"""
        return self.lib if not self._mock_mode and self.lib is not None else None

    # ---------- 设备连接 ----------

    def init_usb(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS
        return lib.dlpInitUsb()

    def open_usb(self, device_index=0):
        lib = self._require_lib()
        if lib is None:
            self._connected = True
            return RetError.SUCCESS
        ret = lib.dlpOpenByUsb(device_index)
        if ret == RetError.SUCCESS:
            self._connected = True
        return ret

    def close(self, device_index=0):
        lib = self._require_lib()
        if lib is None:
            self._connected = False
            return
        lib.dlpClose(device_index)
        self._connected = False

    # ---------- 配置信息 ----------

    def get_config_info(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, {
                "serial_number": "MOCK-001",
                "wavelength_start": 900,
                "wavelength_end": 1700,
            }

        config = PynScanConfig()
        ret = lib.dlpGetConfigInfo(byref(config))
        if ret < 0:
            return ret, None

        serial = bytes(config.head.scanConfig_serial_number).decode("ascii", errors="ignore").rstrip("\x00")

        wl_start = None
        wl_end = None
        if config.head.num_sections > 0:
            wl_start = int(config.section[0].wavelength_start_nm)
            wl_end = int(config.section[0].wavelength_end_nm)

        return ret, {
            "serial_number": serial,
            "wavelength_start": wl_start,
            "wavelength_end": wl_end,
            "num_sections": int(config.head.num_sections),
            "num_repeats": int(config.head.num_repeats),
        }

    # ---------- 光谱数据 ----------

    def get_scan_results(self):
        lib = self._require_lib()
        if lib is None:
            import random
            wls = [900 + i * 3.5 for i in range(WLS_NUM)]
            ints = [random.randint(1000, 5000) for _ in range(WLS_NUM)]
            return RetError.SUCCESS, {
                "system_temp": 25.5,
                "humidity": 45.0,
                "wavelengths": wls,
                "intensities": ints,
                "length": WLS_NUM,
            }

        results = PynScanResults()
        ret = lib.dlpGetScanResults(byref(results))
        if ret < 0:
            return ret, None

        if results.length <= 0:
            return RetError.INVALID_SCAN_LENGTH, None

        adc_len = int(results.adc_data_length) if results.adc_data_length > 0 else ADC_DATA_LEN
        length = min(int(results.length), adc_len, ADC_DATA_LEN)

        if length <= 0:
            return RetError.INVALID_ADC_LENGTH, None

        wavelengths = [results.wavelength[i] for i in range(length)]
        intensities = [results.intensity[i] for i in range(length)]

        return ret, {
            "system_temp": results.system_temp_hundredths / 100.0,
            "detector_temp": results.detector_temp_hundredths / 100.0,
            "humidity": results.humidity_hundredths / 100.0,
            "lamp_pd": int(results.lamp_pd),
            "pga": int(results.pga),
            "wavelengths": wavelengths,
            "intensities": intensities,
            "length": length,
        }

    def get_wavelengths(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, [900 + i * 3.5 for i in range(WLS_NUM)]

        wls = (c_double * WLS_NUM)()
        ret = lib.dlpGetWavelengths(wls, WLS_NUM)
        if ret < 0:
            return ret, None
        return ret, list(wls)

    def get_intensities(self, active_index=0):
        lib = self._require_lib()
        if lib is None:
            import random
            return RetError.SUCCESS, [random.randint(1000, 5000) for _ in range(WLS_NUM)]

        intensities = (c_int * WLS_NUM)()
        ret = lib.dlpGetIntensities(active_index, intensities, WLS_NUM)
        if ret < 0:
            return ret, None
        return ret, list(intensities)

    def get_ref_intensity(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, [4000 + i * 5 for i in range(WLS_NUM)]

        ref = (c_int * WLS_NUM)()
        ret = lib.dlpGetRefIntensityFromDevice(ref, WLS_NUM)
        if ret < 0:
            return ret, None
        return ret, list(ref)

    # ---------- 设备控制 ----------

    def set_lamp(self, on: bool):
        if not isinstance(on, bool):
            return RetError.INVALID_PARAMETER
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS
        return lib.dlpSetLampStatus(on)

    def set_bluetooth(self, on: bool):
        if not isinstance(on, bool):
            return RetError.INVALID_PARAMETER
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS
        return lib.dlpSetBluetoothStatus(on)

    def set_pga_gain(self, gain: int):
        if not isinstance(gain, int):
            return RetError.INVALID_PARAMETER
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS
        return lib.dlpSetPgaGain(gain)

    def get_pga_gain(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, 1

        gain = c_int()
        ret = lib.dlpGetPgaGain(byref(gain))
        if ret < 0:
            return ret, None
        return ret, gain.value

    def set_avg_times(self, times: int):
        if not isinstance(times, int) or times <= 0:
            return RetError.INVALID_PARAMETER
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS
        return lib.dlpSetAvgTimes(c_uint16(times))

    # ---------- 设备信息 ----------

    def get_uuid(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, "AA:BB:CC:DD:EE:FF:11:22"

        uid = (c_uint8 * 8)()
        ret = lib.dlpGetDeviceUuid(uid)
        if ret < 0:
            return ret, None
        uuid_str = ":".join(f"{b:02X}" for b in uid)
        return ret, uuid_str

    def get_humidity_temperature(self):
        lib = self._require_lib()
        if lib is None:
            import random
            return RetError.SUCCESS, {
                "humidity": 45.0 + random.uniform(-5, 5),
                "temperature": 25.0 + random.uniform(-2, 2),
            }

        humidity = c_uint32()
        temperature = c_int()
        ret = lib.dlpGetHumTemp(byref(humidity), byref(temperature))
        if ret < 0:
            return ret, None
        return ret, {
            "humidity": humidity.value / 100.0,
            "temperature": temperature.value / 100.0,
        }

    def get_battery_info(self):
        lib = self._require_lib()
        if lib is None:
            return RetError.SUCCESS, {
                "voltage": 3.7,
                "percent": 85.0,
            }

        volt = c_uint32()
        percent = c_uint32()
        ret = lib.dlpGetBatteryInfo(byref(volt), byref(percent))
        if ret < 0:
            return ret, None
        return ret, {
            "voltage": volt.value / 100.0,
            "percent": percent.value / 100.0,
        }