"""
DLP-NIR BLE GATT peripheral for WeChat mini programs.

Run with system Python because BlueZ D-Bus bindings are installed there:
    python3 ble_gatt_server.py
"""

import argparse
import json
import os
import pprint
import random
import signal
import sys
import threading

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from dlp_device import DLPDevice, RetError


BLUEZ_SERVICE_NAME = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

APP_PATH = "/org/dlp/nir"
ADV_PATH = "/org/dlp/nir/advertisement0"

DLP_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
DLP_COMMAND_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
DLP_NOTIFY_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

FRAME_END = 0x55
DEFAULT_CHUNK_SIZE = 20
DEFAULT_NOTIFY_INTERVAL_MS = 3
MAX_NOTIFY_CHUNK_SIZE = 20


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


class Advertisement(dbus.service.Object):
    def __init__(self, bus, index, local_name):
        self.path = ADV_PATH
        self.bus = bus
        self.ad_type = "peripheral"
        self.service_uuids = [DLP_SERVICE_UUID]
        self.local_name = local_name
        self.includes = ["tx-power"]
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            LE_ADVERTISEMENT_IFACE: {
                "Type": dbus.String(self.ad_type),
                "ServiceUUIDs": dbus.Array(self.service_uuids, signature="s"),
                "LocalName": dbus.String(self.local_name),
                "Includes": dbus.Array(self.includes, signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        print("BLE advertisement released", flush=True)


class MockDLPDevice:
    """Small deterministic-enough mock used when hardware is unavailable."""

    connected = False
    mock_mode = True

    def __init__(self, points=228):
        self.points = points
        self.gain = 1
        self.avg_times = 6
        self.lamp_on = False

    def init_usb(self):
        return RetError.SUCCESS

    def open_usb(self, device_index=0):
        self.connected = True
        return RetError.SUCCESS

    def get_wavelengths(self):
        return RetError.SUCCESS, [900 + i * 3.5 for i in range(self.points)]

    def get_intensities(self, active_index=0):
        return RetError.SUCCESS, [1200 + i * 8 + random.randint(-30, 30) for i in range(self.points)]

    def get_ref_intensity(self):
        return RetError.SUCCESS, [3800 + i * 3 for i in range(self.points)]

    def set_pga_gain(self, gain):
        self.gain = gain
        return RetError.SUCCESS

    def set_avg_times(self, times):
        self.avg_times = times
        return RetError.SUCCESS

    def set_lamp(self, on):
        self.lamp_on = bool(on)
        return RetError.SUCCESS


class Application(dbus.service.Object):
    def __init__(self, bus, device, chunk_size, notify_interval_ms):
        self.path = APP_PATH
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)
        self.add_service(DlpService(bus, 0, device, chunk_size, notify_interval_ms))

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for chrc in service.get_characteristics():
                response[chrc.get_path()] = chrc.get_properties()
        return response


class Service(dbus.service.Object):
    PATH_BASE = APP_PATH + "/service"

    def __init__(self, bus, index, uuid, primary):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                "UUID": dbus.String(self.uuid),
                "Primary": dbus.Boolean(self.primary),
                "Includes": dbus.Array([], signature="o"),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    def get_characteristic_paths(self):
        return [chrc.get_path() for chrc in self.characteristics]

    def get_characteristics(self):
        return self.characteristics

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + "/char" + str(index)
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags
        self.value = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": dbus.String(self.uuid),
                "Flags": dbus.Array(self.flags, signature="s"),
                "Value": dbus.Array(self.value, signature="y"),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.Array(self.value, signature="y")

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}", out_signature="")
    def WriteValue(self, value, options):
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="", out_signature="")
    def StartNotify(self):
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="", out_signature="")
    def StopNotify(self):
        raise NotSupportedException()

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass


class DlpService(Service):
    def __init__(self, bus, index, device, chunk_size, notify_interval_ms):
        Service.__init__(self, bus, index, DLP_SERVICE_UUID, True)
        self.notify_characteristic = NotifyCharacteristic(bus, 1, self, chunk_size, notify_interval_ms)
        self.add_characteristic(CommandCharacteristic(bus, 0, self, device, self.notify_characteristic))
        self.add_characteristic(self.notify_characteristic)
        print(f"GATT service registered: {DLP_SERVICE_UUID}", flush=True)


