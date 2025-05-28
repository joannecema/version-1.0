import quickfix as fix
import threading
import queue

class PhemexMDApp(fix.Application):
    def __init__(self, md_queue):
        super().__init__()
        self.md_queue = md_queue

    def onCreate(self, sessionID): pass
    def onLogon(self, sessionID): print("FIX MD Logon:", sessionID)
    def onLogout(self, sessionID): print("FIX MD Logout:", sessionID)
    def toAdmin(self, msg, sessionID): pass
    def fromAdmin(self, msg, sessionID): pass
    def toApp(self, msg, sessionID): pass

    def fromApp(self, msg, sessionID):
        if msg.getHeader().getField(fix.MsgType()) == fix.MsgType_MarketDataSnapshotFullRefresh:
            symbol = msg.getField(55)
            bid_px = float(msg.getField(132))
            ask_px = float(msg.getField(133))
            self.md_queue.put((symbol, bid_px, ask_px))

def start_fix_md_session(config_path, md_queue):
    settings = fix.SessionSettings(config_path)
    storeFactory = fix.FileStoreFactory(settings)
    logFactory   = fix.FileLogFactory(settings)
    app = PhemexMDApp(md_queue)
    initiator = fix.SocketInitiator(app, storeFactory, settings, logFactory)
    initiator.start()
    return initiator
