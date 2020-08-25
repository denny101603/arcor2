import asyncio
from typing import AsyncIterator, Dict, List, Optional, Set

from arcor2 import helpers as hlp
from arcor2.cached import CachedScene, UpdateableCachedScene
from arcor2.clients import aio_scene_service as scene_srv
from arcor2.data.common import Parameter, Pose, SceneObject
from arcor2.data.object_type import Models
from arcor2.exceptions import Arcor2Exception
from arcor2.object_types.abstract import Generic, GenericWithPose, Robot
from arcor2.object_types.utils import settings_from_params
from arcor2_arserver import globals as glob
from arcor2_arserver.clients import persistent_storage as storage


def instances_names() -> Set[str]:
    return {obj.name for obj in glob.SCENE_OBJECT_INSTANCES.values()}


async def scenes() -> AsyncIterator[CachedScene]:

    for scene_id in (await storage.get_scenes()).items:
        yield CachedScene(await storage.get_scene(scene_id.id))


async def scene_names() -> Set[str]:
    return {scene.name for scene in (await storage.get_scenes()).items}


async def set_object_pose(obj: GenericWithPose, pose: Pose) -> None:
    """
    Object pose is property that might call scene service - that's why it should be called using executor.
    :param obj:
    :param pose:
    :return:
    """

    await hlp.run_in_executor(setattr, obj, "pose", pose)


async def add_object_to_scene(
    obj: SceneObject,
    add_to_scene: bool = True,
    dry_run: bool = False,
    parent_id: Optional[str] = None,
    overrides: Optional[List[Parameter]] = None,
) -> None:
    """

    :param obj:
    :param add_to_scene: Set to false to only create object instance and add its collision model (if any).
    :return:
    """

    assert glob.SCENE
    assert not obj.children

    if obj.type not in glob.OBJECT_TYPES:
        raise Arcor2Exception("Unknown object type.")

    obj_type = glob.OBJECT_TYPES[obj.type]

    if obj_type.meta.disabled:
        raise Arcor2Exception("Object type disabled.")

    if {s.name for s in obj_type.meta.settings if s.default_value is None} > {s.name for s in obj.settings}:
        raise Arcor2Exception("Some required parameter is missing.")

    # TODO check whether object needs parent and if so, if the parent is in scene and parent_id is set
    if obj_type.meta.needs_parent_type:
        pass

    if obj_type.meta.has_pose and obj.pose is None:
        raise Arcor2Exception("Object requires pose.")

    if not obj_type.meta.has_pose and obj.pose is not None:
        raise Arcor2Exception("Object do not have pose.")

    if obj_type.meta.abstract:
        raise Arcor2Exception("Cannot instantiate abstract type.")

    if obj.id in glob.SCENE_OBJECT_INSTANCES:
        raise Arcor2Exception("Object/service with that id already exists.")

    if obj.name in instances_names():
        raise Arcor2Exception("Name is already used.")

    if not hlp.is_valid_identifier(obj.name):
        raise Arcor2Exception("Object name invalid (should be snake_case).")

    if dry_run:
        return None

    glob.logger.debug(f"Creating instance {obj.id} ({obj.type}).")

    # settings -> dataclass
    assert obj_type.type_def
    settings = settings_from_params(obj_type.type_def, obj.settings, overrides)

    assert obj_type.type_def is not None

    try:

        if issubclass(obj_type.type_def, Robot):
            assert obj.pose is not None
            # TODO RPC should return here (instantiation could take long time) -> events
            glob.SCENE_OBJECT_INSTANCES[obj.id] = await hlp.run_in_executor(
                obj_type.type_def, obj.id, obj.name, obj.pose, settings
            )
        elif issubclass(obj_type.type_def, GenericWithPose):
            assert obj.pose is not None
            coll_model: Optional[Models] = None
            if obj_type.meta.object_model:
                coll_model = obj_type.meta.object_model.model()

            # TODO RPC should return here (instantiation could take long time) -> events
            glob.SCENE_OBJECT_INSTANCES[obj.id] = await hlp.run_in_executor(
                obj_type.type_def, obj.id, obj.name, obj.pose, coll_model, settings
            )

        elif issubclass(obj_type.type_def, Generic):
            assert obj.pose is None
            # TODO RPC should return here (instantiation could take long time) -> events
            glob.SCENE_OBJECT_INSTANCES[obj.id] = await hlp.run_in_executor(
                obj_type.type_def, obj.id, obj.name, settings
            )

        else:
            raise Arcor2Exception("Object type with unknown base.")

    except (TypeError, ValueError) as e:  # catch some most often exceptions
        raise Arcor2Exception("Failed to create object instance.") from e

    if add_to_scene:
        glob.SCENE.upsert_object(obj)

    return None


async def clear_scene(do_cleanup: bool = True) -> None:

    glob.logger.info("Clearing the scene.")
    if do_cleanup:
        try:
            await asyncio.gather(*[hlp.run_in_executor(obj.cleanup) for obj in glob.SCENE_OBJECT_INSTANCES.values()])
        except Arcor2Exception:
            glob.logger.exception("Exception occurred while cleaning up objects.")
    glob.SCENE_OBJECT_INSTANCES.clear()
    glob.SCENE = None


async def open_scene(scene_id: str, object_overrides: Optional[Dict[str, List[Parameter]]] = None) -> None:

    await scene_srv.delete_all_collisions()  # just for sure (has to be awaited so things happen in order)
    glob.SCENE = UpdateableCachedScene(await storage.get_scene(scene_id))

    if object_overrides is None:
        object_overrides = {}

    tasks = [
        asyncio.ensure_future(
            add_object_to_scene(
                obj, add_to_scene=False, overrides=object_overrides[obj.id] if obj.id in object_overrides else None
            )
        )
        for obj in glob.SCENE.objects
    ]

    try:
        await asyncio.gather(*tasks)
    except Arcor2Exception as e:
        for t in tasks:
            t.cancel()
        await clear_scene()
        raise Arcor2Exception(f"Failed to open scene. {e.message}") from e

    assert {obj.id for obj in glob.SCENE.objects} == glob.SCENE_OBJECT_INSTANCES.keys()


def get_instance(obj_id: str) -> Generic:

    if obj_id not in glob.SCENE_OBJECT_INSTANCES:
        raise Arcor2Exception("Unknown object/service ID.")

    return glob.SCENE_OBJECT_INSTANCES[obj_id]