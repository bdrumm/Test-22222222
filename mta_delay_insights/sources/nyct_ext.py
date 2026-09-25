"""NYCT GTFS-Realtime extension (train id, assignment, tracks) without a compiled proto.

The MTA subway feeds extend the standard messages with ``nyct-subway.proto``:

    extend TripDescriptor            { optional NyctTripDescriptor nyct_trip_descriptor = 1001; }
    extend TripUpdate.StopTimeUpdate { optional NyctStopTimeUpdate  nyct_stop_time_update = 1001; }
    message NyctTripDescriptor { optional string train_id = 1; optional bool is_assigned = 2; optional Direction direction = 3; }
    message NyctStopTimeUpdate { optional string scheduled_track = 1; optional string actual_track = 2; }

Rather than shipping generated code, the descriptors are built at import time and
registered in protobuf's default pool, so ``FeedMessage.ParseFromString`` fills the
extensions and they are readable through ``Extensions[...]``. The train id
(e.g. ``1A 0630+ PEL/BBR``) identifies the physical train run across trip
re-assignments; ``actual_track`` differing from ``scheduled_track`` is a live
reroute (express/local track swap) that the schedule does not know about.
"""
from __future__ import annotations

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.transit import gtfs_realtime_pb2 as rt

PACKAGE = "transit_realtime"
FILE_NAME = "nyct-subway-dynamic.proto"

_pool = descriptor_pool.Default()


def _build() -> None:
    try:
        _pool.FindFileByName(FILE_NAME)
        return
    except KeyError:
        pass
    fdp = descriptor_pb2.FileDescriptorProto()
    fdp.name = FILE_NAME
    fdp.package = PACKAGE
    fdp.syntax = "proto2"
    fdp.dependency.append(rt.DESCRIPTOR.name)

    trip = fdp.message_type.add()
    trip.name = "NyctTripDescriptor"
    f = trip.field.add(); f.name = "train_id"; f.number = 1; f.type = f.TYPE_STRING; f.label = f.LABEL_OPTIONAL
    f = trip.field.add(); f.name = "is_assigned"; f.number = 2; f.type = f.TYPE_BOOL; f.label = f.LABEL_OPTIONAL
    en = trip.enum_type.add(); en.name = "Direction"
    for name, num in (("NORTH", 1), ("EAST", 2), ("SOUTH", 3), ("WEST", 4)):
        v = en.value.add(); v.name = name; v.number = num
    f = trip.field.add(); f.name = "direction"; f.number = 3; f.type = f.TYPE_ENUM; f.label = f.LABEL_OPTIONAL
    f.type_name = f".{PACKAGE}.NyctTripDescriptor.Direction"

    stu = fdp.message_type.add()
    stu.name = "NyctStopTimeUpdate"
    f = stu.field.add(); f.name = "scheduled_track"; f.number = 1; f.type = f.TYPE_STRING; f.label = f.LABEL_OPTIONAL
    f = stu.field.add(); f.name = "actual_track"; f.number = 2; f.type = f.TYPE_STRING; f.label = f.LABEL_OPTIONAL

    e = fdp.extension.add()
    e.name = "nyct_trip_descriptor"; e.number = 1001; e.type = e.TYPE_MESSAGE; e.label = e.LABEL_OPTIONAL
    e.type_name = f".{PACKAGE}.NyctTripDescriptor"; e.extendee = f".{PACKAGE}.TripDescriptor"
    e = fdp.extension.add()
    e.name = "nyct_stop_time_update"; e.number = 1001; e.type = e.TYPE_MESSAGE; e.label = e.LABEL_OPTIONAL
    e.type_name = f".{PACKAGE}.NyctStopTimeUpdate"; e.extendee = f".{PACKAGE}.TripUpdate.StopTimeUpdate"
    _pool.Add(fdp) if hasattr(_pool, "Add") else _pool.AddSerializedFile(fdp.SerializeToString())


_build()
TRIP_EXT = _pool.FindExtensionByName(f"{PACKAGE}.nyct_trip_descriptor")
STU_EXT = _pool.FindExtensionByName(f"{PACKAGE}.nyct_stop_time_update")
NyctTripDescriptor = message_factory.GetMessageClass(_pool.FindMessageTypeByName(f"{PACKAGE}.NyctTripDescriptor"))
NyctStopTimeUpdate = message_factory.GetMessageClass(_pool.FindMessageTypeByName(f"{PACKAGE}.NyctStopTimeUpdate"))
DIRECTION_NAMES = {1: "N", 2: "E", 3: "S", 4: "W"}


def trip_fields(trip: rt.TripDescriptor) -> dict:
    """{'train_id', 'is_assigned', 'nyct_direction'} or empty values when the extension is absent."""
    out = {"train_id": None, "is_assigned": None, "nyct_direction": None}
    try:
        if trip.HasExtension(TRIP_EXT):
            x = trip.Extensions[TRIP_EXT]
            out["train_id"] = x.train_id or None
            out["is_assigned"] = bool(x.is_assigned) if x.HasField("is_assigned") else None
            out["nyct_direction"] = DIRECTION_NAMES.get(x.direction) if x.HasField("direction") else None
    except Exception:
        pass
    return out


def stop_fields(stu) -> dict:
    out = {"sched_track": None, "actual_track": None}
    try:
        if stu.HasExtension(STU_EXT):
            x = stu.Extensions[STU_EXT]
            out["sched_track"] = x.scheduled_track or None
            out["actual_track"] = x.actual_track or None
    except Exception:
        pass
    return out


def set_trip_fields(trip: rt.TripDescriptor, train_id: str | None = None, is_assigned: bool | None = None, direction: str | None = None) -> None:
    x = trip.Extensions[TRIP_EXT]
    if train_id is not None:
        x.train_id = train_id
    if is_assigned is not None:
        x.is_assigned = is_assigned
    if direction:
        x.direction = {v: k for k, v in DIRECTION_NAMES.items()}[direction]


def set_stop_fields(stu, scheduled_track: str | None = None, actual_track: str | None = None) -> None:
    x = stu.Extensions[STU_EXT]
    if scheduled_track is not None:
        x.scheduled_track = scheduled_track
    if actual_track is not None:
        x.actual_track = actual_track
