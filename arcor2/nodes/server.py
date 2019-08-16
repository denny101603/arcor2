#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import asyncio
import json
import functools
import sys
from typing import Dict, Set, Union, Optional
from types import ModuleType

import websockets
from websockets.server import WebSocketServerProtocol
from aiologger import Logger  # type: ignore
from dataclasses_jsonschema import ValidationError

from arcor2.source.logic import program_src, get_logic_from_source
from arcor2.source.object_types import object_type_meta, get_object_actions
from arcor2.source.utils import derived_resources_class
from arcor2.source import SourceException
from arcor2.nodes.manager import RPC_DICT as MANAGER_RPC_DICT
from arcor2.helpers import response, rpc, server, aiologger_formatter
from arcor2.object_types_utils import built_in_types_names, DataError, obj_description_from_base, built_in_types_meta, \
    built_in_types_actions, add_ancestor_actions, get_built_in_type
from arcor2.data.common import Scene, Project, ObjectTypeMeta, ObjectActionsDict, \
    ObjectTypeMetaDict, ProjectSources
from arcor2.persistent_storage_client import AioPersistentStorageClient
from arcor2.object_types import Generic, Robot
from arcor2.project_utils import get_action_point
from arcor2.exceptions import ApNotFound


logger = Logger.with_default_handlers(name='server', formatter=aiologger_formatter())

SCENE: Union[Scene, None] = None
PROJECT: Union[Project, None] = None
SCENE_OBJECT_INSTANCES: Dict[str, Generic] = {}

INTERFACES: Set[WebSocketServerProtocol] = set()

JSON_SCHEMAS = {"scene": Scene.json_schema(),
                "project": Project.json_schema()}

MANAGER_RPC_REQUEST_QUEUE: asyncio.Queue = asyncio.Queue()
MANAGER_RPC_RESPONSE_QUEUE: asyncio.Queue = asyncio.Queue()
MANAGER_RPC_REQ_ID: int = 0

# TODO watch for changes (just clear on change)
OBJECT_TYPES: ObjectTypeMetaDict = {}
OBJECT_ACTIONS: ObjectActionsDict = {}

STORAGE_CLIENT = AioPersistentStorageClient()


async def handle_manager_incoming_messages(manager_client):

    try:

        async for message in manager_client:

            msg = json.loads(message)
            await logger.info(f"Message from manager: {msg}")

            if "event" in msg:
                await asyncio.wait([intf.send(json.dumps(msg)) for intf in INTERFACES])
            elif "response" in msg:
                await MANAGER_RPC_RESPONSE_QUEUE.put(msg)

    except websockets.exceptions.ConnectionClosed:
        await logger.error("Connection to manager closed.")
        # TODO try to open it again and refuse requests meanwhile


