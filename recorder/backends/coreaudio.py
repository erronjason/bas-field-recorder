from ..audio import AudioBackend


class CoreAudioBackend(AudioBackend):
    """macOS CoreAudio loopback backend — Phase 4."""

    def get_default_mic(self) -> dict:
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def get_default_loopback(self) -> dict:
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def list_input_devices(self) -> list:
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def list_loopback_devices(self) -> list:
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def open_mic_stream(self, device_info: dict, callback):
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def open_loopback_stream(self, device_info: dict, callback):
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def close_streams(self, *streams) -> None:
        raise NotImplementedError("CoreAudio backend not yet implemented (Phase 4)")

    def terminate(self) -> None:
        pass
