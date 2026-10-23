# The project installs the "webrtcvad-wheels" distribution (prebuilt for Windows); the stock hook
# asks for the metadata of "webrtcvad" and fails. The module needs no data files of its own.
from PyInstaller.utils.hooks import copy_metadata

try:
    datas = copy_metadata("webrtcvad-wheels")
except Exception:
    datas = []
