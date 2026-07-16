from ..audio import AudioBackend


class PulseAudioBackend(AudioBackend):
    """Linux PulseAudio/PipeWire loopback backend — Phase 4."""

    def get_default_mic(self) -> dict:
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def get_default_loopback(self) -> dict:
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def list_input_devices(self) -> list:
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def list_loopback_devices(self) -> list:
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def open_mic_stream(self, device_info: dict, callback):
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def open_loopback_stream(self, device_info: dict, callback):
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def close_streams(self, *streams) -> None:
        raise NotImplementedError("PulseAudio backend not yet implemented (Phase 4)")

    def terminate(self) -> None:
        pass