class CommandCharacteristic(Characteristic):
    def __init__(self, bus, index, service, device, notify_characteristic):
        Characteristic.__init__(self, bus, index, DLP_COMMAND_UUID, ["write", "write-without-response"], service)
        self.device = device
        self.notify_characteristic = notify_characteristic
        self._busy = False
        print(
            f"GATT write characteristic registered: {DLP_COMMAND_UUID} "
            "flags=write,write-without-response",
            flush=True,
        )

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="aya{sv}", out_signature="")
    def WriteValue(self, value, options):
        data = bytes(int(v) for v in value)
        print(f"RX raw write: {data.hex(' ')}", flush=True)
        if self._busy:
            print("RX rejected: device busy", flush=True)
            GLib.idle_add(
                self.notify_characteristic.send_json,
                {"ok": False, "type": "error", "error": "device_busy"},
            )
            return

        self._busy = True
        worker = threading.Thread(target=self._handle_command_worker, args=(data,), daemon=True)
        worker.start()

    def _handle_command_worker(self, data):
        try:
            response = handle_command(self.device, data)
        except Exception as exc:
            response = {"ok": False, "type": "error", "error": str(exc)}
        GLib.idle_add(self._finish_command, response)

    def _finish_command(self, response):
        self._busy = False
        self.notify_characteristic.send_json(response)
        return False


class NotifyCharacteristic(Characteristic):
    def __init__(self, bus, index, service, chunk_size, notify_interval_ms):
        Characteristic.__init__(self, bus, index, DLP_NOTIFY_UUID, ["notify", "read"], service)
        self.notifying = False
        self.chunk_size = min(MAX_NOTIFY_CHUNK_SIZE, max(1, int(chunk_size or DEFAULT_CHUNK_SIZE)))
        self.notify_interval_ms = max(1, int(notify_interval_ms or DEFAULT_NOTIFY_INTERVAL_MS))
        self._pending_chunks = []
        self._sending = False
        self._pending_total_len = 0
        self._sent_chunks = 0
        self._total_chunks = 0
        print(
            f"GATT notify characteristic registered: {DLP_NOTIFY_UUID} "
            "flags=notify,read; BlueZ provides CCCD for notify",
            flush=True,
        )

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="", out_signature="")
    def StartNotify(self):
        self.notifying = True
        print("StartNotify: client subscribed", flush=True)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="", out_signature="")
    def StopNotify(self):
        self.notifying = False
        if self._pending_chunks:
            sent = self._pending_total_len - sum(len(chunk) for chunk in self._pending_chunks)
            print(f"Notifications disabled during TX: sent {sent}/{self._pending_total_len} bytes", flush=True)
        self._pending_chunks = []
        self._sending = False
        self._pending_total_len = 0
        self._sent_chunks = 0
        self._total_chunks = 0
        print("StopNotify: client unsubscribed", flush=True)

    def send_json(self, payload):
        frame = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + bytes([FRAME_END])
        if not self.notifying:
            print("Notify skipped; client has not enabled notifications", flush=True)
            return
        if self._sending:
            print("Notify busy; replacing pending response", flush=True)
            self._pending_chunks = []
        self._pending_chunks = [
            frame[start:start + self.chunk_size]
            for start in range(0, len(frame), self.chunk_size)
        ]
        self._sending = True
        self._pending_total_len = len(frame)
        self._sent_chunks = 0
        self._total_chunks = len(self._pending_chunks)
        print(
            f"TX start: {len(frame)} bytes, "
            f"{self._total_chunks} chunks, "
            f"chunk={self.chunk_size}, interval={self.notify_interval_ms}ms",
            flush=True,
        )
        GLib.idle_add(self._send_next_chunk, len(frame))

    def _send_next_chunk(self, total_len):
        if not self.notifying or not self._pending_chunks:
            if self._sending:
                print(f"TX: {total_len} bytes", flush=True)
            self._sending = False
            self._pending_total_len = 0
            self._sent_chunks = 0
            self._total_chunks = 0
            return False

        chunk = self._pending_chunks.pop(0)
        self.value = [dbus.Byte(b) for b in chunk]
        self.PropertiesChanged(
            GATT_CHRC_IFACE,
            {"Value": dbus.Array(self.value, signature="y")},
            [],
        )
        self._sent_chunks += 1
        print(
            f"TX chunk {self._sent_chunks}/{self._total_chunks}: {chunk.hex(' ')}",
            flush=True,
        )
        if self._sent_chunks == 1 or self._sent_chunks % 50 == 0 or not self._pending_chunks:
            sent_bytes = total_len - sum(len(part) for part in self._pending_chunks)
            print(
                f"TX progress: chunk {self._sent_chunks}, "
                f"sent {sent_bytes}/{total_len} bytes",
                flush=True,
            )
        GLib.timeout_add(self.notify_interval_ms, self._send_next_chunk, total_len)
        return False


