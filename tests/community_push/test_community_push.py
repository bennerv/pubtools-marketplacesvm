# SPDX-License-Identifier: GPL-3.0-or-later
import re
from typing import Generator
from unittest import mock

import pytest
from _pytest.capture import CaptureFixture
from pushsource import AmiPushItem, VHDPushItem
from starmap_client.models import QueryResponse, Workflow

from pubtools._marketplacesvm.tasks.community_push import CommunityVMPush, entry_point

from ..command import CommandTester


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
        json={"body": [{"name": "SAMPLE_PRODUCT_HOURLY", "providerShortName": "awstest"}]},
    )
    requests_mocker.register_uri("POST", re.compile("amazon/region"))
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"))
    requests_mocker.register_uri("POST", re.compile("amazon/amis"))


def test_do_community_push(
    fake_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
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
            "--aws-provider-name",
            "awstest",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )

    fake_source.get.assert_called_once()
    fake_starmap.query_image_by_name.assert_called_once_with(
        name="test-build", version="7.0", workflow=Workflow.community
    )


@mock.patch("pubtools._marketplacesvm.tasks.community_push.command.Source")
def test_not_ami_push_item(
    mock_source: mock.MagicMock,
    fake_starmap: mock.MagicMock,
    ami_push_item: AmiPushItem,
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
            "--aws-provider-name",
            "awstest",
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
            "--aws-provider-name",
            "awstest",
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
            "--aws-provider-name",
            "awstest",
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
            "--aws-provider-name",
            "awstest",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )
    _, err = capsys.readouterr()
    assert "Exception: RHSM URL not provided for the RHSM client" in err


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
            "--aws-provider-name",
            "awstest",
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
            "--aws-provider-name",
            "awstest",
            "--debug",
            "koji:https://fakekoji.com?vmi_build=ami_build",
        ],
    )
