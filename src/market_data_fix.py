import quickfix as fix
import threading
import queue
import logging

class PhemexMDApp(fix.Application):
    def __init__(self, md_queue):
        super().__init__()
        self.md_queue = md_queue

    def onCreate(self, sessionID): 
        logging.info(f"[FIX] Session created: {sessionID}")

    def onLogon(self, sessionID): 
        logging.info(f"[FIX] Logon successful: {sessionID}")

    def onLogout(self, sessionID): 
        logging.info(f"[FIX] Logged out: {sessionID}")

    def toAdmin(self, msg, sessionID): 
        pass

    def fromAdmin(self, msg, sessionID): 
        pass

    def toApp(self, msg, sessionID): 
        pass

    def fromApp(self, msg, sessionID):
        try:
            if msg.getHeader().getField(fix.MsgType()) == fix.MsgType_MarketDataSnapshotFullRefresh:
                symbol = msg.getField(55).replace(":", "/")  # Normalize symbol format
                bid_px = float(msg.getField(132))  # Bid Price
                ask_px = float(msg.getField(133))  # Ask Price

                self.md_queue.put_nowait((symbol, bid_px, ask_px))
                logging.debug(f"[FIX] Received {symbol} B:{bid_px} A:{ask_px}")

        except Exception as e:
            logging.error(f"[FIX] Error processing FIX market data: {e}")

def start_fix_md_session(config_path, md_queue):
    try:
        settings = fix.SessionSettings(config_path)
        storeFactory = fix.FileStoreFactory(settings)
        logFactory   = fix.FileLogFactory(settings)
        app = PhemexMDApp(md_queue)
        initiator = fix.SocketInitiator(app, storeFactory, settings, logFactory)
        initiator.start()
        logging.info("[FIX] FIX Market Data session started")
        return initiator
    except Exception as e:
        logging.error(f"[FIX] Failed to start FIX session: {e}")
        return None