def validate_frame(data):
    return len(data) >= 4 and data[0] == 0xAA and data[-1] == FRAME_END


def error_payload(error, **extra):
    payload = {"ok": False, "status": "error", "type": "error", "error": error}
    payload.update(extra)
    return payload


def success_payload(response_type, **extra):
    payload = {"ok": True, "status": "ok", "type": response_type}
    payload.update(extra)
    return payload


def ensure_device_connected(device):
    if device.connected:
        return RetError.SUCCESS
    ret = device.init_usb()
    if ret < 0:
        return ret
    return device.open_usb(0)


def handle_command(device, data):
    if not validate_frame(data):
        print("Command parse error: invalid_frame", flush=True)
        return error_payload("invalid_frame", raw=data.hex())

    command = data[1]
    if command in (0x01, 0x02) and len(data) != 4:
        print(f"Command parse error: invalid_length command=0x{command:02x}", flush=True)
        return error_payload("invalid_length", command=command, expected=4, actual=len(data))
    if command in (0x03, 0x04, 0x05) and len(data) != 5:
        print(f"Command parse error: invalid_length command=0x{command:02x}", flush=True)
        return error_payload("invalid_length", command=command, expected=5, actual=len(data))

    try:
        if command in (0x01, 0x02):
            command_name = "reference" if command == 0x02 else "collect"
            print(f"Command parsed: {command_name}", flush=True)
            ret = ensure_device_connected(device)
            if ret < 0:
                print(f"DLP open failed: {ret}", flush=True)
                return error_payload("device_open_failed", command=command_name, code=ret)

            ret, wavelengths = device.get_wavelengths()
            if ret < 0:
                print(f"DLP get_wavelengths failed: {ret}", flush=True)
                return error_payload("get_wavelengths_failed", command=command_name, code=ret)

            if command == 0x02:
                ret, intensities = device.get_ref_intensity()
                response_type = "reference"
            else:
                ret, intensities = device.get_intensities(0)
                response_type = "spectrum" if getattr(device, "mock_mode", False) else "collect"

            if ret < 0:
                print(f"DLP {response_type} failed: {ret}", flush=True)
                return error_payload("get_intensity_failed", command=command_name, type=response_type, code=ret)

            print(
                f"DLP {response_type} ok: wavelengths={len(wavelengths)}, "
                f"intensities={len(intensities)}",
                flush=True,
            )
            payload = success_payload(
                response_type,
                wavelengths=wavelengths,
                intensities=intensities,
                wavelength=wavelengths,
                intensity=intensities,
            )
            payload["command"] = command_name
            if response_type == "reference":
                payload["data"] = intensities
            return payload

        if command == 0x03:
            value = int(data[3])
            print(f"Command parsed: set_gain value={value}", flush=True)
            ret = ensure_device_connected(device)
            if ret >= 0:
                ret = device.set_pga_gain(value)
            print(f"DLP set_gain result: {ret}", flush=True)
            if ret < 0:
                return error_payload("set_gain_failed", type="set_gain", value=value, code=ret)
            return success_payload("set_gain", value=value, code=ret)

        if command == 0x04:
            value = int(data[3])
            print(f"Command parsed: set_average value={value}", flush=True)
            ret = ensure_device_connected(device)
            if ret >= 0:
                ret = device.set_avg_times(value)
            print(f"DLP set_average result: {ret}", flush=True)
            if ret < 0:
                return error_payload("set_average_failed", type="set_average", value=value, code=ret)
            return success_payload("set_average", value=value, code=ret)

        if command == 0x05:
            on = bool(data[3])
            print(f"Command parsed: set_light on={on}", flush=True)
            ret = ensure_device_connected(device)
            if ret >= 0:
                ret = device.set_lamp(on)
            print(f"DLP set_light result: {ret}", flush=True)
            if ret < 0:
                return error_payload("set_light_failed", type="set_light", on=on, code=ret)
            return success_payload("set_light", on=on, code=ret)

        print(f"Command parse error: unknown_command 0x{command:02x}", flush=True)
        return error_payload("unknown_command", command=command)
    except Exception as exc:
        print(f"Command handling exception: {exc}", flush=True)
        return error_payload(str(exc))


