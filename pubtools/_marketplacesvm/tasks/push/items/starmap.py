import logging
from typing import Any, Dict, List

from attrs import define, evolve, field
from attrs.validators import deep_mapping, instance_of
from pushsource import VMIPushItem
from starmap_client.models import Destination

log = logging.getLogger("pubtools.marketplacesvm")


@define
class MappedVMIPushItem:
    """Wrap a VMIPushItem and its variations with additional information from StArMap."""

    _push_item: VMIPushItem = field(alias="_push_item", validator=instance_of(VMIPushItem))
    """The underlying pushsource.VMIPushItem."""

    clouds: Dict[str, List[Destination]] = field(
        validator=deep_mapping(
            key_validator=instance_of(str),
            value_validator=instance_of(list),
            mapping_validator=instance_of(dict),
        )
    )
    """Dictionary with the marketplace accounts and its destinations."""

    @property
    def state(self) -> str:
        """
        Get the wrapped push item state.

        The expected states are:

        +--------------+-----------+------------------------------------------+
        | State        | Exit type | Description                              |
        +==============+===========+==========================================+
        | PENDING      | Success   | The image is waiting for one of these    |
        |              |           | operations:                              |
        |              |           |                                          |
        |              |           | - Upload to the cloud marketplace        |
        |              |           | - Vulnerability scan result (AWS only)   |
        |              |           | - Product listing go live                |
        +--------------+-----------+------------------------------------------+
        | PUSHED       | Success   | The image was successfully uploaded and  |
        |              |           | associated with a product listing.       |
        +--------------+-----------+------------------------------------------+
        | UPLOADFAILED | Failure   | Failed to upload this content to the     |
        |              |           | remote server.                           |
        +--------------+-----------+------------------------------------------+
        | NOTPUSHED    | Failure   | An error occurred while publishing       |
        |              |           | the push item to a product listing.      |
        +--------------+-----------+------------------------------------------+
        """
        return self._push_item.state

    @state.setter
    def state(self, state: str) -> None:
        if not isinstance(state, str):
            raise TypeError(f"Expected to receive a string for state, got: {type(state)}")
        self._push_item = evolve(self._push_item, state=state)

    @property
    def marketplaces(self) -> List[str]:
        """Return a list of marketplaces accounts for the stored PushItem."""
        return list(self.clouds.keys())

    @property
    def destinations(self) -> List[Destination]:
        """Return a list with all destinations associated with the stored push item."""
        dest = []
        for mkt in self.marketplaces:
            dest.extend([dst for dst in self.clouds[mkt]])
        return dest

    @property
    def meta(self) -> Dict[str, Any]:
        """Return all metadata associated with the stored push item."""
        res = {}
        for dest in self.destinations:
            if dest.meta:
                res.update({k: v for k, v in dest.meta.items()})
        return res

    @property
    def push_item(self) -> VMIPushItem:
        """Return the wrapped push item with the missing attributes set."""
        if self._push_item.dest:  # If it has destinations it means we already mapped it
            return self._push_item

        # Update the missing fields for push item and its release
        pi = self._push_item
        release = pi.release

        # Try to update the release.arch info
        if not release.arch:
            release = evolve(release, arch=self.destinations[0].architecture)
            pi = evolve(pi, release=release)

        # Update the destinations
        pi = evolve(pi, dest=self.destinations)

        # Update the push item attributes for each type using the attrs hidden annotation
        ignore_unset_attributes = ["md5sum", "sha256sum", "signing_key", "origin"]
        new_attrs = {}
        for attribute in pi.__attrs_attrs__:
            if not getattr(pi, attribute.name, None):  # If attribute is not set
                value = self.meta.get(attribute.name, None)  # Get the value from "dst.meta"
                if value:  # If the value is set in the metadata
                    new_attrs.update({attribute.name: value})  # Set the new value
                elif attribute.name not in ignore_unset_attributes:
                    log.warning(
                        "Missing information for the attribute %s.%s, leaving it unset.",
                        self._push_item.name,
                        attribute.name,
                    )

        # Finally return the updated push_item
        self._push_item = evolve(pi, **new_attrs)
        return self._push_item

    @push_item.setter
    def push_item(self, x: VMIPushItem) -> None:
        self._push_item = x

    def get_metadata_for_mapped_item(self, destination: Destination) -> Dict[str, Any]:
        """
        Return all metadata related to a push item containing a single destination.

        Args:
            destination
                A single Destination to obtain the related metadata.
        Returns:
            The related metadata for the given destination.
        """
        for dst in self.destinations:
            if dst == destination:
                return dst.meta
        return {}
