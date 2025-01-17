"""
@ingroup Python
"""

__author__ = 'Giovanni Cerretani'
__copyright__ = 'Copyright (C) 2023 CAEN SpA'
__license__ = 'LGPL-3.0-or-later'
# SPDX-License-Identifier: LGPL-3.0-or-later

import ctypes as ct
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, unique
from functools import wraps
from json import dumps, loads
from typing import Any, ClassVar, Optional, Union

import numpy as np
import numpy.typing as npt
from typing_extensions import Self

from caen_felib import lib, _cache, _utils

# Comments on imports:
# - Self moved to typing on Python 3.11
# - numpy.typing.DTypeLike requires numpy >= 1.20


_DATA_TYPE_MAP: dict[str, npt.DTypeLike] = {
    'U8':           ct.c_uint8,
    'U16':          ct.c_uint16,
    'U32':          ct.c_uint32,
    'U64':          ct.c_uint64,
    'I8':           ct.c_int8,
    'I16':          ct.c_int16,
    'I32':          ct.c_int32,
    'I64':          ct.c_int64,
    'CHAR':         ct.c_char,
    'BOOL':         ct.c_bool,
    'SIZE_T':       ct.c_size_t,
    'PTRDIFF_T':    ct.c_ssize_t,  # not exactly the same, but should be fine at least on supported platforms
    'FLOAT':        ct.c_float,
    'DOUBLE':       ct.c_double,
    'LONG DOUBLE':  ct.c_longdouble,
}


@dataclass(**_utils.dataclass_slots)
class Data:
    """
    Class representing data set by Node.set_read_data_format(). It holds
    a `numpy.ndarray` in value allocated with shape specified in the
    data format.
    """

    # Public members
    name: str  ## Field name
    type: str  ## Field type
    dim: int = field(default=0, repr=False)  ## Field dimension
    shape: list[int] = field(default_factory=list, repr=False)  ## Field shape

    # Private members
    __value: np.ndarray = field(init=False, repr=False)
    __proxy_value_2d: Optional[np.ndarray] = field(default=None, repr=False)
    __arg: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.dim > 2:
            raise ValueError('dim cannot be larger than 2')
        if self.dim != len(self.shape):
            raise ValueError('shape length must match dim')
        dtype = _DATA_TYPE_MAP.get(self.type)
        if dtype is None:
            raise ValueError('Invalid data type')
        # Memory allocation
        self.__value = np.empty(self.shape, dtype=dtype)
        self.__arg = self.__generate_arg()

    @property
    def value(self) -> np.ndarray:
        """Instance of `numpy.ndarray` that holds data"""
        return self.__value

    @property
    def arg(self) -> Any:
        """
        ctypes pointer to Data.value that is used within Node.read_data
        """
        return self.__arg

    def __generate_arg(self) -> Any:
        value: np.ndarray
        if self.dim < 2:
            # NumPy 0D and 1D arrays can be used directly.
            value = self.value
        else:
            # NumPy 2D arrays cannot be directly used because they are
            # implemented as contiguous memory blocks instead of arrays of
            # pointers, used by CAEN_FELib.
            # To overcome the problem we generate a proxy ndarray of pointers.
            ptr_gen = (v.ctypes.data for v in self.value)
            self.__proxy_value_2d = np.fromiter(ptr_gen, dtype=ct.c_void_p)
            value = self.__proxy_value_2d
        # value.ctypes is equivalent to value.ctypes.data_as(ctypes.c_void_p),
        # that is fine for us.
        return value.ctypes

    def __str__(self) -> str:
        return self.name


@unique
class NodeType(IntEnum):
    """
    Binding of ::CAEN_FELib_NodeType_t
    """
    UNKNOWN = -1
    PARAMETER = 0
    COMMAND = 1
    FEATURE = 2
    ATTRIBUTE = 3
    ENDPOINT = 4
    CHANNEL = 5
    DIGITIZER = 6
    FOLDER = 7
    LVDS = 8
    VGA = 9
    HV_CHANNEL = 10
    MONOUT = 11
    VTRACE = 12
    GROUP = 13


