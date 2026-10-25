from krita import Krita, Extension

from .config import log_debug
from .ui.main_dialog import SDForgeDialog

from . import localization

log_debug(localization.get_text("main.init.log_debug"))

class SDForgeExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.dlg = None

    def setup(self):
        pass

    def createActions(self, window):
        action = window.createAction("sdforge_action", "SDForge", "tools/scripts")
        action.triggered.connect(self.show_dialog)

    def show_dialog(self):
        if self.dlg is None:
            self.dlg = SDForgeDialog(Krita.instance().activeWindow().qwindow())
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
