# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime
from typing import Any, Dict

import pytest
from attrs import evolve
from pushsource import AmiPushItem, AmiRelease, KojiBuildInfo
from starmap_client.models import QueryResponse

from pubtools._marketplacesvm.tasks.push.command import MarketplacesVMPush


@pytest.fixture(scope="session")
def monkeysession():
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope="session", autouse=True)
def marketplaces_vm_push(monkeysession: pytest.MonkeyPatch) -> None:
    """Set a single-thread for MarketplacesVMPush."""
    monkeysession.setattr(MarketplacesVMPush, '_REQUEST_THREADS', 1)
    monkeysession.setattr(MarketplacesVMPush, '_PROCESS_THREADS', 1)


@pytest.fixture
def release_params() -> Dict[str, Any]:
    return {
        "product": "sample-product",
        "version": "7.0",
        "arch": "x86_64",
        "respin": 1,
        "date": datetime.strptime("2023-12-12", "%Y-%m-%d"),
        "base_product": "sample-base",
        "base_version": "1.0",
        "variant": "variant",
        "type": "ga",
    }


@pytest.fixture
def push_item_params() -> Dict[str, str]:
    return {
        "name": "name",
        "description": "",
        "build_info": KojiBuildInfo(name="test-build", version="7.0", release="20230101"),
    }


@pytest.fixture
def ami_push_item(release_params: Dict[str, Any], push_item_params: Dict[str, str]) -> AmiPushItem:
    """Return a minimal AmiPushItem."""
    release = AmiRelease(**release_params)
    push_item_params.update({"name": "ami_pushitem", "release": release})
    return AmiPushItem(**push_item_params)


@pytest.fixture
def starmap_response_aws() -> Dict[str, Any]:
    return {
        "mappings": {
            "aws-na": [
                {
                    # FIXME: These may change once we create a proper mapping for
                    # community workflow
                    "architecture": "x86_64",
                    "destination": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                    "overwrite": True,
                    "stage_preview": False,
                    "delete_restricted": False,
                    "meta": {"tag1": "aws-na-value1", "tag2": "aws-na-value2"},
                    "tags": {"key1": "value1", "key2": "value2"},
                }
            ],
        },
        "name": "sample-product",
        "workflow": "community",
    }


@pytest.fixture
def starmap_query_aws(starmap_response_aws: Dict[str, Any]) -> QueryResponse:
    return QueryResponse.from_json(starmap_response_aws)


@pytest.fixture
def mapped_ami_push_item(
    ami_push_item: AmiPushItem, starmap_query_aws: QueryResponse
) -> AmiPushItem:
    destinations = []
    for _, dest_list in starmap_query_aws.clouds.items():
        for dest in dest_list:
            destinations.append(dest)
    return evolve(ami_push_item, dest=destinations)