async def project_manager_client() -> None:

    while True:

        await logger.info("Attempting connection to manager...")

        # TODO if manager does not run initially, this won't connect even if the manager gets started afterwards
        async with websockets.connect("ws://localhost:6790") as manager_client:

            await logger.info("Connected to manager.")

            future = asyncio.ensure_future(handle_manager_incoming_messages(manager_client))

            while True:

                if future.done():
                    break

                try:
                    msg = await asyncio.wait_for(MANAGER_RPC_REQUEST_QUEUE.get(), 1.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    await manager_client.send(json.dumps(msg))
                except websockets.exceptions.ConnectionClosed:
                    await MANAGER_RPC_REQUEST_QUEUE.put(msg)
                    break


def scene_event() -> str:

    data: Dict = {}

    if SCENE is not None:
        data = SCENE.to_dict()

    return json.dumps({"event": "sceneChanged", "data": data})  # TODO use encoder?


def project_event() -> str:

    data: Dict = {}

    if PROJECT is not None:
        data = PROJECT.to_dict()

    return json.dumps({"event": "projectChanged", "data": data})  # TODO use encoder?


async def _notify(interface, msg_source):

    if (interface is None and INTERFACES) or (interface and len(INTERFACES) > 1):
        message = msg_source()
        await asyncio.wait([intf.send(message) for intf in INTERFACES if intf != interface])


async def notify_scene_change_to_others(interface: Optional[WebSocketServerProtocol] = None) -> None:

    await _notify(interface, scene_event)


async def notify_project_change_to_others(interface=None) -> None:

    await _notify(interface, project_event)


async def notify_scene(interface) -> None:
    message = scene_event()
    await asyncio.wait([interface.send(message)])


async def notify_project(interface) -> None:
    message = project_event()
    await asyncio.wait([interface.send(message)])


async def _get_object_types():  # TODO watch db for changes and call this + notify UI in case of something changed

    global OBJECT_TYPES

    object_types: Dict[str, ObjectTypeMeta] = built_in_types_meta()

    obj_ids = await STORAGE_CLIENT.get_object_type_ids()

    for obj_id in obj_ids.items:
        obj = await STORAGE_CLIENT.get_object_type(obj_id.id)
        object_types[obj.id] = object_type_meta(obj.source)

    # if description is missing, try to get it from ancestor(s), or forget the object type
    to_delete: Set[str] = set()

    for obj_type, obj in object_types.items():
        if not obj.description:
            try:
                obj.description = obj_description_from_base(object_types, obj)
            except DataError as e:
                await logger.error(f"Failed to get info from base for {obj_type}, error: '{e}'.")
                to_delete.add(obj_type)

    for obj_type in to_delete:
        del object_types[obj_type]

    OBJECT_TYPES = object_types

    await _get_object_actions()


@rpc(logger)
async def get_object_types_cb(req: str, ui, args: Dict) -> Dict:

    msg = response(req, data=list(OBJECT_TYPES.values()))
    return msg


@rpc(logger)
async def save_scene_cb(req, ui, args) -> Dict:

    if SCENE is None or not SCENE.id:
        return response(req, False, ["Scene not opened or invalid."])

    msg = response(req)
    await STORAGE_CLIENT.update_scene(SCENE)
    return msg


@rpc(logger)
async def save_project_cb(req, ui, args) -> Dict:

    if PROJECT is None or not PROJECT.id:
        return response(req, False, ["Project not opened or invalid."])

    if SCENE is None or not SCENE.id:
        return response(req, False, ["Scene not opened or invalid."])

    action_names = []

    try:
        for obj in PROJECT.objects:
            for aps in obj.action_points:
                for act in aps.actions:
                    action_names.append(act.id)
    except KeyError as e:
        await logger.error(f"Project data invalid: {e}")
        return response(req, False, ["Project data invalid!", str(e)])

    try:
        project_sources = ProjectSources(id=PROJECT.id,
                                         resources=derived_resources_class(PROJECT.id, action_names),
                                         script=program_src(PROJECT, SCENE, built_in_types_names()))
    except SourceException as e:
        await logger.error(e)
        return response(req, False, ["Failed to generate project sources."])

    # TODO do this in parallel
    await STORAGE_CLIENT.update_project(PROJECT)
    await STORAGE_CLIENT.update_project_sources(project_sources)

    return response(req)


@rpc(logger)
async def open_project_cb(req, ui, args) -> Dict:

    global PROJECT
    global SCENE

    PROJECT = await STORAGE_CLIENT.get_project(args["id"])
    SCENE = await STORAGE_CLIENT.get_scene(PROJECT.scene_id)

    project_sources = await STORAGE_CLIENT.get_project_sources(PROJECT.id)

    load_logic_failed = False

    try:
        get_logic_from_source(project_sources.script, PROJECT)
    except SourceException as e:
        load_logic_failed = True
        await logger.error(e)

    await notify_project_change_to_others()
    await notify_scene_change_to_others()

    if load_logic_failed:
        return response(req, True, ["Failed to load logic from source."])

    return response(req)


async def _get_object_actions():

    global OBJECT_ACTIONS

    object_actions: ObjectActionsDict = built_in_types_actions()

    for obj_type, obj in OBJECT_TYPES.items():

        if obj.built_in:  # built-in types are already there
            continue

        # db-stored (user-created) object types
        obj_db = await STORAGE_CLIENT.get_object_type(obj_type)
        try:
            object_actions[obj_type] = get_object_actions(obj_db.source)
        except SourceException as e:
            await logger.error(e)

    # add actions from ancestors
    for obj_type in OBJECT_TYPES.keys():
        add_ancestor_actions(obj_type, object_actions, OBJECT_TYPES)

    OBJECT_ACTIONS = object_actions


@rpc(logger)
async def get_object_actions_cb(req, ui, args) -> Union[Dict, None]:

    try:
        obj_type = args["type"]
    except KeyError:
        return None

    try:
        return response(req, data=OBJECT_ACTIONS[obj_type])
    except KeyError:
        return response(req, False, [f'Unknown object type: {obj_type}.'])


@rpc(logger)
async def manager_request(req, ui, args) -> Dict:

    global MANAGER_RPC_REQ_ID

    req_id = MANAGER_RPC_REQ_ID
    msg = {"request": req, "args": args, "req_id": req_id}
    MANAGER_RPC_REQ_ID += 1

    await MANAGER_RPC_REQUEST_QUEUE.put(msg)
    # TODO process request

    # TODO better way to get correct response based on req_id?
    while True:
        resp = await MANAGER_RPC_RESPONSE_QUEUE.get()
        if resp["req_id"] == req_id:
            del resp["req_id"]
            return resp
        else:
            await MANAGER_RPC_RESPONSE_QUEUE.put(resp)


@rpc(logger)
async def get_schema_cb(req, ui, args) -> Union[Dict, None]:

    try:
        which = args["type"]
    except KeyError:
        return None

    if which not in JSON_SCHEMAS:
        return response(ui, False, ["Unknown type."])

    return response(req, data=JSON_SCHEMAS[which])


@rpc(logger)
async def update_action_point_cb(req, ui, args) -> Union[Dict, None]:

    if not (SCENE and PROJECT and SCENE.id and PROJECT.id):
        return response(req, False, ["Scene/project has to be loaded first."])

    try:
        ap = get_action_point(PROJECT, args["id"])
    except ApNotFound:
        return response(req, False, ["Invalid action point."])

    try:
        robot = SCENE_OBJECT_INSTANCES[args["robot"]]
    except KeyError:
        return response(req, False, ["Unknown robot id."])

    if not isinstance(robot, Robot):
        return response(req, False, [f"Object {args['robot']} is not instance of Robot!"])

    # TODO transform pose from robot to the object
    ap.pose = await asyncio.get_event_loop().run_in_executor(None, robot.get_pose, args["end_effector"])

    await notify_project_change_to_others()
    return response(req)


@rpc(logger)
async def list_projects_cb(req, ui, args) -> Union[Dict, None]:

    projects = await STORAGE_CLIENT.get_projects()
    return response(req, data=projects.items)


@rpc(logger)
async def list_scenes_cb(req, ui, args) -> Union[Dict, None]:

    projects = await STORAGE_CLIENT.get_scenes()
    return response(req, data=projects.items)


async def register(websocket) -> None:
    await logger.info("Registering new ui")
    INTERFACES.add(websocket)

    # TODO do this in parallel
    await notify_scene(websocket)
    await notify_project(websocket)


async def unregister(websocket) -> None:
    await logger.info("Unregistering ui")  # TODO print out some identifier
    INTERFACES.remove(websocket)


async def scene_change(ui, scene) -> None:

    global SCENE

    try:
        SCENE = Scene.from_dict(scene)
    except ValidationError as e:
        await logger.error(e)
        return

    scene_obj_ids: Set[str] = set()

    for obj in SCENE.objects:

        scene_obj_ids.add(obj.id)

        if obj.id not in SCENE_OBJECT_INSTANCES:

            await logger.debug(f"Creating instance {obj.id} ({obj.type}).")

            if obj.type in built_in_types_names():  # TODO is it ok to create instances of built-in object types?
                cls = get_built_in_type(obj.type)
            else:
                obj_type = await STORAGE_CLIENT.get_object_type(obj.type)
                mod = ModuleType('temp_module')
                exec(obj_type.source, mod.__dict__)
                cls = getattr(mod, obj.type)

            SCENE_OBJECT_INSTANCES[obj.id] = cls()  # TODO name, pose, etc.

    # delete instances which were deleted from the scene
    for obj_id in set(SCENE_OBJECT_INSTANCES.keys()) - scene_obj_ids:
        await logger.debug(f"Deleting {obj_id} instance.")
        del SCENE_OBJECT_INSTANCES[obj_id]

    await notify_scene_change_to_others(ui)


async def project_change(ui, project) -> None:

    global PROJECT

    try:
        PROJECT = Project.from_dict(project)
    except ValidationError as e:
        await logger.error(e)
        return

    await notify_project_change_to_others(ui)


RPC_DICT: Dict = {'getObjectTypes': get_object_types_cb,
                  'getObjectActions': get_object_actions_cb,
                  'saveProject': save_project_cb,
                  'saveScene': save_scene_cb,
                  'getSchema': get_schema_cb,
                  'updateActionPointPose': update_action_point_cb,
                  'openProject': open_project_cb,
                  'listProjects': list_projects_cb,
                  'listScenes': list_scenes_cb}

# add Project Manager RPC API
for k, v in MANAGER_RPC_DICT.items():
    RPC_DICT[k] = manager_request

EVENT_DICT: Dict = {'sceneChanged': scene_change,
                    'projectChanged': project_change}


async def multiple_tasks():

    bound_handler = functools.partial(server, logger=logger, register=register, unregister=unregister,
                                      rpc_dict=RPC_DICT, event_dict=EVENT_DICT)
    input_coroutines = [websockets.serve(bound_handler, '0.0.0.0', 6789), project_manager_client(),
                        _get_object_types()]
    res = await asyncio.gather(*input_coroutines, return_exceptions=True)
    return res


def main():

    assert sys.version_info >= (3, 6)

    loop = asyncio.get_event_loop()
    loop.set_debug(enabled=True)
    loop.run_until_complete(multiple_tasks())
    loop.run_forever()


if __name__ == "__main__":
    main()
