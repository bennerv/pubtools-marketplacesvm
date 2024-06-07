# SPDX-License-Identifier: GPL-3.0-or-later
import re
import time
from typing import Any, Dict, Generator
from unittest import mock

import pytest
from _pytest.capture import CaptureFixture
from attrs import evolve
from pushsource import AmiPushItem, VHDPushItem
from starmap_client.models import QueryResponse, Workflow

from pubtools._marketplacesvm.cloud_providers import CloudProvider
from pubtools._marketplacesvm.tasks.community_push import CommunityVMPush, entry_point

from ..command import CommandTester


class UploadResponse(dict):
    """Represent a fake S3 upload response."""

    def __init__(self, *args, **kwargs):
        super(UploadResponse, self).__init__(*args, **kwargs)
        self.__dict__ = self


class FakeCloudProvider(CloudProvider):
    """Define a fake cloud provider for testing."""

    @classmethod
    def from_credentials(cls, _):
        return cls()

    def _upload(self, push_item, custom_tags=None, **kwargs):
        time.sleep(2)
        return push_item, UploadResponse({"id": "foo", "name": "bar"})

    def _pre_publish(self, push_item, **kwargs):
        return push_item, kwargs

    def _publish(self, push_item, nochannel, overwrite, preview_only):
        return push_item, nochannel


@pytest.fixture()
def fake_cloud_instance() -> Generator[mock.MagicMock, None, None]:
    with mock.patch("pubtools._marketplacesvm.tasks.push.MarketplacesVMPush.cloud_instance") as m:
        m.return_value = FakeCloudProvider()
        yield m


@pytest.fixture()
def fake_source(ami_push_item: AmiPushItem) -> Generator[mock.MagicMock, None, None]:
    with mock.patch("pubtools._marketplacesvm.tasks.community_push.command.Source") as m:
        m.get.return_value.__enter__.return_value = [ami_push_item]
        yield m


@pytest.fixture()
def fake_starmap(starmap_query_aws: QueryResponse) -> Generator[mock.MagicMock, None, None]:
    with mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush.starmap") as m:
        m.query_image_by_name.side_effect = [starmap_query_aws]
        yield m


@pytest.fixture(autouse=True)
def fake_rhsm_api(requests_mocker):
    requests_mocker.register_uri(
        "GET",
        re.compile("amazon/provider_image_groups"),
        json={
            "body": [
                {"name": "sample_product_HOURLY", "providerShortName": "awstest"},
                {"name": "sample_product", "providerShortName": "awstest"},
                {"name": "RHEL_HA", "providerShortName": "awstest"},
                {"name": "SAP", "providerShortName": "awstest"},
            ]
        },
    )
    requests_mocker.register_uri("POST", re.compile("amazon/region"))
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"))
    requests_mocker.register_uri("POST", re.compile("amazon/amis"))


