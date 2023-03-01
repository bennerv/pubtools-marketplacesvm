# SPDX-License-Identifier: GPL-3.0-or-later
import datetime
import json
import logging
import os
import sys
from typing import Any, Dict, Iterator, List

from attrs import asdict, evolve
from more_executors import Executors
from pushsource import Source, VMIPushItem

from ...arguments import SplitAndExtend
from ...services import CloudService, CollectorService, StarmapService
from ...task import MarketplacesVMTask
from ..push.items import MappedVMIPushItem, State

log = logging.getLogger("pubtools.marketplacesvm")


class MarketplacesVMPush(MarketplacesVMTask, CloudService, CollectorService, StarmapService):
    """Push and publish content to various cloud marketplaces."""

    _REQUEST_THREADS = int(os.environ.get("MARKETPLACESVM_PUSH_REQUEST_THREADS", "5"))

    @property
    def raw_items(self) -> Iterator[VMIPushItem]:
        """
        Load all push items from the given source(s) and yield them.

        Yields:
            The VMIPushItems from the given sources.
        """
        for source_url in self.args.source:
            with Source.get(source_url) as source:
                log.info("Loading items from %s", source_url)
                for item in source:
                    if not isinstance(item, VMIPushItem):
                        log.warning(
                            "Push Item %s at %s is not a VMIPushItem, dropping it from the queue.",
                            item.name,
                            item.src,
                        )
                        continue
                    yield item

    @property
    def mapped_items(self) -> List[MappedVMIPushItem]:
        """
        Return the mapped push item with destinations and metadata from StArMap.

        Returns
            The wrapped push item with the additional information from StArMap.
        """
        mapped_items = []
        for item in self.raw_items:
            log.debug("Retrieving the mappings for %s." % item.name)
            binfo = item.build_info
            query = self.starmap.query_image_by_name(name=binfo.name, version=binfo.version)
            mapped_items.append(MappedVMIPushItem(item, query.clouds))
        return mapped_items

    def _upload(self, marketplace: str, push_item: VMIPushItem) -> VMIPushItem:
        """
        Upload a single push item to the cloud marketplace and update the status.

        Args:
            marketplace:
                The account name (alias) for the marketplace to upload.
            push_item
                The item to upload
        Returns:
            The push item after the upload.
        """
        try:
            log.debug("Uploading the item %s to %s.", push_item.name, marketplace)
            pi, _ = self.cloud_instance(marketplace).upload(push_item)
            log.debug("Upload finished for %s on %s", push_item.name, marketplace)
        except Exception as exc:
            log.error("Failed to upload %s: %s", push_item.name, str(exc))
            pi = evolve(push_item, state=State.UPLOADFAILED)
        return pi

    def _publish(
        self, marketplace: str, push_item: VMIPushItem, pre_push: bool = True
    ) -> VMIPushItem:
        """
        Publish the VM image to all required marketplace listings.

        Args:
            marketplace:
                The account name (alias) for the marketplace to publish.
            push_item
                The item to publish in a cloud marketplace listing.
            pre_push
                If True it will only associate the images without publishing, if possible.
                This defaults to True
        Returns:
            The push item after publishing.
        """
        try:
            last_destination = ""
            for dest in push_item.dest:
                # We don't want to publish again the same offer when pre-push == False (go live)
                curr_dest = dest.destination.split("/")[0]  # get just the offer name, if applicable
                if not pre_push and curr_dest == last_destination:
                    log.debug("Push already done for offer %s", curr_dest)
                    continue

                log.debug(
                    "Pushing the item \"%s\" (pre-push=%s) to %s.",
                    push_item.name,
                    pre_push,
                    dest.destination,
                )
                single_dest_item = evolve(push_item, dest=dest.destination)

                pi, _ = self.cloud_instance(marketplace).publish(
                    single_dest_item, nochannel=pre_push, overwrite=dest.overwrite
                )

                last_destination = curr_dest
            # Once we process all destinations we set back the list of destinations
            pi = evolve(pi, dest=push_item.dest, state=State.PUSHED)
        except Exception as exc:
            log.error("Failed to publish %s: %s", push_item.name, str(exc))
            pi = evolve(push_item, state=State.NOTPUSHED)
        return pi

    def _push_to_cloud(self, mapped_item: MappedVMIPushItem) -> List[Dict[str, Any]]:
        """
        Perform the whole workflow to upload and publish the VM images in a single thread.

        Args:
            mapped_item
                The item to process.
        Returns:
            Dictionary with the resulting operation for the Collector service.
        """
        res = []
        for marketplace in mapped_item.marketplaces:
            # Upload the VM image to the marketplace
            mapped_item.push_item = self._upload(marketplace, mapped_item.push_item)

            # Associate image with Product/Offer/Plan and publish
            if mapped_item.state != State.UPLOADFAILED:
                # The first publish should always be with `pre_push` set True because it might
                # happen that one offer with multiple plans would receive the same image and
                # we can't `publish` the offer with just the first plan changed and try to change
                # the others (every plan should be changed while the offer is still on draft).
                #
                # Then this first `_publish` call is intended to only associate the image with
                # all the offers/plans but not change it to live, when this is applicable.
                mapped_item.push_item = self._publish(marketplace, mapped_item.push_item)

                # Once we associated all the images with their offer/plans it's now safe to call
                # again the publish if and only if `pre_push == False`.
                # The indepondent operation will guarantee that the images are already associated
                # with the Product/Offer/Plan and just the go-live part is called.
                if not self.args.pre_push:
                    mapped_item.push_item = self._publish(
                        marketplace, mapped_item.push_item, pre_push=False
                    )

            # Update the destinations from List[Destination] to List[str] for collection
            dest_list_str = [d.destination for d in mapped_item.push_item.dest]
            push_item_for_collection = evolve(mapped_item.push_item, dest=dest_list_str)

            # Append the data for collection
            res.append(
                {
                    "push_item": push_item_for_collection,
                    "state": mapped_item.state,
                    "marketplace": marketplace,
                    "destinations": mapped_item.clouds[marketplace],
                }
            )

        return res

    def collect_push_result(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Collect the push results and sends its json to the collector.

        Args:
            results
                The list of dictionaries containing the result data for the push collector.

        Returns:
            The result of push collector `attach_file` call.
        """

        def convert(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.strftime("%Y%m%d")

        push_items = []
        mod_result = []
        for result in results:
            res_dict = asdict(result["push_item"])
            # dict can't be modified during iteration.
            # so iterate over list of keys.
            for key in list(res_dict):
                if res_dict[key] is None:
                    del res_dict[key]
            mod_result.append(res_dict)
            push_items.append(result["push_item"])

        metadata = json.dumps(mod_result, default=convert, indent=2, sort_keys=True)
        self.collector.update_push_items(push_items).result()
        return self.collector.attach_file("clouds.json", metadata).result()

    def add_args(self):
        """Include the required CLI arguments for MarketplacesVMPush."""
        super(MarketplacesVMPush, self).add_args()

        self.parser.add_argument(
            "--skip",
            help="skip given comma-separated sub-steps",
            type=str,
            action=SplitAndExtend,
            split_on=",",
            default=[],
        )

        self.parser.add_argument(
            "--pre-push",
            action="store_true",
            help=(
                "Pre-push mode: do as much as possible without making content "
                "available to end-users, then stop. May be used to improve the "
                "performance of a subsequent full push."
            ),
        )

        self.parser.add_argument(
            "source",
            nargs="+",
            help="Source(s) of content to be pushed",
            action=SplitAndExtend,
            split_on=",",
        )

    def run(self):
        """Execute the push command workflow."""
        mapped_items = self.mapped_items

        executor = Executors.thread_pool(
            name="pubtools-marketplacesvm-push",
            max_workers=min(max(len(mapped_items), 1), self._REQUEST_THREADS),
        )

        to_await = []
        result = []
        for item in mapped_items:
            to_await.append(executor.submit(self._push_to_cloud, item))

        # waiting for results
        for f_out in to_await:
            result.extend(f_out.result())

        # process result for failures
        failed = False
        for r in result:
            if r.get("state", "") != State.PUSHED:
                failed = True

        # send to collector
        log.info("Collecting results")
        self.collect_push_result(result)

        if failed:
            log.error("Marketplace VM push failed")
            sys.exit(30)

        log.info("Marketplace VM push completed")