@dataclass(**_utils.dataclass_slots_weakref)
class Node:
    """
    Class representing a node.
    """

    # Public members
    handle: int  ## Handle representing the node on the C library
    root_node: Optional[Self]  ## Root node, set to None on root node (stored to prevent g.c.)

    # Private members
    __opened: bool = field(init=False, repr=False)

    # Static private members
    __cache_manager: ClassVar[_cache.Manager] = _cache.Manager()

    def __post_init__(self) -> None:
        self.__opened = self.root_node is None

    def __del__(self) -> None:
        if self.__opened:
            self.close()

    # C API bindings

    @classmethod
    def open(cls: type[Self], url: str) -> Self:
        """
        Binding of CAEN_FELib_Open()

        Example:
        ```
        with device.connect("dig2://<host>") as dig:
            # Do stuff here...
        ```

        @sa device.connect()
        @param[in] url				URL of device to connect (a string, with format `scheme://[host][/path][?query][#fragment]`)
        @return						the digitizer node
        @exception					error.Error in case of error
        """
        value = ct.c_uint64()
        lib.open(_utils.to_bytes(url), value)
        return cls(value.value, None)

    @_cache.clear(cache_manager=__cache_manager)
    def close(self) -> None:
        """
        Binding of CAEN_FELib_Close()

        This will also clear class cache to remove all references to
        child nodes. It will impact also nodes of other digitizer, but
        is required to allow garbage collection of unused nodes.

        @exception					error.Error in case of error
        """
        lib.close(self.handle)
        self.__opened = False

    def get_impl_lib_version(self) -> str:
        """
        Binding of CAEN_FELib_GetImplLibVersion()

        @return						version (a string)
        @exception					error.Error in case of error
        """
        value = ct.create_string_buffer(16)
        lib.get_impl_lib_version(self.handle, value)
        return value.value.decode()

    @_cache.cached(cache_manager=__cache_manager)
    def get_child_nodes(self, path: Optional[str] = None, initial_size: int = 2**6) -> tuple[Self, ...]:
        """
        Binding of CAEN_FELib_GetChildHandles()

        @sa child_nodes
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @param[in] initial_size		inizial size to allocate for the first iteration
        @return						child nodes (a list)
        @exception					error.Error in case of error
        """
        b_path = _utils.to_bytes_opt(path)
        while True:
            child_handles = np.empty([initial_size], dtype=ct.c_uint64)
            child_handles_arg = child_handles.ctypes.data_as(ct.POINTER(ct.c_uint64))
            res = lib.get_child_handles(self.handle, b_path, child_handles_arg, initial_size)
            if res <= initial_size:
                return tuple(self.__generate_child(handle.item()) for handle in child_handles[:res])
            initial_size = res

    @_cache.cached(cache_manager=__cache_manager)
    def get_parent_node(self, path: Optional[str] = None) -> Self:
        """
        Binding of CAEN_FELib_GetParentHandle()

        @sa parent_node
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @return						parent node
        @exception					error.Error in case of error
        """
        value = ct.c_uint64()
        lib.get_parent_handle(self.handle, _utils.to_bytes_opt(path), value)
        return self.__generate_child(value.value)

    @_cache.cached(cache_manager=__cache_manager)
    def get_node(self, path: Optional[str] = None) -> Self:
        """
        Binding of CAEN_FELib_GetHandle()

        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @return						node at the provided path
        @exception					error.Error in case of error
        """
        value = ct.c_uint64()
        lib.get_handle(self.handle, _utils.to_bytes_opt(path), value)
        return self.__generate_child(value.value)

    @_cache.cached(cache_manager=__cache_manager)
    def get_path(self) -> str:
        """
        Binding of CAEN_FELib_GetPath()

        @sa path
        @return						absolute path of the provided handle (a string)
        @exception					error.Error in case of error
        """
        value = ct.create_string_buffer(256)
        lib.get_path(self.handle, value)
        return value.value.decode()

    @_cache.cached(cache_manager=__cache_manager)
    def get_node_properties(self, path: Optional[str] = None) -> tuple[str, NodeType]:
        """
        Binding of CAEN_FELib_GetNodeProperties()

        @sa name and Node.type
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @return						tuple containing node name (a string) and the node type (a NodeType)
        @exception					error.Error in case of error
        """
        name = ct.create_string_buffer(32)
        node_type = ct.c_int()
        lib.get_node_properties(self.handle, _utils.to_bytes_opt(path), name, node_type)
        return name.value.decode(), NodeType(node_type.value)

    def get_device_tree(self, initial_size: int = 2**22) -> dict:
        """
        Binding of CAEN_FELib_GetDeviceTree()

        @param[in] initial_size		inizial size to allocate for the first iteration
        @return						JSON representation of the node structure (a dictionary)
        @exception					error.Error in case of error
        """
        while True:
            device_tree = ct.create_string_buffer(initial_size)
            res = lib.get_device_tree(self.handle, device_tree, initial_size)
            if res < initial_size:  # equal not fine, see docs
                return loads(device_tree.value.decode())
            initial_size = res

    def get_value(self, path: Optional[str] = None) -> str:
        """
        Binding of CAEN_FELib_GetValue()

        @sa value
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @return						value of the node (a string)
        @exception					error.Error in case of error
        """
        value = ct.create_string_buffer(256)
        lib.get_value(self.handle, _utils.to_bytes_opt(path), value)
        return value.value.decode()

    def get_value_with_arg(self, path: Optional[str], arg: str) -> str:
        """
        Binding of CAEN_FELib_GetValue()

        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @param[in] arg				optional argument (either a string or `None` that is interpreted as an empty string)
        @return						value of the node (a string)
        @exception					error.Error in case of error
        """
        value = ct.create_string_buffer(_utils.to_bytes(arg), 256)
        lib.get_value(self.handle, _utils.to_bytes_opt(path), value)
        return value.value.decode()

    def set_value(self, path: Optional[str], value: str) -> None:
        """
        Binding of CAEN_FELib_SetValue()

        @sa value
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @param[in] value			value to set (a string)
        @exception					error.Error in case of error
        """
        lib.set_value(self.handle, _utils.to_bytes_opt(path), _utils.to_bytes(value))

    def get_user_register(self, address: int) -> int:
        """
        Binding of CAEN_FELib_GetUserRegister()

        @param[in] address			user register address
        @return						value of the register (a int)
        @exception					error.Error in case of error
        """
        value = ct.c_uint32()
        lib.get_user_register(self.handle, address, value)
        return value.value

    def set_user_register(self, address: int, value: int) -> None:
        """
        Binding of CAEN_FELib_SetUserRegister()

        @param[in] address			user register address
        @param[in] value			value of the register
        @exception					error.Error in case of error
        """
        lib.set_user_register(self.handle, address, value)

    def send_command(self, path: Optional[str] = None) -> None:
        """
        Binding of CAEN_FELib_SendCommand()

        @sa __call__
        @param[in] path				relative path of a node (either a string or `None` that is interpreted as an empty string)
        @exception					error.Error in case of error
        """
        lib.send_command(self.handle, _utils.to_bytes_opt(path))

    def set_read_data_format(self, fmt: Sequence[Union[Mapping[str, Any]]]) -> tuple[Data, ...]:
        """
        Binding of CAEN_FELib_SetReadDataFormat()

        In addition to what happens in C library, it also allocate data.
        Size of fields with `dim > 0` must be specified by a `"shape"`
        entry in the field description, that is a vector passed to the
        `shape` argument of `np.empty` constructor. On fields with
        `dim == 0` the shape can be omitted, and is set to `[]` by
        default. Fields can be accessed on data attribute of this class,
        that is a list of Data inizialized with the field descriptions,
        in the same order of @p format.

        Example:
        ```
        nch = dig.par.numch.value
        reclen = dig.par.recordlengths.value
        format = [
            {
                'name': 'WAVEFORM',
                'type': 'U16',
                'dim': 2,
                'shape': [nch, reclen],
            },
        ]
        data = ep_node.set_read_data_format(format)
        print(data[0])
        ```

        @param[in] fmt				JSON representation of the format, in compliance with the endpoint "format" property (a list of dictionaries)
        @return						Tuple of Data with allocated buffers of specified dim and shape, to be passed as second argument of read_data()
        @exception					error.Error in case of error
        """
        lib.set_read_data_format(self.handle, dumps(fmt).encode())

        # Important:
        # Do not update lib.ReadData.argtypes with data.argtype because lib.ReadData
        # is shared with all other endpoints and it would not be thread safe.
        # More details on a comment on the lib._Lib constructor.
        # Possible unsafe code could be something like:
        # lib.ReadData.argtypes = [ct.c_uint64, ct.c_int] + [d.argtype for d in self.data]

        # Allocate requested fields
        return tuple(Data(**f) for f in fmt)

    def read_data(self, timeout: int, data: Sequence[Data]) -> None:
        """
        Binding of CAEN_FELib_ReadData()

        Unlike what happens in C library, variadic arguments are added
        automatically according to what has been specified by
        set_read_data_format(). Data can be retrieved using the data
        attribute of this class.

        Example:
        ```
        # Start acquisition
        dig.cmd.armacquisition()
        dig.cmd.swstartacquisition()

        while True:
            try:
                ep_node.read_data(100, data)
            except error.Error as ex:
                if ex.code is error.ErrorCode.TIMEOUT:
                    continue
                elif ex.code is error.ErrorCode.STOP:
                    break
                else:
                    raise ex

            # Do stuff with data
            for field in data:
                print(field.value)

        dig.cmd.disarmacquisition()
        ```

        @param[in] timeout			timeout of the function in milliseconds; if this value is -1 the function is blocking with infinite timeout
        @param[out] data			The object returned by set_read_data_format().
        @exception					error.Error in case of error
        """
        lib.read_data(self.handle, timeout, *(d.arg for d in data))

    def has_data(self, timeout: int) -> None:
        """
        Binding of CAEN_FELib_HasData()

        @param[in] timeout			timeout of the function in milliseconds; if this value is -1 the function is blocking with infinite timeout
        @exception					error.Error in case of error
        """
        lib.has_data(self.handle, timeout)

    # Private utilities

    def __generate_child(self, handle: int) -> Self:
        root_node = self if self.root_node is None else self.root_node
        return type(self)(handle, root_node)

    # Python utilities

    @property
    def name(self) -> str:
        """Node name"""
        return self.get_node_properties(None)[0]

    @property
    def type(self) -> NodeType:
        """Node type"""
        return self.get_node_properties(None)[1]

    @property
    def path(self) -> str:
        """Node absolute path"""
        return self.get_path()

    @property
    def parent_node(self) -> Self:
        """Parent node"""
        return self.get_parent_node(None)

    @property
    def child_nodes(self) -> tuple[Self, ...]:
        """list of child nodes"""
        return self.get_child_nodes(None)

    @property
    def value(self) -> str:
        """Current value"""
        return self.get_value(None)

    @value.setter
    def value(self, value: str) -> None:
        self.set_value(None, value)

    def __call__(self) -> None:
        """Execute node"""
        self.send_command(None)

    def __enter__(self) -> Self:
        """Used by `with`"""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Called when exiting from `with` block"""
        self.close()

    def __iter__(self) -> Iterator[Self]:
        """Utility to simplify node browsing"""
        yield from self.child_nodes

    def __getitem__(self, index: Any) -> Self:
        return self.get_node(f'/{index}')

    def __getattr__(self, name: str) -> Self:
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return self.__getitem__(name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Node):
            return NotImplemented
        return self.handle == other.handle

    def __hash__(self) -> int:
        return hash(self.handle)

    def __repr__(self) -> str:
        """Override default dataclass representation"""
        return f'{self.__class__.__name__}({self.path})'

    def __str__(self) -> str:
        return self.path


@wraps(Node.open)
def connect(url: str) -> Node:
    """Binding of Node.open"""
    return Node.open(url)
