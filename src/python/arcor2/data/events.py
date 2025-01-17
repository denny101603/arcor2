from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from dataclasses_jsonschema import JsonSchemaMixin

from arcor2.data import common, object_type

"""
------------------------------------------------------------------------------------------------------------------------
Common stuff
------------------------------------------------------------------------------------------------------------------------
"""


@dataclass
class Event(JsonSchemaMixin):
    class Type(common.StrEnum):
        ADD: str = "add"
        UPDATE: str = "update"
        REMOVE: str = "remove"
        UPDATE_BASE: str = "update_base"

    event: str = field(init=False)
    change_type: Optional[Type] = field(init=False)
    parent_id: Optional[str] = field(init=False)

    def __post_init__(self) -> None:
        self.event = self.__class__.__name__
        self.change_type = None
        self.parent_id = None


@dataclass
class Notification(Event):
    @dataclass
    class Data(JsonSchemaMixin):
        class Level(common.StrEnum):
            INFO: str = "Info"
            WARN: str = "Warn"
            ERROR: str = "Error"

        message: str
        level: Level

    data: Optional[Data] = None


"""
------------------------------------------------------------------------------------------------------------------------
Project execution
------------------------------------------------------------------------------------------------------------------------
"""


@dataclass
class ProjectException(Event):
    @dataclass
    class Data(JsonSchemaMixin):
        message: str
        type: str
        handled: bool = False

    data: Data


# ----------------------------------------------------------------------------------------------------------------------


@dataclass
class PackageState(Event):
    @dataclass
    class Data(JsonSchemaMixin):
        class StateEnum(Enum):
            RUNNING: str = "running"
            STOPPING: str = "stopping"  # it may take some time to stop the package
            STOPPED: str = "stopped"
            PAUSING: str = "pausing"  # it may take some time to pause the package
            PAUSED: str = "paused"
            UNDEFINED: str = "undefined"

        state: StateEnum = StateEnum.UNDEFINED
        package_id: Optional[str] = None

    RUN_STATES = (Data.StateEnum.PAUSED, Data.StateEnum.RUNNING)

    data: Data


# ----------------------------------------------------------------------------------------------------------------------


@dataclass
class PackageInfo(Event):
    @dataclass
    class Data(JsonSchemaMixin):
        package_id: str
        package_name: str
        scene: common.Scene
        project: common.Project
        collision_models: object_type.CollisionModels = field(default_factory=object_type.CollisionModels)

    data: Data


# ----------------------------------------------------------------------------------------------------------------------


@dataclass
class ActionStateBefore(Event):
    @dataclass
    class Data(JsonSchemaMixin):

        action_id: str
        parameters: List[str]

    data: Data


# ----------------------------------------------------------------------------------------------------------------------


@dataclass
class ActionStateAfter(Event):
    @dataclass
    class Data(JsonSchemaMixin):

        action_id: str
        results: Optional[List[str]] = None

    data: Data
