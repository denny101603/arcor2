from websockets.server import WebSocketServerProtocol as WsClient

from arcor2_arserver import globals as glob
from arcor2_arserver.lock.exceptions import CannotLock
from arcor2_arserver_data import rpc as srpc


async def write_lock_cb(req: srpc.lock.WriteLock.Request, ui: WsClient) -> None:

    if not await glob.LOCK.write_lock(req.args.object_id, glob.USERS.user_name(ui), req.args.lock_tree, notify=True):
        raise CannotLock("Cannot lock")


async def write_unlock_cb(req: srpc.lock.WriteUnlock.Request, ui: WsClient) -> None:

    await glob.LOCK.write_unlock(req.args.object_id, glob.USERS.user_name(ui), notify=True)


async def read_lock_cb(req: srpc.lock.ReadLock.Request, ui: WsClient) -> None:

    # TODO currently unused, maybe delete?
    if not await glob.LOCK.read_lock(req.args.object_id, glob.USERS.user_name(ui)):
        raise CannotLock("Cannot lock")


async def read_unlock_cb(req: srpc.lock.ReadUnlock.Request, ui: WsClient) -> None:

    # TODO currently unused, maybe delete?
    await glob.LOCK.read_unlock(req.args.object_id, glob.USERS.user_name(ui))