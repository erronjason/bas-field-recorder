from typing import Optional

import pyaudiowpatch as pyaudio

from ..audio import AudioBackend


class WasapiBackend(AudioBackend):
    """Windows WASAPI audio backend using PyAudioWPatch."""

    def __init__(self):
        self._pa = pyaudio.PyAudio()

    def get_default_mic(self) -> dict:
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            return self._pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        except Exception:
            return self._pa.get_default_input_device_info()

    def get_default_loopback(self) -> Optional[dict]:
        try:
            return self._pa.get_default_wasapi_loopback()
        except Exception:
            return None

    def list_input_devices(self) -> list:
        devices = []
        try:
            wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            host_api_index = wasapi["index"]
            for i in range(self._pa.get_device_count()):
                info = self._pa.get_device_info_by_index(i)
                if (
                    info.get("hostApi") == host_api_index
                    and info.get("maxInputChannels", 0) > 0
                    and not info.get("isLoopbackDevice", False)
                ):
                    devices.append(info)
        except Exception:
            pass
        return devices

    def list_loopback_devices(self) -> list:
        try:
            return list(self._pa.get_loopback_device_info_generator())
        except Exception:
            return []

    def open_mic_stream(self, device_info: dict, callback):
        return self._pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=int(device_info["defaultSampleRate"]),
            input=True,
            input_device_index=int(device_info["index"]),
            frames_per_buffer=1024,
            stream_callback=callback,
        )

    def open_loopback_stream(self, device_info: dict, callback):
        channels = max(1, int(device_info.get("maxInputChannels", 2)))
        return self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=int(device_info["defaultSampleRate"]),
            input=True,
            input_device_index=int(device_info["index"]),
            frames_per_buffer=1024,
            stream_callback=callback,
        )

    def close_streams(self, *streams) -> None:
        for stream in streams:
            if stream is None:
                continue
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def terminate(self) -> None:
        try:
            self._pa.terminate()
        except Exception:
            pass
