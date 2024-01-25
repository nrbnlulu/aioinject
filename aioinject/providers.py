from __future__ import annotations

import collections.abc
import enum
import functools
import inspect
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isclass
from typing import (
    Annotated,
    Any,
    ClassVar,
    Generic,
    Protocol,
    TypeAlias,
    TypeVar,
    runtime_checkable,
)

import typing_extensions

from aioinject.markers import Inject
from aioinject.utils import (
    _get_type_hints,
    is_context_manager_function,
    remove_annotation,
)


_T = TypeVar("_T")



@dataclass(slots=True, kw_only=True, frozen=True)
class Dependency(Generic[_T]):
    name: str
    type_: type[_T]


def _get_annotation_args(type_hint: Any) -> tuple[type, tuple[Any, ...]]:
    try:
        dep_type, *args = typing.get_args(type_hint)
    except ValueError:
        dep_type, args = type_hint, []
    return dep_type, tuple(args)


def _find_inject_marker_in_annotation_args(
    args: tuple[Any, ...],
) -> Inject | None:
    for arg in args:
        try:
            if issubclass(arg, Inject):
                return Inject()
        except TypeError:
            pass

        if isinstance(arg, Inject):
            return arg
    return None


def collect_dependencies(
    dependant: typing.Callable[..., object] | dict[str, Any], ctx: dict[str, type[Any]] | None =  None
) -> typing.Iterable[Dependency[object]]:
    if not isinstance(dependant, dict):
        with remove_annotation(dependant.__annotations__, "return"):
            type_hints = _get_type_hints(dependant, context=ctx)
    else:
        type_hints = dependant
    for name, hint in type_hints.items():
        dep_type, args = _get_annotation_args(hint)
        inject_marker = _find_inject_marker_in_annotation_args(args)
        if inject_marker is None:
            continue

        yield Dependency(
            name=name,
            type_=dep_type,
        )


def _get_provider_type_hints(provider: Provider[Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    source = provider.impl
    if inspect.isclass(source):
        source = source.__init__

    if isinstance(source, functools.partial):
        return {}

    type_hints = _get_type_hints(source, context=context)
    for key, value in type_hints.items():
        _, args = _get_annotation_args(value)
        for arg in args:
            if isinstance(arg, Inject):
                break
        else:
            type_hints[key] = Annotated[value, Inject]

    return type_hints


_GENERATORS = {
    collections.abc.Generator,
    collections.abc.Iterator,
}
_ASYNC_GENERATORS = {
    collections.abc.AsyncGenerator,
    collections.abc.AsyncIterator,
}

_FactoryType: TypeAlias = (
    type[_T]
    | typing.Callable[..., _T]
    | typing.Callable[..., collections.abc.Awaitable[_T]]
    | typing.Callable[..., collections.abc.Coroutine[Any, Any, _T]]
    | typing.Callable[..., collections.abc.Iterator[_T]]
    | typing.Callable[..., collections.abc.AsyncIterator[_T]]
)


def _guess_return_type(factory: _FactoryType[_T], context: dict[str, type[Any] ] | None = None) -> type[_T]:
    if isclass(factory):
        return typing.cast(type[_T], factory)

    type_hints = _get_type_hints(factory, context=context)
    try:
        return_type = type_hints["return"]
    except KeyError as e:
        msg = f"Factory {factory.__qualname__} does not specify return type."
        raise ValueError(msg) from e

    if origin := typing.get_origin(return_type):
        args = typing.get_args(return_type)

        maybe_wrapped = getattr(  # @functools.wraps
            factory,
            "__wrapped__",
            factory,
        )
        if origin in _ASYNC_GENERATORS and inspect.isasyncgenfunction(
            maybe_wrapped,
        ):
            return args[0]
        if origin in _GENERATORS and inspect.isgeneratorfunction(
            maybe_wrapped,
        ):
            return args[0]

    return return_type


class DependencyLifetime(enum.Enum):
    transient = enum.auto()
    scoped = enum.auto()
    singleton = enum.auto()


@runtime_checkable
class Provider(Protocol[_T]):
    impl: Any
    lifetime: DependencyLifetime
    _cached_dependecies: tuple[Dependency[object], ...]

    async def provide(self, kwargs: Mapping[str, Any]) -> _T:
        ...

    def provide_sync(self, kwargs: Mapping[str, Any]) -> _T:
        ...
    
    def resolve_type(self, context: dict[str, Any] | None = None) -> type[_T]:
        ...


    
    def type_hints(self, context: dict[str, Any] | None) -> dict[str, Any]:
        ...

    @property
    def is_async(self) -> bool:
        ...

    def resolve_dependencies(self, context: dict[str, Any] | None = None) ->  tuple[Dependency[object], ...]:
        if deps := getattr(self, "_cached_dependecies", None):
            return deps
        ret = tuple(collect_dependencies(self.type_hints(context), ctx=context))
        self._cached_dependecies = ret
        return ret
  
    @functools.cached_property
    def is_generator(self) -> bool:
        return is_context_manager_function(self.impl)

    def __repr__(self) -> str:
        return f"{self.__class__.__qualname__}(type={self.type_}, implementation={self.impl})"


class Scoped(Provider[_T]):
    lifetime = DependencyLifetime.scoped

    def __init__(
        self,
        factory: _FactoryType[_T],
        type_: type[_T] | None = None,
    ) -> None:
        self.type_ = type_
        self.impl = factory

    def resolve_type(self, context: dict[str, Any] | None = None) -> type[_T]:
        return self.type_ or _guess_return_type(self.impl, context=context)

    def provide_sync(self, kwargs: Mapping[str, Any]) -> _T:
        return self.impl(**kwargs)  # type: ignore[return-value]

    async def provide(self, kwargs: Mapping[str, Any]) -> _T:
        if self.is_async:
            return await self.impl(**kwargs)  # type: ignore[misc]

        return self.provide_sync(kwargs)

    def type_hints(self, context: dict[str, Any] | None) -> dict[str, Any]:
        type_hints = _get_provider_type_hints(self, context=context)
        if "return" in type_hints:
            del type_hints["return"]
        return type_hints

    @functools.cached_property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.impl)


@typing_extensions.deprecated(
    "Callable is deprecated, use aioinject.Scoped instead",
)
class Callable(Scoped[_T]):
    pass


@typing_extensions.deprecated(
    "Factory is deprecated, use aioinject.Scoped instead",
)
class Factory(Scoped[_T]):
    pass


class Singleton(Scoped[_T]):
    lifetime = DependencyLifetime.singleton


class Transient(Scoped[_T]):
    lifetime = DependencyLifetime.transient


class Object(Provider[_T]):
    type_hints: ClassVar[dict[str, Any]] = {}
    is_async = False
    impl: _T
    lifetime = DependencyLifetime.scoped  # It's ok to cache it

    def __init__(
        self,
        object_: _T,
        type_: type[_T] | None = None,
    ) -> None:
        self.type_ = type_ or type(object_)
        self.impl = object_

    def resolve_type(self, _: dict[str, Any] | None = None) -> type[_T]:
        return self.type_

    def provide_sync(self, kwargs: Mapping[str, Any]) -> _T:  # noqa: ARG002
        return self.impl

    async def provide(self, kwargs: Mapping[str, Any]) -> _T:  # noqa: ARG002
        return self.impl