def find_adapter(bus):
    remote_om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objects = remote_om.GetManagedObjects()
    for path, interfaces in objects.items():
        if GATT_MANAGER_IFACE in interfaces and LE_ADVERTISING_MANAGER_IFACE in interfaces:
            print(f"Bluetooth adapter initialized: {path}", flush=True)
            return path
    return None


def device_props_changed(interface, changed, invalidated, path=None):
    if interface != "org.bluez.Device1":
        return
    if "Connected" in changed:
        state = "connected" if bool(changed["Connected"]) else "disconnected"
        print(f"Client {state}: {path}", flush=True)


def register_app_cb():
    print("GATT application registered", flush=True)


def register_app_error_cb(error):
    print("Failed to register GATT application:", error, flush=True)
    mainloop.quit()


def register_ad_cb():
    print("BLE advertisement registered/start", flush=True)


def register_ad_error_cb(error):
    print("Failed to register advertisement:", error, flush=True)
    mainloop.quit()


def log_managed_objects(app):
    print("GATT GetManagedObjects before RegisterApplication:", flush=True)
    pprint.pprint(app.GetManagedObjects(), width=160)
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="DLP-NIR")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--notify-interval-ms", type=int, default=DEFAULT_NOTIFY_INTERVAL_MS)
    parser.add_argument("--mock", action="store_true", help="Use generated spectra instead of the USB spectrometer")
    args = parser.parse_args()

    print("BLE GATT server booting", flush=True)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    bus.add_signal_receiver(
        device_props_changed,
        dbus_interface=DBUS_PROP_IFACE,
        signal_name="PropertiesChanged",
        arg0="org.bluez.Device1",
        path_keyword="path",
    )
    adapter = find_adapter(bus)
    if not adapter:
        print("No BLE adapter with GATT/advertising support found", file=sys.stderr, flush=True)
        return 1

    device = MockDLPDevice() if args.mock else DLPDevice()
    print(f"DLP backend: {'mock' if args.mock or getattr(device, 'mock_mode', False) else 'hardware'}", flush=True)
    app = Application(bus, device, args.chunk_size, args.notify_interval_ms)
    adv = Advertisement(bus, 0, args.name)
    log_managed_objects(app)

    service_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter), GATT_MANAGER_IFACE)
    ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter), LE_ADVERTISING_MANAGER_IFACE)

    service_manager.RegisterApplication(
        app.get_path(),
        {},
        reply_handler=register_app_cb,
        error_handler=register_app_error_cb,
    )
    ad_manager.RegisterAdvertisement(
        adv.path,
        {},
        reply_handler=register_ad_cb,
        error_handler=register_ad_error_cb,
    )

    print("BLE peripheral ready", flush=True)
    print(f"Name: {args.name}", flush=True)
    print(f"Service UUID: {DLP_SERVICE_UUID}", flush=True)
    print(f"Command UUID: {DLP_COMMAND_UUID}", flush=True)
    print(f"Notify UUID: {DLP_NOTIFY_UUID}", flush=True)
    print(f"Notify chunk size: {min(MAX_NOTIFY_CHUNK_SIZE, max(1, args.chunk_size))} bytes", flush=True)
    print(f"Notify interval: {args.notify_interval_ms} ms", flush=True)

    def stop(*_args):
        try:
            ad_manager.UnregisterAdvertisement(adv.path)
            print("BLE advertisement unregistered/stop", flush=True)
        except Exception:
            pass
        mainloop.quit()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    mainloop.run()
    return 0


mainloop = GLib.MainLoop()


if __name__ == "__main__":
    raise SystemExit(main())