def test_do_community_push(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
) -> None:
    """Test a successfull community-push."""
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )

    fake_source.get.assert_called_once()
    fake_starmap.query_image_by_name.assert_called_once_with(
        name="test-build", version="7.0", workflow=Workflow.community
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush.starmap")
def test_do_community_push_no_mappings(
    mock_starmap: mock.MagicMock,
    fake_source: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
) -> None:
    """Test a community-push with no mappings definition from StArMap."""
    mock_starmap.query_image_by_name.return_value = None
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_do_community_push_skip_billing_codes(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a successfull community-push without requiring billing codes."""
    monkeypatch.setattr(CommunityVMPush, '_REQUIRE_BC', False)
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )

    fake_source.get.assert_called_once()
    fake_starmap.query_image_by_name.assert_called_once_with(
        name="test-build", version="7.0", workflow=Workflow.community
    )


@pytest.mark.parametrize("product_name", ["RHEL_HA", "SAP"])
@mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush.starmap")
@mock.patch("pubtools._marketplacesvm.tasks.community_push.command.Source")
def test_do_community_push_skip_houly_sap_ha(
    mock_source: mock.MagicMock,
    mock_starmap: mock.MagicMock,
    product_name: str,
    ami_push_item: AmiPushItem,
    starmap_ami_billing_config: Dict[str, Any],
    command_tester: CommandTester,
) -> None:
    # Set the custom product name to the push item
    release = ami_push_item.release
    release = evolve(release, product=product_name)
    pi = evolve(ami_push_item, release=release)
    mock_source.get.return_value.__enter__.return_value = [pi]

    # Create a fake mapping
    policy = {
        "name": product_name,
        "workflow": "community",
        "mappings": {
            "aws_storage": [
                {
                    "architecture": "x86_64",
                    "destination": "fake-destination-access",
                    "overwrite": False,
                    "restrict_version": False,
                    "provider": "awstest",
                    "meta": {"billing-code-config": starmap_ami_billing_config},
                }
            ]
        },
    }
    mock_starmap.query_image_by_name.return_value = QueryResponse.from_json(policy)

    # Test
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush.starmap")
def test_do_community_push_no_billing_config(
    mock_starmap: mock.MagicMock,
    fake_source: mock.MagicMock,
    ami_push_item: AmiPushItem,
    command_tester: CommandTester,
) -> None:
    # Create a fake mapping
    policy = {
        "name": "sample-product",
        "workflow": "community",
        "mappings": {
            "aws_storage": [
                {
                    "architecture": "x86_64",
                    "destination": "fake-destination-access",
                    "overwrite": False,
                    "restrict_version": False,
                    "provider": "awstest",
                    "meta": {},
                }
            ]
        },
    }
    mock_starmap.query_image_by_name.return_value = QueryResponse.from_json(policy)

    # Test
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush.starmap")
def test_do_community_push_major_minor(
    mock_starmap: mock.MagicMock,
    fake_source: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Create a fake mapping
    monkeypatch.setattr(CommunityVMPush, '_REQUIRE_BC', False)
    policy = {
        "name": "sample-product",
        "workflow": "community",
        "mappings": {
            "aws_storage": [
                {
                    "architecture": "x86_64",
                    "destination": "fake-destination-access",
                    "overwrite": False,
                    "restrict_version": False,
                    "restrict_major": 2,
                    "restrict_minor": 2,
                    "provider": "awstest",
                    "meta": {},
                }
            ]
        },
    }
    mock_starmap.query_image_by_name.return_value = QueryResponse.from_json(policy)

    # Test
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.command.Source")
def test_not_ami_push_item(
    mock_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    ami_push_item: AmiPushItem,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
) -> None:
    """Ensure non AMI pushitem is skipped from inclusion in push list."""
    azure_push_item = VHDPushItem(name="foo", src="bar", description="test")
    mock_source.get.return_value.__enter__.return_value = [
        azure_push_item,
        ami_push_item,
    ]

    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_no_credentials(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    ami_push_item: AmiPushItem,
    command_tester: CommandTester,
) -> None:
    """Raises an error that marketplaces credentials where not provided to process the AMI."""
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_no_source(command_tester: CommandTester, capsys: CaptureFixture) -> None:
    """Checks that exception is raised when the source is missing."""
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
        ],
    )
    _, err = capsys.readouterr()
    assert "error: too few arguments" or "error: the following arguments are required" in err


def test_no_rhsm_url(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    command_tester: CommandTester,
    capsys: CaptureFixture,
) -> None:
    """Checks that exception is raised when the rhsm-url is missing."""
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )
    _, err = capsys.readouterr()
    assert "RHSM URL not provided for the RHSM client" in err


def test_not_in_rhsm(
    fake_starmap: mock.MagicMock,
    fake_source: mock.MagicMock,
    starmap_query_aws: QueryResponse,
    command_tester: CommandTester,
) -> None:
    """Ensure there's an error when the product is not in RHSM."""
    for dest_list in starmap_query_aws.clouds.values():
        for dest in dest_list:
            dest.meta["release"]["product"] = "not_in_rhsm_product"

    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_rhsm_create_region_failure(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
    requests_mocker,
) -> None:
    """Push fails when the region couldn't be created on RHSM."""
    requests_mocker.register_uri("POST", re.compile("amazon/region"), status_code=500)
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://example.com",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_rhsm_create_image_failure(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
    requests_mocker,
) -> None:
    """Push fails if the image metadata couldn't be created on RHSM for a new image."""
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=400)
    requests_mocker.register_uri("POST", re.compile("amazon/amis"), status_code=500)
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://example.com",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.CommunityVMPush._push_to_community")
@mock.patch("pubtools._marketplacesvm.tasks.community_push.command.Source")
def test_empty_value_to_collect(
    mock_source: mock.MagicMock,
    mock_push: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    ami_push_item: AmiPushItem,
    command_tester: CommandTester,
    starmap_query_aws: QueryResponse,
) -> None:
    """Ensure the JSONL exclude missing fields."""
    mock_source.get.return_value.__enter__.return_value = [ami_push_item]
    mock_push.return_value = [
        {
            "push_item": ami_push_item,
            "state": ami_push_item.state,
            "marketplace": None,
            "missing_key": None,
            "starmap_query": starmap_query_aws,
        }
    ]

    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )


def test_beta_images_live_push(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    fake_cloud_instance: mock.MagicMock,
    command_tester: CommandTester,
) -> None:
    """Test a beta community live push."""
    command_tester.test(
        lambda: entry_point(CommunityVMPush),
        [
            "test-push",
            "--starmap-url",
            "https://starmap-example.com",
            "--credentials",
            "eyJtYXJrZXRwbGFjZV9hY2NvdW50IjogInRlc3QtbmEiLCAiYXV0aCI6eyJmb28iOiJiYXIifQo=",
            "--rhsm-url",
            "https://rhsm.com/test/api/",
            "--debug",
            "--beta",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )
