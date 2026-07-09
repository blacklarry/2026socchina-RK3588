# 基于RK3588的便携式高光谱农产品无损鉴别系统

Flask Web 应用，用于通过 USB 控制 DLP-NIR 近红外光谱仪，支持光谱采集、白/黑参考、自动采集保存、历史文件管理、基于 SCF-mtr-c 的玉米/豌豆品种预测，以及微信小程序 BLE GATT 外设服务。

## 文件结构

```text
2026socchina-RK3588/
├── web_app.py                    # Flask Web 后端 REST API
├── dlp_device.py                 # ctypes 封装，调用 wrapper.so / wrapper.dll
├── crop_scf_mtr_c_predictor.py   # 玉米/豌豆 SCF-mtr-c 品种预测
├── ble_gatt_server.py            # 微信小程序 BLE GATT 外设服务
├── templates/index.html          # 单文件 Web 前端
├── submission_materials/         # 比赛技术文档和演示视频
├── requirements.txt
└── wrapper.*
```

## 环境要求

硬件环境：

- DLP-NIR 近红外光谱仪，通过 USB 接入运行主机。
- Linux 主机、Jetson 或 RK3588 类嵌入式设备均可运行。
- 如需微信小程序 BLE 通信，运行主机需要可用的 BlueZ 蓝牙适配器。
- 本地磁盘、TF 卡或外接存储用于保存 CSV/JSON 光谱数据。

软件环境：

- Linux 系统下使用 `wrapper.so`，Windows 环境可使用 `wrapper.dll`。
- Python 3 环境；项目现场建议使用 `/media/elf/tfcard/miniforge3/envs/larry-hsi/bin/python`。
- Flask 提供 Web 服务，NumPy/SciPy 负责光谱预处理，joblib 加载并运行 SCF-mtr-c 模型。
- 品种预测使用 CPU 上的 SCF-mtr-c 模型，不依赖 NPU。

## 安装

```bash
cd /media/elf/tfcard/GUI/1.web/2026socchina-RK3588
pip install -r requirements.txt
```

Jetson / RK3588 设备如果 USB 打开失败，先确认设备 VID/PID：

```bash
lsusb
```

必要时创建 udev 规则，替换实际 VID：

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0451", MODE="0666"' | sudo tee /etc/udev/rules.d/99-dlp-nir.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## 运行

```bash
cd /media/elf/tfcard/GUI/1.web/2026socchina-RK3588
/media/elf/tfcard/miniforge3/envs/larry-hsi/bin/python web_app.py
```

浏览器访问：

```text
http://<设备 IP>:5000
```

例如：

```text
http://192.168.1.100:5000
```

## 使用流程

1. 点击“连接设备”。
2. 连接成功后会弹出“参考准备”窗口。
3. 先采集白参考。白参考是必需项，来自设备内置参考强度接口 `dlpGetRefIntensityFromDevice()`。
4. 可选采集黑参考。黑参考来自当前强度采集，启用后后续光谱执行软件扣黑：`sample - dark`。
5. 点击“采集光谱”进行单次采集，或点击“自动刷新”进入自动采集并保存。
6. 在光谱曲线下方选择作物类型，点击“采集并预测”，系统会先采集一条光谱，再调用 SCF-mtr-c 模型返回作物、品种和置信度。

## 保存规则

- 默认保存格式是 CSV。
- 默认目录是项目内 `spectra_data/`。
- 保存时如果文件名以数字结尾，后端会扫描当前保存目录并自动递增，例如 `sample_001` 到 `sample_002`。
- 自动采集启动时会弹出一次“自动采集设置”窗口，同时填写保存路径和首个文件名。
- 自动采集保存路径留空时使用后端默认目录；首个文件名留空时使用后端默认文件名。
- 每次保存成功后，前端会把返回的 `save_dir` 写回路径输入框和 `localStorage`，后续自动采集继续使用。

CSV 列格式：

```csv
# name,<name>
# timestamp,<iso timestamp>
Wavelength(nm),Intensity,WhiteReference,DarkReference
```

JSON 字段：

```json
{
  "meta": {},
  "wavelengths": [],
  "intensities": [],
  "white_reference": [],
  "dark_reference": []
}
```

历史加载只把 `wavelengths` 和 `intensities` 加载到图表，保存文件中的黑白参考不会作为历史曲线加载。

## 功能概览

| 功能 | 说明 |
|------|------|
| 连接/断开 | USB 初始化、打开设备、读取配置 |
| 白参考 | 读取设备内置参考强度，采集前强制完成 |
| 黑参考 | 采集当前强度作为软件黑参考，可启用/关闭 |
| 单次采集 | 获取强度并绘制光谱，复用缓存波长以减少硬件往返 |
| 自动采集保存 | 串行采集，采完保存，再等待 1 秒进入下一轮 |
| 历史数据 | 保存、列出、加载、删除 JSON/CSV 光谱文件 |
| 品种预测 | 支持自动识别、玉米、豌豆，使用 SCF-mtr-c 模型输出作物、品种和置信度 |
| 设备控制 | 灯、蓝牙、PGA 增益、平均次数 |
| 设备信息 | UUID、温湿度、电池 |
| BLE GATT | 从 Web 端启动/停止微信小程序 BLE 外设服务 |

## 品种预测

Web 端通过 `/api/crop/predict` 调用 `crop_scf_mtr_c_predictor.py`。请求体包含当前光谱强度数组和目标作物：

```json
{
  "target": "自动识别",
  "intensities": [1000.0, 1001.5]
}
```

`target` 可取 `自动识别`、`玉米`、`豌豆`。后端加载 SCF-mtr-c 模型包，按训练流程进行波段裁剪、Savitzky-Golay 平滑、一阶差分和 SNV 标准化，然后返回预测结果。

## 提交材料

比赛提交材料位于 `submission_materials/` 目录：

- `基于RK3588的便携式高光谱农产品无损鉴别系统.pdf`
- `视频.mp4`
